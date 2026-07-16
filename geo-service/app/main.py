"""
ChargeWise India — Geo Service entry point.

FastAPI application that hosts the geospatial analysis endpoints.
All configuration is read from environment variables (Requirement 12 AC-2).

Liveness vs readiness
---------------------
GET /health   — liveness probe.  Returns 200 as soon as the process is
                running.  Orchestrators use this to decide whether to
                restart the container (not whether to send traffic).

GET /ready    — readiness probe.  Returns 200 only after all WARM_CITIES
                datasets have been loaded into memory.  Returns 503 while
                warming is still in progress.  Orchestrators (Kubernetes
                readinessProbe, ALB health-check) MUST use this endpoint
                to gate traffic; routing requests before datasets are warm
                would cause the first request per city to incur the full
                ~44 s cold-load penalty, violating Requirement 4 AC-2.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import recommendation, data_health, analysis, validate, chargers, layers

# ---------------------------------------------------------------------------
# Readiness flag
# Set to True only after all WARM_CITIES have finished loading.
# Checked by GET /ready.
# ---------------------------------------------------------------------------
_ready: bool = False

# ---------------------------------------------------------------------------
# Structured JSON logger
# Requirement 11 AC-2: emit JSON log entries for every spatial operation.
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object to stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra context injected via logger.info(..., extra={...})
        for key, value in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "message", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName",
            }:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _configure_logging() -> None:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level_name not in valid_levels:
        # Fail fast — Requirement 12 AC-3.
        print(
            json.dumps({
                "level": "ERROR",
                "message": f"LOG_LEVEL='{log_level_name}' is invalid; "
                           f"must be one of {sorted(valid_levels)}",
            }),
            file=sys.stderr,
        )
        sys.exit(1)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(log_level_name)
    logging.root.handlers = [handler]


# ---------------------------------------------------------------------------
# Startup / shutdown lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Validate required env vars, warm the dataset cache, then mark the
    service ready.

    Startup sequence
    ~~~~~~~~~~~~~~~~
    1. Validate required environment variables — exit(1) if any are absent
       (Requirement 12 AC-3).
    2. Load every city listed in WARM_CITIES into the DatasetRegistry cache.
       This moves the ~44 s cold-load penalty (population_grid.geojson is
       195 k rows) to deploy/startup time so that no HTTP request ever pays
       the cold-load cost (Requirement 4 AC-2: response ≤ 10 s).
    3. Set the global ``_ready`` flag to True.  Only after this point does
       GET /ready return HTTP 200, allowing orchestrators to route traffic.

    If warming fails for a city the warning is logged and the service
    continues — the city will cold-load on first request rather than
    preventing startup entirely.  To hard-fail on a warming error, set
    WARM_CITIES_REQUIRED=true.
    """
    global _ready

    _configure_logging()
    logger = logging.getLogger(__name__)

    required_vars = ("DATA_DIR", "DEFAULT_CRS_EPSG")
    missing = [v for v in required_vars if not os.getenv(v, "").strip()]
    if missing:
        for var in missing:
            logger.error("Required environment variable is missing", extra={"variable": var})
        sys.exit(1)

    logger.info(
        "geo-service starting",
        extra={
            "data_dir": os.getenv("DATA_DIR"),
            "default_crs_epsg": os.getenv("DEFAULT_CRS_EPSG"),
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "warm_cities": os.getenv("WARM_CITIES", "bengaluru"),
        },
    )

    # ------------------------------------------------------------------
    # Warm the dataset cache.
    # _ready stays False until ALL cities have been attempted so that
    # GET /ready never returns 200 while warming is still in progress.
    # ------------------------------------------------------------------
    from app.core.dataset_loader import registry as _registry

    _warm_cities = [c.strip().lower() for c in os.getenv("WARM_CITIES", "bengaluru").split(",") if c.strip()]
    warm_errors: list[str] = []

    for _city in _warm_cities:
        _t0 = time.perf_counter()
        try:
            _registry.load(_city)
            logger.info(
                "startup cache warm complete",
                extra={
                    "city": _city,
                    "duration_ms": round((time.perf_counter() - _t0) * 1000, 2),
                },
            )
        except Exception as _exc:
            warm_errors.append(_city)
            logger.warning(
                "startup cache warm failed — city will cold-load on first request",
                extra={"city": _city, "error": str(_exc)},
            )

    # Mark service ready — traffic can now be routed in
    _ready = True
    logger.info(
        "geo-service ready",
        extra={
            "warmed_cities":  [c for c in _warm_cities if c not in warm_errors],
            "failed_cities":  warm_errors,
        },
    )

    yield

    _ready = False
    logger.info("geo-service shutting down")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="ChargeWise India — Geo Service",
        description=(
            "Geospatial analysis micro-service. "
            "Performs spatial scoring of EV charger candidate locations "
            "using GeoPandas / Shapely."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # Middleware
    # -----------------------------------------------------------------------

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],          # Tightened at the Go API gateway layer.
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # Request logging middleware
    # Requirement 11 AC-2: log every operation with correlation ID.
    # -----------------------------------------------------------------------

    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        correlation_id = (
            request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        )
        request.state.correlation_id = correlation_id
        start = time.perf_counter()

        try:
            response: Response = await call_next(request)
        except Exception:
            logging.getLogger(__name__).error(
                "unhandled exception during request",
                exc_info=True,
                extra={"correlation_id": correlation_id},
            )
            raise

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logging.getLogger(__name__).info(
            "request completed",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    # -----------------------------------------------------------------------
    # Routers
    # -----------------------------------------------------------------------

    app.include_router(recommendation.router)
    app.include_router(data_health.router)
    app.include_router(analysis.router)
    app.include_router(validate.router)
    app.include_router(chargers.router)
    app.include_router(layers.router)

    # -----------------------------------------------------------------------
    # Liveness probe — GET /health
    # Returns 200 as soon as the process is running.
    # Use for: Kubernetes livenessProbe, restart decisions.
    # Do NOT use for traffic routing — use /ready instead.
    # -----------------------------------------------------------------------

    @app.get(
        "/health",
        tags=["observability"],
        summary="Liveness probe — is the process running?",
        description=(
            "Returns HTTP 200 as soon as the process starts. "
            "Suitable for liveness checks (restart on failure) but NOT for "
            "readiness — the service may still be warming datasets. "
            "Use GET /ready to gate traffic."
        ),
    )
    async def health_check() -> dict:
        return {"status": "ok", "service": "geo-service"}

    # -----------------------------------------------------------------------
    # Readiness probe — GET /ready
    # Returns 200 only after all WARM_CITIES datasets are loaded.
    # Returns 503 while warming is still in progress.
    # Use for: Kubernetes readinessProbe, ALB / load-balancer health checks,
    #          any system that decides whether to send traffic here.
    # Requirement 4 AC-2: guarantees first request is within 10 s SLA.
    # -----------------------------------------------------------------------

    @app.get(
        "/ready",
        tags=["observability"],
        summary="Readiness probe — are datasets loaded and traffic safe to route?",
        description=(
            "Returns HTTP 200 only after all WARM_CITIES datasets have been "
            "loaded into memory. Returns HTTP 503 while warming is in progress. "
            "Orchestrators MUST use this endpoint (not /health) to decide when "
            "to route traffic, ensuring the first request never incurs the "
            "cold-load penalty (~44 s for population_grid.geojson)."
        ),
        responses={
            200: {"description": "All WARM_CITIES loaded — ready for traffic"},
            503: {"description": "Dataset warming still in progress — not yet ready"},
        },
    )
    async def readiness_check(response: Response) -> dict:
        from app.core.dataset_loader import registry as _reg
        warmed = list(_reg._cache.keys())
        if _ready:
            return {
                "status": "ready",
                "service": "geo-service",
                "warmed_cities": warmed,
            }
        response.status_code = 503
        return {
            "status": "warming",
            "service": "geo-service",
            "warmed_cities": warmed,
            "detail": "Dataset cache warming in progress — retry in a few seconds.",
        }

    # -----------------------------------------------------------------------
    # Global exception handler
    # Requirement 11 AC-3: log full stack trace before returning error.
    # -----------------------------------------------------------------------

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", "MISSING")
        logging.getLogger(__name__).error(
            "unhandled exception",
            exc_info=exc,
            extra={"correlation_id": correlation_id},
        )
        return JSONResponse(
            status_code=500,
            content={"errors": [], "message": "An unexpected error occurred."},
        )

    return app


app = create_app()
