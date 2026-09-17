import { useMapSettings } from '../../context/MapContext';
import { useIncidents } from '../../context/IncidentContext';

const labels = { operations: 'SAR / preview composite', sentinel1: 'SENTINEL-1 / VV backscatter', sentinel2: 'SENTINEL-2 / true color' };

export default function MapToolbar({ onFocus }) {
  const { mode, setMode } = useMapSettings();
  const { selectedIncident, selectedDate, setSelectedDate } = useIncidents();
  
  const today = new Date().toISOString().substring(0, 10);
  const isHistorical = selectedDate !== today;

  return <div className="map-toolbar shadow-sm">
    <div className="toolbar-group">
      <span className="toolbar-label">DATE</span>
      <input 
        type="date" 
        className={`form-control form-control-sm ${isHistorical ? 'bg-warning text-dark' : 'bg-dark text-light border-secondary'}`} 
        value={selectedDate} 
        max={today}
        onChange={e => setSelectedDate(e.target.value)}
        title={isHistorical ? "Viewing Historical Archive" : "Live Operations Mode"}
      />
    </div>
    <span className="toolbar-divider" />
    <div className="toolbar-group"><span className="toolbar-label">BASEMAP</span><div className="btn-group btn-group-sm" role="group" aria-label="Satellite view switching">{[['operations', 'Operations'], ['sentinel1', 'Sentinel-1'], ['sentinel2', 'Sentinel-2']].map(([key, label]) => <button key={key} className={`btn map-mode ${mode === key ? 'btn-light active' : 'btn-outline-light'}`} onClick={() => setMode(key)}>{label}</button>)}</div></div>
    <span className="toolbar-divider" />
    <div className="toolbar-group scene-info"><span className="toolbar-label">LATEST SCENE</span><strong>{labels[mode]}</strong></div>
    {selectedIncident?.id && (
      <button className="btn btn-sm btn-outline-light ms-auto" onClick={onFocus}><i className="bi bi-crosshair2" />Focus {selectedIncident.id}</button>
    )}
  </div>;
}
