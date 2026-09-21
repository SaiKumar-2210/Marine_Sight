import { useCallback, useEffect, useMemo, useState } from 'react';
import L from 'leaflet';
import {
  CircleMarker, GeoJSON, ImageOverlay, LayerGroup, MapContainer as LeafletMap, Marker, Polyline, TileLayer, Tooltip, useMap
} from 'react-leaflet';
import { useSpills } from '../../context/SpillContext';
import { useMapSettings } from '../../context/MapContext';
import { getVesselTrack } from '../../services/marineApi';
import { AIS_TYPE_LABEL, VESSEL_TAXONOMY } from '../../utils/vesselTaxonomy';
import VesselLayer from './VesselLayer';

const STATUS_STYLE = {
  confirmed: { color: '#ff3b1f', fillColor: '#ff3b1f', fillOpacity: 0.55, weight: 1.5 },
  review: { color: '#ffb703', fillColor: '#ffb703', fillOpacity: 0.45, weight: 1.2 },
  rejected: { color: '#9e9e9e', fillColor: '#9e9e9e', fillOpacity: 0.2, weight: 1, dashArray: '4 3' }
};

const headIcon = L.divIcon({
  className: '',
  html: '<div class="slick-head-marker" title="Slick head (fresh oil / source end)"><i class="bi bi-crosshair"></i></div>',
  iconSize: [24, 24], iconAnchor: [12, 12]
});

function FitToSpill({ spill }) {
  const map = useMap();
  useEffect(() => {
    if (!spill?.geometry) return;
    const bounds = L.geoJSON(spill.geometry).getBounds();
    if (bounds.isValid()) map.flyToBounds(bounds.pad(0.35), { duration: 0.6, maxZoom: 12 });
  }, [map, spill?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

// Lets automated QA read the Leaflet instance (e.g. to project spill coordinates to screen space).
function ExposeMap() {
  const map = useMap();
  useEffect(() => { map.getContainer().__msMap = map; }, [map]);
  return null;
}

function FlyTo({ target }) {
  const map = useMap();
  useEffect(() => { if (target) map.flyTo(target.center, target.zoom || 8, { duration: 0.6 }); }, [map, target]);
  return null;
}

function fmtAge(ts) {
  const min = Math.round((Date.now() / 1000 - ts) / 60);
  return min < 60 ? `${min} min ago` : `${Math.round(min / 60)} h ago`;
}

function LoadingOverlay({ date, load, onRetry }) {
  if (load.state === 'loading') {
    const p = load.job?.progress || {};
    return <div className="scan-overlay" role="status" data-testid="scan-loading">
      <div className="spinner-border" />
      <h4>Loading…</h4>
      <p>No stored results for <strong>{date}</strong> — running the ML pipeline now.</p>
      <div className="scan-progress"><div style={{ width: `${Math.max(3, p.pct || 0)}%` }} /></div>
      <small>{p.message || 'Queued'}{load.job?.status === 'queued' ? ' (waiting for another scan to finish)' : ''}</small>
    </div>;
  }
  if (load.state === 'failed') {
    return <div className="scan-overlay error" data-testid="scan-failed">
      <i className="bi bi-exclamation-octagon" />
      <h4>Scan failed for {date}</h4>
      <p>{load.error}</p>
      <button className="btn btn-sm btn-primary" onClick={onRetry}>Retry scan</button>
    </div>;
  }
  return null;
}

export default function MapCanvas({ vessels, flyTarget, focusVessel, onVesselSelected }) {
  const { date, spills, load, selectedId, setSelectedId, selected, detail, forceRescan } = useSpills();
  const { mode, layers, vesselFilter } = useMapSettings();
  const [hover, setHover] = useState(null);
  const [vessel, setVessel] = useState(null);
  const [vesselTrack, setVesselTrack] = useState([]);

  const selectVessel = useCallback(v => {
    setVessel(v);
    setVesselTrack([]);
    onVesselSelected?.(v);
    getVesselTrack(v.mmsi, 24).then(r => setVesselTrack(r.track || [])).catch(() => setVesselTrack([]));
  }, [onVesselSelected]);

  useEffect(() => {
    if (!focusVessel) return;
    const v = vessels.find(x => x.mmsi === focusVessel);
    if (v) selectVessel(v);
  }, [focusVessel]); // eslint-disable-line react-hooks/exhaustive-deps

  const attribution = detail?.id === selectedId ? detail.detail?.attribution : null;
  const quicklook = detail?.id === selectedId ? detail.detail?.quicklooks?.s1 : null;
  const aisCandidates = useMemo(() => (attribution?.candidates || []).filter(c => c.kind === 'ais_vessel').slice(0, 3), [attribution]);
  const sarTargets = useMemo(() => (attribution?.candidates || []).filter(c => c.kind === 'sar_vessel'), [attribution]);
  const staticStructures = useMemo(() => (attribution?.candidates || []).filter(c => c.kind === 'static_structure'), [attribution]);

  const baseUrl = mode === 'sentinel2'
    ? 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
    : 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}';
  const collection = mode === 'sentinel1' ? 'sentinel-1-grd' : 'sentinel-2-l2a';

  return (
    <div className="map-shell">
      <LoadingOverlay date={date} load={load} onRetry={forceRescan} />
      <LeafletMap id="map" className="map-canvas" center={[17.5, 57.0]} zoom={7} minZoom={2} worldCopyJump zoomControl>
        <ExposeMap />
        <TileLayer url={baseUrl} attribution="Tiles © Esri" />
        {(mode === 'sentinel1' || mode === 'sentinel2') && (
          <TileLayer key={`${collection}-${date}`} url={`/api/sentinel/tiles/${collection}/{z}/{x}/{y}.png?date=${date}`}
            attribution="© Copernicus Data Space Ecosystem" opacity={0.9} />
        )}
        <FlyTo target={flyTarget} />
        <FitToSpill spill={selected} />

        {layers.sarEvidence && quicklook && (
          <ImageOverlay key={quicklook.url} url={quicklook.url} opacity={0.85}
            bounds={[[quicklook.bbox[1], quicklook.bbox[0]], [quicklook.bbox[3], quicklook.bbox[2]]]} />
        )}

        {layers.slicks && <LayerGroup>
          {spills.map(s => (
            <GeoJSON key={`${s.id}-${s.id === selectedId}`} data={s.geometry}
              style={{ ...STATUS_STYLE[s.status], className: `slick-polygon slick-${s.status}`, ...(s.id === selectedId ? { weight: 2.5, color: '#fff' } : {}) }}
              eventHandlers={{ click: () => setSelectedId(s.id) }}>
              <Tooltip sticky>{s.id} · {s.status} · P(oil) {(s.oilProbability * 100).toFixed(0)}% · {s.areaKm2.toFixed(1)} km²</Tooltip>
            </GeoJSON>
          ))}
          {spills.map(s => (
            <CircleMarker key={`c-${s.id}`} center={[s.centroid.lat, s.centroid.lon]} radius={s.id === selectedId ? 9 : 6}
              pathOptions={{ color: STATUS_STYLE[s.status].color, weight: 2, fillOpacity: 0.15 }}
              eventHandlers={{ click: () => setSelectedId(s.id) }} className="spill-centroid" />
          ))}
        </LayerGroup>}

        {layers.attribution && attribution && <LayerGroup>
          {aisCandidates.map((c, i) => (
            <LayerGroup key={`cand-${c.mmsi}`}>
              <Polyline positions={c.track.map(p => [p[1], p[0]])}
                pathOptions={{ color: i === 0 ? '#ffd600' : '#c0b89a', weight: i === 0 ? 3 : 1.5, opacity: 0.9 }}>
                <Tooltip sticky>{c.name || c.mmsi} AIS track · score {c.score}</Tooltip>
              </Polyline>
              <Polyline positions={c.driftCorrectedTrack.map(p => [p[1], p[0]])}
                pathOptions={{ color: i === 0 ? '#ffd600' : '#c0b89a', weight: 1.5, dashArray: '6 5', opacity: 0.8 }}>
                <Tooltip sticky>Where oil released along this track would be at the SAR pass (drift-corrected)</Tooltip>
              </Polyline>
            </LayerGroup>
          ))}
          {sarTargets.map(t => (
            <CircleMarker key={`sar-${t.lon}-${t.lat}`} center={[t.lat, t.lon]} radius={7}
              pathOptions={{ color: t.darkVessel ? '#ff2bd6' : '#3ce0ff', weight: 2, fillOpacity: 0 }}>
              <Tooltip>{t.darkVessel ? 'AIS-dark vessel' : `SAR vessel = ${t.aisMatch?.name || t.aisMatch?.mmsi}`} · {t.peakDb} dB · {t.distanceToHeadKm} km from slick head</Tooltip>
            </CircleMarker>
          ))}
          {staticStructures.map(t => (
            <CircleMarker key={`static-${t.lon}-${t.lat}`} center={[t.lat, t.lon]} radius={6}
              pathOptions={{ color: '#bdbdbd', weight: 2, dashArray: '2 2', fillOpacity: 0 }}>
              <Tooltip>Static structure (island, rock or platform) · {t.reason}</Tooltip>
            </CircleMarker>
          ))}
          <Marker position={[attribution.head.lat, attribution.head.lon]} icon={headIcon}>
            <Tooltip>Slick head — estimated source end (confidence {(attribution.head.confidence * 100).toFixed(0)}%)</Tooltip>
          </Marker>
        </LayerGroup>}

        {layers.vessels && (
          <VesselLayer vessels={vessels} visibleCategories={vesselFilter} highlightMmsi={vessel?.mmsi}
            onHover={setHover} onSelect={selectVessel} />
        )}
        {vessel && vesselTrack.length > 1 && (
          <Polyline positions={vesselTrack.map(p => [p.lat, p.lon])} pathOptions={{ color: '#ffd600', weight: 2, opacity: 0.8 }} />
        )}
      </LeafletMap>

      {hover && (
        <div className="vessel-hover" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <strong style={{ color: VESSEL_TAXONOMY[hover.vessel.category].hex }}>{hover.vessel.name || `MMSI ${hover.vessel.mmsi}`}</strong>
          <span>{AIS_TYPE_LABEL(hover.vessel.shipType)} · {hover.vessel.sog ?? '–'} kn · {hover.vessel.cog ?? '–'}°</span>
        </div>
      )}
      {vessel && (
        <div className="vessel-card" data-testid="vessel-card">
          <button className="icon-clear" onClick={() => { setVessel(null); setVesselTrack([]); }} aria-label="Close"><i className="bi bi-x-lg" /></button>
          <span className="eyebrow">AIS VESSEL</span>
          <strong style={{ color: VESSEL_TAXONOMY[vessel.category].hex }}>{vessel.name || 'Unknown name'}</strong>
          <dl>
            <dt>MMSI</dt><dd>{vessel.mmsi}</dd>
            <dt>Type</dt><dd>{AIS_TYPE_LABEL(vessel.shipType)}{vessel.shipType ? ` (${vessel.shipType})` : ''}</dd>
            <dt>Speed / course</dt><dd>{vessel.sog ?? '–'} kn / {vessel.cog ?? '–'}°</dd>
            <dt>Destination</dt><dd>{vessel.destination || '–'}</dd>
            <dt>Position</dt><dd>{vessel.lat.toFixed(4)}, {vessel.lon.toFixed(4)}</dd>
            <dt>Last report</dt><dd>{fmtAge(vessel.ts)}</dd>
            <dt>Stored track (24 h)</dt><dd>{vesselTrack.length} positions</dd>
          </dl>
        </div>
      )}
    </div>
  );
}
