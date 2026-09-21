import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { VESSEL_TAXONOMY } from '../utils/vesselTaxonomy';

const MapContext = createContext(null);
const initialLayers = { slicks: true, vessels: true, sarEvidence: true, attribution: true };
const allCategories = () => new Set(Object.keys(VESSEL_TAXONOMY));

export function MapProvider({ children }) {
  const [mode, setMode] = useState('operations');
  const [layers, setLayers] = useState(initialLayers);
  const [vesselFilter, setVesselFilter] = useState(allCategories);
  const setLayer = useCallback((layer, active) => setLayers(cur => ({ ...cur, [layer]: active })), []);
  const toggleCategory = useCallback(cat => setVesselFilter(cur => {
    const next = new Set(cur);
    if (next.has(cat)) next.delete(cat); else next.add(cat);
    return next;
  }), []);
  const resetLayers = useCallback(() => { setLayers(initialLayers); setVesselFilter(allCategories()); }, []);
  const value = useMemo(() => ({ mode, setMode, layers, setLayer, resetLayers, vesselFilter, toggleCategory }),
    [mode, layers, setLayer, resetLayers, vesselFilter, toggleCategory]);
  return <MapContext.Provider value={value}>{children}</MapContext.Provider>;
}

export const useMapSettings = () => useContext(MapContext);
