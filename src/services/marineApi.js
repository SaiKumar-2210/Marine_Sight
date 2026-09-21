async function json(url, options) {
  const res = await fetch(url, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok && res.status !== 202) {
    throw Object.assign(new Error(body.error || `Request failed (${res.status})`), { status: res.status, body });
  }
  return { status: res.status, body };
}

export const getHealth = () => json('/api/health').then(r => r.body);
export const getAois = () => json('/api/aois').then(r => r.body);
export const getScans = () => json('/api/scans').then(r => r.body);
export const getScan = date => json(`/api/scans/${date}`).then(r => r.body);

/** Returns {state:'ready', spills, scan} or {state:'loading', job} (the backend is running the ML pipeline). */
export const getSpills = (date, includeRejected = false) =>
  json(`/api/spills?date=${encodeURIComponent(date)}${includeRejected ? '&include=rejected' : ''}`).then(r => r.body);

export const getSpill = id => json(`/api/spills/${encodeURIComponent(id)}`).then(r => r.body);
export const rescan = date => json('/api/scans', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date, force: true })
}).then(r => r.body);

export const getVesselTrack = (mmsi, hours = 24, to) =>
  json(`/api/ais/vessels/${encodeURIComponent(mmsi)}?hours=${hours}${to ? `&to=${encodeURIComponent(to)}` : ''}`).then(r => r.body);
