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

// ---------------------------------------------------------------------------
// API error types — typed discriminated union for POST /recommendation
// failures so components can render distinct, actionable messages.
// ---------------------------------------------------------------------------

/**
 * 400 Bad Request — one or more request fields failed server-side validation.
 * The `field` property names the offending field (city, chargerType, radius).
 */
export interface ApiError400 {
  kind: '400';
  field: string;
  message: string;
}

/**
 * 422 Unprocessable Entity — city is known but one or more geo datasets are
 * missing or incomplete. `missing_datasets` lists the affected layer names.
 */
export interface ApiError422 {
  kind: '422';
  city: string;
  missing_datasets: string[];
  message: string;
}

/**
 * 503 Service Unavailable — the geo-service dependency is unreachable.
 * `retryAfterSeconds` is parsed from the Retry-After response header (or
 * the body if the header is absent); null when not provided.
 */
export interface ApiError503 {
  kind: '503';
  retryAfterSeconds: number | null;
  message: string;
}

/**
 * Catch-all for any other non-2xx status (401, 500, network failure, etc.).
 */
export interface ApiErrorGeneric {
  kind: 'generic';
  status: number | null;
  message: string;
}

export type QueryError = ApiError400 | ApiError422 | ApiError503 | ApiErrorGeneric;

// ---------------------------------------------------------------------------
// Parse an unknown thrown value into a typed QueryError.
// ---------------------------------------------------------------------------

/** Shape we expect from the server's JSON error body. */
interface RawErrorBody {
  field?: string;
  message?: string;
  missing_datasets?: string[];
  city?: string;
}

function isRawBody(v: unknown): v is RawErrorBody {
  return typeof v === 'object' && v !== null;
}

/**
 * Convert whatever the apiClient throws into a typed QueryError so the UI
 * can branch on `kind` without any string-matching heuristics.
 */
export function parseQueryError(err: unknown): QueryError {
  // Network / non-HTTP errors (TypeError from fetch, etc.)
  if (!(err instanceof Error)) {
    return { kind: 'generic', status: null, message: String(err) };
  }

  const httpErr = err as Error & {
    status?: number;
    body?: unknown;
    retryAfter?: string | null;
  };

  const status = httpErr.status ?? null;
  const raw = isRawBody(httpErr.body) ? httpErr.body : {};
  const serverMessage = typeof raw.message === 'string' ? raw.message : err.message;

  if (status === 400) {
    return {
      kind: '400',
      field: typeof raw.field === 'string' ? raw.field : 'unknown',
      message: serverMessage,
    };
  }

  if (status === 422) {
    return {
      kind: '422',
      city: typeof raw.city === 'string' ? raw.city : '',
      missing_datasets: Array.isArray(raw.missing_datasets)
        ? (raw.missing_datasets as string[])
        : [],
      message: serverMessage,
    };
  }

  if (status === 503) {
    // Prefer the Retry-After header value; fall back to parsing the body.
    const headerVal = httpErr.retryAfter ?? null;
    let retryAfterSeconds: number | null = null;
    if (headerVal !== null) {
      const parsed = parseInt(headerVal, 10);
      retryAfterSeconds = Number.isFinite(parsed) ? parsed : null;
    }
    return {
      kind: '503',
      retryAfterSeconds,
      message: serverMessage,
    };
  }

  return {
    kind: 'generic',
    status,
    message: serverMessage,
  };
}
