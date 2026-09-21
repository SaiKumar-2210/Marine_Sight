import { useEffect, useState } from 'react';
import { useSpills } from '../../context/SpillContext';
import { AIS_TYPE_LABEL } from '../../utils/vesselTaxonomy';

const pct = v => (v == null ? '–' : `${(v * 100).toFixed(0)}%`);
const num = (v, d = 1, unit = '') => (v == null || Number.isNaN(v) ? '–' : `${Number(v).toFixed(d)}${unit}`);
const compass = deg => (deg == null ? '' : ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(deg / 45) % 8]);

function candidateTitle(c) {
  if (c.kind === 'ais_vessel') return c.name || `MMSI ${c.mmsi}`;
  if (c.kind === 'sar_vessel') return c.aisMatch ? `${c.aisMatch.name || c.aisMatch.mmsi} (seen in SAR)` : (c.relation === 'ahead_on_axis' ? 'Vessel ahead of slick (SAR, no AIS)' : 'AIS-dark vessel seen in SAR');
  return c.name;
}

function candidateNote(c) {
  if (c.kind === 'ais_vessel') {
    return `${AIS_TYPE_LABEL(c.shipType)} · MMSI ${c.mmsi} · explains ${pct(c.coverage)} of slick, mean offset ${num(c.meanOffsetKm, 2)} km`
      + (c.releaseWindow ? ` · est. release ${c.releaseWindow[0].slice(11, 16)}–${c.releaseWindow[1].slice(11, 16)} UTC` : '');
  }
  if (c.kind === 'sar_vessel' && c.relation === 'ahead_on_axis') {
    return `Ship seen in SAR ${c.distanceToHeadKm} km ahead of the slick head, ${c.angleFromAxisDeg}° off its axis — consistent with a vessel that stopped discharging and steamed on${c.darkVessel ? ' · no AIS match' : ''}`;
  }
  if (c.kind === 'sar_vessel') return `Bright point target ${c.peakDb} dB, ${c.distanceToHeadKm} km from slick head${c.darkVessel ? ' · no AIS match at acquisition time' : ''}`;
  return `${c.distanceToEndKm} km from a slick end${c.reason ? ` · ${c.reason}` : ''}`;
}

export default function IncidentInspector({ onEvidence, onTrack, onReport, minimized, setMinimized }) {
  const { selected, detail, load, date } = useSpills();
  const [tab, setTab] = useState('overview');
  useEffect(() => setTab('overview'), [selected?.id]);

  if (!selected) {
    return <section className={`incident-inspector shadow ${minimized ? 'minimized' : ''}`} aria-label="Selected spill">
      <div className="inspector-header"><div>
        <h2>{load.state === 'loading' ? 'Scanning…' : 'No spill selected'}</h2>
        <p>{load.state === 'ready' ? `Select a detection from ${date} on the map or in the list.` : 'Results appear here when the scan completes.'}</p>
      </div></div>
    </section>;
  }

  const d = detail?.id === selected.id ? detail.detail : null;
  const v = d?.verification;
  const met = v?.metocean || {};
  const feats = v?.features || {};
  const att = d?.attribution;
  const culprit = d?.culprit;

  return <section className={`incident-inspector shadow ${minimized ? 'minimized' : ''}`} aria-label="Selected spill details" data-testid="inspector">
    <div className="inspector-header">
      <div>
        <div className="incident-identity">
          <span className={`severity-status ${selected.severity === 'REVIEW' ? 'medium' : selected.severity === 'LOW' ? 'low' : ''}`} />
          <span>{selected.id}</span>
          <span className="incident-severity">{selected.status.toUpperCase()}</span>
        </div>
        <h2>{selected.aoiName}</h2>
        <p>Sentinel-1 {d?.scene?.platform || ''} pass {selected.acquiredAt?.replace('T', ' ').slice(0, 16)} UTC</p>
      </div>
      <button className="btn btn-sm btn-outline-light inspector-close" onClick={() => setMinimized(m => !m)} title={minimized ? 'Expand' : 'Collapse'}>
        <i className={`bi ${minimized ? 'bi-chevron-left' : 'bi-chevron-right'}`} />
      </button>
    </div>

    <div className="primary-confidence">
      <div><span>P(OIL) — MULTI-MODAL VERIFIER</span><strong data-testid="oil-probability">{pct(selected.oilProbability)}</strong></div>
      <div className="progress"><div className="progress-bar" style={{ width: pct(selected.oilProbability) }} /></div>
      <p>{selected.status === 'confirmed' ? 'Confirmed oil slick' : selected.status === 'review' ? 'Probable slick — analyst review' : 'Rejected as look-alike'}</p>
    </div>

    <div className="inspector-actions">
      <button className="btn btn-primary btn-sm" onClick={onEvidence} disabled={!d}><i className="bi bi-columns-gap" /> Evidence</button>
      <button className="btn btn-outline-light btn-sm" onClick={() => onReport(selected, d)} disabled={!d}><i className="bi bi-file-earmark-arrow-down" /> Report</button>
    </div>

    <ul className="nav nav-pills inspector-tabs" role="tablist">
      {['overview', 'verification', 'attribution'].map(name => (
        <li key={name}><button className={`nav-link ${tab === name ? 'active' : ''}`} onClick={() => setTab(name)} data-testid={`tab-${name}`}>{name}</button></li>
      ))}
    </ul>

    {tab === 'overview' && <div className="detail-pane active">
      <div className="fact-grid">
        <div><span>SLICK AREA</span><strong data-testid="spill-area">{num(selected.areaKm2, 2)} km²</strong></div>
        <div><span>LENGTH</span><strong>{num(selected.lengthKm, 1)} km</strong></div>
        <div><span>POLYGON PARTS</span><strong>{d?.metrics?.nParts ?? '–'}</strong></div>
        <div><span>SAR CONTRAST</span><strong>{num(d?.metrics?.contrastDb, 1)} dB</strong></div>
        <div><span>CENTROID</span><strong>{selected.centroid.lat.toFixed(3)}°, {selected.centroid.lon.toFixed(3)}°</strong></div>
        <div><span>U-NET MEAN PROB</span><strong>{pct(d?.metrics?.meanProb)}</strong></div>
      </div>
      <div className="source-callout" data-testid="culprit-callout">
        <div className="callout-icon"><i className="bi bi-bullseye" /></div>
        <div>
          <span>MOST LIKELY SOURCE</span>
          <strong>{culprit ? candidateTitle(culprit) : (att ? 'No source identified' : 'Not attributed (rejected)')}</strong>
          <p>{culprit ? `${pct(culprit.confidence)} attribution confidence · ${culprit.kind.replace('_', ' ')}` :
            att ? `${att.aisCoverage.vessels} AIS vessels in the ±24 h window` : ''}</p>
        </div>
        {culprit?.kind === 'ais_vessel' && <button className="btn btn-sm btn-link" onClick={() => onTrack(culprit.mmsi)}>Track</button>}
      </div>
    </div>}

    {tab === 'verification' && <div className="detail-pane active">
      <div className="fact-grid">
        <div><span>WIND (10 m)</span><strong>{num(met.windMs, 1, ' m/s')} {met.windFromDeg != null ? `from ${compass(met.windFromDeg)}` : ''}</strong></div>
        <div><span>CURRENT</span><strong>{num(met.currentMs, 2, ' m/s')} {met.currentToDeg != null ? `to ${compass(met.currentToDeg)}` : ''}</strong></div>
        <div><span>WAVE HEIGHT</span><strong>{num(met.waveHeightM, 1, ' m')}</strong></div>
        <div><span>SENTINEL-2</span><strong>{v?.optical?.status?.replace('_', ' ') || '–'}{v?.optical?.dtHours != null ? ` (${num(Math.abs(v.optical.dtHours), 1)} h)` : ''}</strong></div>
      </div>
      <div className="signal-header mt-3"><span>Verification indicators</span></div>
      <div className="evidence-list">
        {(v?.indicators || []).map(t => <div className="evidence-item" key={t}><p>{t}</p></div>)}
        {v?.optical?.note && <div className="evidence-item"><p>{v.optical.note}</p></div>}
      </div>
      <div className="historical-note">
        <i className="bi bi-cpu" />
        <span><strong>Gradient-boosted verifier</strong>
          <small>SAR shape & contrast + wind/current/waves + S2 FAI/NDVI/visible contrast → P(oil) {pct(selected.oilProbability)}.
            Scene dark fraction {pct(feats.sceneDarkFrac)}. Nearest SAR vessel {num(feats.nearestSarVesselKm, 1, ' km')}.</small></span>
      </div>
    </div>}

    {tab === 'attribution' && <div className="detail-pane active" data-testid="attribution-pane">
      {!att ? <p className="text-muted small">Attribution runs for confirmed/review slicks only.</p> : <>
        <div className="fact-grid">
          <div><span>AIS VESSELS (±24 h)</span><strong>{att.aisCoverage.vessels}</strong></div>
          <div><span>AIS POSITIONS</span><strong>{att.aisCoverage.positions}</strong></div>
          <div><span>OIL DRIFT</span><strong>{num(att.drift.speedKnots, 2)} kn</strong></div>
          <div><span>HEAD CONFIDENCE</span><strong>{pct(att.head.confidence)}</strong></div>
        </div>
        {att.aisCoverage.positions === 0 && (
          <div className="historical-note"><i className="bi bi-info-circle" /><span><strong>No stored AIS for this window</strong>
            <small>AIS history is recorded from the live feed going forward; for older dates attribution relies on vessels detected directly in the SAR image.</small></span></div>
        )}
        <div className="signal-header mt-3"><span>Ranked candidate sources</span><span>Confidence</span></div>
        <div className="evidence-list">
          {att.candidates.length === 0 && <p className="text-muted small">No vessel track, SAR target or platform fits this slick.</p>}
          {att.candidates.slice(0, 6).map((c, i) => (
            <div className="evidence-item" key={`${c.kind}-${c.mmsi || c.id || `${c.lon},${c.lat}`}`} data-testid="candidate">
              <div className="evidence-top"><strong>{i + 1}. {candidateTitle(c)}</strong><span>{pct(c.confidence)}</span></div>
              <p>{candidateNote(c)}</p>
              <div className="progress"><div className="progress-bar" style={{ width: pct(c.confidence) }} /></div>
            </div>
          ))}
        </div>
        <div className="historical-note"><i className="bi bi-diagram-3" /><span><strong>Method</strong><small>{att.method}. Oil drift = current + 3 % wind.</small></span></div>
      </>}
    </div>}
  </section>;
}
