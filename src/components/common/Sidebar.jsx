import { useEffect, useState } from 'react';
import { getAois } from '../../services/marineApi';

export default function Sidebar({ activeView, setActiveView, panels, setPanels, health, spillCount, onSelectAoi }) {
  const [aois, setAois] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(null);
  useEffect(() => { getAois().then(a => { setAois(a); setActive(a[0]); }).catch(() => {}); }, []);

  const choose = aoi => { setOpen(false); setActive(aoi); onSelectAoi(aoi); };
  const ais = health?.ais;
  const aisOk = ais && !ais.lastError && ais.vessels > 0;

  return <aside className="app-sidebar">
    <div className="product-mark"><span className="mark-icon"><i className="bi bi-water" /></span><span><strong>MARINESIGHT</strong><small>OIL SPILL OPERATIONS</small></span></div>
    <div className="workspace-select">
      <span className="eyebrow">MONITORED AREA</span>
      <button className="workspace-button" type="button" onClick={() => setOpen(o => !o)} data-testid="aoi-button">
        <span className="workspace-dot" />{active?.name || 'Loading…'} <i className={`bi bi-chevron-${open ? 'up' : 'down'}`} />
      </button>
      {open && <div className="aoi-list">
        {aois.map(a => (
          <button key={a.id} onClick={() => choose(a)} className={a.id === active?.id ? 'active' : ''}>
            <strong>{a.name}</strong><small>{a.riskZone}</small>
          </button>
        ))}
      </div>}
    </div>
    <nav className="nav flex-column sidebar-nav" aria-label="Operations navigation">
      <span className="nav-caption">OPERATIONS</span>
      {[['Live monitoring', 'bi-radar'], ['Incidents', 'bi-exclamation-triangle']].map(([name, icon]) => (
        <button key={name} className={`nav-link ${activeView === name ? 'active' : ''}`} onClick={() => setActiveView(name)}>
          <i className={`bi ${icon}`} />{name}
          {name === 'Live monitoring' && <span className="live-indicator" />}
          {name === 'Incidents' && <span className="count-chip">{spillCount}</span>}
        </button>
      ))}
      <span className="nav-caption mt-4">PANELS</span>
      {[['layers', 'bi-layers', 'Layers'], ['queue', 'bi-list-task', 'Detections']].map(([key, icon, label]) => (
        <label className="nav-toggle-item" key={key}>
          <span className="nav-link-text"><i className={`bi ${icon}`} />{label}</span>
          <span className="form-check form-switch"><input className="form-check-input" type="checkbox" checked={panels[key]} onChange={e => setPanels(p => ({ ...p, [key]: e.target.checked }))} /></span>
        </label>
      ))}
    </nav>
    <div className="sidebar-bottom">
      <div className="feed-health" data-testid="feed-health">
        <div><span className="status-led ok" style={aisOk ? undefined : { background: '#b8b49a' }} /><strong>{aisOk ? `AIS live · ${ais.vessels.toLocaleString()} vessels` : 'AIS cache warming up'}</strong></div>
        <small>{ais?.lastRefreshAt ? `Cache refreshed ${ais.lastRefreshAt.slice(11, 16)} UTC · next ${ais.nextRefreshAt?.slice(11, 16)} UTC` : 'Refreshes every 10 minutes'}</small>
        <small>{health?.integrations?.mlModelsReady ? 'ML models loaded' : 'ML models missing'} · daily scan {String(health?.scheduler?.dailyUtcHour ?? 3).padStart(2, '0')}:00 UTC</small>
      </div>
    </div>
  </aside>;
}
