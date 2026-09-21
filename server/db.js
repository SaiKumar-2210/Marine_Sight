const sqlite3 = require('sqlite3');

const SCHEMA = [
  'PRAGMA journal_mode = WAL',
  'PRAGMA synchronous = NORMAL',
  `CREATE TABLE IF NOT EXISTS scans (
     date TEXT PRIMARY KEY,
     status TEXT NOT NULL,              -- queued | running | complete | failed
     trigger TEXT,                      -- cron | on-demand | manual
     partial INTEGER DEFAULT 0,         -- scanned before the UTC day ended
     started_at TEXT, finished_at TEXT,
     error TEXT, stats_json TEXT, models_json TEXT)`,
  `CREATE TABLE IF NOT EXISTS spills (
     id TEXT PRIMARY KEY,
     date TEXT NOT NULL, aoi_id TEXT, aoi_name TEXT,
     scene_id TEXT, acquired_at TEXT,
     status TEXT, severity TEXT, oil_probability REAL,
     area_km2 REAL, length_km REAL, centroid_lat REAL, centroid_lon REAL,
     geometry_json TEXT NOT NULL,
     culprit_kind TEXT, culprit_mmsi TEXT, culprit_name TEXT, culprit_confidence REAL,
     detail_json TEXT NOT NULL,
     created_at TEXT DEFAULT CURRENT_TIMESTAMP)`,
  'CREATE INDEX IF NOT EXISTS spills_date ON spills(date)',
  `CREATE TABLE IF NOT EXISTS ais_positions (
     mmsi TEXT NOT NULL, ts INTEGER NOT NULL,
     lat REAL NOT NULL, lon REAL NOT NULL, sog REAL, cog REAL, heading REAL,
     PRIMARY KEY (mmsi, ts)) WITHOUT ROWID`,
  'CREATE INDEX IF NOT EXISTS ais_positions_ts ON ais_positions(ts, lat, lon)',
  `CREATE TABLE IF NOT EXISTS vessels (
     mmsi TEXT PRIMARY KEY, name TEXT, ship_type INTEGER, callsign TEXT, imo TEXT,
     flag TEXT, length REAL, destination TEXT, updated_at INTEGER)`,
  'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)'
];

function open(file) {
  const raw = new sqlite3.Database(file);
  raw.configure('busyTimeout', 10000);
  const db = {
    raw,
    run: (sql, params = []) => new Promise((resolve, reject) =>
      raw.run(sql, params, function onRun(err) { err ? reject(err) : resolve({ changes: this.changes, lastID: this.lastID }); })),
    get: (sql, params = []) => new Promise((resolve, reject) => raw.get(sql, params, (err, row) => (err ? reject(err) : resolve(row)))),
    all: (sql, params = []) => new Promise((resolve, reject) => raw.all(sql, params, (err, rows) => (err ? reject(err) : resolve(rows)))),
    async transaction(fn) {
      await db.run('BEGIN IMMEDIATE');
      try {
        const out = await fn();
        await db.run('COMMIT');
        return out;
      } catch (err) {
        await db.run('ROLLBACK').catch(() => {});
        throw err;
      }
    },
    async getMeta(key) {
      const row = await db.get('SELECT value FROM meta WHERE key = ?', [key]);
      return row ? row.value : null;
    },
    setMeta: (key, value) => db.run('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value', [key, String(value)]),
    close: () => new Promise(resolve => raw.close(() => resolve()))
  };
  db.ready = (async () => { for (const sql of SCHEMA) await db.run(sql); })();
  return db;
}

module.exports = { open };
