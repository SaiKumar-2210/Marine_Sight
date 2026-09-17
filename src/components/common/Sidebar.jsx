import { useState } from 'react';
import { COASTAL_REGIONS } from '../../data/coasts';

export default function Sidebar({ activeView, setActiveView, panels, setPanels, feedMode, activeCoastId, onSelectCoast }) {
  const [coastOpen, setCoastOpen] = useState(false);
  const activeCoast = COASTAL_REGIONS.find(c => c.id === activeCoastId) || COASTAL_REGIONS[0];

  const handleCoastSelect = async (coast) => {
    setCoastOpen(false);
    try {
      const apiHost = window.location.port === '5173' ? 'http://localhost:3000' : '';
      await fetch(`${apiHost}/api/coasts/select`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: coast.id })
      });
    } catch { /* Ignore */ }
    onSelectCoast(coast);
  };

  const navItems = [['Overview', 'bi-grid-1x2'], ['Live monitoring', 'bi-radar'], ['Incidents', 'bi-exclamation-triangle']];

  return <aside className="app-sidebar">
    <div className="product-mark"><span className="mark-icon"><i className="bi bi-water" /></span><span><strong>MARINESIGHT</strong><small>INCIDENT OPERATIONS</small></span></div>

    {/* Coast Selector (moved from header) */}
    <div className="workspace-select">
      <span className="eyebrow">ACTIVE THEATER</span>
      <button className="workspace-button" type="button" onClick={() => setCoastOpen(!coastOpen)}>
        <span className="workspace-dot" />{activeCoast.flag} {activeCoast.name} <i className={`bi bi-chevron-${coastOpen ? 'up' : 'down'}`} />
      </button>
      {coastOpen && (
        <div style={{ background: 'rgba(20,20,30,0.95)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: '8px', marginTop: '6px', padding: '6px', maxHeight: '280px', overflowY: 'auto' }}>
          {COASTAL_REGIONS.map(coast => (
            <button
              key={coast.id}
              onClick={() => handleCoastSelect(coast)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
                padding: '8px 10px', marginBottom: '3px', border: 'none', borderRadius: '6px', cursor: 'pointer',
                background: coast.id === activeCoastId ? 'rgba(212,175,55,0.2)' : 'transparent',
                color: '#e0e0e0', fontSize: '12px', textAlign: 'left'
              }}
            >
              <div>
                <div style={{ fontWeight: 600 }}>{coast.flag} {coast.name}</div>
                <div style={{ fontSize: '10px', opacity: 0.6, marginTop: '2px' }}>{coast.riskZone}</div>
              </div>
              {coast.id === activeCoastId && <i className="bi bi-check-circle-fill" style={{ color: '#d4af37' }} />}
            </button>
          ))}
        </div>
      )}
    </div>

    <nav className="nav flex-column sidebar-nav" aria-label="Operations navigation">
      <span className="nav-caption">OPERATIONS</span>
      {navItems.map(([name, icon]) => <button key={name} className={`nav-link ${activeView === name ? 'active' : ''}`} onClick={() => setActiveView(name)}><i className={`bi ${icon}`} />{name}{name === 'Live monitoring' && <span className="live-indicator" />}{name === 'Incidents' && <span className="count-chip">10</span>}</button>)}
      <span className="nav-caption mt-4">PANELS</span>
      <label className="nav-toggle-item"><span className="nav-link-text"><i className="bi bi-layers" />Live layers</span><span className="form-check form-switch"><input className="form-check-input" type="checkbox" checked={panels.layers} onChange={event => setPanels(current => ({ ...current, layers: event.target.checked }))} /></span></label>
      <label className="nav-toggle-item"><span className="nav-link-text"><i className="bi bi-list-task" />Live queue</span><span className="form-check form-switch"><input className="form-check-input" type="checkbox" checked={panels.queue} onChange={event => setPanels(current => ({ ...current, queue: event.target.checked }))} /></span></label>
    </nav>
    <div className="sidebar-bottom"><div className="feed-health"><div><span className="status-led ok" /><strong>{feedMode === 'aisstream' ? 'Live feeds connected' : 'Preview feeds ready'}</strong></div><small>{feedMode === 'aisstream' ? 'AIS stream active' : 'Add .env keys for live providers'}</small></div><button className="profile-button"><span className="profile-avatar">SK</span><span><strong>Samir Kumar</strong><small>Operations analyst</small></span><i className="bi bi-three-dots" /></button></div>
  </aside>;
}
