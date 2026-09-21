/**
 * AIS service.
 *
 * Every 10 minutes (exactly — one timer, no other refresh path) we:
 *   1. open one aisstream.io WebSocket burst (global subscription) for `burstSeconds`, then close it,
 *   2. merge the reports into the in-memory vessel state (last known position per MMSI),
 *   3. persist one position per vessel into `ais_positions` for vessels near the monitored AOIs
 *      (this history is what the attribution model back-propagates against),
 *   4. rebuild the served snapshot (compact rows, pre-gzipped, ETag) used by the map.
 * Between refreshes clients get the identical cached buffer (HTTP 304 via ETag), so upstream
 * traffic is one short burst per 10 minutes regardless of how many users are connected.
 */
const zlib = require('zlib');
const crypto = require('crypto');
const { WebSocket } = require('ws');

const FIELDS = ['mmsi', 'lat', 'lon', 'sog', 'cog', 'heading', 'shipType', 'name', 'ts', 'destination'];

function createAisService({ db, config, log = console, onRefresh = () => {} }) {
  const vessels = new Map(); // mmsi -> {mmsi, lat, lon, sog, cog, heading, ts, name, shipType, destination}
  const statics = new Map(); // mmsi -> {name, shipType, callsign, imo, destination, length}
  const historyBoxes = config.aois.map(a => [a.bbox[0] - 2, a.bbox[1] - 2, a.bbox[2] + 2, a.bbox[3] + 2]);
  let cache = null;
  let timer = null;
  let refreshing = null;
  const status = {
    provider: 'aisstream.io (terrestrial, burst mode)',
    lastRefreshAt: null, nextRefreshAt: null, lastBurst: null, lastError: null,
    kpler: { configured: Boolean(config.ais.kplerToken), state: 'unchecked' }
  };

  const inHistoryArea = (lat, lon) => historyBoxes.some(b => lon >= b[0] && lon <= b[2] && lat >= b[1] && lat <= b[3]);

  async function loadFromDb() {
    for (const row of await db.all('SELECT * FROM vessels')) {
      statics.set(row.mmsi, { name: row.name, shipType: row.ship_type, callsign: row.callsign, imo: row.imo, destination: row.destination, length: row.length });
    }
    // Warm the live state from recent history so a restart doesn't blank the map.
    const since = Math.floor(Date.now() / 1000) - config.ais.staleAfterHours * 3600;
    const rows = await db.all(
      `SELECT p.* FROM ais_positions p JOIN (SELECT mmsi, MAX(ts) AS ts FROM ais_positions WHERE ts >= ? GROUP BY mmsi) l
       ON l.mmsi = p.mmsi AND l.ts = p.ts`, [since]);
    for (const r of rows) upsert({ mmsi: r.mmsi, lat: r.lat, lon: r.lon, sog: r.sog, cog: r.cog, heading: r.heading, ts: r.ts });
    const snap = await db.getMeta('ais_snapshot');
    if (snap) {
      try {
        for (const v of JSON.parse(snap)) if (!vessels.has(v.mmsi) || vessels.get(v.mmsi).ts < v.ts) vessels.set(v.mmsi, v);
      } catch { /* corrupt snapshot — ignore */ }
    }
  }

  function upsert(p) {
    const prev = vessels.get(p.mmsi);
    if (prev && prev.ts > p.ts) return;
    const s = statics.get(p.mmsi) || {};
    vessels.set(p.mmsi, { ...prev, ...p, name: s.name || prev?.name || null, shipType: s.shipType ?? prev?.shipType ?? null, destination: s.destination || prev?.destination || null });
  }

  function collectBurst(seconds) {
    return new Promise(resolve => {
      const positions = new Map(); // mmsi -> latest report in this burst
      const staticUpdates = new Map();
      let messages = 0;
      let error = null;
      let finished = false;
      const ws = new WebSocket('wss://stream.aisstream.io/v0/stream', { handshakeTimeout: 20000 });
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(stopTimer);
        try { ws.terminate(); } catch { /* already closed */ }
        resolve({ positions, staticUpdates, messages, error });
      };
      const stopTimer = setTimeout(finish, seconds * 1000);
      ws.on('open', () => ws.send(JSON.stringify({
        APIKey: config.ais.aisStreamKey,
        BoundingBoxes: [[[-90, -180], [90, 180]]],
        FilterMessageTypes: ['PositionReport', 'StandardClassBPositionReport', 'ShipStaticData']
      })));
      ws.on('message', raw => {
        let msg;
        try { msg = JSON.parse(raw.toString()); } catch { return; }
        if (msg.error) { error = String(msg.error); finish(); return; }
        const meta = msg.MetaData || {};
        const mmsi = String(meta.MMSI || '');
        if (!mmsi) return;
        messages += 1;
        const type = msg.MessageType;
        const body = msg.Message?.[type];
        if (!body) return;
        if (type === 'ShipStaticData') {
          const dim = body.Dimension || {};
          staticUpdates.set(mmsi, {
            name: (body.Name || meta.ShipName || '').trim() || null,
            shipType: Number.isFinite(body.Type) ? body.Type : null,
            callsign: (body.CallSign || '').trim() || null,
            imo: body.ImoNumber ? String(body.ImoNumber) : null,
            destination: (body.Destination || '').trim() || null,
            length: (dim.A || 0) + (dim.B || 0) || null
          });
          return;
        }
        const lat = body.Latitude ?? meta.latitude;
        const lon = body.Longitude ?? meta.longitude;
        if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
        if (lat === 0 && lon === 0) return;
        const t = Date.parse((meta.time_utc || '').replace(/\.\d+/, '').replace(' +0000 UTC', 'Z').replace(' ', 'T'));
        positions.set(mmsi, {
          mmsi, lat, lon,
          sog: body.Sog >= 102.3 ? null : body.Sog,
          cog: body.Cog >= 360 ? null : body.Cog,
          heading: body.TrueHeading === 511 ? null : body.TrueHeading,
          ts: Math.floor((Number.isFinite(t) ? t : Date.now()) / 1000),
          nameHint: (meta.ShipName || '').trim() || null
        });
      });
      ws.on('error', err => { error = err.message; finish(); });
      ws.on('close', () => finish());
    });
  }

  async function probeKpler() {
    // The supplied Kpler credentials are checked against Maritime 2.0 (GraphQL). They are only used
    // if the auth server accepts them; otherwise the state is reported via /api/ais/status.
    if (!config.ais.kplerToken || status.kpler.state === 'rejected') return;
    try {
      const res = await fetch('https://api.sml.kpler.com/graphql', {
        method: 'POST',
        headers: { Authorization: `Bearer ${config.ais.kplerToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: '{ __typename }' }),
        signal: AbortSignal.timeout(15000)
      });
      const body = await res.json().catch(() => ({}));
      const unauth = (body.errors || []).some(e => e.extensions?.code === 'UNAUTHENTICATED');
      status.kpler.state = unauth || res.status === 401 ? 'rejected' : 'authenticated';
      status.kpler.detail = unauth ? body.errors[0].message : `HTTP ${res.status}`;
    } catch (err) {
      status.kpler.state = 'unreachable';
      status.kpler.detail = err.message;
    }
  }

  function buildCache() {
    const cutoff = Math.floor(Date.now() / 1000) - config.ais.staleAfterHours * 3600;
    const rows = [];
    for (const [mmsi, v] of vessels) {
      if (v.ts < cutoff) { vessels.delete(mmsi); continue; }
      rows.push([mmsi, +v.lat.toFixed(5), +v.lon.toFixed(5), v.sog ?? null, v.cog ?? null, v.heading ?? null,
        v.shipType ?? null, v.name || null, v.ts, v.destination || null]);
    }
    const payload = {
      generatedAt: new Date().toISOString(),
      nextRefreshAt: status.nextRefreshAt,
      refreshIntervalSec: config.ais.refreshMs / 1000,
      source: status.provider,
      count: rows.length,
      fields: FIELDS,
      rows
    };
    const json = Buffer.from(JSON.stringify(payload));
    cache = {
      json,
      gzip: zlib.gzipSync(json, { level: 6 }),
      etag: `"${crypto.createHash('sha1').update(json).digest('hex').slice(0, 20)}"`,
      generatedAt: payload.generatedAt,
      count: rows.length
    };
  }

  async function persist(burst) {
    const now = Math.floor(Date.now() / 1000);
    await db.transaction(async () => {
      for (const [mmsi, s] of burst.staticUpdates) {
        await db.run(
          `INSERT INTO vessels (mmsi, name, ship_type, callsign, imo, destination, length, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(mmsi) DO UPDATE SET name = COALESCE(excluded.name, name), ship_type = COALESCE(excluded.ship_type, ship_type),
             callsign = COALESCE(excluded.callsign, callsign), imo = COALESCE(excluded.imo, imo),
             destination = COALESCE(excluded.destination, destination), length = COALESCE(excluded.length, length), updated_at = excluded.updated_at`,
          [mmsi, s.name, s.shipType, s.callsign, s.imo, s.destination, s.length, now]);
      }
      for (const [mmsi, p] of burst.positions) {
        if (p.nameHint && !burst.staticUpdates.has(mmsi) && !statics.get(mmsi)?.name) {
          await db.run('INSERT INTO vessels (mmsi, name, updated_at) VALUES (?, ?, ?) ON CONFLICT(mmsi) DO UPDATE SET name = COALESCE(name, excluded.name)', [mmsi, p.nameHint, now]);
        }
        if (inHistoryArea(p.lat, p.lon)) {
          await db.run('INSERT OR IGNORE INTO ais_positions (mmsi, ts, lat, lon, sog, cog, heading) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [mmsi, p.ts, p.lat, p.lon, p.sog, p.cog, p.heading]);
        }
      }
      await db.run('DELETE FROM ais_positions WHERE ts < ?', [now - config.ais.retentionDays * 86400]);
    });
    const snapshot = [...vessels.values()];
    await db.setMeta('ais_snapshot', JSON.stringify(snapshot));
  }

  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      const started = Date.now();
      status.nextRefreshAt = new Date(started + config.ais.refreshMs).toISOString();
      try {
        if (!config.ais.aisStreamKey) throw new Error('AIS_STREAM_API_KEY not configured');
        probeKpler();
        const burst = await collectBurst(config.ais.burstSeconds);
        for (const [mmsi, s] of burst.staticUpdates) statics.set(mmsi, { ...statics.get(mmsi), ...s });
        for (const [mmsi, p] of burst.positions) {
          if (p.nameHint && !statics.get(mmsi)?.name) statics.set(mmsi, { ...statics.get(mmsi), name: p.nameHint });
          upsert({ mmsi, lat: p.lat, lon: p.lon, sog: p.sog, cog: p.cog, heading: p.heading, ts: p.ts });
        }
        await persist(burst);
        status.lastBurst = {
          at: new Date(started).toISOString(), seconds: config.ais.burstSeconds, messages: burst.messages,
          positions: burst.positions.size, statics: burst.staticUpdates.size, error: burst.error
        };
        status.lastError = burst.error;
      } catch (err) {
        status.lastError = err.message;
        log.warn(`[ais] refresh failed: ${err.message}`);
      }
      buildCache();
      status.lastRefreshAt = new Date().toISOString();
      log.log(`[ais] cache rebuilt: ${cache.count} vessels (burst ${status.lastBurst?.positions ?? 0} positions)`);
      onRefresh({ count: cache.count, generatedAt: cache.generatedAt, nextRefreshAt: status.nextRefreshAt });
    })().finally(() => { refreshing = null; });
    return refreshing;
  }

  return {
    status: () => ({ ...status, vessels: cache?.count ?? vessels.size, cacheGeneratedAt: cache?.generatedAt ?? null }),
    async start() {
      await loadFromDb();
      status.nextRefreshAt = new Date(Date.now() + 5000).toISOString();
      buildCache();
      if (!config.ais.enabled) return;
      refresh();
      timer = setInterval(refresh, config.ais.refreshMs);
      timer.unref?.();
    },
    stop() { if (timer) clearInterval(timer); },
    refresh,
    snapshot: () => cache,
    vessel: mmsi => {
      const v = vessels.get(String(mmsi));
      return v ? { ...v, ...statics.get(String(mmsi)) } : (statics.has(String(mmsi)) ? { mmsi: String(mmsi), ...statics.get(String(mmsi)) } : null);
    },
    async track(mmsi, fromTs, toTs) {
      return db.all('SELECT ts, lat, lon, sog, cog, heading FROM ais_positions WHERE mmsi = ? AND ts BETWEEN ? AND ? ORDER BY ts', [String(mmsi), fromTs, toTs]);
    }
  };
}

module.exports = { createAisService, FIELDS };
