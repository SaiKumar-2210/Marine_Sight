/** XYZ tile proxy for Copernicus Sentinel-1 / Sentinel-2 basemaps (credentials stay server-side). */
const fs = require('fs');
const path = require('path');

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

function createTileProxy({ config, cacheDir }) {
  let token = { value: null, expiresAt: 0 };

  async function getToken() {
    if (token.value && Date.now() < token.expiresAt - 60000) return token.value;
    if (!config.cdse.clientId || !config.cdse.clientSecret) throw Object.assign(new Error('Copernicus credentials not configured'), { status: 503 });
    const res = await fetch(config.cdse.tokenUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'client_credentials', client_id: config.cdse.clientId, client_secret: config.cdse.clientSecret }),
      signal: AbortSignal.timeout(15000)
    });
    if (!res.ok) throw new Error(`Copernicus auth failed (${res.status})`);
    const body = await res.json();
    token = { value: body.access_token, expiresAt: Date.now() + Number(body.expires_in || 600) * 1000 };
    return token.value;
  }

  return async function tileHandler(req, res, next) {
    try {
      const { collection } = req.params;
      const z = Number(req.params.z), x = Number(req.params.x), y = Number(req.params.y);
      if (!EVALSCRIPTS[collection] || ![z, x, y].every(Number.isInteger)) return res.status(400).json({ error: 'bad tile request' });
      const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || '') ? req.query.date : new Date().toISOString().slice(0, 10);
      res.set('Content-Type', 'image/png');
      if (z < 7) { res.set('Cache-Control', 'public, max-age=86400'); return res.send(TRANSPARENT); }
      const file = path.join(cacheDir, collection, date, String(z), String(x), `${y}.png`);
      if (fs.existsSync(file)) { res.set('Cache-Control', 'public, max-age=86400'); return res.send(fs.readFileSync(file)); }

      const n = 2 ** z;
      const lon0 = (x / n) * 360 - 180, lon1 = ((x + 1) / n) * 360 - 180;
      const lat = t => (Math.atan(Math.sinh(Math.PI * (1 - (2 * t) / n))) * 180) / Math.PI;
      const day = Date.parse(`${date}T00:00:00Z`);
      const back = collection === 'sentinel-1-grd' ? 3 : 5;
      const dataFilter = {
        timeRange: { from: new Date(day - back * 86400000).toISOString(), to: new Date(day + 86400000 - 1000).toISOString() },
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

module.exports = { createTileProxy };
