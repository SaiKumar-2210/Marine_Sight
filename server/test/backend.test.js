const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { createApp } = require('../app');

const quiet = { log() {}, warn() {}, error() {} };

async function boot(extra = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ms-test-'));
  const callLog = path.join(dir, 'calls.log');
  fs.writeFileSync(callLog, '');
  process.env.FAKE_PIPELINE_LOG = callLog;
  const ctx = await createApp({
    log: quiet,
    overrides: {
      dbPath: path.join(dir, 'test.sqlite'),
      pipelineCmd: [process.execPath, path.join(__dirname, 'fake_pipeline.js')],
      ML_DIR: __dirname,
      ais: { enabled: false },
      scan: { schedulerEnabled: false, jobTimeoutMs: 30000, retryFailedAfterMs: 600000 },
      ...extra
    }
  });
  const port = await ctx.start(0);
  const base = `http://127.0.0.1:${port}`;
  const calls = () => fs.readFileSync(callLog, 'utf8').split('\n').filter(Boolean);
  return { ctx, base, calls, restore: () => {} };
}

test('on-demand scan: unknown date -> 202 loading, single-flight, stored, then served from DB', async () => {
  const { ctx, base, calls, restore } = await boot();
  try {
    const date = '2026-08-07';
    const [a, b] = await Promise.all([fetch(`${base}/api/spills?date=${date}`), fetch(`${base}/api/spills?date=${date}`)]);
    assert.strictEqual(a.status, 202);
    assert.strictEqual(b.status, 202);
    assert.strictEqual((await a.json()).state, 'loading');
    await ctx.scans.wait(date);
    assert.deepStrictEqual(calls(), [date], 'two concurrent requests must share one pipeline run');

    const ready = await fetch(`${base}/api/spills?date=${date}`);
    assert.strictEqual(ready.status, 200);
    const body = await ready.json();
    assert.strictEqual(body.state, 'ready');
    assert.strictEqual(body.spills.length, 1);
    assert.strictEqual(body.spills[0].culprit.mmsi, '419000001');
    assert.deepStrictEqual(body.spills[0].geometry.type, 'MultiPolygon');
    const again = await fetch(`${base}/api/spills?date=${date}`);
    assert.strictEqual(again.status, 200);
    assert.deepStrictEqual(calls(), [date], 'stored dates must not re-run the pipeline');
    const detail = await (await fetch(`${base}/api/spills/${body.spills[0].id}`)).json();
    assert.strictEqual(detail.detail.oilProbability, 0.93);
  } finally { restore(); await ctx.stop(); }
});

test('date validation', async () => {
  const { ctx, base, restore } = await boot();
  try {
    assert.strictEqual((await fetch(`${base}/api/spills?date=2031-01-01`)).status, 400);
    assert.strictEqual((await fetch(`${base}/api/spills?date=2010-01-01`)).status, 400);
    assert.strictEqual((await fetch(`${base}/api/spills?date=nope`)).status, 400);
  } finally { restore(); await ctx.stop(); }
});

test('a queued scan is dropped when the operator moves to another date', async () => {
  process.env.FAKE_PIPELINE_DELAY_MS = '1500';
  const { ctx, base, calls, restore } = await boot();
  try {
    const running = '2026-08-07';
    const abandoned = '2026-08-06';
    assert.strictEqual((await fetch(`${base}/api/spills?date=${running}`)).status, 202);
    assert.strictEqual((await fetch(`${base}/api/spills?date=${abandoned}`)).status, 202);
    const cancelled = await (await fetch(`${base}/api/scans/${abandoned}`, { method: 'DELETE' })).json();
    assert.strictEqual(cancelled.cancelled, true, 'a queued scan must be cancellable');

    await ctx.scans.wait(running);
    await new Promise(r => setTimeout(r, 400)); // let the queue drain if anything was left on it
    assert.deepStrictEqual(calls(), [running], 'the abandoned date must never reach the pipeline');
    assert.strictEqual(await ctx.db.get('SELECT * FROM scans WHERE date = ?', [abandoned]), undefined);

    // A scan already running is left to finish rather than thrown away mid-flight.
    assert.strictEqual((await (await fetch(`${base}/api/scans/${running}`, { method: 'DELETE' })).json()).cancelled, false);
  } finally { delete process.env.FAKE_PIPELINE_DELAY_MS; restore(); await ctx.stop(); }
});

test('nearest stored detection is capped at the lookback window', async () => {
  const { ctx, base, restore } = await boot();
  try {
    const hit = '2026-08-07';
    await fetch(`${base}/api/spills?date=${hit}`);
    await ctx.scans.wait(hit);

    const near = await (await fetch(`${base}/api/spills/nearest?date=2026-08-10&maxDays=5`)).json();
    assert.strictEqual(near.state, 'ready');
    assert.strictEqual(near.date, hit);
    assert.strictEqual(near.spills, 1);

    // Six days later the detection is outside the window: the search stops instead of walking on.
    const far = await (await fetch(`${base}/api/spills/nearest?date=2026-08-13&maxDays=5`)).json();
    assert.strictEqual(far.state, 'none');
    assert.strictEqual(far.searchedFrom, '2026-08-09');
    // The cap cannot be widened from the client.
    const clamped = await (await fetch(`${base}/api/spills/nearest?date=2026-08-13&maxDays=90`)).json();
    assert.strictEqual(clamped.maxDays, 5);
    assert.strictEqual(clamped.state, 'none');
    // Later dates are never offered — the operator asked for history, not the future.
    const forward = await (await fetch(`${base}/api/spills/nearest?date=2026-08-05&maxDays=5`)).json();
    assert.strictEqual(forward.state, 'none');
  } finally { restore(); await ctx.stop(); }
});

test('a scan interrupted by a restart does not leave the UI waiting forever', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ms-restart-'));
  const dbPath = path.join(dir, 'test.sqlite');
  const overrides = {
    dbPath, pipelineCmd: [process.execPath, path.join(__dirname, 'fake_pipeline.js')], ML_DIR: __dirname,
    ais: { enabled: false }, scan: { schedulerEnabled: false, jobTimeoutMs: 30000, retryFailedAfterMs: 600000 }
  };
  const first = await createApp({ log: quiet, overrides });
  await first.db.run(`INSERT INTO scans (date, status, trigger, started_at) VALUES ('2026-08-07', 'running', 'on-demand', ?)`,
    [new Date().toISOString()]);
  await first.stop();

  const second = await createApp({ log: quiet, overrides });
  try {
    assert.strictEqual(await second.db.get(`SELECT * FROM scans WHERE date = '2026-08-07'`), undefined,
      'the orphaned row must be cleared so the date can be scanned again');
  } finally { await second.stop(); }
});

test('daily cron runs exactly once per UTC day, for the previous day', async () => {
  const { ctx, calls, restore } = await boot({ scan: { schedulerEnabled: false, dailyUtcHour: 0, jobTimeoutMs: 30000, retryFailedAfterMs: 600000 } });
  try {
    await Promise.all([ctx.scheduler.tick(), ctx.scheduler.tick()]);
    await ctx.scheduler.tick();
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    await ctx.scans.wait(yesterday);
    await ctx.scheduler.tick();
    assert.deepStrictEqual(calls(), [yesterday]);
    const row = await ctx.db.get('SELECT trigger, status FROM scans WHERE date = ?', [yesterday]);
    assert.deepStrictEqual(row, { trigger: 'cron', status: 'complete' });
  } finally { restore(); await ctx.stop(); }
});
