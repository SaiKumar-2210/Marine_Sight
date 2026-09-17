import { useMapSettings } from '../../context/MapContext';

const layers = [
  ['slicks', 'bi-droplet-half', 'Oil slick candidates'], ['vessels', 'bi-send-fill', 'AIS vessel tracks'],
  ['weather', 'bi-wind', 'Wind & currents'], ['assets', 'bi-building-gear', 'Offshore assets'], ['risk', 'bi-activity', 'Spill risk model']
];

export default function LayerPanel({ onReset }) {
  const { layers: state, setLayer, resetLayers } = useMapSettings();
  const reset = () => { resetLayers(); onReset?.(); };
  return <section className="map-layers shadow-sm" aria-label="Map layers"><div className="floating-heading"><div><span className="eyebrow">LIVE LAYERS</span><strong>Operational overlays</strong></div><button className="icon-clear" onClick={reset} title="Reset layers"><i className="bi bi-arrow-counterclockwise" /></button></div><div className="layer-list">{layers.map(([key, icon, label]) => <label className="form-check form-switch" key={key}><span><i className={`bi ${icon}`} />{label}</span><input className="form-check-input" type="checkbox" checked={state[key]} onChange={event => setLayer(key, event.target.checked)} /></label>)}</div></section>;
}
