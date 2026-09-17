const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const PROJECT_ROOT = path.join(__dirname, '..');
const PIPELINE_SCRIPT = path.join(PROJECT_ROOT, 'ml_service', 'run_pipeline.py');
const CACHE_DIR = path.join(PROJECT_ROOT, 'ml_service', 'cache');

function pythonBin() {
  return process.platform === 'win32' ? 'python' : 'python3';
}

function vesselsInBbox(vessels, bbox) {
  if (!bbox || bbox.length !== 4) return vessels;
  const [minLng, minLat, maxLng, maxLat] = bbox;
  return vessels.filter(v => {
    const lat = Number(v.latitude);
    const lng = Number(v.longitude);
    return lat >= minLat && lat <= maxLat && lng >= minLng && lng <= maxLng;
  });
}

function publicChips(chips, scanId) {
  if (!chips) return {};
  const out = {};
  for (const key of ['s1', 's2']) {
    if (chips[key]) out[key] = `/api/pipeline/chips/${scanId}/${path.basename(chips[key])}`;
  }
  return out;
}

function rewriteIncidents(result) {
  const scanId = result.scanId;
  const mapOne = (inc) => ({ ...inc, chips: publicChips(inc.chips, scanId) });
  return {
    ...result,
    incidents: (result.incidents || []).map(mapOne),
    rejected: (result.rejected || []).map(mapOne)
  };
}

function runOilSpillWorkflow(opts) {
  const {
    date,
    coast,
    vessels = [],
    mode = 'scan',
    lat,
    lng,
    dryRun = false,
    timeoutMs = 240000
  } = opts;

  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'marinesight-'));
  const aisPath = path.join(tmp, 'ais.json');
  const outPath = path.join(tmp, 'out.json');
  const bbox = coast.bbox || [69, 15, 75, 21];
  const nearby = vesselsInBbox(vessels, bbox);

  fs.writeFileSync(aisPath, JSON.stringify({ vessels: nearby }));

  const args = [
    PIPELINE_SCRIPT,
    '--date', date,
    '--coast-id', coast.id,
    '--coast-name', coast.name || coast.id,
    '--bbox', bbox.join(','),
    '--ais', aisPath,
    '--out', outPath,
    '--cache-dir', CACHE_DIR,
    '--mode', mode,
    '--scan-id', `${coast.id}-${date}`
  ];
  if (mode === 'point' && Number.isFinite(lat) && Number.isFinite(lng)) {
    args.push('--lat', String(lat), '--lng', String(lng));
  }
  if (dryRun) args.push('--dry-run');

  try {
    execFileSync(pythonBin(), args, {
      encoding: 'utf8',
      timeout: timeoutMs,
      cwd: PROJECT_ROOT,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe']
    });
  } catch (err) {
    const stderr = (err.stderr || err.message || '').toString();
    console.error('[workflow] python failed:', stderr.slice(0, 2000));
    if (!fs.existsSync(outPath)) {
      throw new Error(stderr.slice(0, 500) || 'Oil-spill workflow failed');
    }
  }

  const raw = JSON.parse(fs.readFileSync(outPath, 'utf8'));
  try { fs.rmSync(tmp, { recursive: true, force: true }); } catch { /* ignore */ }
  if (!raw.ok) {
    throw new Error(raw.error || 'Oil-spill workflow returned ok=false');
  }
  return rewriteIncidents(raw);
}

module.exports = { runOilSpillWorkflow, CACHE_DIR, vesselsInBbox };
