import React, { useEffect, useMemo, useState, useCallback, Fragment } from 'react';
import L from 'leaflet';
import { ImageOverlay, LayerGroup, MapContainer as LeafletMap, Marker, Polygon, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet';
import { useIncidents } from '../../context/IncidentContext';
import { useMapSettings } from '../../context/MapContext';
import { useAisStream } from '../../hooks/useAisStream';
import { getCachedSatelliteImage, getWeather } from '../../services/marineApi';
import { getMaxPriorityForZoom, VESSEL_TAXONOMY } from '../../utils/vesselTaxonomy';

const markerIcon = (html, className, size = [24, 24]) => L.divIcon({ className: '', html: `<div class="${className}">${html}</div>`, iconSize: size, iconAnchor: [size[0] / 2, size[1] / 2] });
const incidentIcon = (selected) => markerIcon('<i class="bi bi-record-circle"></i>', `incident-marker ${selected ? 'selected' : ''}`, selected ? [26, 26] : [22, 22]);

function createTaxonomyVesselIcon(vessel) {
  const taxonomy = VESSEL_TAXONOMY[vessel.type] || VESSEL_TAXONOMY.OTHER;
  const color = taxonomy.color;
  const course = vessel.course || 0;
  if (vessel.type === 'OFFSHORE_RIG') {
    const html = `<svg viewBox="0 0 24 24" style="width:22px;height:22px;display:block;filter:drop-shadow(0 0 4px ${color});"><polygon points="12,2 22,12 12,22 2,12" fill="${color}" stroke="#FFFFFF" stroke-width="1.5"/></svg>`;
    return markerIcon(html, 'ais-target rig-marker', [24, 24]);
  }
  const html = `<svg viewBox="0 0 24 24" style="transform:rotate(${course}deg);width:20px;height:20px;display:block;filter:drop-shadow(0 0 3px ${color});"><path d="M12 2 L18 20 L12 17 L6 20 Z" fill="${color}" stroke="#FFFFFF" stroke-width="1.2" stroke-linejoin="round"/></svg>`;
  return markerIcon(html, 'ais-target', [22, 22]);
}

function MapFocus({ coordinate, zoom = 8 }) {
  const map = useMap();
  useEffect(() => { if (coordinate) map.flyTo(coordinate, zoom, { duration: 0.55 }); }, [map, coordinate, zoom]);
  return null;
}

function ZoomEventListener({ onZoomChange }) {
  const map = useMap();
  useEffect(() => {
    const handleZoom = () => onZoomChange(map.getZoom());
    map.on('zoomend', handleZoom);
    return () => map.off('zoomend', handleZoom);
  }, [map, onZoomChange]);
  return null;
}

/**
 * SatelliteScene — fetches cached Copernicus imagery for the active coast
 */
function SatelliteScene({ mode, coastId, coastBbox, onStatus }) {
  const [scene, setScene] = useState(null);
  const bounds = useMemo(() => {
    if (!coastBbox) return [[15, 69], [21, 75]];
    return [[coastBbox[1], coastBbox[0]], [coastBbox[3], coastBbox[2]]];
  }, [coastBbox]);

  useEffect(() => {
    if (mode === 'operations') { setScene(null); return undefined; }
    let active = true;
    const collection = mode === 'sentinel1' ? 'sentinel-1-grd' : 'sentinel-2-l2a';
    getCachedSatelliteImage(collection, coastId || 'ARABIAN_SEA')
      .then(url => { if (active) { setScene(current => { if (current) URL.revokeObjectURL(current); return url; }); onStatus?.(`Live ${mode === 'sentinel1' ? 'Sentinel-1 SAR' : 'Sentinel-2 optical'} scene loaded`); } })
      .catch(() => { if (active) { setScene(null); onStatus?.('Copernicus scene pending — showing basemap preview'); } });
    return () => { active = false; };
  }, [mode, coastId, onStatus]);

  useEffect(() => () => { if (scene) URL.revokeObjectURL(scene); }, [scene]);
  return scene ? <ImageOverlay url={scene} bounds={bounds} opacity={0.88} /> : null;
}

function oilShape(incident) {
  const radius = incident.id === 'INC-040' ? 0.22 : incident.id === 'INC-042' ? 0.16 : 0.075;
  return Array.from({ length: 22 }, (_, index) => {
    const angle = (index / 22) * Math.PI * 2;
    const organic = 1 + Math.sin(index * 1.7 + Number(incident.id.slice(-2))) * 0.22;
    return [incident.lat + Math.cos(angle) * radius * organic, incident.lng + Math.sin(angle) * radius * organic * 1.7];
  });
}

/**
 * Wind & Current direction arrow icon
 */
function createWindArrow(direction, color, label) {
  const html = `<svg viewBox="0 0 32 32" style="transform:rotate(${direction}deg);width:28px;height:28px;display:block;filter:drop-shadow(0 0 2px rgba(0,0,0,0.6));">
    <path d="M16 4 L22 24 L16 20 L10 24 Z" fill="${color}" stroke="#fff" stroke-width="0.8" opacity="0.8"/>
  </svg>`;
  return L.divIcon({ className: '', html: `<div title="${label}">${html}</div>`, iconSize: [28, 28], iconAnchor: [14, 14] });
}

/**
 * WeatherOverlay — dynamic wind & current direction arrows fetched from weather API
 */
function WeatherOverlay({ coastCenter }) {
  const [weather, setWeather] = useState(null);
  const [lat, lng] = coastCenter || [18.72, 72.23];

  useEffect(() => {
    getWeather(lat, lng).then(setWeather).catch(() => setWeather(null));
  }, [lat, lng]);

  if (!weather) return null;

  const windDir = weather.wind?.direction || 'NW';
  const windSpeed = weather.wind?.speedKnots || 12;
  const currentDir = weather.current?.direction || 'SE';
  const currentSpeed = weather.current?.speedKnots || 0.6;

  const dirToDeg = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 };
  const windDeg = dirToDeg[windDir] || 315;
  const currentDeg = dirToDeg[currentDir] || 135;

  return <>
    {/* Single Wind arrow (blue) */}
    <Marker position={[lat + 0.3, lng + 0.3]} icon={createWindArrow(windDeg, '#5b9fff', `Wind: ${windDir} ${windSpeed} kn`)} interactive={false} />
    
    {/* Single Current arrow (teal) */}
    <Marker position={[lat + 0.25, lng + 0.35]} icon={createWindArrow(currentDeg, '#4ecdc4', `Current: ${currentDir} ${currentSpeed} kn`)} interactive={false} />
    
    {/* Legend marker at bottom-left of area */}
    <Marker position={[lat - 1.2, lng - 1.0]} icon={L.divIcon({
      className: '', iconSize: [180, 50], iconAnchor: [0, 0],
      html: `<div style="background:rgba(10,10,20,0.85);border:1px solid rgba(255,255,255,0.2);border-radius:6px;padding:5px 10px;font-size:11px;color:#fff;white-space:nowrap;pointer-events:none;">
        <span style="color:#5b9fff;">▲</span> Wind: <b>${windDir} ${windSpeed} kn</b> &nbsp;
        <span style="color:#4ecdc4;">▲</span> Current: <b>${currentDir} ${currentSpeed} kn</b>
      </div>`
    })} interactive={false} />
  </>;
}

export default function MapCanvas({ focusCoast, focusVessel, focusToken, onFocusComplete, onStatus }) {
  const { incidents, selectedIncident, selectedId, setSelectedId, selectedDate, isScanning } = useIncidents();
  const { mode, layers } = useMapSettings();
  const [currentZoom, setCurrentZoom] = useState(8);
  const { vessels, snapshotMeta } = useAisStream(currentZoom, null, selectedDate);
  const [focusCoordinate, setFocusCoordinate] = useState([selectedIncident.lat || 18.72, selectedIncident.lng || 72.23]);
  const [focusZoom, setFocusZoom] = useState(8);

  useEffect(() => setFocusCoordinate([selectedIncident.lat || 18.72, selectedIncident.lng || 72.23]), [selectedIncident]);
  useEffect(() => {
    if (focusCoast) {
      setFocusCoordinate(focusCoast.center);
      setFocusZoom(focusCoast.zoom);
    }
  }, [focusCoast]);
  useEffect(() => { if (focusToken) setFocusCoordinate([selectedIncident.lat || 18.72, selectedIncident.lng || 72.23]); }, [focusToken, selectedIncident]);
  useEffect(() => {
    if (!focusVessel) return;
    const vessel = vessels.find(item => item.name === focusVessel);
    if (vessel) setFocusCoordinate([vessel.latitude, vessel.longitude]);
    onFocusComplete?.();
  }, [focusVessel, vessels, onFocusComplete]);

  const maxPriorityAllowed = useMemo(() => getMaxPriorityForZoom(currentZoom), [currentZoom]);
  const lodFilteredVessels = useMemo(() => vessels.filter(v => (v.priority || 3) <= maxPriorityAllowed), [vessels, maxPriorityAllowed]);
  const slickIncidents = useMemo(() => incidents.filter(item => item.severity !== 'LOW' && !isNaN(Number(item.lat)) && !isNaN(Number(item.lng))), [incidents]);
  const isSentinelMode = mode === 'sentinel1' || mode === 'sentinel2';

  const satelliteCollection = mode === 'sentinel1' ? 'sentinel-1-grd' : 'sentinel-2-l2a';
  const baseMapUrl = mode === 'sentinel1' 
    ? 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}'
    : 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  const baseAttribution = mode === 'sentinel1' ? 'Tiles © Esri, HERE, Garmin, FAO, NOAA, USGS' : 'Tiles © Esri';
  const coastCenter = focusCoast?.center || [selectedIncident.lat || 18.72, selectedIncident.lng || 72.23];

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {isScanning && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.55)', zIndex: 9999,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          color: '#00f2fe', backdropFilter: 'blur(4px)'
        }}>
          <div className="spinner-border" style={{ width: '3rem', height: '3rem', marginBottom: '1rem' }} role="status" />
          <h4 style={{ fontWeight: '600' }}>Running Semantic Segmentation...</h4>
          <span style={{ opacity: 0.8 }}>Analyzing Sentinel-1 SAR tiles for {selectedDate}</span>
        </div>
      )}
      <LeafletMap id="map" className="map-canvas" center={[selectedIncident.lat || 18.72, selectedIncident.lng || 72.23]} zoom={8} minZoom={3} maxBounds={[[-85, -180], [85, 180]]} maxBoundsViscosity={1} zoomControl attributionControl preferCanvas>
    <TileLayer url={baseMapUrl} attribution={baseAttribution} />
    
    {isSentinelMode && (
      <TileLayer 
        url={`/api/sentinel/tiles/${satelliteCollection}/{z}/{x}/{y}.png`} 
        attribution="© Copernicus Data Space Ecosystem"
        opacity={0.85}
      />
    )}

    <MapFocus coordinate={focusCoordinate} zoom={focusVessel ? 10 : focusZoom} />
    <ZoomEventListener onZoomChange={setCurrentZoom} />
    {layers.weather && <WeatherOverlay coastCenter={coastCenter} />}

    {layers.slicks && (
      <LayerGroup>
        {slickIncidents.map(item => {
          if (!item.polygon) return null;
          return (
            <Fragment key={`shapes-${item.id}`}>
              {/* Hazard Zone */}
              <Polygon 
                positions={item.polygon} 
                pathOptions={{ color: '#ff2a00', fillColor: 'transparent', weight: 8, opacity: 0.5, dashArray: '10 10' }} 
              />
              {/* Oil Spill footprint extracted from ML */}
              <Polygon 
                positions={item.polygon} 
                pathOptions={{ color: item.id === selectedId ? '#ff2a00' : '#ffb703', fillColor: item.id === selectedId ? '#ff2a00' : '#ffb703', fillOpacity: 0.65, weight: item.id === selectedId ? 3 : 1 }} 
                eventHandlers={{ click: () => setSelectedId(item.id) }} 
              />
            </Fragment>
          );
        })}
        {slickIncidents.map(item => (
          <Marker 
            key={`incident-${item.id}`} 
            position={[item.lat, item.lng]} 
            icon={incidentIcon(item.id === selectedId)} 
            eventHandlers={{ click: () => setSelectedId(item.id) }}
          >
            <Popup>
              <div className="map-popup">
                <strong>{item.id} · {item.title}</strong>
                <span>{item.confidence}% confidence · {item.area || 'Review pending'}</span>
              </div>
            </Popup>
          </Marker>
        ))}
      </LayerGroup>
    )}
    
    {layers.vessels && <LayerGroup>{lodFilteredVessels.map(vessel => {
      const taxonomy = VESSEL_TAXONOMY[vessel.type] || VESSEL_TAXONOMY.OTHER;
      return (
        <Marker key={vessel.mmsi || vessel.name} position={[vessel.latitude, vessel.longitude]} icon={createTaxonomyVesselIcon(vessel)}>
          <Tooltip direction="top" offset={[0, -10]}>
            <div style={{ textAlign: 'left', lineHeight: '1.3' }}>
              <div style={{ fontWeight: 'bold', color: taxonomy.color }}>{vessel.name}</div>
              <div style={{ fontSize: '11px', opacity: 0.85 }}>{taxonomy.name} · Priority {vessel.priority || 3}</div>
              <div style={{ fontSize: '11px' }}>Speed: {Number(vessel.speed || vessel.sog || 0).toFixed(1)} kn · Course: {Number(vessel.course || vessel.cog || 0).toFixed(0)}°</div>
            </div>
          </Tooltip>
        </Marker>
      );
    })}</LayerGroup>}

    {layers.weather && <LayerGroup><WeatherOverlay coastCenter={coastCenter} /></LayerGroup>}
    {layers.assets && <LayerGroup>{[[17.8, 71.59, 'FPSO-07'], [19.04, 72.83, 'Mumbai offshore']].map(([lat, lng, name]) => <Marker key={name} position={[lat, lng]} icon={markerIcon('<i class="bi bi-building-gear"></i>', 'asset-icon')}><Tooltip>{name}</Tooltip></Marker>)}</LayerGroup>}
  </LeafletMap>
  </div>
  );
}
