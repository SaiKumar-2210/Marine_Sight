/**
 * Which satellite pass is actually being shown?
 *
 * The map asks for a date, but a Sentinel pass only covers a given area every few days, so the
 * pixels on screen are usually older than the requested date. This resolves the real acquisition
 * and hands it to the UI (for the "imagery date" label) and to the tile proxy (so the tiles it
 * renders are that one pass, not a mosaic of whatever the window happened to contain).
 *
 * Lookback rules (deliberately bounded — an unbounded walk back through the archive is both slow
 * and misleading):
 *   1. look at most MAX_LOOKBACK_DAYS (5) days back from the requested date;
 *   2. if that window is empty, make ONE more catalog query for the single most recent scene
 *      before the date and offer that, flagged as `beyondLookback`;
 *   3. if even that is empty, report no coverage. Never loop day by day.
 */
const CATALOG_URL = 'https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search';
const S1_ARCHIVE_START = '2014-10-03';

const MAX_LOOKBACK_DAYS = 5;
const ARCHIVE_PROBE_DAYS = 30; // width of the single extra probe when the 5-day window is empty
const CACHE_TTL_MS = 10 * 60 * 1000;
const CACHE_MAX = 500;

const COLLECTIONS = { 'sentinel-1-grd': 'sentinel-1-grd', 'sentinel-2-l2a': 'sentinel-2-l2a' };

const iso = ms => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const dayStart = date => Date.parse(`${date}T00:00:00Z`);

/** Coarse bbox key so panning a little re-uses the cached answer. */
function cacheKey(collection, date, bbox) {
  return `${collection}|${date}|${bbox.map(v => v.toFixed(1)).join(',')}`;
}

function pickScene(features, collection) {
  // Most recent first; for Sentinel-2 prefer a usable (not fully clouded) scene among that day's.
  const sorted = [...features].sort((a, b) => Date.parse(b.properties.datetime) - Date.parse(a.properties.datetime));
  if (collection === 'sentinel-2-l2a') {
    const newest = Date.parse(sorted[0].properties.datetime);
    const sameDay = sorted.filter(f => Math.abs(Date.parse(f.properties.datetime) - newest) < 12 * 3600 * 1000);
    sameDay.sort((a, b) => (a.properties['eo:cloud_cover'] ?? 100) - (b.properties['eo:cloud_cover'] ?? 100));
    return sameDay[0];
  }
  return sorted[0];
}

function describe(feature, collection, date) {
  const acquiredAt = feature.properties.datetime;
  const acquiredMs = Date.parse(acquiredAt);
  const daysBefore = Math.floor((dayStart(date) + 86400000 - acquiredMs) / 86400000);
  return {
    state: 'ready',
    collection,
    requestedDate: date,
    sceneId: feature.id,
    acquiredAt,
    acquiredDate: acquiredAt.slice(0, 10),
    // 0 = the pass happened on the requested date itself.
    daysBefore: Math.max(0, daysBefore),
    sameDay: acquiredAt.slice(0, 10) === date,
    cloudCover: feature.properties['eo:cloud_cover'] ?? null,
    orbitState: feature.properties['sat:orbit_state'] ?? null,
    maxLookbackDays: MAX_LOOKBACK_DAYS
  };
}

function createCoverageService({ config, getToken, log = console }) {
  const cache = new Map();

  // Note: the CDSE catalog rejects `sortby`, so "most recent" is resolved by asking for a bounded
  // window and picking the newest feature it returns.
  async function catalogSearch({ collection, bbox, from, to, limit }) {
    const body = { bbox, datetime: `${iso(from)}/${iso(to)}`, collections: [collection], limit };
    const res = await fetch(CATALOG_URL, {
      method: 'POST',
      headers: { Authorization: `Bearer ${await getToken()}`, 'Content-Type': 'application/json', Accept: 'application/geo+json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000)
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw Object.assign(new Error(`Copernicus catalog search failed (${res.status}) ${text.slice(0, 160)}`), { status: 502 });
    }
    return (await res.json()).features || [];
  }

  /** Resolve the pass shown for (collection, date, bbox). Cached for CACHE_TTL_MS. */
  async function resolve({ collection, date, bbox }) {
    if (!COLLECTIONS[collection]) throw Object.assign(new Error('unknown collection'), { status: 400 });
    const key = cacheKey(collection, date, bbox);
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.value;

    const end = dayStart(date) + 86400000 - 1000;
    let value;
    // 1. the bounded window: the requested day plus at most MAX_LOOKBACK_DAYS before it.
    const windowed = await catalogSearch({
      collection, bbox, from: dayStart(date) - MAX_LOOKBACK_DAYS * 86400000, to: end, limit: 50
    });
    if (windowed.length) {
      value = { ...describe(pickScene(windowed, collection), collection, date), beyondLookback: false };
    } else {
      // 2. one bounded probe, from which only the single most recent scene is offered — never a
      //    day-by-day walk back through the archive.
      const latest = await catalogSearch({
        collection, bbox,
        from: Math.max(dayStart(S1_ARCHIVE_START), dayStart(date) - (MAX_LOOKBACK_DAYS + ARCHIVE_PROBE_DAYS) * 86400000),
        to: dayStart(date) - MAX_LOOKBACK_DAYS * 86400000 - 1000,
        limit: 100
      });
      value = latest.length
        ? { ...describe(pickScene(latest, collection), collection, date), beyondLookback: true }
        : { state: 'none', collection, requestedDate: date, maxLookbackDays: MAX_LOOKBACK_DAYS,
          reason: `no ${collection} scene over this view within ${MAX_LOOKBACK_DAYS} days of ${date}` };
    }

    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
    cache.set(key, { at: Date.now(), value });
    return value;
  }

  /** True when a Sentinel-1 pass exists within the capped window — used to skip pointless scans. */
  async function hasS1Coverage(bbox, date) {
    try {
      const cov = await resolve({ collection: 'sentinel-1-grd', date, bbox });
      return cov.state === 'ready' && !cov.beyondLookback;
    } catch (err) {
      log.error('[coverage]', err.message);
      return true; // never block a scan because the catalog was unreachable
    }
  }

  return { resolve, hasS1Coverage, MAX_LOOKBACK_DAYS };
}

module.exports = { createCoverageService, MAX_LOOKBACK_DAYS, S1_ARCHIVE_START };
