import { useEffect, useMemo, useRef } from 'react';
import { useMap } from 'react-leaflet';
import { MapView } from '@deck.gl/core';
import { IconLayer, ScatterplotLayer } from '@deck.gl/layers';
import { LeafletLayer } from 'deck.gl-leaflet';
import { VESSEL_TAXONOMY } from '../../utils/vesselTaxonomy';

// White glyphs, tinted per vessel category (mask: true). Arrow = under way, dot = stationary.
const ARROW = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><path d="M32 4 L50 58 L32 47 L14 58 Z" fill="#fff"/></svg>')}`;
const DOT = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><circle cx="32" cy="32" r="18" fill="#fff"/></svg>')}`;
const ICONS = {
  arrow: { url: ARROW, id: 'arrow', width: 64, height: 64, mask: true },
  dot: { url: DOT, id: 'dot', width: 64, height: 64, mask: true }
};

const moving = v => (v.sog ?? 0) >= 0.5;
const bearing = v => (v.heading ?? v.cog ?? 0);

/**
 * WebGL AIS layer (deck.gl on Leaflet): renders tens of thousands of vessels at 60 fps, with
 * MarineTraffic-style heading arrows. Hover/click picking is driven from Leaflet mouse events.
 */
export default function VesselLayer({ vessels, visibleCategories, highlightMmsi, onHover, onSelect }) {
  const map = useMap();
  const deckRef = useRef(null);
  const handlers = useRef({ onHover, onSelect });
  handlers.current = { onHover, onSelect };

  const data = useMemo(
    () => vessels.filter(v => !visibleCategories || visibleCategories.has(v.category)),
    [vessels, visibleCategories]
  );

  useEffect(() => {
    const layer = new LeafletLayer({ views: [new MapView({ repeat: true })], layers: [] });
    map.addLayer(layer);
    deckRef.current = layer;
    const pick = e => layer.pickObject({ x: e.containerPoint.x, y: e.containerPoint.y, radius: 5, layerIds: ['ais-vessels'] });
    const move = e => {
      const info = pick(e);
      map.getContainer().style.cursor = info?.object ? 'pointer' : '';
      handlers.current.onHover?.(info?.object ? { vessel: info.object, x: e.containerPoint.x, y: e.containerPoint.y } : null);
    };
    const click = e => {
      const info = pick(e);
      if (info?.object) handlers.current.onSelect?.(info.object);
    };
    map.on('mousemove', move);
    map.on('click', click);
    return () => {
      map.off('mousemove', move);
      map.off('click', click);
      map.removeLayer(layer);
      deckRef.current = null;
    };
  }, [map]);

  useEffect(() => {
    const highlighted = highlightMmsi ? data.filter(v => v.mmsi === highlightMmsi) : [];
    deckRef.current?.setProps({
      layers: [
        new IconLayer({
          id: 'ais-vessels',
          data,
          pickable: true,
          getPosition: v => [v.lon, v.lat],
          getIcon: v => (moving(v) ? ICONS.arrow : ICONS.dot),
          getAngle: v => (moving(v) ? -bearing(v) : 0),
          getColor: v => [...VESSEL_TAXONOMY[v.category].color, 235],
          getSize: v => (moving(v) ? 18 : 9),
          sizeUnits: 'pixels',
          sizeMinPixels: 5,
          updateTriggers: { getColor: data.length }
        }),
        new ScatterplotLayer({
          id: 'ais-highlight',
          data: highlighted,
          getPosition: v => [v.lon, v.lat],
          getRadius: 14,
          radiusUnits: 'pixels',
          stroked: true,
          filled: false,
          getLineColor: [255, 214, 0, 255],
          lineWidthMinPixels: 2.5
        })
      ]
    });
  }, [data, highlightMmsi]);

  return null;
}
