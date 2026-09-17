import { useState } from 'react';
import { COASTAL_REGIONS } from '../../data/coasts';

export default function CoastSelector({ activeCoastId, onSelectCoast }) {
  const [isOpen, setIsOpen] = useState(false);
  const activeCoast = COASTAL_REGIONS.find(c => c.id === activeCoastId) || COASTAL_REGIONS[0];

  const handleSelect = async (coast) => {
    setIsOpen(false);
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

  return (
    <div className="coast-selector-dropdown position-relative me-2">
      <button 
        className="btn btn-sm btn-outline-warning dropdown-toggle text-light d-flex align-items-center gap-1"
        type="button" 
        onClick={() => setIsOpen(!isOpen)}
        style={{ borderColor: 'rgba(255,204,0,0.4)', background: 'rgba(20,20,30,0.7)' }}
      >
        <span className="me-1">{activeCoast.flag}</span>
        <strong className="small">{activeCoast.name}</strong>
      </button>

      {isOpen && (
        <div 
          className="dropdown-menu show dropdown-menu-end shadow-lg p-2 position-absolute end-0 mt-1" 
          style={{ width: '280px', background: '#141722', border: '1px solid rgba(255,255,255,0.15)', zIndex: 1100 }}
        >
          <div className="dropdown-header text-uppercase small text-muted px-2 py-1">Select Coastal Theater</div>
          {COASTAL_REGIONS.map(coast => (
            <button
              key={coast.id}
              className={`dropdown-item text-light rounded d-flex align-items-center justify-content-between p-2 mb-1 ${coast.id === activeCoastId ? 'bg-primary' : 'bg-dark'}`}
              onClick={() => handleSelect(coast)}
              style={{ cursor: 'pointer' }}
            >
              <div>
                <div className="fw-bold small">{coast.flag} {coast.name}</div>
                <div className="text-muted extra-small" style={{ fontSize: '11px' }}>{coast.riskZone}</div>
              </div>
              {coast.id === activeCoastId && <i className="bi bi-check-circle-fill text-warning" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
