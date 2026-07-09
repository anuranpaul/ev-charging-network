"""
POST /validate router — GeoJSON FeatureCollection validation.

Requirement 10 AC-4–6:
  - Accepts a GeoJSON FeatureCollection body up to 50 MB.
  - Returns record_count, CRS authority string, geometry_types[], and
    validationErrors[] (featureIndex + message for each detected error).
  - Detects: null geometry, self-intersection, coordinates outside WGS-84 bounds.
  - Returns 400 for non-JSON or non-FeatureCollection input.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from shapely.geometry import mapping, shape
from shapely.validation import explain_validity

from app.models.schemas import ValidateResponse, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["validation"])

_MAX_BODY_BYTES: int = 50 * 1024 * 1024   # 50 MB

# WGS-84 bounds
_LON_MIN, _LON_MAX = -180.0, 180.0
_LAT_MIN, _LAT_MAX = -90.0, 90.0


@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="Validate a GeoJSON FeatureCollection",
    description=(
        "Parses a GeoJSON FeatureCollection (max 50 MB) and returns "
        "record count, CRS string, geometry types present, and a list of "
        "validation errors (null geometry, self-intersection, out-of-bounds "
        "coordinates). Returns 400 if the body is not valid JSON or not a "
        "FeatureCollection. (Requirement 10 AC-4–6)"
    ),
    responses={
        400: {"description": "Body is not valid JSON or not a GeoJSON FeatureCollection"},
    },
)
async def validate_geojson(request: Request) -> ValidateResponse:
    # --- size guard ---
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"message": f"Request body exceeds maximum allowed size of 50 MB."},
        )

    # --- read + parse JSON ---
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"message": "Request body exceeds maximum allowed size of 50 MB."},
        )

    import json
    try:
        geojson: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"errors": [], "message": f"Invalid JSON: {exc}"},
        )

    # --- must be a FeatureCollection ---
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "errors": [],
                "message": (
                    "Body must be a GeoJSON object with type='FeatureCollection'; "
                    f"got type='{geojson.get('type') if isinstance(geojson, dict) else type(geojson).__name__}'"
                ),
            },
        )

    features: list[Any] = geojson.get("features") or []
    record_count = len(features)

    # --- determine CRS ---
    crs_str = "EPSG:4326"   # GeoJSON default per RFC 7946
    named_crs = geojson.get("crs")
    if isinstance(named_crs, dict):
        props = named_crs.get("properties", {})
        name  = props.get("name", "")
        if name:
            crs_str = name

    # --- iterate features ---
    geometry_types: set[str] = set()
    validation_errors: list[ValidationError] = []

    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            validation_errors.append(ValidationError(
                feature_index=idx,
                message="Feature is not a JSON object.",
            ))
            continue

        geom_raw = feature.get("geometry")

        # null geometry
        if geom_raw is None:
            validation_errors.append(ValidationError(
                feature_index=idx,
                message="Null geometry.",
            ))
            continue

        # parse geometry
        try:
            geom = shape(geom_raw)
        except Exception as exc:
            validation_errors.append(ValidationError(
                feature_index=idx,
                message=f"Geometry could not be parsed: {exc}",
            ))
            continue

        geometry_types.add(geom.geom_type)

        # null / empty geometry
        if geom.is_empty:
            validation_errors.append(ValidationError(
                feature_index=idx,
                message="Empty geometry.",
            ))
            continue

        # self-intersection check
        if not geom.is_valid:
            reason = explain_validity(geom)
            validation_errors.append(ValidationError(
                feature_index=idx,
                message=f"Invalid geometry: {reason}",
            ))
            # still check bounds even if invalid

        # coordinate bounds check (WGS-84)
        bounds_error = _check_wgs84_bounds(idx, geom)
        if bounds_error:
            validation_errors.append(bounds_error)

    return ValidateResponse(
        record_count=record_count,
        crs=crs_str,
        geometry_types=sorted(geometry_types),
        validation_errors=validation_errors,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_wgs84_bounds(
    idx: int, geom
) -> ValidationError | None:
    """Return a ValidationError if any coordinate is outside WGS-84 bounds."""
    minx, miny, maxx, maxy = geom.bounds
    if (minx < _LON_MIN or maxx > _LON_MAX or miny < _LAT_MIN or maxy > _LAT_MAX):
        return ValidationError(
            feature_index=idx,
            message=(
                f"Coordinates out of WGS-84 bounds: "
                f"lon=[{minx}, {maxx}], lat=[{miny}, {maxy}]"
            ),
        )
    return None
