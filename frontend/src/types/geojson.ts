/**
 * Typed wrappers around the GeoJSON spec for features used in this project.
 * Extends the standard GeoJSON shapes with domain-specific property contracts.
 */

// ---------------------------------------------------------------------------
// Core GeoJSON primitives
// ---------------------------------------------------------------------------

export interface GeoJsonPoint {
  type: 'Point';
  coordinates: [longitude: number, latitude: number];
}

export interface GeoJsonPolygon {
  type: 'Polygon';
  coordinates: Array<Array<[longitude: number, latitude: number]>>;
}

export type GeoJsonGeometry = GeoJsonPoint | GeoJsonPolygon;

// ---------------------------------------------------------------------------
// Generic Feature / FeatureCollection
// ---------------------------------------------------------------------------

export interface GeoJsonFeature<
  G extends GeoJsonGeometry = GeoJsonGeometry,
  P extends Record<string, any> = Record<string, any>,
> {
  type: 'Feature';
  geometry: G;
  properties: P;
  id?: string | number;
}

export interface GeoJsonFeatureCollection<
  G extends GeoJsonGeometry = GeoJsonGeometry,
  P extends Record<string, any> = Record<string, any>,
> {
  type: 'FeatureCollection';
  features: Array<GeoJsonFeature<G, P>>;
}

// ---------------------------------------------------------------------------
// Domain-specific property shapes
// ---------------------------------------------------------------------------

export interface EvChargerProperties {
  id: string;
  name: string;
  operator?: string;
  plugTypes?: string[];
  powerKw?: number;
  available?: boolean;
}

export interface MetroStationProperties {
  id: string;
  name: string;
  line?: string;
}

export interface ParkingProperties {
  id: string;
  name?: string;
  capacity?: number;
}

// ---------------------------------------------------------------------------
// Recommendation candidate — shape returned by POST /recommendation
// ---------------------------------------------------------------------------

export interface FactorScores {
  population: number;
  charger_distance: number;
  road_proximity: number;
  parking: number;
  mall_proximity: number;
}

export interface CandidateProperties {
  rank: number;
  score: number;
  factor_scores: FactorScores;
  population_1km: number;
  /** null when no charger found within search radius */
  nearest_charger_distance_m: number | null;
  road_type: string;
  parking_available: boolean;
  /** null when no mall found within 500 m */
  nearest_mall_distance_m: number | null;
  warnings: string[];
}

export type CandidateFeature = GeoJsonFeature<GeoJsonPoint, CandidateProperties>;

export interface RecommendationResponse {
  type: 'FeatureCollection';
  features: CandidateFeature[];
  city: string;
  chargerType: string;
  radius: number;
  total_candidates: number;
}

// ---------------------------------------------------------------------------
// Convenience aliases
// ---------------------------------------------------------------------------

export type EvChargerFeature = GeoJsonFeature<GeoJsonPoint, EvChargerProperties>;
export type EvChargerCollection = GeoJsonFeatureCollection<GeoJsonPoint, EvChargerProperties>;

export type MetroStationFeature = GeoJsonFeature<GeoJsonPoint, MetroStationProperties>;
export type MetroStationCollection = GeoJsonFeatureCollection<GeoJsonPoint, MetroStationProperties>;

// ---------------------------------------------------------------------------
// Analysis response — shape returned by GET /analysis
// ---------------------------------------------------------------------------

export interface WardStat {
  ward_name: string;
  candidate_count: number;
  mean_score: number;
}

export interface AnalysisResponse {
  city: string;
  /**
   * FastAPI serialises this as `charger_type` (field name) unless
   * response_model_by_alias=True is set on the route. Both keys are
   * accepted here so the type works regardless of serialisation mode.
   */
  charger_type?: string;
  /** Alias form — present if the backend ever enables by_alias serialisation. */
  chargerType?: string;
  total_candidates: number;
  /** Server-computed mean over ALL candidates (not just the displayed slice). */
  score_mean: number;
  score_median: number;
  score_p90: number;
  coverage_pct: number;
  ward_stats: WardStat[];
}

