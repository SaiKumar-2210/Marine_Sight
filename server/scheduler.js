/**
 * Daily global scan — exactly once per UTC day.
 *
 * At DAILY_SCAN_UTC_HOUR the previous UTC day (complete Sentinel-1 coverage by then) is scanned.
 * The day that has been handled is persisted in `meta.daily_scan_day`, and it is claimed *before*
 * the job starts, so restarts, multiple timers or a slow scan can never run it twice; a server that
 * was down at the scheduled hour catches up on boot.
 */
function createScheduler({ db, scans, config, log = console }) {
  let timer = null;

  async function tick() {
    const now = new Date();
    const today = now.toISOString().slice(0, 10);
    if (now.getUTCHours() < config.scan.dailyUtcHour) return;
    // Atomic claim: only the first caller for this UTC day changes the row.
    const claim = await db.run(
      `INSERT INTO meta (key, value) VALUES ('daily_scan_day', ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE meta.value != excluded.value`, [today]);
    if (claim.changes !== 1) return;
    const target = new Date(Date.parse(`${today}T00:00:00Z`) - 86400000).toISOString().slice(0, 10);
    log.log(`[cron] daily global scan for ${target}`);
    await db.setMeta('daily_scan_last_target', target);
    await scans.ensure(target, { trigger: 'cron', force: true });
  }

  function msToNextCheck() {
    // Wake at the top of every UTC hour (cheap; the meta guard makes it idempotent).
    const now = Date.now();
    return 3600000 - (now % 3600000) + 1000;
  }

  function loop() {
    timer = setTimeout(async () => {
      try { await tick(); } catch (err) { log.error('[cron]', err.message); }
      loop();
    }, msToNextCheck());
    timer.unref?.();
  }

  return {
    async start() {
      try { await tick(); } catch (err) { log.error('[cron]', err.message); }
      loop();
    },
    stop() { clearTimeout(timer); },
    tick
  };
}

module.exports = { createScheduler };
