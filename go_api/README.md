# go_api — ChargeWise API Gateway

A lightweight Go HTTP gateway that sits in front of the Python `geo-service`,
handling authentication, caching, CORS, request validation, and structured
logging for the ChargeWise EV charging network API.

## Architecture

```text
Client
  │
  │  HTTP/JSON
  ▼
go_api (this service)
  ├── correlationID middleware  — attaches / generates X-Correlation-ID
  ├── logger middleware         — structured JSON access log per request
  ├── recovery middleware       — catches panics, returns 500 JSON
  ├── CORS middleware           — configurable origin allow-list
  ├── auth middleware           — X-API-Key header validation
  └── route handlers
        ├── /health             — liveness + geo-service dependency probe
        ├── /cities             — static city registry (public)
        ├── /chargers           — EV charger list proxy (protected)
        ├── /recommendation     — charger recommendation proxy + cache (protected)
        └── /analysis           — coverage analysis proxy (protected)
              │
              │  HTTP/JSON (proxied)
              ▼
          geo-service (Python / FastAPI)
```

## Project Structure

```text
go_api/
├── cmd/
│   └── server/
│       └── main.go           # Entry point: config, middleware chain, routes,
│                             # graceful shutdown
├── internal/
│   ├── auth/
│   │   └── apikey.go         # X-API-Key middleware (constant-time compare)
│   ├── cache/
│   │   └── memory.go         # TTL in-memory cache with background eviction
│   ├── config/
│   │   └── config.go         # Environment variable loading + validation
│   ├── handlers/
│   │   ├── cities_registry.go # Canonical city list shared across handlers
│   │   ├── cities.go         # GET /cities
│   │   ├── chargers.go       # GET /chargers
│   │   ├── recommendation.go # POST /recommendation (cache-aside)
│   │   ├── analysis.go       # GET /analysis
│   │   └── health.go         # GET /health
│   ├── middleware/
│   │   ├── cors.go           # CORS headers + pre-flight handling
│   │   └── recover.go        # Panic recovery → 500 JSON
│   └── proxy/
│       └── geoservice.go     # HTTP client for geo-service; hop-by-hop
│                             # header filtering; 503 helper
├── go.mod
├── go.sum
└── README.md
```

## Prerequisites

- Go 1.22+
- A running instance of the `geo-service` (see `../geo-service/README.md`)

## Configuration

All configuration is read from environment variables at startup. The process
exits immediately with a fatal log if a required variable is absent or invalid.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEO_SERVICE_URL` | ✅ | — | Base URL of the geo-service, e.g. `http://localhost:8000` |
| `API_KEY` | ✅ | — | Secret key clients must send in `X-API-Key` |
| `PORT` | | `8080` | Port the gateway listens on |
| `CACHE_TTL_SECONDS` | | `300` | Recommendation cache TTL (1–86400 s) |
| `CORS_ORIGINS` | | `*` | Comma-separated allowed origins, e.g. `https://app.example.com` |
| `GEO_SERVICE_TIMEOUT_SECONDS` | | `3` | Per-request timeout for upstream calls |

## Running Locally

```bash
# From the go_api directory
export GEO_SERVICE_URL=http://localhost:8000
export API_KEY=dev-secret

go run ./cmd/server
```

The server logs a JSON startup line and begins accepting requests on `:8080`.

## Building

```bash
go build -o chargewise-api ./cmd/server
./chargewise-api
```

## Testing

```bash
go test ./...
```

Unit tests live alongside source files (e.g. `auth/apikey_test.go`,
`cache/memory_test.go`, `middleware/cors_test.go`).

## API Reference

### Authentication

Endpoints marked **protected** require the `X-API-Key` header:

```text
X-API-Key: <your-api-key>
```

Requests without a valid key receive `401 Unauthorized`.

### Common Response Headers

| Header | Description |
|---|---|
| `X-Correlation-ID` | Echoes the request ID (generated if absent) |
| `X-Cache` | `HIT` or `MISS` on `/recommendation` responses |
| `Retry-After` | Included on `503` responses (value: `30`) |

---

### `GET /health`

Liveness and dependency probe. Public — no API key required.

Checks whether the geo-service `/data-health` endpoint responds within 2 s.

**200 OK** — all dependencies reachable:

```json
{
  "status": "ok",
  "dependencies": { "geo_service": "reachable" }
}
```

**503 Service Unavailable** — geo-service unreachable:

```json
{
  "status": "degraded",
  "dependencies": { "geo_service": "unreachable" }
}
```

---

### `GET /cities`

Returns the static list of supported cities. Public — no API key required.

**200 OK:**

```json
[
  { "name": "Bengaluru" },
  { "name": "Mumbai" },
  { "name": "Hyderabad" },
  { "name": "Chennai" },
  { "name": "Pune" }
]
```

---

### `GET /chargers?city=<city>`

Returns existing EV charger locations for the given city. Protected.

City names are normalised (title-cased) before being forwarded upstream, so
`bengaluru`, `BENGALURU`, and `Bengaluru` are all accepted.

**Query parameters:**

| Parameter | Required | Description |
|---|---|---|
| `city` | ✅ | City name (see `/cities` for valid values) |

**200 OK** — proxied response from geo-service.

**422 Unprocessable Entity** — missing or unsupported city:

```json
{
  "message": "City not supported.",
  "supported": ["Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"]
}
```

**503 Service Unavailable** — geo-service error or timeout.

---

### `POST /recommendation`

Returns recommended EV charger locations for a city. Protected.

Uses a TTL in-memory cache (keyed on `city + chargerType + radius`). Cached
responses include `X-Cache: HIT`; upstream calls include `X-Cache: MISS`.

**Request body:**

```json
{
  "city": "Bengaluru",
  "chargerType": "FAST",
  "radius": 1000
}
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `city` | string | ✅ | Must be a supported city (case-insensitive) |
| `chargerType` | string | ✅ | One of `SLOW`, `FAST`, `DC_FAST` |
| `radius` | integer | ✅ | 250–10000 metres |

**200 OK** — recommendation payload from geo-service.

**400 Bad Request** — field validation failure:

```json
{
  "errors": [
    { "field": "radius", "message": "radius must be between 250 and 10000 metres" }
  ]
}
```

**422 Unprocessable Entity** — unsupported city:

```json
{
  "message": "City not supported.",
  "supported": ["Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"]
}
```

**503 Service Unavailable** — geo-service error or timeout.

---

### `GET /analysis?city=<city>&chargerType=<type>`

Returns coverage analysis for a city and charger type. Protected.

**Query parameters:**

| Parameter | Required | Description |
|---|---|---|
| `city` | ✅ | Supported city name (case-insensitive) |
| `chargerType` | ✅ | One of `SLOW`, `FAST`, `DC_FAST` |

**200 OK** — analysis payload from geo-service.

**422 Unprocessable Entity** — unsupported city or charger type:

```json
{
  "message": "City or charger type not supported.",
  "supported": ["Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"]
}
```

**503 Service Unavailable** — geo-service error or timeout.

---

## Middleware Chain

Requests traverse the middleware stack in the following order:

```text
correlationID → logger → recovery → CORS → auth → router
```

- **correlationID** — reads `X-Correlation-ID` from the request header (or
  generates a UUID) and propagates it on the response and context.
- **logger** — emits a structured JSON log line per request with method, path,
  status code, latency, and correlation ID.
- **recovery** — catches any downstream panic, logs the stack trace, and
  returns `500 Internal Server Error` without crashing the process.
- **CORS** — sets `Access-Control-Allow-*` headers; short-circuits
  `OPTIONS` pre-flight requests with `204 No Content`.
- **auth** — validates `X-API-Key` using a constant-time comparison.
  `/health` and `/cities` bypass this check.

## Caching

The `/recommendation` endpoint uses a goroutine-safe, TTL-based in-memory
cache. The cache key is the normalised triple `(city, chargerType, radius)`.
A background goroutine sweeps expired entries every 60 seconds.

Cache TTL is controlled by `CACHE_TTL_SECONDS` (default 5 minutes).

## Server Timeouts

| Timeout | Value | Purpose |
|---|---|---|
| `ReadHeaderTimeout` | 5 s | Closes Slowloris-style connections early |
| `ReadTimeout` | 10 s | Full request read (headers + body) |
| `WriteTimeout` | 15 s | Full response write |
| `IdleTimeout` | 60 s | Keep-alive connection idle limit |

## Graceful Shutdown

On `SIGINT` or `SIGTERM` the server stops accepting new connections and waits
up to 10 seconds for in-flight requests to complete before exiting cleanly.
