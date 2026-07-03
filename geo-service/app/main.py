"""
ChargeWise India — Geo Service entry point.

FastAPI application that hosts the geospatial analysis endpoints.
All configuration is read from environment variables (Requirement 12 AC-2).
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

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import recommendation

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
    Validate required environment variables at startup.
    Requirement 12 AC-3: exit with non-zero code if any required var is absent.
    """
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
        },
    )
    yield
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

    # -----------------------------------------------------------------------
    # Health check — GET /health
    # Requirement 9 AC-6: return HTTP 200 when all dependencies are reachable.
    # (Stub: no upstream deps in the geo-service itself.)
    # -----------------------------------------------------------------------

    @app.get(
        "/health",
        tags=["observability"],
        summary="Service health check",
    )
    async def health_check() -> dict:
        return {"status": "ok", "service": "geo-service"}

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
