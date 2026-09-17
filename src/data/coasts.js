// Multi-Coast Operational Geographic Regions Database

export const COASTAL_REGIONS = [
  {
    id: 'ARABIAN_SEA',
    name: 'Arabian Sea / Mumbai Coast',
    country: 'India',
    flag: '\u{1F1EE}\u{1F1F3}',
    center: [18.72, 72.23],
    zoom: 8,
    bbox: [69.0, 15.0, 75.0, 21.0],
    riskZone: 'Mumbai High Platform Field',
    description: 'High-density crude transport corridor & offshore platforms'
  },
  {
    id: 'GULF_OF_MEXICO',
    name: 'Gulf of Mexico / Texas Corridor',
    country: 'United States',
    flag: '\u{1F1FA}\u{1F1F8}',
    center: [27.80, -93.50],
    zoom: 8,
    bbox: [-97.0, 25.0, -90.0, 30.0],
    riskZone: 'Mississippi Canyon & Deepwater Horizon Zone',
    description: 'Offshore drilling rigs & major Gulf refinery channels'
  },
  {
    id: 'MALACCA_STRAIT',
    name: 'Strait of Malacca / Singapore Strait',
    country: 'Singapore / Malaysia',
    flag: '\u{1F1F8}\u{1F1EC}',
    center: [1.28, 103.85],
    zoom: 9,
    bbox: [100.0, 1.0, 105.0, 5.0],
    riskZone: 'Jurong Island Refinery Channel',
    description: "World's busiest petroleum shipping strait"
  },
  {
    id: 'PERSIAN_GULF',
    name: 'Persian Gulf / Strait of Hormuz',
    country: 'UAE / Oman / KSA',
    flag: '\u{1F1E6}\u{1F1EA}',
    center: [26.20, 56.30],
    zoom: 8,
    bbox: [52.0, 24.0, 58.0, 28.0],
    riskZone: 'Hormuz Crude Chokepoint',
    description: 'Strategic global oil export corridor'
  },
  {
    id: 'NORTH_SEA',
    name: 'North Sea / UK Offshore Field',
    country: 'United Kingdom / Norway',
    flag: '\u{1F1EC}\u{1F1E7}',
    center: [57.50, 1.50],
    zoom: 7,
    bbox: [-1.0, 55.0, 4.0, 60.0],
    riskZone: 'Brent & Forties Platform Cluster',
    description: 'Deepwater offshore crude production field'
  }
];

export function getCoastById(id) {
  return COASTAL_REGIONS.find(c => c.id === id) || COASTAL_REGIONS[0];
}
