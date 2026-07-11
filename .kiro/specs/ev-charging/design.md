# Design Document — ChargeWise India

## Overview

ChargeWise India is a three-tier geospatial planning tool that answers
"Where should we install the next N EV charging stations?" for Indian
cities. A planner selects a city, charger type, and search radius; the
system overlays real infrastructure data with demand signals and returns a
ranked list of candidate locations as scored GeoJSON features.

The MVP targets a 2–3 week delivery window, with Bengaluru as the primary
fully-populated city and four additional cities (Mumbai, Hyderabad, Chennai,
Pune) available in the UI but potentially with partial datasets.

### Key Design Decisions

- **Synchronous processing** — recommendation requests are handled
  synchronously within a 10 s SLA. Async job polling is a Stretch goal.
- **Static API key auth** — a single `API_KEY` env var protects all
  non-public endpoints. JWKS/Bearer is a Stretch goal.
- **File-based data store** — GeoJSON files on disk, loaded into memory at
  startup and re-projected once. No database required for MVP.
- **In-memory cache** — the Go API gateway holds a TTL-based in-memory
  cache keyed on `(city, chargerType, radius)`.
- **EPSG:32643 internally** — all spatial operations use UTM Zone 43N
  (metres); input and output always use WGS-84 (EPSG:4326).


## Architecture

### Component Diagram

```text
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  React + MapLibre GL + Deck.gl (Vite)                    │   │
│  │  VITE_API_URL  VITE_MAP_STYLE_URL                        │   │
│  └──────────────────┬───────────────────────────────────────┘   │
└─────────────────────│───────────────────────────────────────────┘
                      │  HTTPS  X-API-Key
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│  Go API Gateway  (:8080)                                         │
│  • API key validation (Req 8A)                                   │
│  • CORS middleware                                               │
│  • TTL in-memory cache  (city+type+radius, 5 min)               │
│  • Correlation ID generation / propagation                       │
│  • Structured JSON access log                                    │
│  • Timeout + 503 circuit breaker (3 s to geo-service)           │
│                                                                   │
│  Endpoints                                                        │
│   GET  /health          (public — no auth)                       │
│   GET  /cities          (public — no auth)                       │
│   GET  /chargers?city=  (protected)                              │
│   POST /recommendation  (protected)                              │
│   GET  /analysis?city=&chargerType= (protected)                 │
└──────────────────────┬──────────────────────────────────────────┘
                       │  HTTP  X-Correlation-ID
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  Geo Service  (:8000)  Python / FastAPI                          │
│  • Dataset loader + CRS re-projection                            │
│  • Spatial Scorer (GeoPandas batch sjoin)                        │
│  • POST /recommendation   POST /validate                         │
│  • GET  /data-health      GET  /analysis                         │
│  • GET  /health                                                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │  filesystem read
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  Data Store  (local disk / mounted volume)                       │
│  $DATA_DIR/{city}/                                               │
│   ev_chargers.geojson    roads.geojson                           │
│   parking.geojson        malls.geojson                           │
│   metro_stations.geojson tech_parks.geojson                      │
│   fuel_stations.geojson  ward_boundaries.geojson                 │
│   population_grid.geojson                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Request Flow — POST /recommendation (cache miss)

```text
Browser ──POST /recommendation──► Go API (validate API key)
         ◄── 401 ─────────────── (if missing/invalid key)
Go API  ──POST /recommendation──► Geo Service (X-Correlation-ID)
         ◄── 200 GeoJSON ──────── (within 10 s SLA)
Go API  stores response in cache (TTL 5 min)
         ──200 GeoJSON──────────► Browser

On cache hit: Go API returns stored response, no Geo Service call.
On timeout (>3 s) or 5xx: Go API returns 503 + Retry-After: 30.
```


## Components and Interfaces

### 1. Geo Service (Python / FastAPI)

**Responsibility** — all geospatial computation: data loading, CRS
management, spatial scoring, GeoJSON validation, and analysis statistics.

#### Module structure

```text
geo-service/
├── app/
│   ├── main.py                  # FastAPI app factory, lifespan, middleware
│   ├── models/
│   │   └── schemas.py           # Pydantic request/response models (existing)
│   ├── routers/
│   │   ├── recommendation.py    # POST /recommendation (stub → full impl)
│   │   ├── data_health.py       # GET /data-health
│   │   ├── analysis.py          # GET /analysis
│   │   └── validate.py          # POST /validate
│   ├── core/
│   │   ├── dataset_loader.py    # DatasetRegistry, load_city_datasets()
│   │   ├── candidates.py        # generate_candidates() — parking or grid fallback
│   │   ├── scorer.py            # Scorer.score_batch()
│   │   └── analysis_engine.py  # AnalysisEngine.compute()
│   └── config.py                # Settings via pydantic-settings
└── tests/
    ├── conftest.py              # Fixtures, env setup (existing)
    ├── test_health.py           # GET /health
    ├── test_recommendation.py   # POST /recommendation (existing)
    ├── test_scorer.py           # Unit tests: all 5 factor functions
    ├── test_dataset_loader.py   # CRS assertion, round-trip
    └── test_validate.py         # POST /validate
```

#### Key interfaces

```python
# core/dataset_loader.py
@dataclass
class CityDatasets:
    ev_chargers: gpd.GeoDataFrame
    roads: gpd.GeoDataFrame        # motorway/trunk/primary/secondary
    parking: gpd.GeoDataFrame
    malls: gpd.GeoDataFrame
    metro_stations: gpd.GeoDataFrame
    tech_parks: gpd.GeoDataFrame
    fuel_stations: gpd.GeoDataFrame
    ward_boundaries: gpd.GeoDataFrame
    population_grid: gpd.GeoDataFrame
    missing_layers: list[str]      # factor names for warning injection

class DatasetRegistry:
    """Singleton holding loaded GeoDataFrames per city."""
    def load(self, city: str) -> CityDatasets: ...
    def health(self) -> DataHealthResponse: ...

# core/scorer.py
@dataclass
class ScorerResult:
    geometry: Point
    score: int
    factor_scores: FactorScores
    population_1km: int
    nearest_charger_distance_m: float | None
    road_type: str
    parking_available: bool
    nearest_mall_distance_m: float | None
    warnings: list[str]

class Scorer:
    def score_batch(
        self,
        candidates: gpd.GeoDataFrame,   # EPSG:32643 Points
        datasets: CityDatasets,
        search_radius: int,
    ) -> list[ScorerResult]: ...
```


### 2. Go API Gateway

**Responsibility** — authentication, CORS, in-memory caching, request
routing to the Geo Service, correlation ID propagation, and access logging.

#### Package structure

```text
go_api/
├── cmd/server/main.go           # Entry point, env validation, HTTP server
├── internal/
│   ├── auth/
│   │   └── apikey.go            # Middleware: X-API-Key header check
│   ├── cache/
│   │   └── memory.go            # TTL cache keyed on CacheKey struct
│   ├── proxy/
│   │   └── geoservice.go        # HTTP client wrapper, timeout, 503 logic
│   ├── middleware/
│   │   ├── cors.go              # CORS_ORIGINS env var
│   │   ├── correlation.go       # X-Correlation-ID generate / propagate
│   │   └── logger.go            # Structured JSON access log
│   ├── handlers/
│   │   ├── health.go            # GET /health
│   │   ├── cities.go            # GET /cities  (static config)
│   │   ├── chargers.go          # GET /chargers?city=
│   │   ├── recommendation.go    # POST /recommendation
│   │   └── analysis.go          # GET /analysis
│   └── config/
│       └── config.go            # All env vars with validation
├── go.mod
└── go.sum
```

#### Cache key design

```go
type CacheKey struct {
    City        string
    ChargerType string
    Radius      int
}

type CacheEntry struct {
    Body      []byte
    ExpiresAt time.Time
}
```

The cache is a `sync.Map` (or a `map` guarded by a `sync.RWMutex`) with
background TTL eviction. On every cache write the entry stores
`time.Now().Add(CACHE_TTL_SECONDS)`. Cache reads check `ExpiresAt` before
returning; expired entries are treated as misses and evicted.

#### Timeout / circuit-breaker logic

The Go client sets a `context.WithTimeout` of 3 seconds for each proxied
call to the Geo Service. On `context.DeadlineExceeded` or any HTTP 5xx
response, the handler returns `503 Service Unavailable` with
`Retry-After: 30`. Failures are never stored in the cache.


### 3. Frontend (React / Vite)

**Responsibility** — parameter selection, map rendering via MapLibre GL JS
and Deck.gl, candidate display, layer toggles, tooltip, side panel, and
CSV export.

#### Component structure

```text
frontend/
├── src/
│   ├── main.tsx                 # App entry, env validation
│   ├── App.tsx                  # Root layout
│   ├── components/
│   │   ├── SelectionPanel/
│   │   │   ├── SelectionPanel.tsx
│   │   │   ├── CityDropdown.tsx
│   │   │   ├── ChargerTypeSelector.tsx
│   │   │   └── RadiusInput.tsx
│   │   ├── MapView/
│   │   │   ├── MapView.tsx       # MapLibre map container
│   │   │   ├── LayerToggleBar.tsx
│   │   │   └── CandidateLayer.tsx  # Deck.gl ScatterplotLayer
│   │   ├── SidePanel/
│   │   │   ├── SidePanel.tsx
│   │   │   ├── CandidateList.tsx
│   │   │   └── CandidateRow.tsx
│   │   └── shared/
│   │       ├── Tooltip.tsx
│   │       └── Toast.tsx
│   ├── hooks/
│   │   ├── useRecommendations.ts
│   │   └── useLayerData.ts
│   ├── services/
│   │   └── apiClient.ts         # Fetch wrapper, API key injection
│   ├── types/
│   │   └── geojson.ts
│   └── config.ts                # VITE_API_URL, VITE_MAP_STYLE_URL
├── index.html
├── vite.config.ts
└── .env.example
```

#### Layer configuration

```typescript
export const BASE_LAYERS = [
  { id: "ev_chargers",     label: "EV Chargers",     color: "#00CC44" },
  { id: "fuel_stations",   label: "Petrol Pumps",    color: "#FF6600" },
  { id: "roads",           label: "Major Roads",     color: "#3399FF" },
  { id: "parking",         label: "Parking Lots",    color: "#FFCC00" },
  { id: "metro_stations",  label: "Metro Stations",  color: "#9900CC" },
  { id: "malls",           label: "Shopping Malls",  color: "#FF3366" },
  { id: "tech_parks",      label: "Tech Parks",      color: "#00CCCC" },
] as const;
```

#### Candidate colour gradient

Score bands map to Deck.gl `ScatterplotLayer` fill colours:

| Score range | Colour | Hex |
|-------------|--------|-----|
| 0–33 | Red | `#FF0000` |
| 34–66 | Amber | `#FFA500` |
| 67–100 | Green | `#00AA00` |

Each marker has a fixed radius of 60 m. Highlighted markers (selected in
side panel) add a white border of 3 px.

#### API key storage

The API key is held exclusively in a React `useRef` or module-level
variable (never `localStorage`, `sessionStorage`, or cookies). On page
reload, the app renders an `ApiKeyGate` component prompting re-entry before
any protected request is issued.


## Data Models

### Data Store Layout

```text
$DATA_DIR/
├── bengaluru/
│   ├── ev_chargers.geojson
│   ├── fuel_stations.geojson
│   ├── parking.geojson
│   ├── roads.geojson
│   ├── metro_stations.geojson
│   ├── malls.geojson
│   ├── tech_parks.geojson
│   ├── ward_boundaries.geojson
│   └── population_grid.geojson
├── mumbai/         (same structure; may be partial)
├── hyderabad/
├── chennai/
└── pune/
```

**Dataset naming convention** — lowercase snake_case matching the keys in
`CityDatasets`. The `DatasetRegistry` maps city name (lowercased) to its
directory. Missing files are recorded in `missing_layers`.

### CRS Strategy

| Stage | CRS | Why |
|-------|-----|-----|
| Files on disk | EPSG:4326 (WGS-84) | Standard GeoJSON |
| After `load_city_datasets()` | EPSG:32643 (UTM 43N) | Metre-based distances |
| API request body | EPSG:4326 | Interoperability |
| API response (GeoJSON) | EPSG:4326 | Interoperability |

The loader calls `gdf.to_crs(epsg=32643)` immediately after reading each
file. All `buffer()`, `sjoin_nearest()`, and distance calculations operate
in projected metres. Before serialisation, the response GeoDataFrame is
converted back with `gdf.to_crs(epsg=4326)`.

### City Registry (Go API)

```go
type CityInfo struct {
    Name        string          `json:"name"`
    BoundingBox geojson.Polygon `json:"boundingBox"`
    Center      [2]float64      // [lng, lat] for map centering
}

var SupportedCities = []CityInfo{
    {Name: "Bengaluru", Center: [2]float64{77.5946, 12.9716}, ...},
    {Name: "Mumbai",    Center: [2]float64{72.8777, 19.0760}, ...},
    {Name: "Hyderabad", Center: [2]float64{78.4867, 17.3850}, ...},
    {Name: "Chennai",   Center: [2]float64{80.2707, 13.0827}, ...},
    {Name: "Pune",      Center: [2]float64{73.8567, 18.5204}, ...},
}
```

This static registry is the source of truth for `/cities` and for
city-name validation in all other endpoints.

### Pydantic Schemas (Geo Service — existing)

All schemas are already defined in `app/models/schemas.py`. Key types:

- `RecommendationRequest` — `city`, `charger_type`, `radius`
- `RecommendationResponse` — GeoJSON FeatureCollection + metadata
- `CandidateFeature` / `CandidateProperties` / `FactorScores`
- `DataHealthResponse` / `DatasetHealth`
- `ValidateResponse` / `ValidationError`
- `ErrorResponse` / `FieldError`

No schema changes are required. The `AnalysisResponse` model needs to be
added:

```python
class ScoreDistribution(BaseModel):
    mean: float
    median: float
    p90: float

class WardStats(BaseModel):
    ward_name: str
    candidate_count: int
    mean_score: float

class AnalysisResponse(BaseModel):
    city: str
    charger_type: ChargerType = Field(..., alias="chargerType")
    total_candidates: int
    score_distribution: ScoreDistribution
    coverage_pct: float = Field(
        ..., description="Fraction of city bounding polygon area covered"
    )
    ward_stats: list[WardStats]

    model_config = {"populate_by_name": True}
```


## API Contract

Auth requirements below are the single source of truth for this document —
see also the **Authentication Design** section, which now matches this
table exactly.

| Endpoint | Auth required |
|----------|----------------|
| `GET /health` | No |
| `GET /cities` | No |
| `GET /chargers?city=` | Yes (`X-API-Key`) |
| `POST /recommendation` | Yes (`X-API-Key`) |
| `GET /analysis?city=&chargerType=` | Yes (`X-API-Key`) |

Missing or invalid key on a protected endpoint → `401 Unauthorized`.

### Go API Gateway Endpoints

#### GET /health

Public — no auth. Returns `200 OK` when the Geo Service is reachable
(responds to `GET /data-health` within 2 s). Returns `503` with degraded
dependency list otherwise. Deliberately unauthenticated so container
orchestration health probes (which do not send custom headers) can call it
directly.

```json
200: { "status": "ok",
       "dependencies": { "geo_service": "reachable" } }
503: { "status": "degraded",
       "dependencies": { "geo_service": "unreachable" } }
```

#### GET /cities

Returns static city registry. No auth required.

```json
200: [
  { "name": "Bengaluru",
    "boundingBox": { "type": "Polygon", "coordinates": [[...]] } },
  ...
]
```

#### GET /chargers?city={city}

Returns a GeoJSON FeatureCollection of all EV charger locations for the
city. Auth required.

```
200: GeoJSON FeatureCollection
422: { "message": "City not supported.", "supported": [...] }
```

#### POST /recommendation

Auth required. Cache TTL 5 min keyed on `(city, chargerType, radius)`.

**Request body:**

```json
{ "city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500 }
```

**Responses:**

```
200: RecommendationResponse (GeoJSON FeatureCollection + metadata)
400: ErrorResponse  (invalid field value or range)
422: ErrorResponse  (unsupported city)
503: { "message": "Geo service unavailable." }
     Retry-After: 30
```

**Cache-hit indicator** — the Go API adds `X-Cache: HIT` or
`X-Cache: MISS` to the response headers for observability.

#### GET /analysis?city={city}&chargerType={type}

Auth required. Delegated to Geo Service.

```
200: AnalysisResponse
422: ErrorResponse  (unsupported city or charger type)
```

### Geo Service Endpoints

Internal — called by Go API only. No auth header; network isolation via
container/Docker network is sufficient.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/recommendation` | Spatial scoring |
| `GET` | `/data-health` | Dataset load status |
| `GET` | `/analysis` | City statistics |
| `POST` | `/validate` | GeoJSON validation |
| `GET` | `/health` | Liveness probe |

**POST /recommendation** (Geo Service internal)

Input/output schemas are the same `RecommendationRequest` /
`RecommendationResponse` as the Go API passes through. The Geo Service
also accepts and echoes the `X-Correlation-ID` header.

**GET /data-health**

```json
{
  "datasets": {
    "bengaluru/ev_chargers": {
      "record_count": 142, "last_loaded_at": "2024-06-01T09:00:00Z",
      "status": "ok" },
    "bengaluru/population_grid": {
      "record_count": 0,   "last_loaded_at": null, "status": "missing" }
  },
  "city_availability": {
    "Bengaluru": "available",
    "Mumbai": "partial"
  }
}
```

**POST /validate** (max body 50 MB)

```json
{
  "record_count": 3,
  "crs": "EPSG:4326",
  "geometry_types": ["Point", "Polygon"],
  "validation_errors": [
    { "feature_index": 2, "message": "Self-intersecting polygon" }
  ]
}
```


## Scoring Algorithm Implementation

### Overview

The `Scorer` class in `geo-service/app/core/scorer.py` operates on a batch
of candidate `Point` geometries (already projected to EPSG:32643). It never
loops row-by-row for spatial predicates; every factor uses GeoPandas
vectorised operations.

### Candidate Generation

Before scoring, a set of candidate locations is produced from the city's
parking areas GeoDataFrame. Each parking polygon centroid is a candidate.

**If the parking layer is unavailable**, candidates are generated on a
uniform grid across the city bounding polygon at approximately 500 m
spacing (about 25–100 points per city, depending on extent). This grid
**must be deterministic**: it is generated by iterating fixed-step
offsets from the bounding polygon's `minx`/`miny` corner (not by any
random or hash-seeded process), so that the same city and bounding
polygon always produce the same candidate set in the same order. This is
required for Property 5 (score determinism) to hold meaningfully — a
non-deterministic candidate set would make "same scores regardless of
input order" a vacuous guarantee. This candidate-generation step runs
once per request, in `core/candidates.py`.

### Factor Computation

```python
WEIGHTS = {
    "population":      0.35,
    "charger_distance": 0.25,
    "road_proximity":  0.15,
    "parking":         0.15,
    "mall_proximity":  0.10,
}
POPULATION_BUFFER_M = 1000   # fixed — NOT search_radius
ROAD_PROXIMITY_M    = 200
MALL_PROXIMITY_M    = 500
```

#### 1. Population factor (35%)

```python
# Buffer each candidate by EXACTLY 1 000 m (fixed, Req 5 AC-4)
buffers = candidates_gdf.geometry.buffer(POPULATION_BUFFER_M)
buf_gdf = gpd.GeoDataFrame(geometry=buffers, crs=candidates_gdf.crs)

# Spatial join candidate buffers with population grid cells
joined = gpd.sjoin(buf_gdf, population_grid, how="left", predicate="intersects")
pop_sums = joined.groupby("index_left")["population"].sum().reindex(
    candidates_gdf.index, fill_value=0
)
pop_factor = (pop_sums / 50_000).clip(upper=1.0) * 100
```

The 50 000 normalisation constant mirrors the formula in Requirement 5
AC-2. The `clip` ensures the result never exceeds 100.

#### 2. Charger distance factor (25%)

```python
# sjoin_nearest returns NaN for candidates beyond max_distance
nearest = gpd.sjoin_nearest(
    candidates_gdf, ev_chargers_gdf,
    how="left", max_distance=search_radius,
    distance_col="dist_m"
)
charger_factor = (nearest["dist_m"] / search_radius * 100).clip(upper=100)
charger_factor = charger_factor.fillna(100)  # no charger within radius → 100
```

#### 3. Road proximity factor (15%)

```python
# Filter to arterial roads only
arterial = roads_gdf[roads_gdf["highway"].isin(
    ["motorway", "trunk", "primary"]
)]
nearest_road = gpd.sjoin_nearest(
    candidates_gdf, arterial,
    how="left", max_distance=ROAD_PROXIMITY_M,
    distance_col="road_dist_m"
)
road_factor = nearest_road["road_dist_m"].notna().astype(int) * 100
```

Binary 0/100 per Requirement 5 AC-2.

#### 4. Parking factor (15%)

`sjoin` (unlike `sjoin_nearest`) returns **one row per match**, so a
candidate intersecting multiple parking polygons appears more than once
in the joined frame. Deduplicate on the candidate index before building
the factor series, and reindex explicitly onto `candidates_gdf.index` so
row alignment can't drift:

```python
matched_idx = gpd.sjoin(
    candidates_gdf, parking_gdf,
    how="inner", predicate="intersects"
).index.unique()

parking_factor = pd.Series(
    candidates_gdf.index.isin(matched_idx).astype(int) * 100,
    index=candidates_gdf.index,
)
```

Binary 0/100 per Requirement 5 AC-2.

#### 5. Mall proximity factor (10%)

```python
nearest_mall = gpd.sjoin_nearest(
    candidates_gdf, malls_gdf,
    how="left", max_distance=MALL_PROXIMITY_M,
    distance_col="mall_dist_m"
)
mall_factor = nearest_mall["mall_dist_m"].notna().astype(int) * 100
```

Binary 0/100 per Requirement 5 AC-2.

### Final Score Assembly

```python
score = (
    WEIGHTS["population"]       * pop_factor       +
    WEIGHTS["charger_distance"] * charger_factor    +
    WEIGHTS["road_proximity"]   * road_factor       +
    WEIGHTS["parking"]          * parking_factor    +
    WEIGHTS["mall_proximity"]   * mall_factor
).round().astype(int).clip(0, 100)
```

`round()` returns the nearest even integer for .5 ties (Python default);
`clip(0, 100)` guards against floating-point edge cases.

### Missing Layer Handling (Req 5 AC-8)

Before scoring, the loader populates `CityDatasets.missing_layers`. For
each missing layer the corresponding factor series is set to `0` and the
factor name is appended to every candidate's `warnings` list.

```python
if "population" in datasets.missing_layers:
    pop_factor = pd.Series(0, index=candidates_gdf.index)
    warnings_col = warnings_col + ["population"]
```

### Determinism (Req 5 AC-7)

All GeoPandas sjoin operations are deterministic given the same input
GeoDataFrames. The candidates are sorted by their index before scoring and
the index is reset, so row order in the input does not affect output scores.
This determinism guarantee depends on candidate *generation* also being
deterministic — see the grid-fallback note above. The final output is
sorted descending by `score`, ascending by original index to break ties,
then ranked 1-based.


## Caching Strategy

### Go API In-Memory Cache

**Structure:** `map[CacheKey]CacheEntry` guarded by `sync.RWMutex`.

**Key:** `CacheKey{City, ChargerType, Radius}` — all three fields must
match for a hit. The key is case-normalised (city name as returned by
`/cities`, charger type uppercased) before lookup.

**TTL:** `CACHE_TTL_SECONDS` env var (default 300). Each `CacheEntry`
stores the serialised response body (raw `[]byte`) and an `ExpiresAt`
timestamp.

**Eviction:** A background goroutine sweeps the map every 60 s and deletes
expired entries. This prevents unbounded memory growth on servers handling
many distinct city/radius combinations.

**Cache-miss sequence:**
1. Acquire read lock; check map; release.
2. If miss, proxy to Geo Service.
3. On 2xx response: acquire write lock; store entry; release.
4. Return response. Add `X-Cache: MISS` header.

**Cache-hit sequence:**
1. Acquire read lock; check map; find entry; check `ExpiresAt`.
2. Return stored body. Add `X-Cache: HIT` header.

**Failure handling:** Any non-2xx response from the Geo Service is never
cached. The caller receives the upstream error code (or 503 on timeout).

**Capacity planning:** Each Bengaluru recommendation response is roughly
50–100 KB (200 candidates × ~500 bytes/feature). With 5 cities × 3 charger
types × ~10 radius buckets, peak cache size ≈ 150 entries × 100 KB =
~15 MB. Well within typical container memory limits.


## Authentication Design

### MVP: Static API Key (Requirement 8A)

The Go API Gateway reads the expected key from `API_KEY` env var at
startup. If `API_KEY` is empty the service refuses to start (Req 12 AC-4).

**Middleware behaviour** (matches the API Contract table above exactly):

```text
Public endpoints:    GET /health, GET /cities  (no key required)
Protected endpoints: GET /chargers, POST /recommendation, GET /analysis
```

For each protected request:

1. Read `X-API-Key` header.
2. Compare with `API_KEY` using `subtle.ConstantTimeCompare` to prevent
   timing-based disclosure.
3. On mismatch or absence: return `401 Unauthorized`. Do not log the
   supplied key value.
4. On match: proceed; never log or echo the key in any log field.

**Frontend behaviour:**

The API key is stored in a module-level variable in `apiClient.ts` (not in
any Web Storage or cookie). On first page load (or after reload), if the
key variable is empty, `ApiKeyGate` blocks all protected routes and prompts
the user with a secure text input. The entered key is stored in memory via
a React `ref` and injected into every `fetch` call as
`X-API-Key: <value>`.

### Stretch: JWKS Bearer Token (Requirement 8B)

Not implemented in MVP. When implemented, the Go API middleware will:

1. Extract `Authorization: Bearer <token>`.
2. Fetch JWKS from `JWKS_URL`; cache public keys for 300 s.
3. Verify signature, `exp`, `iss` (`TOKEN_ISSUER`), `aud`
   (`TOKEN_AUDIENCE`).
4. On JWKS unreachability beyond 300 s cache: return `503`.


## Frontend Architecture

### Map Rendering Stack

| Library | Version | Role |
|---------|---------|------|
| MapLibre GL JS | `^4.x` | Base tile map, city centering, zoom |
| Deck.gl | `^9.x` | Candidate `ScatterplotLayer`, base layer overlays |
| React | `^18.x` | Component framework |
| Vite | `^5.x` | Build tool, env vars |

MapLibre handles the base tile map (`VITE_MAP_STYLE_URL`). Deck.gl layers
are mounted as an `interleaved` overlay using the
`MapboxOverlay` adapter (`@deck.gl/mapbox`), so Deck.gl features render
inside the MapLibre render pipeline and respect z-ordering.

### Deck.gl Layer Design

#### Base layers (toggleable)

Each of the seven base layers is a `GeoJsonLayer` or `IconLayer` loaded
lazily when its toggle is activated. The hook `useLayerData` caches fetched
GeoJSON in a `useRef` so re-toggling doesn't re-fetch within the session.

```typescript
const baseLayer = new GeoJsonLayer({
  id: `base-${layerConfig.id}`,
  data: geojsonData,
  filled: true,
  getFillColor: hexToRgba(layerConfig.color),
  stroked: true,
  getLineColor: [0, 0, 0, 128],
  lineWidthMinPixels: 1,
  pickable: false,
});
```

#### Candidate layer

```typescript
const candidateLayer = new ScatterplotLayer({
  id: "candidates",
  data: visibleCandidates,
  getPosition: (f) => f.geometry.coordinates,
  getRadius: 60,
  radiusUnits: "meters",
  getFillColor: (f) => scoreToColor(f.properties.score),
  getLineColor: (f) =>
    selectedCandidate?.rank === f.properties.rank
      ? [255, 255, 255, 255]
      : [0, 0, 0, 0],
  lineWidthMinPixels: (f) =>
    selectedCandidate?.rank === f.properties.rank ? 3 : 0,
  pickable: true,
  onClick: ({ object }) => setSelectedCandidate(object),
});

function scoreToColor(score: number): [number, number, number, number] {
  if (score <= 33) return [255,   0,   0, 200]; // red
  if (score <= 66) return [255, 165,   0, 200]; // amber
  return              [  0, 170,   0, 200];     // green
}
```

### State Management

React `useState` and `useReducer` are sufficient for MVP. No Redux or
Zustand required at this scale. Key state:

```typescript
type AppState = {
  city: string | null;
  chargerType: ChargerType | null;
  radius: number;
  apiKey: string;               // in-memory only
  recommendations: RecommendationResponse | null;
  activeLayers: Set<string>;
  selectedCandidate: CandidateFeature | null;
  displayCount: number;         // 10–200, default 50
  sortColumn: "rank" | "score" | "address";
  sortDir: "asc" | "desc";
  toasts: Toast[];
};
```

### CSV Export

The download button generates a CSV from `visibleCandidates` using the
native `URL.createObjectURL(new Blob([csvString], { type: "text/csv" }))`.
Columns: rank, latitude, longitude, score, population_1km,
nearest_charger_distance_m, road_type, parking_available,
nearest_mall_distance_m.


## Correctness Properties

_A property is a characteristic or behavior that should hold true across
all valid executions of a system — essentially, a formal statement about
what the system should do. Properties serve as the bridge between
human-readable specifications and machine-verifiable correctness
guarantees._

PBT is applicable here because the system's core logic — spatial scoring,
GeoJSON round-tripping, and statistical aggregation — consists of pure or
near-pure functions where input variation meaningfully reveals edge cases
and 100+ iterations provide value. **For the MVP, property-based testing
is scoped to the Geo Service only**, using **Hypothesis**
(`hypothesis[pandas]`), since that's where the highest-value, hardest-to-
spot bugs live (CRS mismatches, join misalignment, formula edge cases).
Cache idempotence (Property 7) and frontend selection validation are
still tested, but as plain example/table-driven unit tests rather than a
separate PBT toolchain per language — see **Testing Strategy** below for
the rationale.

### Property 1: GeoJSON round-trip preserves geometry fidelity

_For any_ valid GeoJSON FeatureCollection containing Point, LineString,
or Polygon geometries, parsing it into a GeoDataFrame, serialising that
GeoDataFrame back to GeoJSON, and parsing again must yield a GeoDataFrame
with the same record count, the same geometry types for each feature, and
coordinate values within 1×10⁻⁷ degrees of the originals for every
vertex.

**Validates: Requirements 3.5, 10.3**

### Property 2: All loaded datasets are projected to EPSG:32643

_For any_ GeoJSON file read by `load_city_datasets()`, the resulting
GeoDataFrame's `.crs.to_epsg()` must equal `32643` before the function
returns control to the caller.

**Validates: Requirements 3.2**

### Property 3: Final weighted score is correctly computed and bounded

_For any_ five factor scores each in the range [0, 100], the Scorer's
`compute_final_score(pop, chr, road, park, mall)` must return
`round(0.35×pop + 0.25×chr + 0.15×road + 0.15×park + 0.10×mall)` and
the result must be an integer in [0, 100] inclusive.

**Validates: Requirements 5.2**

### Property 4: Population factor uses a fixed 1 km buffer

_For any_ search_radius value in [250, 10 000], calling
`Scorer.score_batch()` must apply a population buffer of exactly 1 000 m
(not `search_radius`) to every candidate. This is verified by inspecting
the buffer GeoDataFrame passed to the population sjoin, which must have
area ≈ π × 1 000² m² per candidate (within floating-point tolerance).

**Validates: Requirements 5.4**

### Property 5: Score determinism under candidate order permutation

_For any_ candidate GeoDataFrame and reference `CityDatasets`, shuffling
the row order of the candidates GeoDataFrame before calling
`Scorer.score_batch()` must produce identical score values for each
candidate (identified by original geometry centroid coordinates). This
property assumes deterministic candidate generation (see Candidate
Generation section) — the input GeoDataFrame's *contents*, not just its
row order, must be reproducible for the property to be meaningful.

**Validates: Requirements 5.7**

### Property 6: Missing layer triggers zero factor score and warning

_For any_ candidate set, when a `CityDatasets` object has one or more
layers set to an empty GeoDataFrame (simulating absence), every candidate
in the output must have factor score `0` for each affected factor, and each
candidate's `warnings` list must contain the affected factor name.

**Validates: Requirements 5.8**

### Property 7: Cache idempotence yields single geo-service call

_For any_ valid `(city, chargerType, radius)` triple, issuing the same
`POST /recommendation` request twice through the Go API must: (a) return
byte-identical response bodies, (b) result in exactly one forwarded call
to the Geo Service, and (c) return `X-Cache: HIT` on the second response.
Tested via table-driven unit tests (representative city/type/radius
combinations, including boundary radius values) rather than a Go PBT
library — see Testing Strategy.

**Validates: Requirements 4.5, 9.3**

### Property 8: GeoJSON validation reports correct record count and types

_For any_ valid GeoJSON FeatureCollection, `POST /validate` must return
`record_count == len(features)`, `geometry_types` containing exactly the
set of geometry type strings present in the input (no extras, no omissions),
and `validation_errors == []` when all features are geometrically valid.

**Validates: Requirements 10.4**

### Property 9: Analysis score statistics are mathematically correct

_For any_ list of candidate scores (integers in [0, 100]) with at least
one element, `AnalysisEngine.compute()` must return `mean` equal to
`statistics.mean(scores)`, `median` equal to `statistics.median(scores)`,
and `p90` equal to `numpy.percentile(scores, 90)`, each within floating-
point rounding tolerance of 0.01.

**Validates: Requirements 7.1**

### Redundancy Analysis

After reviewing all nine properties:

- Properties 1 and 2 are complementary (round-trip vs. CRS after load);
  they test different things and both provide value.
- Properties 3 and 5 could seem related (score correctness vs.
  determinism), but they validate distinct invariants — the formula vs.
  order-independence. Both are retained.
- Property 6 (missing layer) is narrower than Property 3 (score formula)
  but tests a distinct fallback code path. Retained.
- Property 7 (cache) operates at the Go API level, not the scorer —
  different system layer. Retained, but downgraded to table-driven unit
  tests for MVP (see Testing Strategy).
- No redundancy detected. All nine properties provide unique validation
  value across different components and requirement clauses.


## Error Handling

### Geo Service Error Catalogue

| Condition | HTTP | Body |
|-----------|------|------|
| Required env var missing at startup | exit 1 | — |
| GeoJSON file missing | 503 | `ErrorResponse` with dataset name |
| Unsupported city | 422 | `ErrorResponse` with supported list |
| Pydantic validation failure | 422 | FastAPI default |
| Spatial layer empty (missing factor) | 200 | warnings in each candidate |
| POST /validate body not JSON | 400 | `ErrorResponse` with parse message |
| POST /validate body not FeatureCollection | 400 | `ErrorResponse` |
| Unhandled exception | 500 | `ErrorResponse` + full stack trace logged |

### Go API Error Catalogue

| Condition | HTTP | Notes |
|-----------|------|-------|
| Missing/invalid API key | 401 | Never log the supplied key |
| Invalid request body | 400 | `errors` array per field |
| Unsupported city | 422 | Supported cities list |
| Geo Service timeout (>3 s) | 503 | `Retry-After: 30` |
| Geo Service 5xx | 503 | `Retry-After: 30` |
| Unknown route | 404 | Standard JSON envelope |

### Frontend Error Handling

- **Network/HTTP error on layer fetch** — toast notification with layer
  name and HTTP status or "network error". Other layers remain visible.
- **Network/HTTP error on recommendation** — inline error banner in the
  results area. Selection panel remains interactive.
- **API key rejected (401)** — clear in-memory key, show `ApiKeyGate`
  again.
- **422 city unavailable** — show descriptive message in the results area
  listing missing datasets.

### Structured Log Fields (both services)

Every log entry is a single-line JSON object with at minimum:

```json
{
  "timestamp": "2024-06-01T09:00:00",
  "level": "INFO",
  "logger": "app.routers.recommendation",
  "message": "recommendation request received",
  "correlation_id": "abc-123",
  "city": "Bengaluru",
  "charger_type": "DC_FAST",
  "radius": 1500
}
```

Spatial operation logs additionally include `operation`, `input_count`,
`output_count`, `duration_ms`.


## Testing Strategy

### Dual Testing Approach

Unit tests verify specific examples, edge cases, and error conditions.
Property-based tests verify universal properties across many generated
inputs. Both are complementary.

**Scope decision for MVP:** property-based testing is used in the Geo
Service only (Hypothesis). Standing up separate PBT toolchains for Go
(`rapid`) and TypeScript (`fast-check`) inside a 2–3 week solo build is
disproportionate to the value they add over well-chosen table-driven unit
tests, given that the Go cache and frontend validation logic are both
small, low-branching pieces of code where a handful of hand-picked cases
(including boundary values) already gives strong coverage. `rapid` and
`fast-check` are moved to **Stretch** — see the tracker. If the MVP
timeline has slack, they're a reasonable next investment.

### Geo Service — Python / pytest + Hypothesis

**PBT library:** `hypothesis[pandas]` (≥6.100), `hypothesis-geopandas`
strategies or hand-rolled `st.builds()` on GeoJSON dicts.

All property tests are configured with `@settings(max_examples=100)` at
minimum. Each test is tagged with a comment referencing the design
property:

```python
# Feature: chargewise-india, Property 3: final weighted score is correctly
# computed and bounded
```

#### Property-based tests (`tests/test_scorer_properties.py`)

| Test | Property |
|------|----------|
| `test_score_formula_and_bounds` | Property 3 |
| `test_population_buffer_is_1km` | Property 4 |
| `test_score_determinism_under_shuffle` | Property 5 |
| `test_missing_layer_zero_and_warning` | Property 6 |
| `test_analysis_stats_correctness` | Property 9 |

#### Property-based tests (`tests/test_dataset_loader_properties.py`)

| Test | Property |
|------|----------|
| `test_geojson_roundtrip` | Property 1 |
| `test_crs_is_32643_after_load` | Property 2 |

#### Property-based tests (`tests/test_validate_properties.py`)

| Test | Property |
|------|----------|
| `test_validate_record_count_and_types` | Property 8 |

#### Unit / example-based tests

- `tests/test_scorer.py` — each of the 5 factor functions with: normal
  case, zero-match case (fallback), boundary case at exact Search_Radius
  distance, and — specifically for the parking factor — a case where a
  single candidate intersects two overlapping parking polygons, to guard
  against the sjoin-duplication bug. (Req 13 AC-1)
- `tests/test_candidates.py` — grid fallback produces the same candidate
  set (same points, same order) across repeated calls for the same city
  bounding polygon.
- `tests/test_recommendation.py` — existing tests covering valid request,
  invalid radius, unsupported city, correlation ID. (Req 13 AC-4)
- `tests/test_health.py` — GET /health returns 200 with no auth header.

### Go API — Go / testing + testify

Standard table-driven tests (`testify/assert` + `testify/require`); no
separate PBT library for MVP (see Scope decision above).

#### `internal/cache/memory_test.go`

Table-driven cases covering: fresh key (miss → store → hit), same key
requested twice returns byte-identical body and increments a call
counter to the mocked Geo Service exactly once, TTL expiry causes a miss,
boundary radius values (250, 10000), and case-normalisation (e.g.
`"bengaluru"` vs `"Bengaluru"` vs `"BENGALURU"` all hit the same entry).
This exercises the same idempotence guarantee as Property 7.

#### Other unit / example-based tests

- `internal/handlers/recommendation_test.go` — valid request, invalid
  radius, unsupported city, Geo Service 5xx/timeout → 503.
  (Req 13 AC-4)
- `internal/auth/apikey_test.go` — missing key, invalid key, valid key,
  and confirmation that `GET /health` and `GET /cities` succeed with
  **no** key header at all.
- `internal/cache/memory_test.go` — TTL expiry, eviction, concurrent
  access (`go test -race`).

### Frontend — Vitest

Standard example/table-driven tests; no separate PBT library for MVP (see
Scope decision above).

#### `src/components/SelectionPanel/__tests__/validateSelection.test.ts`

Table-driven cases covering the full constraint matrix: valid city +
valid type + valid radius (pass); each field individually invalid (fail,
correct error message); radius at exact boundaries 250 and 10000 (pass);
radius at 249 and 10001 (fail); unsupported city string; unsupported
charger type string.

#### Other example-based tests

- `SelectionPanel` — renders all fields, shows validation errors,
  resets on city change.
- `CandidateLayer` — score-to-colour mapping for boundary values
  (0, 33, 34, 66, 67, 100).
- `apiClient` — injects `X-API-Key` header, stores key in memory.
- `CandidateList` — sort by column, CSV export content.

### Performance Targets

| Operation | Target | Test approach |
|-----------|--------|---------------|
| Score 500 candidates | < 5 s | `time.perf_counter()` assert in pytest |
| Cache hit response | < 200 ms p95 | Unit test with mock Geo Service |
| GET /cities | < 500 ms p95 | Load test (k6, Stretch) |


## Configuration and Environment Variables

### Geo Service (`geo-service/.env.example`)

```bash
# Required
DATA_DIR=/data                  # Absolute path to dataset directory
DEFAULT_CRS_EPSG=32643          # Integer EPSG code for spatial ops

# Optional
LOG_LEVEL=INFO                  # DEBUG|INFO|WARNING|ERROR|CRITICAL
PORT=8000                       # Uvicorn listen port (default 8000)
```

Validated at startup in `lifespan()`. Missing required vars → log + exit 1.

### Go API (`go_api/.env.example`)

```bash
# Required
GEO_SERVICE_URL=http://geo-service:8000  # Base URL of Geo Service
API_KEY=changeme                          # Static API key for X-API-Key

# Required with defaults
CACHE_TTL_SECONDS=300                     # 1–86400 (default 300)
CORS_ORIGINS=*                            # Comma-separated URLs or *

# Optional
PORT=8080                                 # Listen port (default 8080)
GEO_SERVICE_TIMEOUT_SECONDS=3            # Proxy timeout (default 3)

# Stretch (JWKS auth — not used in MVP)
# JWKS_URL=https://auth.example.com/.well-known/jwks.json
# TOKEN_ISSUER=https://auth.example.com/
# TOKEN_AUDIENCE=chargewise-api
```

`API_KEY` is read with `os.Getenv`; if empty the server exits 1 with an
error log. The value is **never** logged.

`CACHE_TTL_SECONDS` is validated at startup: must parse as integer in
[1, 86400].

`CORS_ORIGINS` is split on commas and passed to the CORS middleware. The
wildcard `*` means allow all origins.

### Frontend (`frontend/.env.example`)

```bash
# Required build-time variables (Vite injects via import.meta.env)
VITE_API_URL=http://localhost:8080     # Go API base URL
VITE_MAP_STYLE_URL=https://...         # MapLibre tile style JSON URL
```

Vite validates these at build time via a `src/config.ts` guard:

```typescript
const API_URL = import.meta.env.VITE_API_URL;
const MAP_STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL;
if (!API_URL || !MAP_STYLE_URL) {
  throw new Error(
    "Missing required build-time env vars: " +
    "VITE_API_URL, VITE_MAP_STYLE_URL"
  );
}
```

This causes the Vite build to fail, preventing a broken bundle from
being deployed (Req 12 AC-4).

### Environment Variable Summary

| Variable | Service | Required | Default |
|----------|---------|----------|---------|
| `DATA_DIR` | geo-service | ✓ | — |
| `DEFAULT_CRS_EPSG` | geo-service | ✓ | — |
| `LOG_LEVEL` | geo-service | — | `INFO` |
| `GEO_SERVICE_URL` | go_api | ✓ | — |
| `API_KEY` | go_api | ✓ | — |
| `CACHE_TTL_SECONDS` | go_api | — | `300` |
| `CORS_ORIGINS` | go_api | — | `*` |
| `GEO_SERVICE_TIMEOUT_SECONDS` | go_api | — | `3` |
| `VITE_API_URL` | frontend | ✓ (build) | — |
| `VITE_MAP_STYLE_URL` | frontend | ✓ (build) | — |