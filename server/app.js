const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const cors = require('cors');
const { WebSocketServer, WebSocket } = require('ws');

const config = require('./config');
const { open } = require('./db');
const { createAisService } = require('./ais');
const { createScanJobs, validateDate, todayUtc } = require('./scanJobs');
const { createScheduler } = require('./scheduler');
const { createTileProxy } = require('./sentinelTiles');
const { createTokenSource } = require('./cdseAuth');
const { createCoverageService, MAX_LOOKBACK_DAYS } = require('./sentinelCoverage');

function spillSummary(row) {
  return {
    id: row.id,
    date: row.date,
    aoiId: row.aoi_id,
    aoiName: row.aoi_name,
    sceneId: row.scene_id,
    acquiredAt: row.acquired_at,
    status: row.status,
    severity: row.severity,
    oilProbability: row.oil_probability,
    areaKm2: row.area_km2,
    lengthKm: row.length_km,
    centroid: { lat: row.centroid_lat, lon: row.centroid_lon },
    geometry: JSON.parse(row.geometry_json),
    culprit: row.culprit_kind ? {
      kind: row.culprit_kind, mmsi: row.culprit_mmsi, name: row.culprit_name, confidence: row.culprit_confidence
    } : null
  };
}

function withQuicklookUrls(detail) {
  for (const [key, ql] of Object.entries(detail.quicklooks || {})) {
    detail.quicklooks[key] = { ...ql, url: `/api/quicklooks/${encodeURIComponent(ql.file)}` };
  }
  return detail;
}

async function createApp({ log = console, overrides = {} } = {}) {
  const cfg = { ...config, ...overrides, ais: { ...config.ais, ...(overrides.ais || {}) }, scan: { ...config.scan, ...(overrides.scan || {}) } };
  const db = open(cfg.dbPath);
  await db.ready;
  // A scan can only be 'running' while this process runs it, so a row left over from a previous
  // process was interrupted and holds no results. Drop it: the UI would otherwise wait forever for
  // a pipeline that no longer exists, and the date can simply be scanned again on demand.
  await db.run(`DELETE FROM scans WHERE status = 'running'`).catch(() => {});

  const app = express();
  const server = http.createServer(app);
  const wss = new WebSocketServer({ server, path: '/api/events' });
  const broadcast = msg => {
    const data = JSON.stringify(msg);
    wss.clients.forEach(c => { if (c.readyState === WebSocket.OPEN) c.send(data); });
  };

  const ais = createAisService({ db, config: cfg, log, onRefresh: info => broadcast({ type: 'ais-refresh', ...info }) });
  const getCdseToken = createTokenSource(cfg);
  const coverage = createCoverageService({ config: cfg, getToken: getCdseToken, log });
  const scans = createScanJobs({ db, config: cfg, broadcast, log });
  const scheduler = createScheduler({ db, scans, config: cfg, log });

  app.use(cors({ origin: process.env.CORS_ORIGIN || true }));
  app.use(express.json({ limit: '256kb' }));

  app.get('/api/health', async (_req, res) => {
    const unet = fs.existsSync(path.join(cfg.ML_DIR, 'models', 'unet_s1_slick.pt'));
    const verifier = fs.existsSync(path.join(cfg.ML_DIR, 'models', 'verifier_hgb.joblib'));
    res.json({
      ok: true,
      now: new Date().toISOString(),
      integrations: {
        copernicusConfigured: Boolean(cfg.cdse.clientId && cfg.cdse.clientSecret),
        aisConfigured: Boolean(cfg.ais.aisStreamKey),
        mlModelsReady: unet && verifier
      },
      ais: ais.status(),
      scheduler: {
        dailyUtcHour: cfg.scan.dailyUtcHour,
        lastRunDay: await db.getMeta('daily_scan_day'),
        lastTarget: await db.getMeta('daily_scan_last_target')
      },
      activeScans: scans.active()
    });
  });

  app.get('/api/aois', (_req, res) => res.json(cfg.aois));

  app.get('/api/scans', async (_req, res, next) => {
    try {
      const rows = await db.all(`SELECT s.date, s.status, s.trigger, s.partial, s.finished_at,
        (SELECT COUNT(*) FROM spills p WHERE p.date = s.date AND p.status != 'rejected') AS spills
        FROM scans s ORDER BY s.date DESC LIMIT 400`);
      res.json({ latestComplete: rows.find(r => r.status === 'complete')?.date || null, scans: rows, active: scans.active() });
    } catch (err) { next(err); }
  });

  app.get('/api/scans/:date', async (req, res, next) => {
    try {
      const job = scans.get(req.params.date);
      if (job) return res.json({ state: 'loading', job });
      const row = await db.get('SELECT * FROM scans WHERE date = ?', [req.params.date]);
      if (!row) return res.json({ state: 'none' });
      res.json({ state: row.status === 'complete' ? 'ready' : row.status, scan: { ...row, stats: JSON.parse(row.stats_json || 'null') } });
    } catch (err) { next(err); }
  });

  app.post('/api/scans', async (req, res, next) => {
    try {
      const out = await scans.ensure(req.body?.date, { trigger: 'manual', force: Boolean(req.body?.force) });
      res.status(out.state === 'loading' ? 202 : 200).json(out);
    } catch (err) { next(err); }
  });

  /** Drop a scan the user navigated away from, so it cannot hold up the date they are now viewing. */
  app.delete('/api/scans/:date', (req, res) => {
    res.json({ cancelled: scans.cancel(req.params.date) });
  });

  /**
   * GET /api/spills?date=YYYY-MM-DD
   * Ready -> 200 with spills. Not in the DB -> starts the ML pipeline on demand and answers
   * 202 {state:'loading', job} so the UI can show "Loading..." and poll /api/scans/:date.
   */
  app.get('/api/spills', async (req, res, next) => {
    try {
      const date = req.query.date || todayUtc();
      const err = validateDate(date);
      if (err) return res.status(400).json({ error: err });
      const out = await scans.ensure(date, { trigger: 'on-demand' });
      if (out.state === 'loading') return res.status(202).json(out);
      if (out.state === 'failed') return res.status(502).json({ state: 'failed', error: out.scan.error, date });
      const includeRejected = req.query.include === 'rejected';
      const rows = await db.all(
        `SELECT * FROM spills WHERE date = ? ${includeRejected ? '' : "AND status != 'rejected'"} ORDER BY
           CASE severity WHEN 'HIGH' THEN 0 WHEN 'REVIEW' THEN 1 ELSE 2 END, area_km2 DESC`, [date]);
      const scan = out.scan;
      res.json({
        state: 'ready', date,
        scan: { status: scan.status, trigger: scan.trigger, finishedAt: scan.finished_at, partial: Boolean(scan.partial), stats: JSON.parse(scan.stats_json || 'null') },
        spills: rows.map(spillSummary)
      });
    } catch (err) { next(err); }
  });

  /**
   * GET /api/spills/nearest?date=YYYY-MM-DD[&maxDays=5]
   * The most recent already-scanned date at or before `date` that actually holds a detection.
   * Bounded by maxDays (default MAX_LOOKBACK_DAYS): if nothing was detected in that window the
   * answer is 'none' — the UI must not keep walking further back through the archive.
   */
  app.get('/api/spills/nearest', async (req, res, next) => {
    try {
      const date = req.query.date || todayUtc();
      const err = validateDate(date);
      if (err) return res.status(400).json({ error: err });
      const maxDays = Math.min(Math.max(Number(req.query.maxDays) || MAX_LOOKBACK_DAYS, 1), MAX_LOOKBACK_DAYS);
      const from = new Date(Date.parse(`${date}T00:00:00Z`) - (maxDays - 1) * 86400000).toISOString().slice(0, 10);
      const row = await db.get(
        `SELECT p.date, COUNT(*) AS spills FROM spills p
           JOIN scans s ON s.date = p.date AND s.status = 'complete'
          WHERE p.date <= ? AND p.date >= ? AND p.status != 'rejected'
          GROUP BY p.date ORDER BY p.date DESC LIMIT 1`, [date, from]);
      res.json(row
        ? { state: 'ready', date: row.date, spills: row.spills, searchedFrom: from, maxDays }
        : { state: 'none', searchedFrom: from, maxDays, reason: `no stored detections between ${from} and ${date}` });
    } catch (err) { next(err); }
  });

  app.get('/api/spills/:id', async (req, res, next) => {
    try {
      const row = await db.get('SELECT * FROM spills WHERE id = ?', [req.params.id]);
      if (!row) return res.status(404).json({ error: 'spill not found' });
      res.json({ ...spillSummary(row), detail: withQuicklookUrls(JSON.parse(row.detail_json)) });
    } catch (err) { next(err); }
  });

  app.get('/api/quicklooks/:file', (req, res) => {
    const name = path.basename(req.params.file);
    if (!/^[\w.-]+\.png$/.test(name)) return res.status(400).end();
    const file = path.join(cfg.quicklookDir, name);
    if (!fs.existsSync(file)) return res.status(404).end();
    res.set('Cache-Control', 'public, max-age=604800, immutable');
    res.sendFile(file);
  });

  app.get('/api/ais/vessels', (req, res) => {
    const snap = ais.snapshot();
    if (!snap) return res.status(503).json({ error: 'AIS cache warming up' });
    const next = Date.parse(ais.status().nextRefreshAt || 0);
    const maxAge = Math.max(0, Math.floor((next - Date.now()) / 1000));
    res.set({ ETag: snap.etag, 'Cache-Control': `public, max-age=${maxAge}`, 'X-AIS-Generated-At': snap.generatedAt, Vary: 'Accept-Encoding' });
    if (req.headers['if-none-match'] === snap.etag) return res.status(304).end();
    res.type('application/json');
    if (/\bgzip\b/.test(req.headers['accept-encoding'] || '')) {
      res.set('Content-Encoding', 'gzip');
      return res.send(snap.gzip);
    }
    res.send(snap.json);
  });

  app.get('/api/ais/status', (_req, res) => res.json(ais.status()));

  app.get('/api/ais/vessels/:mmsi', async (req, res, next) => {
    try {
      const hours = Math.min(Number(req.query.hours) || 24, 24 * 14);
      const to = req.query.to ? Math.floor(Date.parse(req.query.to) / 1000) : Math.floor(Date.now() / 1000);
      const track = await ais.track(req.params.mmsi, to - hours * 3600, to);
      const vessel = ais.vessel(req.params.mmsi);
      if (!vessel && !track.length) return res.status(404).json({ error: 'vessel not found' });
      res.json({ vessel, track });
    } catch (err) { next(err); }
  });

  /**
   * GET /api/sentinel/coverage?collection=&date=&bbox=lon0,lat0,lon1,lat1
   * Which pass the map is really showing for this view — its acquisition time and how far back it
   * is from the requested date. The UI labels the screen with it and pins the tiles to that pass.
   */
  app.get('/api/sentinel/coverage', async (req, res, next) => {
    try {
      const date = req.query.date || todayUtc();
      const err = validateDate(date);
      if (err) return res.status(400).json({ error: err });
      const bbox = String(req.query.bbox || '').split(',').map(Number);
      if (bbox.length !== 4 || bbox.some(v => !Number.isFinite(v))) return res.status(400).json({ error: 'bbox must be lon0,lat0,lon1,lat1' });
      res.json(await coverage.resolve({ collection: req.query.collection || 'sentinel-1-grd', date, bbox }));
    } catch (err) { next(err); }
  });

  app.get('/api/sentinel/tiles/:collection/:z/:x/:y.png',
    createTileProxy({ config: cfg, cacheDir: path.join(cfg.ML_DIR, 'cache', 'tiles'), getToken: getCdseToken }));

  app.use('/api', (req, res) => res.status(404).json({ error: `no route ${req.method} ${req.path}` }));
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    log.error('[api]', err.message);
    res.status(err.status || 500).json({ error: err.message });
  });

  const distPath = path.join(cfg.ROOT, 'dist');
  if (fs.existsSync(distPath)) {
    app.use(express.static(distPath));
    app.get('*', (_req, res) => res.sendFile(path.join(distPath, 'index.html')));
  }

  return {
    app, server, db, ais, scans, scheduler, config: cfg,
    async start(port = cfg.port) {
      await new Promise(resolve => server.listen(port, '0.0.0.0', resolve));
      log.log(`MarineSight API on http://localhost:${server.address().port}`);
      ais.start().catch(err => log.error('[ais] start', err));
      if (cfg.scan.schedulerEnabled) scheduler.start();
      return server.address().port;
    },
    async stop() {
      ais.stop();
      scheduler.stop();
      scans.shutdown();
      wss.close();
      await new Promise(resolve => server.close(() => resolve()));
      await db.close();
    }
  };
}

module.exports = { createApp };
