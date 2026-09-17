import { useCallback, useEffect, useState } from 'react';
import Header from '../components/common/Header';
import Sidebar from '../components/common/Sidebar';
import Toast from '../components/common/Toast';
import MapCanvas from '../components/map/MapContainer';
import MapToolbar from '../components/map/MapToolbar';
import LayerPanel from '../components/map/LayerPanel';
import IncidentQueue from '../components/queue/IncidentQueue';
import IncidentInspector from '../components/inspector/IncidentInspector';
import EvidenceOverlayModal from '../components/modals/EvidenceOverlayModal';
import { useMapSettings } from '../context/MapContext';
import { useIncidents } from '../context/IncidentContext';
import { getHealth } from '../services/marineApi';

function exportReport(incident) {
  const content = [
    'MARINESIGHT INCIDENT REPORT', '', `Incident: ${incident.id}`, `Status: ${incident.severity}`,
    `Detected: ${incident.detected || incident.time}`, `Confidence: ${incident.confidence}%`, `Estimated area: ${incident.area || 'Pending assessment'}`,
    `Likely source: ${incident.source || 'Under review'}`, `Origin distance: ${incident.distance || '—'}`, '', 'Evidence:',
    ...(incident.signals || []).map(([name, score, note]) => `- ${name}: ${score}% — ${note}`)
  ].join('\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
  link.download = `${incident.id}-marinesight-report.txt`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1_000);
}

export default function OperationsDashboard() {
  const { mode } = useMapSettings();
  const { selectedIncident, setFilter } = useIncidents();
  const [activeView, setActiveView] = useState('Live monitoring');
  const [panels, setPanels] = useState({ layers: true, queue: true });
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const [toast, setToast] = useState('');
  const [focusVessel, setFocusVessel] = useState(null);
  const [focusToken, setFocusToken] = useState(0);
  const [feedMode, setFeedMode] = useState('preview');
  const [activeCoastId, setActiveCoastId] = useState('ARABIAN_SEA');
  const [focusCoast, setFocusCoast] = useState(null);

  useEffect(() => { getHealth().then(data => setFeedMode(data.integrations.aisConfigured ? 'aisstream' : 'preview')).catch(() => setFeedMode('preview')); }, []);
  useEffect(() => { if (activeView === 'Incidents') setFilter('all'); }, [activeView, setFilter]);
  const notify = useCallback(message => setToast(message), []);

  const handleCoastSelect = useCallback(coast => {
    setActiveCoastId(coast.id);
    setFocusCoast(coast);
    notify(`Switched operational theater to ${coast.name}`);
  }, [notify]);
  const showAll = () => { setActiveView('Incidents'); setFilter('all'); notify('Showing all incident records'); };
  const trackSource = source => {
    if (!source || source.includes('Unknown') || source.includes('platform')) { notify('No AIS vessel is associated with this incident'); return; }
    setFocusVessel(source); notify(`Tracking ${source}`);
  };
  const mapClass = mode === 'sentinel1' ? 'sar-mode' : mode === 'sentinel2' ? 'sentinel2-mode' : 'operations-mode';

  return <div className="ops-app">
    <Sidebar activeView={activeView} setActiveView={setActiveView} panels={panels} setPanels={setPanels} feedMode={feedMode} activeCoastId={activeCoastId} onSelectCoast={handleCoastSelect} />
    <main className="app-main"><Header activeView={activeView} onNewWatch={() => notify(`New monitoring watch created for ${activeCoastId}`)} onNotification={() => notify('No unacknowledged operational notifications')} />
      <section className={`map-workspace ${mapClass}`}>
        <MapCanvas focusCoast={focusCoast} focusVessel={focusVessel} focusToken={focusToken} onFocusComplete={() => setFocusVessel(null)} onStatus={notify} />
        <MapToolbar onFocus={() => { setFocusToken(value => value + 1); notify(`Focusing ${selectedIncident?.id || 'Map'}`); }} />
        {panels.layers && activeView !== 'Incidents' && <LayerPanel onReset={() => notify('Operational layers reset')} />}
        {panels.queue && <IncidentQueue onViewAll={showAll} />}
        <IncidentInspector onEvidence={() => setEvidenceOpen(true)} onTrack={trackSource} onReport={incident => { exportReport(incident); notify(`Report downloaded for ${incident.id}`); }} minimized={minimized} setMinimized={setMinimized} />
        <div className="map-footer-status"><span><i className="bi bi-cloud-check" /> Sentinel-1 <strong>ready</strong></span><span><i className="bi bi-broadcast" /> AIS <strong>{feedMode === 'aisstream' ? 'live' : 'preview'}</strong></span><span><i className="bi bi-brightness-alt-high" /> Weather <strong>ready</strong></span></div>
      </section>
    </main>
    <EvidenceOverlayModal open={evidenceOpen} onClose={() => setEvidenceOpen(false)} />
    <Toast message={toast} onDismiss={() => setToast('')} />
  </div>;
}
