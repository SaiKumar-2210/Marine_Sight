export async function getWeather(lat, lng) {
  const response = await fetch(`/api/weather/currents?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`);
  if (!response.ok) throw new Error('Weather service is unavailable');
  return response.json();
}

export async function getSatelliteImage(collection, bbox) {
  const response = await fetch('/api/sentinel/process-tile', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ collection, bbox, width: 1280, height: 720 })
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || 'Satellite scene unavailable');
  return URL.createObjectURL(await response.blob());
}

export async function getCachedSatelliteImage(collection, coastId, highRes = false) {
  const params = new URLSearchParams({ collection, coastId });
  if (highRes) params.set('highRes', 'true');
  const response = await fetch(`/api/sentinel/cached-tile?${params}`);
  if (!response.ok) throw new Error('Cached satellite imagery unavailable');
  return URL.createObjectURL(await response.blob());
}

export async function getHealth() {
  const response = await fetch('/api/health');
  if (!response.ok) throw new Error('API health check failed');
  return response.json();
}

export async function analyzeAoi(lat, lng, incidentId) {
  const response = await fetch('/api/incidents/analyze-aoi', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lng, incidentId })
  });
  if (!response.ok) throw new Error('Targeted AOI analysis failed');
  return response.json();
}
