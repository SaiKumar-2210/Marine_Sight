import { useEffect } from 'react';
import { useSpills } from '../../context/SpillContext';

function Card({ title, subtitle, children }) {
  return <article className="evidence-card real">
    <div className="evidence-card-title"><span>{title}</span><small>{subtitle}</small></div>
    {children}
  </article>;
}

function sourceLabel(c) {
  if (!c) return 'Not identified';
  if (c.name || c.aisMatch?.name) return c.name || c.aisMatch.name;
  if (c.kind === 'sar_vessel') return c.relation === 'ahead_on_axis' ? `Vessel ${c.distanceToHeadKm} km ahead of the slick (SAR, no AIS)` : 'Vessel at the slick head (SAR, no AIS)';
  return c.mmsi || c.kind;
}

export default function EvidenceOverlayModal({ open, onClose }) {
  const { selected, detail } = useSpills();
  useEffect(() => {
    if (!open) return undefined;
    const esc = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [open, onClose]);
  if (!open || !selected || detail?.id !== selected.id) return null;
  const d = detail.detail;
  const q = d.quicklooks || {};
  const opt = d.verification?.optical || {};
  return <div className="offcanvas-backdrop-custom open" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <section className="evidence-workspace" role="dialog" aria-modal="true" aria-label="Evidence view" data-testid="evidence-modal">
      <header>
        <div><span className="eyebrow">EVIDENCE / {selected.id}</span><h2>Satellite evidence for this detection</h2></div>
        <button className="btn btn-outline-light btn-sm" onClick={onClose} aria-label="Close"><i className="bi bi-x-lg" /></button>
      </header>
      <div className="evidence-grid-real">
        <Card title="SENTINEL-1 VV BACKSCATTER" subtitle={`${d.scene.id.slice(0, 32)}… · U-Net polygon in red, SAR vessels in cyan`}>
          {q.s1 ? <img src={q.s1.url} alt="Sentinel-1 chip with extracted slick polygon" /> : <p>No chip</p>}
        </Card>
        <Card title="SENTINEL-2 L2A TRUE COLOUR" subtitle={opt.scene ? `${opt.datetime?.slice(0, 16)} UTC · ${Math.abs(opt.dtHours).toFixed(1)} h from SAR · cloud ${(opt.cloudFrac * 100).toFixed(0)}%` : opt.note || 'No pass'}>
          {q.s2 ? <img src={q.s2.url} alt="Sentinel-2 true colour over the slick" /> : <p className="evidence-missing">{opt.note || 'No Sentinel-2 pass within ±36 h'}</p>}
        </Card>
      </div>
      <footer>
        <div><span>Verifier</span><strong>P(oil) {(selected.oilProbability * 100).toFixed(0)}% · {selected.status}</strong></div>
        <div><span>Likely source</span><strong>{sourceLabel(d.culprit)}</strong></div>
        <button className="btn btn-primary btn-sm" onClick={onClose}>Back to map</button>
      </footer>
    </section>
  </div>;
}
