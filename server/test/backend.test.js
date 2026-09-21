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
