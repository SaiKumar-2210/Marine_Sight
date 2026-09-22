/** Shared Copernicus (CDSE) OAuth client-credentials token, cached until shortly before expiry. */
function createTokenSource(config) {
  let token = { value: null, expiresAt: 0 };
  let inflight = null;

  async function fetchToken() {
    if (!config.cdse.clientId || !config.cdse.clientSecret) {
      throw Object.assign(new Error('Copernicus credentials not configured'), { status: 503 });
    }
    const res = await fetch(config.cdse.tokenUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'client_credentials',
        client_id: config.cdse.clientId,
        client_secret: config.cdse.clientSecret
      }),
      signal: AbortSignal.timeout(15000)
    });
    if (!res.ok) throw Object.assign(new Error(`Copernicus auth failed (${res.status})`), { status: 502 });
    const body = await res.json();
    token = { value: body.access_token, expiresAt: Date.now() + Number(body.expires_in || 600) * 1000 };
    return token.value;
  }

  return async function getToken() {
    if (token.value && Date.now() < token.expiresAt - 60000) return token.value;
    if (!inflight) inflight = fetchToken().finally(() => { inflight = null; });
    return inflight;
  };
}

module.exports = { createTokenSource };
