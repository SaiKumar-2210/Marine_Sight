// Vessel Taxonomy & Visual Encoding Matrix based on PRD Specification

export const VESSEL_TYPES = {
  OIL_TANKER: 'OIL_TANKER',
  CHEMICAL: 'CHEMICAL',
  CARGO: 'CARGO',
  CONTAINER: 'CONTAINER',
  PASSENGER: 'PASSENGER',
  FISHING: 'FISHING',
  TUG_SERVICE: 'TUG_SERVICE',
  OFFSHORE_RIG: 'OFFSHORE_RIG',
  OTHER: 'OTHER'
};

export const VESSEL_TAXONOMY = {
  OIL_TANKER: {
    name: 'Oil Tanker',
    priority: 1,
    color: '#FF3B30', // Bright Red
    badgeClass: 'bg-danger',
    description: 'Petroleum & Crude Carrier'
  },
  CHEMICAL: {
    name: 'Chemical Carrier',
    priority: 1,
    color: '#FF9500', // Warning Orange
    badgeClass: 'bg-warning text-dark',
    description: 'Hazardous Chemical Transport'
  },
  CONTAINER: {
    name: 'Container Ship',
    priority: 2,
    color: '#007AFF', // Maritime Blue
    badgeClass: 'bg-primary',
    description: 'Commercial Freight & Intermodal'
  },
  CARGO: {
    name: 'Bulk Cargo',
    priority: 2,
    color: '#30B0C7', // Cyan Blue
    badgeClass: 'bg-info text-dark',
    description: 'Dry Bulk Carrier'
  },
  PASSENGER: {
    name: 'Passenger / Cruise',
    priority: 2,
    color: '#AF52DE', // Deep Purple
    badgeClass: 'bg-purple',
    description: 'Passenger Ferry or Liner'
  },
  FISHING: {
    name: 'Fishing Vessel',
    priority: 3,
    color: '#FFCC00', // Amber Yellow
    badgeClass: 'bg-warning text-dark',
    description: 'Trawler & Fishing Vessel'
  },
  TUG_SERVICE: {
    name: 'Tug / Service Craft',
    priority: 3,
    color: '#34C759', // Emerald Green
    badgeClass: 'bg-success',
    description: 'Harbor Utility & Supply Vessel'
  },
  OFFSHORE_RIG: {
    name: 'Offshore Infrastructure',
    priority: 1,
    color: '#FFD60A', // Diamond Amber
    badgeClass: 'bg-warning text-dark',
    description: 'Stationary Oil Rig / Platform'
  },
  OTHER: {
    name: 'Unclassified Craft',
    priority: 4,
    color: '#8E8E93', // Muted Gray
    badgeClass: 'bg-secondary',
    description: 'General Maritime Vessel'
  }
};

/**
 * Classifies a vessel based on its name or type string
 */
export function classifyVessel(rawVessel) {
  const typeStr = (rawVessel.vessel_type || rawVessel.type || '').toUpperCase();
  const nameStr = (rawVessel.name || '').toUpperCase();

  if (typeStr.includes('TANKER') || nameStr.includes('TANKER') || nameStr.includes('OIL') || nameStr.includes('PETRO')) {
    return VESSEL_TYPES.OIL_TANKER;
  }
  if (typeStr.includes('CHEM') || nameStr.includes('CHEM')) {
    return VESSEL_TYPES.CHEMICAL;
  }
  if (nameStr.includes('RIG') || nameStr.includes('FPSO') || nameStr.includes('PLATFORM') || typeStr.includes('RIG')) {
    return VESSEL_TYPES.OFFSHORE_RIG;
  }
  if (typeStr.includes('CONTAINER') || nameStr.includes('CONTAINER') || nameStr.includes('EXPRESS')) {
    return VESSEL_TYPES.CONTAINER;
  }
  if (typeStr.includes('CARGO') || nameStr.includes('BULK') || nameStr.includes('MERIDIAN') || nameStr.includes('SAFFRON')) {
    return VESSEL_TYPES.CARGO;
  }
  if (typeStr.includes('PASSENGER') || nameStr.includes('FERRY') || nameStr.includes('CRUISE') || nameStr.includes('QUEEN')) {
    return VESSEL_TYPES.PASSENGER;
  }
  if (typeStr.includes('FISH') || nameStr.includes('TRAWLER') || nameStr.includes('KAVERI')) {
    return VESSEL_TYPES.FISHING;
  }
  if (typeStr.includes('TUG') || nameStr.includes('SERVICE') || nameStr.includes('HARBOR')) {
    return VESSEL_TYPES.TUG_SERVICE;
  }
  return VESSEL_TYPES.OTHER;
}

/**
 * Determines maximum visible priority level based on map zoom scale context
 * Zoom 1-5  => Priority 1 (Oil Tankers & Rigs Only)
 * Zoom 6-9  => Priority 1 & 2 (+ Commercial & Passenger)
 * Zoom 10-12 => Priority 1, 2 & 3 (+ Fishing & Tugs)
 * Zoom 13+  => Priority 4 (All Vessels)
 */
export function getMaxPriorityForZoom(zoom) {
  if (zoom <= 5) return 1;
  if (zoom <= 9) return 2;
  if (zoom <= 12) return 3;
  return 4;
}
