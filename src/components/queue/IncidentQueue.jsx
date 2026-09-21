import { useMemo, useState } from 'react';
import { useSpills } from '../../context/SpillContext';

const dotClass = s => (s.status === 'rejected' ? 'low' : s.severity === 'REVIEW' ? 'medium' : '');

function culpritLabel(c) {
  if (!c) return 'No source identified';
  if (c.kind === 'ais_vessel') return `${c.name || `MMSI ${c.mmsi}`} (AIS)`;
  if (c.kind === 'sar_vessel') return c.name || 'Vessel seen in SAR';
  if (c.kind === 'infrastructure') return c.name || 'Offshore infrastructure';
  return c.name || c.kind;
}

export default function IncidentQueue() {
  const { spills, selectedId, setSelectedId, date, load } = useSpills();
  const [filter, setFilter] = useState('all');
  const filtered = useMemo(() => spills.filter(s => filter === 'all' || (filter === 'high' ? s.severity === 'HIGH' : s.status === filter)), [spills, filter]);
  const counts = {
    all: spills.length,
    high: spills.filter(s => s.severity === 'HIGH').length,
    review: spills.filter(s => s.status === 'review').length
  };
  const stats = load.scan?.stats;
  return <section className="incident-queue shadow-sm" aria-label="Detected spills" data-testid="spill-queue">
    <div className="floating-heading">
      <div><span className="eyebrow">DETECTIONS · {date || '—'}</span><strong>Oil spill candidates <span className="queue-badge">{spills.length}</span></strong></div>
    </div>
    <div className="queue-filters">
      {[['all', 'All'], ['high', 'High'], ['review', 'Review']].map(([key, label]) => (
        <button key={key} className={`queue-filter ${filter === key ? 'active' : ''}`} onClick={() => setFilter(key)}>{label} <span>{counts[key]}</span></button>
      ))}
    </div>
    <div className="list-group list-group-flush incident-list">
      {filtered.map(s => (
        <button key={s.id} className={`list-group-item ${s.id === selectedId ? 'active' : ''}`} onClick={() => setSelectedId(s.id)} data-testid="spill-row">
          <span className={`incident-dot ${dotClass(s)}`} />
          <span className="incident-row">
            <div><strong>{s.id}</strong><time>{s.acquiredAt?.slice(11, 16)} UTC</time></div>
            <p>{s.aoiName} · {s.areaKm2.toFixed(1)} km² · {culpritLabel(s.culprit)}</p>
            <small><span>{s.status.toUpperCase()}</span><b>P(oil) {(s.oilProbability * 100).toFixed(0)}%</b></small>
          </span>
        </button>
      ))}
      {load.state === 'ready' && !spills.length && (
        <div className="queue-empty" data-testid="queue-empty">
          No oil spills detected on {date}.
          {stats && <small>{stats.scenes} Sentinel-1 scene(s) over {stats.aois} areas · {stats.candidates} SAR candidate(s) · {stats.rejected} rejected as look-alikes</small>}
        </div>
      )}
    </div>
  </section>;
}
