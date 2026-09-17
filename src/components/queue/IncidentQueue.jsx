import { useMemo } from 'react';
import { useIncidents } from '../../context/IncidentContext';

const severityClass = severity => severity === 'REVIEW' ? 'medium' : severity === 'LOW' ? 'low' : '';

export default function IncidentQueue({ onViewAll }) {
  const { incidents, selectedId, setSelectedId, filter, setFilter } = useIncidents();
  const filtered = useMemo(() => incidents.filter(item => filter === 'all' || item.severity === (filter === 'high' ? 'HIGH' : 'REVIEW')), [filter, incidents]);
  const counts = { all: incidents.length, high: incidents.filter(item => item.severity === 'HIGH').length, review: incidents.filter(item => item.severity === 'REVIEW').length };
  return <section className="incident-queue shadow-sm" aria-label="Live incident queue"><div className="floating-heading"><div><span className="eyebrow">LIVE QUEUE</span><strong>Priority incidents <span className="queue-badge">{incidents.length}</span></strong></div><button className="btn btn-sm btn-link" onClick={onViewAll}>View all</button></div><div className="queue-filters">{[['all', 'All'], ['high', 'High'], ['review', 'Review']].map(([key, label]) => <button key={key} className={`queue-filter ${filter === key ? 'active' : ''}`} onClick={() => setFilter(key)}>{label} <span>{counts[key]}</span></button>)}</div><div className="list-group list-group-flush incident-list">{filtered.map(item => <button key={item.id} className={`list-group-item ${item.id === selectedId ? 'active' : ''}`} onClick={() => setSelectedId(item.id)}><span className={`incident-dot ${severityClass(item.severity)}`} /><span className="incident-row"><div><strong>{item.id}</strong><time>{item.time}</time></div><p>{item.summary}</p><small><span>{item.severity}</span><b>{item.confidence}% confidence</b></small></span></button>)}</div></section>;
}
