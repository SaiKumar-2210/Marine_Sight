import { useMapSettings } from '../../context/MapContext';
import { useSpills } from '../../context/SpillContext';

const MODES = [['operations', 'Operations'], ['sentinel1', 'Sentinel-1'], ['sentinel2', 'Sentinel-2']];

export default function MapToolbar({ onFocus }) {
  const { mode, setMode } = useMapSettings();
  const { date, setDate, load, selected, scanIndex } = useSpills();
  const today = new Date().toISOString().slice(0, 10);
  const stored = scanIndex.find(s => s.date === date);
  const status = load.state === 'loading' ? 'Scanning…'
    : load.state === 'failed' ? 'Scan failed'
      : stored ? `Stored · ${stored.trigger}` : load.state === 'ready' ? 'Stored' : '—';

  return <div className="map-toolbar shadow-sm">
    <div className="toolbar-group">
      <span className="toolbar-label">DATE (UTC)</span>
      <input type="date" className="form-control form-control-sm bg-dark text-light border-secondary" data-testid="date-input"
        value={date || ''} max={today} min="2014-10-03" onChange={e => setDate(e.target.value)} />
      <span className={`scan-chip ${load.state}`} data-testid="scan-status">{status}</span>
    </div>
    <span className="toolbar-divider" />
    <div className="toolbar-group">
      <span className="toolbar-label">BASEMAP</span>
      <div className="btn-group btn-group-sm" role="group">
        {MODES.map(([key, label]) => (
          <button key={key} className={`btn map-mode ${mode === key ? 'btn-light active' : 'btn-outline-light'}`} onClick={() => setMode(key)}>{label}</button>
        ))}
      </div>
    </div>
    {selected && <button className="btn btn-sm btn-outline-light ms-auto" onClick={onFocus}><i className="bi bi-crosshair2" /> Focus {selected.id}</button>}
  </div>;
}
