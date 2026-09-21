// Stand-in for `python -m marinesight.cli scan` used by the backend tests: emits progress
// on stderr and writes a result file with one spill, exactly like the real CLI contract.
const fs = require('fs');

const args = process.argv.slice(2);
const date = args[args.indexOf('--date') + 1];
const out = args[args.indexOf('--out') + 1];
const delay = Number(process.env.FAKE_PIPELINE_DELAY_MS || 300);

process.stderr.write(JSON.stringify({ event: 'progress', stage: 'segmentation', pct: 40, message: 'U-Net segmentation' }) + '\n');
setTimeout(() => {
  fs.appendFileSync(process.env.FAKE_PIPELINE_LOG, `${date}\n`);
  const spill = {
    id: `MS-${date.replace(/-/g, '')}-TEST-01`, date, aoiId: 'OMAN_ARABIAN_SEA', aoiName: 'Test area',
    scene: { id: 'S1D_TEST', acquiredAt: `${date}T14:22:05Z`, platform: 'S1D' },
    geometry: { type: 'MultiPolygon', coordinates: [[[[57, 17.5], [57.1, 17.5], [57.1, 17.6], [57, 17.5]]]] },
    centroid: { lon: 57.05, lat: 17.55 }, areaKm2: 12.5, lengthKm: 20, oilProbability: 0.93,
    status: 'confirmed', severity: 'HIGH', culprit: { kind: 'ais_vessel', mmsi: '419000001', name: 'TEST TANKER', confidence: 0.8 },
    quicklooks: {}, verification: {}, attribution: null
  };
  fs.writeFileSync(out, JSON.stringify({ ok: true, date, stats: { scenes: 1, candidates: 1, confirmed: 1, review: 0, rejected: 0 }, models: {}, spills: [spill] }));
  process.exit(0);
}, delay);
