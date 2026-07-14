/**
 * Domain types shared across components and hooks.
 * Keep this file free of React imports so it can also be used in plain TS
 * tests without a JSX transform.
 */

// ---------------------------------------------------------------------------
// Charger types — must match the values accepted by POST /recommendation
// ---------------------------------------------------------------------------

export const CHARGER_TYPES = ['SLOW', 'FAST', 'DC_FAST'] as const;
export type ChargerType = (typeof CHARGER_TYPES)[number];

export const CHARGER_TYPE_LABELS: Record<ChargerType, string> = {
  SLOW: 'Slow (AC Level 1)',
  FAST: 'Fast (AC Level 2)',
  DC_FAST: 'DC Fast Charger',
};

// ---------------------------------------------------------------------------
// Radius constraints
// ---------------------------------------------------------------------------

export const RADIUS_MIN = 250;
export const RADIUS_MAX = 10_000;
export const RADIUS_DEFAULT = 1_500;

// ---------------------------------------------------------------------------
// City — shape returned by GET /cities
// ---------------------------------------------------------------------------

export interface CityInfo {
  name: string;
  boundingBox: {
    type: 'Polygon';
    coordinates: Array<Array<[number, number]>>;
  };
}

// ---------------------------------------------------------------------------
// City centres — mirrors Go API cities_registry.go
// Used by MapView to fly to the selected city on first load.
// ---------------------------------------------------------------------------

export const CITY_CENTRES: Record<string, [number, number]> = {
  Bengaluru: [77.5946, 12.9716],
  Mumbai:    [72.8777, 19.0760],
  Hyderabad: [78.4867, 17.3850],
  Chennai:   [80.2707, 13.0827],
  Pune:      [73.8567, 18.5204],
};

// ---------------------------------------------------------------------------
// Base layer configuration — from design doc §Frontend Architecture
// ---------------------------------------------------------------------------

export const BASE_LAYERS = [
  { id: 'ev_chargers',    label: 'EV Chargers',     color: '#00CC44' },
  { id: 'fuel_stations',  label: 'Petrol Pumps',    color: '#FF6600' },
  { id: 'roads',          label: 'Major Roads',     color: '#3399FF' },
  { id: 'parking',        label: 'Parking Lots',    color: '#FFCC00' },
  { id: 'metro_stations', label: 'Metro Stations',  color: '#9900CC' },
  { id: 'malls',          label: 'Shopping Malls',  color: '#FF3366' },
  { id: 'tech_parks',     label: 'Tech Parks',      color: '#00CCCC' },
] as const;

export type BaseLayerId = (typeof BASE_LAYERS)[number]['id'];

/**
 * Returns the API path for a given layer id and city.
 * ev_chargers uses the existing GET /chargers?city= endpoint.
 * All other layers use GET /layers/{id}?city= (forthcoming).
 */
export function layerApiPath(layerId: BaseLayerId, city: string): string {
  if (layerId === 'ev_chargers') return `/chargers?city=${encodeURIComponent(city)}`;
  return `/layers/${layerId}?city=${encodeURIComponent(city)}`;
}

// ---------------------------------------------------------------------------
// Selection state — what the SelectionPanel manages
// ---------------------------------------------------------------------------

export interface SelectionState {
  city: string | null;
  chargerType: ChargerType | null;
  radius: number;
}
