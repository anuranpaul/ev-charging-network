# EV-Charging network

AI-assisted geospatial planning tool for EV charging infrastructure in India.
Given a city, charger type, and search radius, it overlays existing
infrastructure with demand signals (population density, roads, parking, malls,
metro stations, tech parks) and returns a ranked list of candidate locations
using a weighted spatial scoring algorithm.

---

## System Architecture

```text
Browser
  │  HTTPS  X-API-Key
  ▼
frontend/          React + MapLibre GL + Deck.gl (Vite)
  │  HTTPS  X-API-Key
  ▼
go_api/            Go HTTP gateway  (:8080)
  │  auth · cache · CORS · logging · correlation ID
  │  HTTP  X-Correlation-ID
  ▼
geo-service/       Python / FastAPI  (:8000 or :8001)
  │  dataset loading · spatial scoring · GeoJSON validation
  │  filesystem read
  ▼
data/              GeoJSON files per city ($DATA_DIR/{city}/)
```

## Repository Layout

```text
chargewise/
├── geo-service/   Python/FastAPI geospatial micro-service
├── go_api/        Go HTTP API gateway
├── frontend/      React + MapLibre + Deck.gl web client  ⚠ not yet started
└── README.md      (this file)
```

---

## Services

### geo-service — Python / FastAPI

Handles all geospatial computation. Loads nine GeoJSON layers per city,
reprojects them to EPSG:32643 (UTM Zone 43N) at startup, then scores EV
charger candidate locations across five spatial factors using GeoPandas
vectorised batch operations.

**Status: implemented and tested**

Implemented endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/recommendation` | Full scoring pipeline → ranked GeoJSON |
| `GET` | `/analysis` | Score distribution + ward stats + coverage % |
| `GET` | `/chargers` | Existing EV charger locations for a city |
| `GET` | `/data-health` | Per-dataset record counts + load timestamps |
| `POST` | `/validate` | GeoJSON FeatureCollection linter (up to 50 MB) |
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe (datasets warmed?) |

Scoring factors and weights:

| Factor | Weight |
|---|---|
| Population density within 1 km | 35% |
| Distance from nearest existing charger | 25% |
| Arterial road proximity (≤ 200 m) | 15% |
| Parking lot availability | 15% |
| Shopping mall proximity (≤ 500 m) | 10% |

Supported cities: Bengaluru (fully populated), Mumbai, Hyderabad, Chennai,
Pune (datasets may be partial for non-Bengaluru cities).

See [`geo-service/README.md`](geo-service/README.md) for setup, data
pipeline, configuration, and API details.

---

### go_api — Go HTTP Gateway

Thin gateway in front of `geo-service`. Handles authentication, CORS,
in-memory caching, request routing, correlation ID propagation, and
structured access logging.

**Status: implemented and tested**

Implemented endpoints:

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | public | Liveness + geo-service dependency probe |
| `GET` | `/cities` | public | Static city registry |
| `GET` | `/chargers?city=` | `X-API-Key` | Proxy to geo-service |
| `POST` | `/recommendation` | `X-API-Key` | Proxy + TTL cache |
| `GET` | `/analysis?city=&chargerType=` | `X-API-Key` | Proxy to geo-service |

Key behaviours:

- **API key auth** — `X-API-Key` header validated with constant-time
  comparison; `/health` and `/cities` are public.
- **In-memory cache** — `POST /recommendation` responses cached for 5 min
  keyed on `(city, chargerType, radius)`. Responds with `X-Cache: HIT/MISS`.
- **503 circuit breaker** — geo-service timeout at 3 s; returns
  `503 Service Unavailable` with `Retry-After: 30` on timeout or 5xx.
- **Correlation IDs** — generates a UUID v4 `X-Correlation-ID` if absent,
  echoes it on the response, and propagates it to the geo-service.
- **CORS** — configurable allow-list via `CORS_ORIGINS` env var.
- **Graceful shutdown** — drains in-flight requests on `SIGINT`/`SIGTERM`.

See [`go_api/README.md`](go_api/README.md) for setup, configuration, and
API details.

---

### frontend — React / Vite

Web client for the planner UI. Not yet implemented.

**Status: not started**

Planned features (per spec):

- City, charger type, and search radius selection panel with inline
  validation.
- MapLibre GL JS base map centred on the selected city.
- Seven independently toggleable map overlays (EV chargers, petrol pumps,
  roads, parking, metro stations, malls, tech parks).
- Deck.gl `ScatterplotLayer` for ranked candidate markers, coloured by
  score band (red / amber / green).
- Side panel with sortable candidate list (rank, address, score).
- Candidate tooltip showing rank, score, and all five factor detail values.
- CSV export of the top-N displayed candidates.
- In-memory API key gate (no `localStorage`, no cookies).

See [`frontend/README.md`](frontend/README.md) — not yet written.

---

## Running Locally

### 1. Start the geo-service

```bash
cd geo-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATA_DIR and DEFAULT_CRS_EPSG
uvicorn app.main:app --reload --port 8001
```

The service is ready when `GET /ready` returns `200`. Interactive docs at
`http://localhost:8001/docs`.

### 2. Start the Go API gateway

```bash
cd go_api
export GEO_SERVICE_URL=http://localhost:8001
export API_KEY=dev-secret
go run ./cmd/server
```

The gateway starts on `:8080`. Test it:

```bash
curl http://localhost:8080/health
curl -H "X-API-Key: dev-secret" \
     "http://localhost:8080/chargers?city=Bengaluru"
```

### 3. Frontend

Not yet implemented. Once built, it will be configured via:

```
VITE_API_URL=http://localhost:8080
VITE_MAP_STYLE_URL=<your MapLibre tile style URL>
```

---

## What's Done

- **geo-service** — all seven API endpoints implemented and tested,
  including the full five-factor spatial scoring pipeline, startup dataset
  warming with a readiness probe, structured JSON logging, correlation ID
  propagation, and a GeoJSON FeatureCollection validator.
- **geo-service data pipeline** — five one-time scripts to fetch OSM data,
  prepare ward boundaries, compute the city bounding box, split OSM layers,
  and build the population grid from WorldPop raster data. Bengaluru
  datasets are present and fully loaded.
- **go_api** — all five API endpoints implemented and tested, including API
  key auth, in-memory TTL cache, 503 circuit breaker, CORS middleware, panic
  recovery, correlation ID middleware, structured JSON access log, and
  graceful shutdown.

---

## What's Remaining

The backend is functionally complete end-to-end. The remaining work is
primarily the frontend and production hardening:

### Frontend (not started)

- [ ] Scaffold React + Vite + TypeScript project.
- [ ] MapLibre GL JS base map — city-centred at zoom 11–13.
- [ ] Selection panel — city dropdown, charger type, radius with validation.
- [ ] Seven toggleable base layer overlays via `useLayerData` hook.
- [ ] Deck.gl `ScatterplotLayer` for ranked candidates (red/amber/green).
- [ ] Side panel — sortable candidate list (rank / score / address).
- [ ] Candidate click tooltip — all five factor detail fields.
- [ ] CSV export of top-N displayed candidates.
- [ ] In-memory API key gate on page load.
- [ ] Toast notifications for layer fetch errors.
- [ ] `VITE_API_URL` / `VITE_MAP_STYLE_URL` env vars + `.env.example`.

### Stretch goals (spec'd but deferred)

- [ ] **JWKS / Bearer token auth** (Req 8B) — replace static API key with
  OIDC `Authorization: Bearer` validation against a JWKS endpoint.
- [ ] **Async job polling** (Req 9B) — return `202 Accepted` with a `jobId`
  when the geo-service takes > 3 s; expose `GET /recommendation/{jobId}`.
  Requires a persistent job store (Redis or similar).

### Data and ops

- [ ] Populate datasets for Mumbai, Hyderabad, Chennai, and Pune (currently
  only Bengaluru has full data).
- [ ] Docker Compose setup for local multi-service development.
- [ ] Production deployment configuration (container images, env var
  injection, Kubernetes manifests or equivalent).
- [ ] Load testing to verify `GET /cities` p95 < 500 ms and cache-hit
  `POST /recommendation` p95 < 200 ms under 50 concurrent users (Req 9A).
- [ ] `GET /cities` `boundingBox` field — the Go API currently omits the
  GeoJSON Polygon bounding box from the city registry response (Req 4 AC-6).

---

## Spec and Design

Full requirements and design documents are in
[`.kiro/specs/ev-charging/`](.kiro/specs/ev-charging/):

- [`requirements.md`](.kiro/specs/ev-charging/requirements.md) — all
  acceptance criteria, tagged `[MVP]` or `[Stretch]`.
- [`design.md`](.kiro/specs/ev-charging/design.md) — architecture,
  component interfaces, scoring algorithm, caching strategy, and frontend
  component tree.
