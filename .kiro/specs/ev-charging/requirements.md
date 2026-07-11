# Requirements Document

## Introduction

ChargeWise India is an AI-assisted geospatial planning tool for EV charging
infrastructure in India. It helps EV charging companies, urban planners,
municipal corporations, real estate developers, and fleet operators answer the
question: "Where should we install the next N charging stations?"

Given a city, charger type, and search radius, the system overlays existing
infrastructure with demand signals (population density, roads, parking, malls,
metro stations, tech parks) and computes a ranked list of candidate locations
using a weighted spatial scoring algorithm powered by GeoPandas.

The MVP targets a 2–3 week delivery window and covers city-level analysis for
Indian cities, starting with Bengaluru.

**Scope note:** Requirements are tagged **[MVP]** or **[Stretch]**. MVP
requirements are the 2–3 week delivery target. Stretch requirements are
real, specified, and intended for implementation, but only after the MVP is
functionally complete end-to-end. Two items in particular — full JWKS-based
auth (Requirement 8) and asynchronous job polling (Requirement 9, criterion
4) — assume infrastructure (an OIDC provider, a job queue/store) that isn't
part of the MVP build and would consume disproportionate time relative to
the recruiter-facing value they add at this stage. Simpler MVP-equivalents
are specified in their place.

---

## Glossary

- **System**: The ChargeWise India application as a whole.
- **Frontend**: The React + MapLibre + Deck.gl web client.
- **API_Server**: The Go HTTP service that handles auth, caching, and routing.
- **Geo_Service**: The Python service (GeoPandas, Shapely) that performs
  spatial analysis.
- **Scorer**: The component inside Geo_Service that computes candidate scores.
- **Data_Store**: The file-based or database storage holding GeoJSON /
  Shapefile datasets.
- **User**: Any authenticated human operator of the Frontend.
- **City**: An Indian urban area supported by the system (e.g., Bengaluru,
  Mumbai, Hyderabad).
- **Charger_Type**: One of `SLOW`, `FAST`, or `DC_FAST`.
- **Search_Radius**: A distance in metres within which candidates are
  evaluated (e.g., 500 m – 5000 m).
- **Candidate_Location**: A parking lot or open space identified as a
  potential EV charger installation site.
- **Score**: A normalised integer 0–100 representing the suitability of a
  Candidate_Location.
- **Layer**: A map overlay (e.g., existing chargers, petrol pumps, metro
  stations).
- **OSM**: OpenStreetMap — the primary open data source for roads, parking,
  and POIs.
- **OGD**: Open Government Data platform India — source for administrative
  boundaries and EV infrastructure data.
- **Spatial_Join**: A GeoPandas `sjoin` or `sjoin_nearest` operation that
  combines two GeoDataFrames by geometry relationship.
- **Score_Weight**: The percentage contribution of a single factor to the
  final Score (must sum to 100%).

---

## Requirements

### Requirement 1: City and Parameter Selection [MVP]

**User Story:** As an EV charging company analyst, I want to select a city,
charger type, and search radius before running analysis, so that I can scope
the results to my specific expansion plan.

#### Acceptance Criteria

1. THE Frontend SHALL display a selection panel containing a city dropdown,
   a charger type selector, and a numeric search radius input before any
   analysis is requested.
2. WHEN the User submits the selection panel, THE Frontend SHALL validate
   that a city is chosen from the supported list, a charger type of `SLOW`,
   `FAST`, or `DC_FAST` is selected, and a search radius integer between 250
   and 10 000 (inclusive) is entered, before sending a request to the
   API_Server.
3. IF the User submits the selection panel with an invalid or missing value,
   THEN THE Frontend SHALL display an inline validation message adjacent to
   the invalid field, identifying the field name and constraint violated,
   without navigating away from the selection panel.
4. THE Frontend SHALL provide city options for at least Bengaluru, Mumbai,
   Hyderabad, Chennai, and Pune in the MVP. **Note:** only Bengaluru requires
   fully populated datasets for MVP delivery; other cities may appear in the
   dropdown but rely on Requirement 3, criterion 6 to correctly report
   partial unavailability.
5. THE Frontend SHALL provide charger type options labelled `SLOW`, `FAST`,
   and `DC_FAST`.
6. WHEN the User changes the city selection, THE Frontend SHALL reset the
   search radius and charger type fields to their default values and clear any
   previously displayed recommendations or map layers from the prior session.

---

### Requirement 2: Map Display of Base Layers [MVP]

**User Story:** As an urban planner, I want to see existing infrastructure on
the map before running analysis, so that I can understand the current state of
the city before interpreting recommendations.

#### Acceptance Criteria

1. WHEN a City is selected, THE Frontend SHALL render a MapLibre map centred
   on that city at a zoom level between 11 and 13 (inclusive) within 2
   seconds of selection.
2. THE Frontend SHALL display the following seven base layers as independently
   toggleable overlays on the map: existing EV chargers, petrol pumps, major
   roads, parking lots, metro stations, shopping malls, and tech parks.
3. WHEN a base layer toggle is activated, THE Frontend SHALL fetch the
   corresponding GeoJSON from the API_Server and render all features on the
   map within 3 seconds of the toggle activation.
4. WHEN a base layer toggle is deactivated, THE Frontend SHALL remove all
   features of that layer from the map within 200 ms without requiring a full
   page reload.
5. THE Frontend SHALL visually distinguish each of the seven layers using a
   unique colour (expressed as a hex code in the layer configuration) and a
   unique icon or shape, so that each layer can be identified without reading
   a text label.
6. IF the API_Server returns an HTTP error or a network timeout for a layer
   request, THEN THE Frontend SHALL display a non-blocking toast notification
   identifying the layer name and the HTTP status code or "network error",
   while keeping all other visible layers rendered.

---

### Requirement 3: Charger and POI Data Ingestion [MVP]

**User Story:** As a system administrator, I want the system to ingest and
store geospatial datasets from OSM and OGD, so that analysis is grounded in
real, current data.

#### Acceptance Criteria

1. THE Data_Store SHALL contain GeoJSON datasets for the following feature
   types for each supported City: EV charging stations, fuel stations, parking
   areas, roads classified as motorway / trunk / primary / secondary, metro
   stations, shopping malls, tech parks, and administrative ward boundaries.
2. WHEN the Geo_Service loads a dataset at startup or on refresh, THE
   Geo_Service SHALL re-project all loaded GeoDataFrames to EPSG:32643
   (UTM Zone 43N) before making them available for any spatial operation.
3. IF a GeoJSON file is absent from the data directory or cannot be parsed,
   THEN THE Geo_Service SHALL log a structured error message at ERROR level
   containing the file path, the exception class name, and an ISO-8601
   timestamp, and SHALL return a 503 error response to the API_Server
   identifying the affected dataset.
4. THE Geo_Service SHALL expose a `GET /data-health` endpoint that returns a
   JSON object where each key is a dataset name and each value contains the
   record count and the ISO-8601 timestamp of the last successful load.
5. WHEN a GeoJSON file is loaded and then serialised back to GeoJSON and
   loaded again, the resulting GeoDataFrame SHALL have the same record count,
   geometry type, and coordinate values within a tolerance of 1×10⁻⁷ degrees
   for every feature.
6. IF a City is configured as supported but one or more of its required
   feature-type datasets are absent, THEN THE Geo_Service SHALL mark that
   City as partially unavailable in the `GET /data-health` response and SHALL
   return a 422 response to any recommendation or analysis request for that
   City, identifying the missing dataset names.

---

### Requirement 4: Recommendation API [MVP]

**User Story:** As an EV charging company analyst, I want to call a single
API endpoint with city, charger type, and radius parameters, so that I
receive a ranked list of candidate locations without having to orchestrate
multiple service calls myself.

#### Acceptance Criteria

1. THE API_Server SHALL expose a `POST /recommendation` endpoint that accepts
   a JSON body with fields `city` (non-empty string), `chargerType` (one of
   `SLOW`, `FAST`, `DC_FAST`), and `radius` (positive integer, metres,
   250–10 000 inclusive).
2. WHEN a valid `POST /recommendation` request is received (all fields present
   and within bounds), THE API_Server SHALL forward the request to the
   Geo_Service and return a `200 OK` response containing the ranked candidate
   list within 10 seconds.
3. IF the request body is missing a required field or contains a field value
   that violates type or range constraints (e.g., `radius` ≤ 0 or
   unrecognised `chargerType`), THEN THE API_Server SHALL return a
   `400 Bad Request` response with a JSON body containing an `errors` array
   where each entry names the invalid field and describes the violation.
4. IF the `city` field contains a value not in the supported city list, THEN
   THE API_Server SHALL return a `422 Unprocessable Entity` response with a
   JSON body listing the supported city names.
5. THE API_Server SHALL cache `POST /recommendation` responses keyed on the
   tuple `(city, chargerType, radius)` with a TTL of 5 minutes; cache hits
   SHALL be served without forwarding the request to the Geo_Service.
6. THE API_Server SHALL expose a `GET /cities` endpoint that returns a JSON
   array of objects, each containing `name` (string) and `boundingBox`
   (GeoJSON Polygon), for all supported cities.
7. THE API_Server SHALL expose a `GET /chargers?city={city}` endpoint that
   returns all known EV charger locations for the specified city as a GeoJSON
   FeatureCollection.
8. IF the Geo_Service is unreachable or returns an HTTP 5xx response to a
   forwarded recommendation request, THEN THE API_Server SHALL return a
   `503 Service Unavailable` response with a `Retry-After` header set to 30
   seconds and SHALL NOT cache the failure.
9. IF `GET /chargers` is called with a `city` value not in the supported city
   list, THEN THE API_Server SHALL return a `422 Unprocessable Entity`
   response listing the supported city names.

---

### Requirement 5: Spatial Scoring Algorithm [MVP]

**User Story:** As an EV charging company analyst, I want each candidate
location to receive a transparent, weighted score based on spatial attributes,
so that I can prioritise sites by their expected commercial and logistical
value.

#### Acceptance Criteria

1. THE Scorer SHALL evaluate each Candidate_Location using the following five
   weighted factors that sum to exactly 100%: population within 1 km radius
   (35%), distance from nearest existing EV charger — inverse score (25%),
   proximity to an arterial road of type motorway / trunk / primary (15%),
   parking lot availability at the candidate site (15%), and presence of a
   shopping mall within 500 m (10%).
2. THE Scorer SHALL compute each factor score on a 0–100 scale using the
   following fixed rules before applying weights: population factor =
   min(population_within_1km / 50 000, 1) × 100; charger distance factor =
   (distance_to_nearest_charger_m / Search_Radius) × 100 capped at 100, or
   100 if no charger is within Search_Radius; road factor = 100 if the
   nearest arterial road centroid is within 200 m, else 0; parking factor =
   100 if a parking lot polygon intersects the candidate point, else 0; mall
   factor = 100 if a shopping mall centroid is within 500 m, else 0. The
   final Score = round(sum of weight × factor_score for each factor) and
   SHALL be an integer between 0 and 100 inclusive.
3. WHEN computing the charger distance factor, THE Scorer SHALL use
   `GeoDataFrame.sjoin_nearest()` with `max_distance` equal to the
   Search_Radius, and SHALL assign a factor score of 100 to candidates where
   no charger is found within the Search_Radius.
4. WHEN computing the population factor, THE Scorer SHALL create a buffer of
   **1 km** (fixed) around each candidate using `GeoDataFrame.buffer()`,
   build a spatial index via `GeoDataFrame.sindex`, and intersect the buffer
   with population grid cells to sum the population within the buffer area.
   **Design note:** this buffer is intentionally fixed at 1 km regardless of
   the user-selected Search_Radius — population density near a site is a
   fixed-scale signal, whereas Search_Radius governs how far out to look for
   competing infrastructure (chargers, roads, malls). This asymmetry is
   deliberate and SHALL NOT be "corrected" to use Search_Radius.
5. WHEN scoring a batch of candidates, THE Scorer SHALL execute all layer
   intersections using `GeoDataFrame.sjoin()` on the full batch and SHALL NOT
   iterate row-by-row in Python for spatial predicates, so that a batch of up
   to 500 candidates completes within 5 seconds on reference hardware.
6. THE Scorer SHALL return, for each Candidate_Location, a result object
   containing: candidate geometry (Point), final Score (integer 0–100),
   individual factor scores (five integers 0–100), and nearest charger
   distance in metres (float, or `null` if no charger exists within
   Search_Radius).
7. WHEN the Scorer is called with the same candidate set, the same reference
   layer data, and the same Search_Radius, THE Scorer SHALL produce identical
   Score values for every candidate regardless of the order in which
   candidates appear in the input GeoDataFrame.
8. IF a required spatial layer (population grid, road network, parking areas,
   or shopping mall data) is absent or empty for the requested city, THEN THE
   Scorer SHALL assign a factor score of 0 for the affected factor for every
   candidate and SHALL include a `"warnings"` field in the result identifying
   which factor(s) used a zero-score fallback.

---

### Requirement 6: Ranked Candidate Display [MVP]

**User Story:** As an EV charging company analyst, I want to see ranked
candidate locations on the map and in a side panel, so that I can quickly
compare options and shortlist sites for field survey.

#### Acceptance Criteria

1. WHEN the API_Server returns a recommendation response, THE Frontend SHALL
   render candidate locations as Deck.gl `ScatterplotLayer` markers, coloured
   using a gradient where scores 0–33 are red (#FF0000), 34–66 are amber
   (#FFA500), and 67–100 are green (#00AA00), with a radius of 60 metres per
   marker.
2. THE Frontend SHALL display the top 50 ranked candidates by default and
   SHALL provide a slider with a step size of 10 to adjust the displayed count
   from 10 to 200.
3. WHEN a candidate marker is clicked, THE Frontend SHALL display a tooltip
   containing: rank (integer), final score (0–100), population within 1 km
   (integer), nearest charger distance in metres or "None" if null, road type
   (string), parking availability (Yes / No), and nearest mall distance in
   metres.
4. THE Frontend SHALL display a side-panel list of candidates sorted by
   descending final score by default, showing rank, address or Plus Code, and
   final score; the list SHALL be re-sortable by clicking column headers for
   rank, score, or address.
5. WHEN a candidate in the side-panel list is clicked, THE Frontend SHALL pan
   and zoom the map to that candidate's location at zoom level 15 and
   highlight its marker with a white border of 3 px.
6. THE Frontend SHALL provide a download button that exports a CSV file
   containing, for each of the top-N displayed candidates: rank, latitude,
   longitude, final score, population within 1 km, nearest charger distance,
   road type, parking availability flag, and nearest mall distance.

---

### Requirement 7: Analysis Endpoint and Geo Service Processing [MVP]

**User Story:** As a developer integrating with ChargeWise India, I want a
`GET /analysis` endpoint that returns spatial statistics for a city, so that
I can build dashboards and reports without re-implementing geospatial logic.

#### Acceptance Criteria

1. THE API_Server SHALL expose a `GET /analysis?city={city}&chargerType={type}`
   endpoint that returns a JSON object containing: total candidate count,
   score distribution (mean, median, and p90 computed on a 0–100 scale), and
   coverage percentage defined as the proportion of the city bounding polygon
   area (in km²) that contains at least one candidate feature within 500 m.
   **Computation method:** union all candidate buffers (radius 500 m) into a
   single polygon via `GeoDataFrame.union_all()` (or equivalent dissolve),
   intersect that union with the city bounding polygon, and divide the
   intersection area by the total bounding polygon area.
2. WHEN the Geo_Service receives an analysis request, THE Geo_Service SHALL
   aggregate statistics at the ward boundary level and compute area coverage
   by intersecting candidate buffers with the city bounding polygon before
   returning results.
3. WHEN the Geo_Service receives an analysis request, THE Geo_Service SHALL
   restrict all spatial computations to features within the city bounding
   polygon before performing any spatial joins.
4. THE Geo_Service SHALL return the complete analysis response, measured from
   receipt of the request to delivery of the complete response, within 15
   seconds for a city with up to 10 000 candidate features.
5. IF `GET /analysis` is called with an unsupported `city` or unrecognised
   `chargerType`, THEN THE API_Server SHALL return a `422 Unprocessable
   Entity` response with a JSON body describing the invalid parameter, and
   SHALL NOT return any partial statistics.

---

### Requirement 8: Authentication and Authorisation

#### 8A. MVP: API Key Authentication [MVP]

**User Story:** As an organisation administrator, I want API access to be
protected by a simple credential, so that recommendations and analysis data
are not publicly accessible during the MVP phase, without requiring a full
identity provider integration.

##### Acceptance Criteria

1. THE API_Server SHALL require a valid static API key, supplied via an
   `X-API-Key` header, on all endpoints except `GET /cities` and
   `GET /health`.
2. WHEN a request arrives on a protected endpoint without an `X-API-Key`
   header or with a key that does not match the configured value, THE
   API_Server SHALL return a `401 Unauthorized` response and SHALL NOT
   process the request body.
3. THE API_Server SHALL read the expected API key exclusively from the
   `API_KEY` environment variable and SHALL never log or echo the key value.
4. THE Frontend SHALL store the API key exclusively in JavaScript memory
   (not `localStorage`, `sessionStorage`, or cookies); WHEN a page reload
   occurs and no in-memory key is present, THE Frontend SHALL prompt the
   User to re-enter it before any protected request is made.

#### 8B. Stretch: JWKS-Based Bearer Token Authentication [Stretch]

**User Story:** As an organisation administrator, I want API access
protected by a standards-based identity provider, so that the system can
support multiple users and organisations with proper token lifecycle
management ahead of any production/multi-tenant use.

##### Acceptance Criteria

1. THE API_Server SHALL require a valid Bearer token on all endpoints except
   `GET /cities` and `GET /health`; tokens present on public endpoints SHALL
   be ignored.
2. WHEN a request arrives on a protected endpoint without a Bearer token or
   with a token that fails validation, THE API_Server SHALL return a
   `401 Unauthorized` response with a `WWW-Authenticate: Bearer` header and
   SHALL NOT process the request body.
3. THE API_Server SHALL validate Bearer tokens by verifying the cryptographic
   signature against the public keys retrieved from the JWKS endpoint
   configured via the `JWKS_URL` environment variable, checking that the
   token `exp` claim has not elapsed, and verifying that the `iss` and `aud`
   claims match the values in the `TOKEN_ISSUER` and `TOKEN_AUDIENCE`
   environment variables.
4. IF the JWKS endpoint is unreachable at startup or during a token
   validation attempt, THEN THE API_Server SHALL use a locally cached JWKS
   response (valid for up to 300 seconds) and SHALL return a
   `503 Service Unavailable` response with a log entry at ERROR level if the
   cache has also expired.
5. THE Frontend SHALL store session tokens exclusively in JavaScript memory
   (not `localStorage`, `sessionStorage`, or cookies); WHEN a page reload
   occurs and no in-memory token is present, THE Frontend SHALL redirect the
   User to the authentication entry point.

---

### Requirement 9: Performance and Reliability

#### 9A. MVP: Core Performance and Health [MVP]

**User Story:** As an EV charging company analyst, I want the application to
respond quickly and remain available during peak use, so that planning
sessions are not interrupted by slow or failed responses.

##### Acceptance Criteria

1. THE API_Server SHALL respond to `GET /cities` requests within 500 ms at
   the 95th percentile under a sustained load of 50 concurrent users.
2. THE API_Server SHALL respond to `GET /chargers?city={city}` requests
   within 500 ms at the 95th percentile under a sustained load of 50
   concurrent users.
3. THE API_Server SHALL respond to `POST /recommendation` requests that
   result in a cache hit — defined as a stored response for the same
   `(city, chargerType, radius)` tuple that has not yet expired — within
   200 ms at the 95th percentile under a sustained load of 50 concurrent
   users.
4. IF the Geo_Service returns an HTTP 5xx response or does not respond within
   3 seconds, THE API_Server SHALL return a `503 Service Unavailable`
   response with a `Retry-After` header value between 1 and 60 seconds
   (inclusive). **This synchronous timeout-and-503 behaviour is the MVP
   contract for all `POST /recommendation` requests** — see 9B for the
   async alternative deferred to Stretch.
5. THE API_Server SHALL expose a `GET /health` endpoint that returns HTTP 200
   with a JSON body listing each upstream dependency and its reachability
   status (reachable / unreachable) when all dependencies are reachable and
   responding within their expected SLA. Expected SLA per dependency:
   Geo_Service reachable and responding to `GET /data-health` within 2
   seconds; JWKS/auth provider (Stretch only) reachable within 2 seconds.
6. IF one or more upstream dependencies are unreachable or exceed their
   expected SLA (as defined in criterion 5), THEN `GET /health` SHALL return
   HTTP 503 with a JSON body identifying each degraded dependency by name.

#### 9B. Stretch: Asynchronous Recommendation Processing [Stretch]

**User Story:** As an EV charging company analyst, I want long-running
recommendation requests to be handled asynchronously, so that the client
never blocks or times out on a slow analysis for a very large city or radius.

##### Acceptance Criteria

1. WHEN a forwarded recommendation request has not returned from the
   Geo_Service within 3 seconds, THE API_Server SHALL return a `202 Accepted`
   response containing a `jobId` (UUID string) and expose a
   `GET /recommendation/{jobId}` polling endpoint that returns the job status
   as one of `pending`, `complete`, or `failed`.
2. THE API_Server SHALL persist job state (status, result, timestamps) in a
   store that survives an API_Server process restart before this requirement
   is considered complete; an in-memory-only job store does not satisfy this
   criterion.

---

### Requirement 10: Data Parsing and Round-Trip Integrity [MVP]

**User Story:** As a data engineer, I want all geospatial data I/O to be
validated for round-trip correctness, so that no location data is silently
corrupted during serialisation or deserialisation.

#### Acceptance Criteria

1. WHEN the Geo_Service receives a spatial dataset, THE Geo_Service SHALL
   parse it as a GeoJSON FeatureCollection before any spatial processing.
2. WHEN the Geo_Service serialises a GeoDataFrame to a response or file, THE
   Geo_Service SHALL output a valid GeoJSON FeatureCollection using the same
   CRS as the original input.
3. WHEN a valid GeoJSON FeatureCollection containing Point, LineString, or
   Polygon geometries is parsed and then serialised and then parsed again, the
   resulting GeoDataFrame SHALL have the same record count, the same geometry
   types, and coordinate values within a tolerance of 1×10⁻⁷ degrees for
   every feature in every row.
4. WHEN a `POST /validate` request is received with a GeoJSON body not
   exceeding 50 MB, THE Geo_Service SHALL return a JSON object containing:
   record count (integer), CRS as an EPSG authority string, geometry types
   present (array of strings), and a `validationErrors` array where each
   entry contains zero-based `featureIndex` (integer) and `message` (string)
   for each detected error (self-intersection, null geometry, or coordinate
   out of WGS-84 bounds).
5. IF a `POST /validate` request body is not a valid JSON document or is not
   a GeoJSON FeatureCollection, THEN THE Geo_Service SHALL return a
   `400 Bad Request` response with a JSON body containing a human-readable
   `message` describing the parse failure, without raising an unhandled
   exception.
6. IF a geometry in the input passes JSON parsing but fails geometric validity
   checks (self-intersection, null geometry, or coordinates outside WGS-84
   bounds), THEN THE Geo_Service SHALL include the zero-based feature index
   and an error message indicating the nature of the invalidity in the
   `validationErrors` array, without raising an unhandled exception.

---

### Requirement 11: Logging, Observability, and Error Reporting [MVP]

**User Story:** As a platform engineer, I want structured logs and error
traces from all services, so that I can diagnose production incidents without
SSH-ing into containers.

#### Acceptance Criteria

1. WHEN the API_Server receives an inbound HTTP request and sends a response,
   THE API_Server SHALL write a structured JSON log entry to standard output
   containing: timestamp (ISO-8601), HTTP method, path, status code, latency
   in milliseconds, and correlation ID.
2. WHEN the Geo_Service executes a spatial operation, THE Geo_Service SHALL
   write a structured JSON log entry to standard output containing: operation
   name, input record count, output record count, duration in milliseconds,
   and correlation ID. IF no correlation ID was provided in the inbound
   request, THE Geo_Service SHALL log `"MISSING"` as the correlation ID value.
3. IF an unhandled exception occurs in the API_Server or Geo_Service, THEN
   THE affected service SHALL write the full stack trace at ERROR level with
   the correlation ID to standard output before returning an error response.
4. THE API_Server SHALL propagate the correlation ID as an `X-Correlation-ID`
   HTTP header to the Geo_Service on every proxied request.
5. WHEN the API_Server receives an inbound request that does not contain an
   `X-Correlation-ID` header, THE API_Server SHALL generate a new UUID v4
   value and use it as the correlation ID for that request. WHEN an inbound
   `X-Correlation-ID` header is present and between 1 and 128 characters
   (non-empty), THE API_Server SHALL reuse the provided value as the
   correlation ID.

---

### Requirement 12: Configuration and Environment Management [MVP]

**User Story:** As a DevOps engineer, I want all service configuration to be
externalisable through environment variables, so that the same container
images can be deployed across local, staging, and production environments
without code changes.

#### Acceptance Criteria

1. THE API_Server SHALL read the following configuration values exclusively
   from environment variables: Geo_Service base URL (`GEO_SERVICE_URL`),
   static API key (`API_KEY`, see Requirement 8A), cache TTL in seconds
   (`CACHE_TTL_SECONDS`, integer 1–86400), and allowed CORS origins
   (`CORS_ORIGINS`, comma-separated list of absolute URLs or the wildcard
   `*`). **Stretch-only:** `JWKS_URL`, `TOKEN_ISSUER`, `TOKEN_AUDIENCE`
   (Requirement 8B).
2. THE Geo_Service SHALL read the following configuration values exclusively
   from environment variables: data directory path (`DATA_DIR`), default CRS
   EPSG code (`DEFAULT_CRS_EPSG`, integer), and log level (`LOG_LEVEL`, one
   of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
3. THE Frontend SHALL read the following configuration values exclusively
   from build-time environment variables: API_Server base URL
   (`VITE_API_URL`) and the map style/tile source URL (`VITE_MAP_STYLE_URL`).
4. IF a required environment variable is absent at startup or is set to an
   empty string, THEN THE affected service SHALL log the missing variable name
   at ERROR level and SHALL exit with a non-zero exit code within 5 seconds
   of startup. For the Frontend, this SHALL instead render a build-time
   error preventing a broken bundle from being deployed.
5. THE API_Server, Geo_Service, and Frontend SHALL each provide a
   `.env.example` file listing every required and optional environment
   variable with a description, accepted values or format, and an example
   value.

---

### Requirement 13: Testing Requirements [MVP]

**User Story:** As a developer, I want the Scorer and spatial join logic
covered by tests against synthetic data, so that correctness bugs — which
are often silent in geospatial code (e.g. a CRS mismatch producing a
plausible-looking but wrong distance) — are caught before they reach real
Bengaluru-scale data.

#### Acceptance Criteria

1. THE Geo_Service SHALL include unit tests for every Scorer factor function
   (population, charger distance, road, parking, mall) using synthetic
   GeoDataFrames of no more than 10 features each, covering at minimum: a
   normal case, a case with zero matching features (triggering the
   Requirement 5, criterion 8 fallback), and a boundary case at the exact
   Search_Radius distance.
2. THE Geo_Service SHALL include a unit test asserting that
   `GeoDataFrame.crs` is EPSG:32643 immediately after any layer load
   function returns, per Requirement 3, criterion 2.
3. THE Geo_Service SHALL include a round-trip test implementing Requirement
   10, criterion 3 using a synthetic FeatureCollection with at least one
   Point, one LineString, and one Polygon feature.
4. THE API_Server SHALL include tests for `POST /recommendation` covering:
   a valid request, a request with an out-of-range `radius`, an unsupported
   `city`, and a simulated Geo_Service 5xx/timeout triggering the
   Requirement 9A, criterion 4 fallback.