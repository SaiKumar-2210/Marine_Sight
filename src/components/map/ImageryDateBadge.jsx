/**
 * Says, on screen, exactly which imagery is being looked at.
 *
 * The operator picks a date, but a Sentinel pass only revisits an area every few days, so what is
 * drawn is usually an earlier acquisition. Showing the requested date alone would be misleading,
 * so this names the real acquisition, how far back it is, and when it falls outside the capped
 * lookback window.
 */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function pretty(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return `${String(d.getUTCDate()).padStart(2, '0')} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

const timeOf = iso => (iso && iso.length > 11 ? `${iso.slice(11, 16)} UTC` : '');

function offsetLabel(days) {
  if (days <= 0) return 'same day as the selected date';
  return `${days} day${days === 1 ? '' : 's'} before the selected date`;
}

export default function ImageryDateBadge({ mode, date, coverage, error, spills = [] }) {
  const sourceName = mode === 'sentinel2' ? 'Sentinel-2 optical' : 'Sentinel-1 radar';
  // In the operations view the basemap is a chart, so the imagery on show is the pass the
  // detections came from.
  const detectionPass = spills.find(s => s.acquiredAt)?.acquiredAt || null;

  let tone = 'ok';
  let headline;
  let detail;

  if (error) {
    tone = 'warn';
    headline = `${sourceName} · date unresolved`;
    detail = error;
  } else if (mode === 'operations') {
    headline = detectionPass
      ? `Detections from ${pretty(detectionPass)} ${timeOf(detectionPass)}`
      : `No imagery loaded for ${pretty(date)}`;
    detail = detectionPass
      ? `Sentinel-1 pass behind the detections shown · selected date ${date}`
      : `Selected date ${date} · switch to Sentinel-1 or Sentinel-2 to view imagery`;
  } else if (!coverage) {
    headline = `${sourceName} · resolving pass…`;
    detail = `Selected date ${date}`;
  } else if (coverage.state === 'none') {
    tone = 'warn';
    headline = `No ${sourceName} pass for this view`;
    detail = `Nothing within ${coverage.maxLookbackDays} days of ${date} — the search stops there. Zoom out or pick another date.`;
  } else {
    tone = coverage.beyondLookback ? 'warn' : coverage.sameDay ? 'ok' : 'info';
    headline = `${sourceName} · ${pretty(coverage.acquiredAt)} ${timeOf(coverage.acquiredAt)}`;
    detail = coverage.beyondLookback
      ? `No pass within ${coverage.maxLookbackDays} days of ${date} — showing the most recent one instead, ${coverage.daysBefore} days earlier.`
      : offsetLabel(coverage.daysBefore) + (coverage.cloudCover != null ? ` · cloud ${Math.round(coverage.cloudCover)}%` : '');
  }

  return <div className={`imagery-date ${tone}`} data-testid="imagery-date" title={coverage?.sceneId || ''}>
    <span className="eyebrow">IMAGERY ON SCREEN</span>
    <strong data-testid="imagery-date-value">{headline}</strong>
    <small>{detail}</small>
  </div>;
}
