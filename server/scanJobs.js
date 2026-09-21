/**
 * Scan jobs: run the Python ML pipeline for a UTC date and store its spills.
 *
 * - Single-flight per date: concurrent requests for the same date share one job.
 * - One pipeline process at a time (CPU + Copernicus quota); other dates wait in a queue.
 * - Progress lines (JSON on the child's stderr) are kept on the job and pushed over WebSocket.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const S1_START = '2014-10-03';

function todayUtc() {
  return new Date().toISOString().slice(0, 10);
}

function validateDate(date) {
  if (!DATE_RE.test(date || '') || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) return 'date must be YYYY-MM-DD';
  if (date < S1_START) return `Sentinel-1 archive starts ${S1_START}`;
  if (date > todayUtc()) return 'date is in the future';
  return null;
}

function createScanJobs({ db, config, broadcast = () => {}, log = console }) {
  const jobs = new Map(); // date -> job
  const queue = [];
  let running = null;

  function publicJob(job) {
    if (!job) return null;
    const { child, resolvers, ...rest } = job;
    return rest;
  }

  async function storeResult(date, result, trigger) {
    const partial = date >= todayUtc() ? 1 : 0;
    await db.transaction(async () => {
      await db.run('DELETE FROM spills WHERE date = ?', [date]);
      for (const s of result.spills) {
        const c = s.culprit || {};
        await db.run(
          `INSERT INTO spills (id, date, aoi_id, aoi_name, scene_id, acquired_at, status, severity, oil_probability, area_km2, length_km,
             centroid_lat, centroid_lon, geometry_json, culprit_kind, culprit_mmsi, culprit_name, culprit_confidence, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [s.id, date, s.aoiId, s.aoiName, s.scene.id, s.scene.acquiredAt, s.status, s.severity, s.oilProbability, s.areaKm2,
            s.lengthKm, s.centroid.lat, s.centroid.lon, JSON.stringify(s.geometry), c.kind || null,
            c.mmsi || c.aisMatch?.mmsi || null,
            c.name || c.aisMatch?.name || (c.kind === 'sar_vessel'
              ? (c.relation === 'ahead_on_axis' ? 'SAR vessel ahead of slick (no AIS)' : 'SAR vessel at slick head (no AIS)') : null),
            c.confidence ?? null, JSON.stringify(s)]);
      }
      await db.run(
        `INSERT INTO scans (date, status, trigger, partial, started_at, finished_at, error, stats_json, models_json)
         VALUES (?, 'complete', ?, ?, ?, ?, NULL, ?, ?)
         ON CONFLICT(date) DO UPDATE SET status = 'complete', trigger = excluded.trigger, partial = excluded.partial,
           started_at = excluded.started_at, finished_at = excluded.finished_at, error = NULL,
           stats_json = excluded.stats_json, models_json = excluded.models_json`,
        [date, trigger, partial, jobs.get(date)?.startedAt || null, new Date().toISOString(), JSON.stringify(result.stats), JSON.stringify(result.models)]);
    });
  }

  function runNext() {
    if (running || !queue.length) return;
    const job = queue.shift();
    running = job;
    job.status = 'running';
    job.startedAt = new Date().toISOString();
    job.progress = { pct: 0, stage: 'starting', message: 'Starting ML pipeline' };
    db.run(`INSERT INTO scans (date, status, trigger, started_at) VALUES (?, 'running', ?, ?)
            ON CONFLICT(date) DO UPDATE SET status = 'running', trigger = excluded.trigger, started_at = excluded.started_at, error = NULL`,
    [job.date, job.trigger, job.startedAt]).catch(err => log.error('[scan] db', err));
    broadcast({ type: 'scan-progress', job: publicJob(job) });

    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'marinesight-scan-'));
    const outFile = path.join(tmp, 'result.json');
    const [cmd, ...base] = config.pipelineCmd || [config.python, '-m', 'marinesight.cli'];
    const args = [...base, 'scan', '--date', job.date, '--ais-db', config.dbPath, '--out', outFile];
    for (const aoi of job.aois || []) args.push('--aoi', aoi);
    log.log(`[scan] ${job.date} (${job.trigger}) -> ${cmd} ${args.join(' ')}`);
    const child = spawn(cmd, args, { cwd: config.ML_DIR, env: { ...process.env, PYTHONUNBUFFERED: '1' }, windowsHide: true });
    job.child = child;
    const tail = [];
    let buf = '';
    child.stderr.on('data', chunk => {
      buf += chunk.toString();
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        tail.push(line);
        if (tail.length > 40) tail.shift();
        if (line.startsWith('{')) {
          try {
            const ev = JSON.parse(line);
            if (ev.event === 'progress') {
              job.progress = { pct: ev.pct ?? job.progress?.pct ?? 0, stage: ev.stage, message: ev.message, aoi: ev.aoi };
              broadcast({ type: 'scan-progress', job: publicJob(job) });
            } else if (ev.event === 'warning') {
              job.warnings.push(ev.message);
            }
          } catch { /* not a progress line */ }
        }
      }
    });
    child.stdout.on('data', () => {});
    const killer = setTimeout(() => child.kill(), config.scan.jobTimeoutMs);

    child.on('close', async code => {
      clearTimeout(killer);
      let result = null;
      try { result = JSON.parse(fs.readFileSync(outFile, 'utf8')); } catch { /* no output */ }
      fs.rmSync(tmp, { recursive: true, force: true });
      try {
        if (result?.ok) {
          await storeResult(job.date, result, job.trigger);
          job.status = 'complete';
          job.stats = result.stats;
          job.progress = { pct: 100, stage: 'done', message: 'Scan complete' };
        } else {
          throw new Error(result?.error || `pipeline exited with code ${code}: ${tail.slice(-3).join(' | ')}`);
        }
      } catch (err) {
        job.status = 'failed';
        job.error = err.message;
        log.error(`[scan] ${job.date} failed: ${err.message}`);
        await db.run(`UPDATE scans SET status = 'failed', finished_at = ?, error = ? WHERE date = ?`,
          [new Date().toISOString(), err.message, job.date]).catch(() => {});
      }
      job.finishedAt = new Date().toISOString();
      broadcast({ type: job.status === 'complete' ? 'scan-complete' : 'scan-failed', job: publicJob(job) });
      job.resolvers.forEach(r => r(publicJob(job)));
      running = null;
      jobs.delete(job.date);
      runNext();
    });
  }

  // The DB lookup below is async, so the whole check-then-start step is single-flighted per date.
  const inflight = new Map();
  function ensure(date, opts = {}) {
    if (inflight.has(date)) return inflight.get(date);
    const p = ensureOnce(date, opts).finally(() => inflight.delete(date));
    inflight.set(date, p);
    return p;
  }

  /** Returns {state:'ready'} if the date is stored, otherwise starts/joins a job and returns it. */
  async function ensureOnce(date, { trigger = 'on-demand', force = false, aois = null } = {}) {
    const err = validateDate(date);
    if (err) throw Object.assign(new Error(err), { status: 400 });
    const existingJob = jobs.get(date);
    if (existingJob) return { state: 'loading', job: publicJob(existingJob) };
    const row = await db.get('SELECT * FROM scans WHERE date = ?', [date]);
    if (row && !force) {
      const dayOver = Date.now() > Date.parse(`${date}T00:00:00Z`) + 27 * 3600 * 1000;
      if (row.status === 'complete' && !(row.partial && dayOver)) return { state: 'ready', scan: row };
      if (row.status === 'failed' && Date.now() - Date.parse(row.finished_at || 0) < config.scan.retryFailedAfterMs) {
        return { state: 'failed', scan: row };
      }
    }
    const job = { date, trigger, aois, status: 'queued', queuedAt: new Date().toISOString(), warnings: [], resolvers: [], progress: { pct: 0, stage: 'queued', message: 'Queued' } };
    job.done = new Promise(resolve => job.resolvers.push(resolve));
    jobs.set(date, job);
    queue.push(job);
    runNext();
    return { state: 'loading', job: publicJob(job) };
  }

  return {
    ensure,
    get: date => publicJob(jobs.get(date)),
    wait: date => jobs.get(date)?.done,
    active: () => [...jobs.values()].map(publicJob),
    shutdown() { if (running?.child) running.child.kill(); }
  };
}

module.exports = { createScanJobs, validateDate, todayUtc };
