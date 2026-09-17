import { useEffect, useState } from 'react';

function utcTime() {
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date()) + ' UTC';
}

export default function Header({ activeView, onNewWatch, onNotification }) {
  const [clock, setClock] = useState(utcTime);
  useEffect(() => { const timer = setInterval(() => setClock(utcTime()), 1_000); return () => clearInterval(timer); }, []);
  const heading = activeView === 'Overview' ? 'Operations overview' : activeView === 'Incidents' ? 'Incident management' : 'Maritime incident monitor';

  return <header className="top-navigation">
    <div className="title-block">
      <span className="eyebrow">OPERATIONS / {activeView.toUpperCase()}</span>
      <h1>{heading}</h1>
    </div>
    <div className="top-actions align-items-center">
      <div className="live-clock"><span className="status-led ok" /><span>LIVE</span><strong>{clock}</strong></div>
      <button className="btn btn-sm btn-outline-light top-icon" onClick={onNotification} title="Notifications" aria-label="Notifications"><i className="bi bi-bell" /><span className="notification-dot" /></button>
      <button className="btn btn-primary btn-sm new-watch" onClick={onNewWatch}><i className="bi bi-plus-lg" />New watch</button>
    </div>
  </header>;
}
