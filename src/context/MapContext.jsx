import { createContext, useContext, useMemo, useState } from 'react';

const MapContext = createContext(null);
const initialLayers = { slicks: true, vessels: true, weather: true, assets: false, risk: true };

export function MapProvider({ children }) {
  const [mode, setMode] = useState('operations');
  const [layers, setLayers] = useState(initialLayers);
  const setLayer = (layer, active) => setLayers(current => ({ ...current, [layer]: active }));
  const resetLayers = () => setLayers(initialLayers);
  const value = useMemo(() => ({ mode, setMode, layers, setLayer, resetLayers }), [mode, layers]);
  return <MapContext.Provider value={value}>{children}</MapContext.Provider>;
}

export const useMapSettings = () => useContext(MapContext);
