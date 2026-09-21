const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const ROOT = path.join(__dirname, '..');
const ML_DIR = path.join(ROOT, 'ml_service');

module.exports = {
  ROOT,
  ML_DIR,
  port: Number(process.env.PORT || 3000),
  dbPath: process.env.MARINESIGHT_DB || path.join(ROOT, 'marinesight.sqlite'),
  quicklookDir: path.join(process.env.MARINESIGHT_CACHE_DIR || path.join(ML_DIR, 'cache'), 'quicklooks'),
  python: process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
  // Optional override of the pipeline command as a JSON array (used by tests); default: python -m marinesight.cli
  pipelineCmd: process.env.MARINESIGHT_PIPELINE_CMD ? JSON.parse(process.env.MARINESIGHT_PIPELINE_CMD) : null,
  aois: require(path.join(ML_DIR, 'aois.json')),

  cdse: {
    clientId: process.env.CDSE_CLIENT_ID || '',
    clientSecret: process.env.CDSE_CLIENT_SECRET || '',
    tokenUrl: process.env.CDSE_TOKEN_URL || 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token',
    processUrl: process.env.CDSE_PROCESS_URL || 'https://sh.dataspace.copernicus.eu/api/v1/process'
  },

  ais: {
    aisStreamKey: process.env.AIS_STREAM_API_KEY || '',
    kplerToken: process.env.KPLER_AIS_AUTH_TOKEN || '',
    refreshMs: 10 * 60 * 1000, // the served AIS cache is rebuilt exactly once every 10 minutes
    burstSeconds: Number(process.env.AIS_BURST_SECONDS || 90),
    staleAfterHours: Number(process.env.AIS_STALE_HOURS || 6),
    retentionDays: Number(process.env.AIS_RETENTION_DAYS || 30),
    enabled: process.env.AIS_DISABLED !== '1'
  },

  scan: {
    dailyUtcHour: Number(process.env.DAILY_SCAN_UTC_HOUR || 3),
    schedulerEnabled: process.env.SCHEDULER_DISABLED !== '1',
    jobTimeoutMs: Number(process.env.SCAN_TIMEOUT_MS || 45 * 60 * 1000),
    retryFailedAfterMs: 10 * 60 * 1000
  }
};
