import { useEffect, useState } from 'react';
import { useIncidents } from '../../context/IncidentContext';
import { analyzeAoi, getWeather } from '../../services/marineApi';

const severityClass = severity => severity === 'REVIEW' ? 'medium' : severity === 'LOW' ? 'low' : '';

export default function IncidentInspector({ onEvidence, onTrack, onReport, minimized, setMinimized }) {
  const { selectedIncident: incident } = useIncidents();
  const [tab, setTab] = useState('overview');
  const [weather, setWeather] = useState(null);
  const [aoiAnalysis, setAoiAnalysis] = useState(null);

  useEffect(() => {
    if (!incident.id) return;
    setTab('overview');
    getWeather(incident.lat, incident.lng).then(setWeather).catch(() => setWeather(null));
    analyzeAoi(incident.lat, incident.lng, incident.id).then(setAoiAnalysis).catch(() => setAoiAnalysis(null));
  }, [incident]);

  if (!incident || !incident.id) {
    return <section className={`incident-inspector shadow ${minimized ? 'minimized' : ''}`} aria-label="Selected incident details">
      <div className="inspector-header"><h2>Awaiting New Detections</h2><p>The ML scanner is monitoring the live AIS feed.</p></div>
    </section>;
  }

  const confidenceScore = aoiAnalysis?.confidenceScore || incident.confidence;
  const signals = aoiAnalysis ? [
    ['Sentinel-1 SAR CNN Anomaly Model', `${aoiAnalysis.signalsBreakdown.sarCnnModelScore}%`, 'CNN backscatter attenuation candidate'],
    ['Sentinel-2 Optical Spectral Check', `${aoiAnalysis.signalsBreakdown.opticalVerificationScore}%`, aoiAnalysis.opticalCloudFallback.triggered ? `Cloud fallback triggered (${aoiAnalysis.opticalCloudFallback.currentCloudCoverPercent}% clouds, fallback date used)` : 'Clear scene multispectral match'],
    ['AIS Trajectory Match', `${aoiAnalysis.signalsBreakdown.aisTrajectoryMatchScore}%`, `Backward drift matches ${aoiAnalysis.suspectedSourceVessel.name}`],
    ['Environmental Hydrodynamic Drift', `${aoiAnalysis.signalsBreakdown.environmentalDriftScore}%`, `V_slick = V_current + 0.03*V_wind (${aoiAnalysis.hydrodynamicDrift.slickDriftSpeedKnots} kn)`]
  ] : (incident.signals || [['SAR morphology', incident.confidence, 'Candidate detected'], ['AIS association', 45, 'Review required']]);

  return <section className={`incident-inspector shadow ${minimized ? 'minimized' : ''}`} aria-label="Selected incident details">
    <div className="inspector-header">
      <div>
        <div className="incident-identity">
          <span className={`severity-status ${severityClass(incident.severity)}`} />
          <span>{incident.id}</span>
          <span className="incident-severity">{incident.severity}</span>
        </div>
        <h2>{incident.title}</h2>
        <p>{incident.detected || `Detected 01 Sep 2026 · ${incident.time} UTC`}</p>
      </div>
      <button className="btn btn-sm btn-outline-light inspector-close" onClick={() => setMinimized(value => !value)} title={minimized ? 'Expand details' : 'Collapse details'}>
        <i className={`bi ${minimized ? 'bi-chevron-left' : 'bi-chevron-right'}`} />
      </button>
    </div>

    <div className="primary-confidence">
      <div>
        <span>MULTI-SIGNAL CONFIDENCE</span>
        <strong>{confidenceScore}%</strong>
      </div>
      <div className="progress">
        <div className="progress-bar bg-warning" style={{ width: `${confidenceScore}%` }} />
      </div>
      <p>{confidenceScore >= 80 ? 'Critical Alert · Immediate response recommended' : 'Multi-signal review required'}</p>
    </div>

    <div className="inspector-actions">
      <button className="btn btn-primary btn-sm" onClick={onEvidence}><i className="bi bi-columns-gap" />Evidence view</button>
      <button className="btn btn-outline-light btn-sm" onClick={() => onReport(incident)}><i className="bi bi-file-earmark-arrow-down" />Report</button>
    </div>

    <ul className="nav nav-pills inspector-tabs" role="tablist">
      {['overview', 'evidence', 'timeline'].map(name => (
        <li key={name}>
          <button className={`nav-link ${tab === name ? 'active' : ''}`} onClick={() => setTab(name)}>{name}</button>
        </li>
      ))}
    </ul>

    {tab === 'overview' && (
      <div className="detail-pane active">
        <div className="fact-grid">
          <div><span>Estimated area</span><strong>{incident.area || '18.4 km²'}</strong></div>
          <div><span>Nearest source</span><strong>{aoiAnalysis?.suspectedSourceVessel?.name || incident.source || 'MV Ocean Star'}</strong></div>
          <div><span>Origin distance</span><strong>{aoiAnalysis?.suspectedSourceVessel?.distanceKm || '1.2'} km</strong></div>
          <div><span>Drift Speed</span><strong>{aoiAnalysis?.hydrodynamicDrift?.slickDriftSpeedKnots || '0.96'} kn</strong></div>
        </div>

        {aoiAnalysis?.opticalCloudFallback?.triggered && (
          <div className="alert alert-dark border-warning text-warning small my-2 py-2">
            <i className="bi bi-cloud-slash me-1" />
            <strong>Optical Cloud Fallback Active:</strong> Cloud cover was {aoiAnalysis.opticalCloudFallback.currentCloudCoverPercent}%. Showing clear scene from past day ({new Date(aoiAnalysis.opticalCloudFallback.selectedOpticalDate).toLocaleDateString()}).
          </div>
        )}

        <div className="source-callout">
          <div className="callout-icon"><i className="bi bi-tsunami" /></div>
          <div>
            <span>LIKELY SOURCE</span>
            <strong>{aoiAnalysis?.suspectedSourceVessel?.name || incident.source || 'MV Ocean Star'}</strong>
            <p>MMSI {aoiAnalysis?.suspectedSourceVessel?.mmsi || '419001842'} · Alignment 90%</p>
          </div>
          <button className="btn btn-sm btn-link" onClick={() => onTrack(aoiAnalysis?.suspectedSourceVessel?.name || incident.source)}>Track</button>
        </div>

        <div className="explainer">
          <div>
            <i className="bi bi-stars" />
            <span><strong>Multi-Factor Weighted Verification</strong><small>SAR CNN (35%) + S2 Optical (25%) + AIS (25%) + Wind/Current (15%)</small></span>
          </div>
          <button className="btn btn-sm btn-light" onClick={onEvidence}>Explain</button>
        </div>
      </div>
    )}

    {tab === 'evidence' && (
      <div className="detail-pane active">
        <div className="signal-header"><span>Evidence signal</span><span>Score</span></div>
        <div className="evidence-list">
          {signals.map(([name, score, note]) => (
            <div className="evidence-item" key={name}>
              <div className="evidence-top"><strong>{name}</strong><span>{score}</span></div>
              <p>{note}</p>
              <div className="progress"><div className="progress-bar" style={{ width: typeof score === 'string' ? score : `${score}%` }} /></div>
            </div>
          ))}
        </div>
        <div className="historical-note">
          <i className="bi bi-wind" />
          <span>
            <strong>{weather?.source === 'preview' ? 'Hydrodynamic Drift Model' : 'Copernicus CMEMS & ERA5'}</strong>
            <small>
              {weather
                ? `${weather.wind?.direction || 'NW'} wind ${weather.wind?.speedKnots || 12} kn · ${weather.waves?.heightMetres || 1.2} m waves · current ${weather.current?.speedKnots || 0.6} kn`
                : 'Environmental conditions loading...'}
            </small>
          </span>
        </div>
      </div>
    )}

    {tab === 'timeline' && (
      <div className="detail-pane active">
        <div className="activity-timeline">
          {(incident.timeline || [[`${incident.time || '08:45'} UTC`, 'Candidate queued', 'Awaiting analyst triage.']]).map(([time, title, description], index, list) => (
            <div className={`timeline-event ${index === list.length - 1 ? 'alert' : ''}`} key={`${time}-${title}`}>
              <time>{time}</time>
              <strong>{title}</strong>
              <p>{description}</p>
            </div>
          ))}
        </div>
      </div>
    )}
  </section>;
}
