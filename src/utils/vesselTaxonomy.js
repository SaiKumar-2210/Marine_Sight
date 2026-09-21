// MarineTraffic-style vessel categories, keyed off the AIS ship-type code.
export const VESSEL_TAXONOMY = {
  TANKER: { name: 'Tanker', color: [229, 57, 53], hex: '#e53935' },
  CARGO: { name: 'Cargo', color: [67, 160, 71], hex: '#43a047' },
  PASSENGER: { name: 'Passenger', color: [30, 136, 229], hex: '#1e88e5' },
  HIGH_SPEED: { name: 'High-speed craft', color: [253, 216, 53], hex: '#fdd835' },
  TUG_SPECIAL: { name: 'Tug / special craft', color: [38, 198, 218], hex: '#26c6da' },
  FISHING: { name: 'Fishing', color: [255, 138, 101], hex: '#ff8a65' },
  PLEASURE: { name: 'Pleasure craft', color: [216, 27, 96], hex: '#d81b60' },
  OTHER: { name: 'Unspecified', color: [158, 158, 158], hex: '#9e9e9e' }
};

export function vesselCategory(shipType) {
  const t = Number(shipType);
  if (!Number.isFinite(t) || t <= 0) return 'OTHER';
  if (t >= 80 && t <= 89) return 'TANKER';
  if (t >= 70 && t <= 79) return 'CARGO';
  if (t >= 60 && t <= 69) return 'PASSENGER';
  if (t >= 40 && t <= 49) return 'HIGH_SPEED';
  if (t === 30) return 'FISHING';
  if (t === 36 || t === 37) return 'PLEASURE';
  if (t === 31 || t === 32 || t === 33 || t === 34 || t === 35 || (t >= 50 && t <= 59)) return 'TUG_SPECIAL';
  return 'OTHER';
}

export const AIS_TYPE_LABEL = shipType => VESSEL_TAXONOMY[vesselCategory(shipType)].name;
