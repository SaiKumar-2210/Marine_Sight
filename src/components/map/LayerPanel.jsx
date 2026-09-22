import { useMemo } from 'react';
import { useMapSettings } from '../../context/MapContext';
import { useSpills } from '../../context/SpillContext';
import { VESSEL_TAXONOMY } from '../../utils/vesselTaxonomy';

const LAYERS = [
  ['slicks', 'bi-droplet-half', 'Oil slick polygons'],
  ['sarEvidence', 'bi-radar', 'Sentinel-1 evidence chip'],
  ['attribution', 'bi-bullseye', 'Source attribution'],
  ['vessels', 'bi-send-fill', 'Live AIS vessels']
];

export default function LayerPanel({ vessels = [], onReset }) {
  const { mode, layers, setLayer, resetLayers, vesselFilter, toggleCategory } = useMapSettings();
  const { showRejected, setShowRejected } = useSpills();
  const counts = useMemo(() => vessels.reduce((acc, v) => { acc[v.category] = (acc[v.category] || 0) + 1; return acc; }, {}), [vessels]);
  return <section className="map-layers shadow-sm" aria-label="Map layers">
    <div className="floating-heading">
      <div><span className="eyebrow">LAYERS</span><strong>Operational overlays</strong></div>
      <button className="icon-clear" onClick={() => { resetLayers(); setShowRejected(false); onReset?.(); }} title="Reset layers"><i className="bi bi-arrow-counterclockwise" /></button>
    </div>
    <div className="layer-list">
      {LAYERS.map(([key, icon, label]) => {
        // The SAR chip is a raster: in the Sentinel-2 view it would sit on top of the optical
        // imagery, so it is held back until the operator leaves that view.
        const suppressed = key === 'sarEvidence' && mode === 'sentinel2';
        return <label className={`form-check form-switch ${suppressed ? 'suppressed' : ''}`} key={key}
          title={suppressed ? 'Hidden while the Sentinel-2 basemap is shown' : undefined}>
          <span><i className={`bi ${icon}`} />{label}{suppressed && <em className="layer-note">hidden over Sentinel-2</em>}</span>
          <input className="form-check-input" type="checkbox" checked={layers[key] && !suppressed} disabled={suppressed}
            onChange={e => setLayer(key, e.target.checked)} data-testid={`layer-${key}`} />
        </label>;
      })}
      <label className="form-check form-switch">
        <span><i className="bi bi-slash-circle" />Rejected look-alikes</span>
        <input className="form-check-input" type="checkbox" checked={showRejected} onChange={e => setShowRejected(e.target.checked)} data-testid="toggle-rejected" />
      </label>
    </div>
    {layers.vessels && <div className="vessel-legend">
      {Object.entries(VESSEL_TAXONOMY).map(([key, t]) => (
        <button key={key} className={`legend-chip ${vesselFilter.has(key) ? '' : 'off'}`} onClick={() => toggleCategory(key)} title={`Toggle ${t.name}`}>
          <i style={{ background: t.hex }} />{t.name}<b>{counts[key] || 0}</b>
        </button>
      ))}
    </div>}
  </section>;
}
