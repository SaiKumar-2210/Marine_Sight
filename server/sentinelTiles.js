/** XYZ tile proxy for Copernicus Sentinel-1 / Sentinel-2 basemaps (credentials stay server-side). */
const fs = require('fs');
const path = require('path');
const { MAX_LOOKBACK_DAYS } = require('./sentinelCoverage');

const TRANSPARENT = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAQAAAAEAAQMAAABmvDolAAAAA1BMVEUAAACnej3aAAAAH0lEQVR42u3BAQEAAACAkP6v7ggKAAAAAAAAAAAAeA0WAAABF4f0hQAAAABJRU5ErkJggg==', 'base64');

const EVALSCRIPTS = {
  'sentinel-1-grd': `//VERSION=3
function setup(){return {input:["VV","dataMask"],output:{bands:4}};}
function evaluatePixel(s){ if (s.dataMask < 1 || s.VV <= 0) return [0,0,0,0];
  var db = 10*Math.log(s.VV)/Math.LN10; var v = Math.max(0, Math.min(1, (db + 28)/22)); return [v,v,v,1]; }`,
  'sentinel-2-l2a': `//VERSION=3
function setup(){return {input:["B02","B03","B04","dataMask"],output:{bands:4}};}
function evaluatePixel(s){ return [2.8*s.B04, 2.8*s.B03, 2.8*s.B02, s.dataMask]; }`
};

/**
 * Time range for one tile request.
 * With `t` (an acquisition resolved by the coverage service) the tile shows exactly that pass, so
 * the imagery matches the date label the UI is showing. Without it, fall back to a window that is
 * capped at MAX_LOOKBACK_DAYS so a sparsely covered area cannot silently reach far back in time.
 */
function timeRangeFor(collection, date, t) {
  const day = Date.parse(`${date}T00:00:00Z`);
  if (t) {
    const at = Date.parse(t);
    if (collection === 'sentinel-1-grd') {
      return { from: new Date(at - 3 * 3600000).toISOString(), to: new Date(at + 3 * 3600000).toISOString() };
    }
    const d0 = Date.parse(`${t.slice(0, 10)}T00:00:00Z`);
    return { from: new Date(d0).toISOString(), to: new Date(d0 + 86400000 - 1000).toISOString() };
  }
  return {
    from: new Date(day - MAX_LOOKBACK_DAYS * 86400000).toISOString(),
    to: new Date(day + 86400000 - 1000).toISOString()
  };
}

function createTileProxy({ config, cacheDir, getToken }) {
  return async function tileHandler(req, res, next) {
    try {
      const { collection } = req.params;
      const z = Number(req.params.z), x = Number(req.params.x), y = Number(req.params.y);
      if (!EVALSCRIPTS[collection] || ![z, x, y].every(Number.isInteger)) return res.status(400).json({ error: 'bad tile request' });
      const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || '') ? req.query.date : new Date().toISOString().slice(0, 10);
      const t = /^\d{4}-\d{2}-\d{2}T[\d:.]+Z?$/.test(req.query.t || '') ? req.query.t : null;
      res.set('Content-Type', 'image/png');
      if (z < 7) { res.set('Cache-Control', 'public, max-age=86400'); return res.send(TRANSPARENT); }
      // Cache per resolved pass when known, so switching dates cannot serve another pass's pixels.
      const stamp = t ? t.replace(/[:.]/g, '-') : date;
      const file = path.join(cacheDir, collection, stamp, String(z), String(x), `${y}.png`);
      if (fs.existsSync(file)) { res.set('Cache-Control', 'public, max-age=86400'); return res.send(fs.readFileSync(file)); }

      const n = 2 ** z;
      const lon0 = (x / n) * 360 - 180, lon1 = ((x + 1) / n) * 360 - 180;
      const lat = tile => (Math.atan(Math.sinh(Math.PI * (1 - (2 * tile) / n))) * 180) / Math.PI;
      const dataFilter = {
        timeRange: timeRangeFor(collection, date, t),
        mosaickingOrder: collection === 'sentinel-1-grd' ? 'mostRecent' : 'leastCC'
      };
      const body = {
        input: {
          bounds: { bbox: [lon0, lat(y + 1), lon1, lat(y)], properties: { crs: 'http://www.opengis.net/def/crs/EPSG/0/4326' } },
          data: [{ type: collection, dataFilter, ...(collection === 'sentinel-1-grd' ? { processing: { backCoeff: 'SIGMA0_ELLIPSOID', orthorectify: true } } : {}) }]
        },
        output: { width: 256, height: 256, responses: [{ identifier: 'default', format: { type: 'image/png' } }] },
        evalscript: EVALSCRIPTS[collection]
      };
      const upstream = await fetch(config.cdse.processUrl, {
        method: 'POST',
        headers: { Authorization: `Bearer ${await getToken()}`, 'Content-Type': 'application/json', Accept: 'image/png' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30000)
      });
      if (!upstream.ok) {
        res.set('Cache-Control', 'no-store');
        return res.send(TRANSPARENT);
      }
      const png = Buffer.from(await upstream.arrayBuffer());
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, png);
      res.set('Cache-Control', 'public, max-age=86400');
      res.send(png);
    } catch (err) { next(err); }
  };
}

module.exports = { createTileProxy, timeRangeFor };
