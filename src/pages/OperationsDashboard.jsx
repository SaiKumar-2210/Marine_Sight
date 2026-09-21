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
import { useSpills } from '../context/SpillContext';
import { useAisVessels } from '../hooks/useAisVessels';
import { getHealth } from '../services/marineApi';

function exportReport(spill, detail) {
  const a = detail?.attribution;
  const met = detail?.verification?.metocean || {};
  const lines = [
    'MARINESIGHT OIL SPILL REPORT', '',
    `Detection: ${spill.id}`, `Area of interest: ${spill.aoiName}`,
    `Sentinel-1 scene: ${detail?.scene?.id} (${spill.acquiredAt} UTC)`,
    `Status: ${spill.status} (P(oil) ${(spill.oilProbability * 100).toFixed(1)}%)`,
    `Area: ${spill.areaKm2.toFixed(2)} km²   Length: ${spill.lengthKm.toFixed(1)} km`,
    `Centroid: ${spill.centroid.lat.toFixed(4)}, ${spill.centroid.lon.toFixed(4)}`,
    `Wind: ${met.windMs ?? '–'} m/s from ${met.windFromDeg ?? '–'}°   Current: ${met.currentMs?.toFixed?.(2) ?? '–'} m/s to ${met.currentToDeg ?? '–'}°`,
    '', 'Verification indicators:', ...(detail?.verification?.indicators || []).map(t => `- ${t}`),
    '', 'Attribution candidates:',
    ...((a?.candidates || []).slice(0, 5).map((c, i) => `${i + 1}. ${c.kind} ${c.name || c.mmsi || ''} confidence ${(c.confidence * 100).toFixed(0)}%`)),
    '', 'Polygon (GeoJSON):', JSON.stringify(spill.geometry)
  ];
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }));
  link.download = `${spill.id}-marinesight-report.txt`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

export default function OperationsDashboard() {
  const { mode } = useMapSettings();
  const { spills, selected } = useSpills();
  const { vessels } = useAisVessels();
  const [activeView, setActiveView] = useState('Live monitoring');
  const [panels, setPanels] = useState({ layers: true, queue: true });
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const [toast, setToast] = useState('');
  const [flyTarget, setFlyTarget] = useState(null);
  const [focusVessel, setFocusVessel] = useState(null);
  const [health, setHealth] = useState(null);

  useEffect(() => {
    const load = () => getHealth().then(setHealth).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);
  const notify = useCallback(m => setToast(m), []);
  const mapClass = mode === 'sentinel1' ? 'sar-mode' : mode === 'sentinel2' ? 'sentinel2-mode' : 'operations-mode';

  return <div className="ops-app">
    <Sidebar activeView={activeView} setActiveView={setActiveView} panels={panels} setPanels={setPanels} health={health}
      spillCount={spills.length} onSelectAoi={aoi => { setFlyTarget({ center: aoi.center, zoom: aoi.zoom }); notify(`Viewing ${aoi.name}`); }} />
    <main className="app-main">
      <Header activeView={activeView} onNewWatch={() => notify('Monitored areas are configured in ml_service/aois.json')} onNotification={() => notify(`${spills.length} detection(s) for the selected date`)} />
      <section className={`map-workspace ${mapClass}`}>
        <MapCanvas vessels={vessels} flyTarget={flyTarget} focusVessel={focusVessel} onVesselSelected={() => setFocusVessel(null)} />
        <MapToolbar onFocus={() => selected && setFlyTarget({ center: [selected.centroid.lat, selected.centroid.lon], zoom: 10, t: Date.now() })} />
        {panels.layers && activeView !== 'Incidents' && <LayerPanel vessels={vessels} onReset={() => notify('Layers reset')} />}
        {panels.queue && <IncidentQueue />}
        <IncidentInspector onEvidence={() => setEvidenceOpen(true)} onTrack={mmsi => { setFocusVessel(mmsi); notify(`Tracking MMSI ${mmsi}`); }}
          onReport={(s, d) => { exportReport(s, d); notify(`Report downloaded for ${s.id}`); }} minimized={minimized} setMinimized={setMinimized} />
        <div className="map-footer-status" data-testid="footer-status">
          <span><i className="bi bi-broadcast" /> AIS <strong>{vessels.length.toLocaleString()} vessels</strong></span>
          <span><i className="bi bi-clock-history" /> cache <strong>{health?.ais?.lastRefreshAt ? `${health.ais.lastRefreshAt.slice(11, 16)} UTC` : '—'}</strong></span>
          <span><i className="bi bi-cpu" /> models <strong>{health?.integrations?.mlModelsReady ? 'ready' : 'missing'}</strong></span>
        </div>
      </section>
    </main>
    <EvidenceOverlayModal open={evidenceOpen} onClose={() => setEvidenceOpen(false)} />
    <Toast message={toast} onDismiss={() => setToast('')} />
  </div>;
}
