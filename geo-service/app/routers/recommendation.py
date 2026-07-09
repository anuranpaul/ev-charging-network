"""
POST /recommendation router — real pipeline implementation.

Pipeline stages:
  1. Load city datasets via DatasetRegistry (cached after first call).
  2. Derive city bounding box from ward_boundaries.
  3. Generate candidates via generate_candidates().
  4. Filter roads to arterial classes.
  5. Score all candidates via Scorer.score_batch() — returns factor scores
     AND raw detail fields (population_1km, charger dist, road_type, etc.)
     in a single pass.  No second spatial pass is performed.
  6. Sort descending by score, assign 1-based ranks.
  7. Serialise to GeoJSON FeatureCollection (WGS-84 output).

Performance notes (Requirement 5 AC-5, Requirement 4 AC-2)
-----------------------------------------------------------
* DatasetRegistry caches loaded GeoDataFrames after the first cold load.
  Subsequent requests hit the in-memory cache (0 ms load time).
* score_batch() now returns raw detail columns alongside factor scores so
  the router never repeats any spatial operation.  The previous
  _compute_candidate_details() pass that duplicated all five spatial joins
  has been removed entirely.
* The grid fallback in candidates.py uses numpy meshgrid + points_from_xy
  instead of a Python-level Point() loop.

Correlation ID (Requirement 11)
---------------------------------
Every structured log record carries ``correlation_id``.
"""

from __future__ import annotations

import logging
import math
import time
import types

import geopandas as gpd
import pandas as pd
from fastapi import APIRouter, Header, HTTPException, status

from app.core.candidates import generate_candidates
from app.core.dataset_loader import registry
from app.core.scorer import Scorer
from app.models.schemas import (
    CandidateFeature,
    CandidateProperties,
    FactorScores,
    PointGeometry,
    RecommendationRequest,
    RecommendationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendation", tags=["recommendation"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ARTERIAL_ROAD_TYPES: frozenset[str] = frozenset({"motorway", "trunk", "primary"})

_SUPPORTED_CITIES: frozenset[str] = frozenset(
    {"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}
)

_TARGET_EPSG: int = 32643
_SOURCE_EPSG: int = 4326

# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Return ranked EV charger candidate locations",
    description=(
        "Loads city datasets (cached), generates candidate locations, "
        "scores them across five geospatial factors in a single pass, "
        "and returns a ranked GeoJSON FeatureCollection."
    ),
)
async def get_recommendations(
    request: RecommendationRequest,
    x_correlation_id: str | None = Header(
        default=None,
        alias="X-Correlation-ID",
        description="Correlation ID propagated from the API Gateway (Req 11 AC-4)",
    ),
) -> RecommendationResponse:
    # ------------------------------------------------------------------
    # Step 0 — validate city
    # ------------------------------------------------------------------
    if request.city not in _SUPPORTED_CITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"City '{request.city}' is not supported.",
                "supported_cities": sorted(_SUPPORTED_CITIES),
            },
        )

    cid = x_correlation_id or "MISSING"
    log_ctx: dict = {
        "correlation_id": cid,
        "city": request.city,
        "charger_type": request.charger_type,
        "radius": request.radius,
    }
    logger.info("recommendation request received", extra=log_ctx)
    pipeline_start = time.perf_counter()

    # ------------------------------------------------------------------
    # Step 1 — load datasets (cached after first call)
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    datasets = registry.load(request.city.lower())
    logger.info(
        "datasets loaded",
        extra={
            **log_ctx,
            "missing_layers": datasets.missing_layers,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )

    # ------------------------------------------------------------------
    # Step 2 — derive city bounding box
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    city_bbox = _derive_city_bbox(datasets)
    logger.info(
        "city bbox derived",
        extra={
            **log_ctx,
            "bbox_bounds": city_bbox.bounds,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )

    # ------------------------------------------------------------------
    # Step 3 — generate candidates
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    candidates_gdf = generate_candidates(datasets, city_bbox)
    logger.info(
        "candidates generated",
        extra={
            **log_ctx,
            "candidate_count": len(candidates_gdf),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )

    # ------------------------------------------------------------------
    # Step 4 — filter roads to arterial classes only
    # ------------------------------------------------------------------
    arterial_roads = _filter_arterial_roads(datasets.roads)

    # ------------------------------------------------------------------
    # Step 5 — single-pass scoring
    #
    # score_batch() returns factor scores AND raw detail fields
    # (population_1km, nearest_charger_dist_m, road_type,
    # parking_available, nearest_mall_dist_m) from the same spatial
    # operations — no second pass needed.
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    scorer = Scorer()

    scoring_datasets = types.SimpleNamespace(
        population_grid=datasets.population_grid,
        ev_chargers=datasets.ev_chargers,
        roads=arterial_roads,
        parking=datasets.parking,
        malls=datasets.malls,
    )

    scores_df = scorer.score_batch(candidates_gdf, scoring_datasets, request.radius)
    logger.info(
        "scoring complete",
        extra={
            **log_ctx,
            "candidate_count": len(scores_df),
            "score_min": int(scores_df["score"].min()),
            "score_max": int(scores_df["score"].max()),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )

    # ------------------------------------------------------------------
    # Step 6 — build warnings list from missing layers
    # ------------------------------------------------------------------
    warnings_list = _compute_warnings(datasets)

    # ------------------------------------------------------------------
    # Step 7 — sort, rank, reproject to WGS-84, assemble features
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    features = _build_features(candidates_gdf, scores_df, warnings_list)
    logger.info(
        "features assembled",
        extra={
            **log_ctx,
            "feature_count": len(features),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )

    # ------------------------------------------------------------------
    # Step 8 — assemble and return response
    # ------------------------------------------------------------------
    response = RecommendationResponse(
        features=features,
        city=request.city,
        chargerType=request.charger_type,
        radius=request.radius,
        total_candidates=len(features),
    )

    logger.info(
        "recommendation response dispatched",
        extra={
            **log_ctx,
            "candidate_count": len(features),
            "pipeline_duration_ms": round(
                (time.perf_counter() - pipeline_start) * 1000, 2
            ),
        },
    )
    return response


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_city_bbox(datasets) -> "shapely.geometry.base.BaseGeometry":
    from shapely.ops import unary_union

    if not datasets.ward_boundaries.empty:
        safe_geoms = datasets.ward_boundaries.geometry.convex_hull
        return unary_union(safe_geoms)

    all_geoms: list = []
    for layer_name in (
        "ev_chargers", "roads", "parking", "malls",
        "metro_stations", "tech_parks", "fuel_stations", "population_grid",
    ):
        layer: gpd.GeoDataFrame = getattr(datasets, layer_name, None)
        if layer is not None and not layer.empty:
            all_geoms.extend(layer.geometry.tolist())

    if not all_geoms:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "No spatial data available for city — all layers missing."},
        )

    return unary_union(all_geoms).convex_hull


def _filter_arterial_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if roads.empty or "highway" not in roads.columns:
        return roads
    return roads[roads["highway"].isin(_ARTERIAL_ROAD_TYPES)].copy()


def _compute_warnings(datasets) -> list[str]:
    _LAYER_TO_FACTOR: dict[str, str] = {
        "population_grid": "population",
        "ev_chargers":     "charger_distance",
        "roads":           "road_proximity",
        "parking":         "parking",
        "malls":           "mall_proximity",
    }
    return [
        _LAYER_TO_FACTOR[layer]
        for layer in datasets.missing_layers
        if layer in _LAYER_TO_FACTOR
    ]


def _build_features(
    candidates_gdf: gpd.GeoDataFrame,
    scores_df: pd.DataFrame,
    warnings_list: list[str],
) -> list[CandidateFeature]:
    """
    Sort candidates by score descending (ties by original index ascending),
    assign 1-based ranks, reproject to WGS-84, and build CandidateFeature
    objects.

    Detail fields (population_1km, nearest distances, road_type,
    parking_available) are read directly from scores_df — they were
    computed in the same spatial pass as the factor scores.
    """
    candidates_wgs84 = candidates_gdf.to_crs(epsg=_SOURCE_EPSG)

    combined = scores_df[["score"]].copy()
    combined["original_index"] = range(len(combined))
    combined_sorted = combined.sort_values(
        ["score", "original_index"], ascending=[False, True]
    )

    features: list[CandidateFeature] = []
    for rank, row_idx in enumerate(combined_sorted.index, start=1):
        row  = scores_df.loc[row_idx]
        geom = candidates_wgs84.geometry.loc[row_idx]

        factor_scores = FactorScores(
            population=       int(row["pop_factor"]),
            charger_distance= int(row["charger_factor"]),
            road_proximity=   int(row["road_factor"]),
            parking=          int(row["park_factor"]),
            mall_proximity=   int(row["mall_factor"]),
        )

        properties = CandidateProperties(
            rank=rank,
            score=int(row["score"]),
            factor_scores=factor_scores,
            population_1km=int(row["population_1km"]),
            nearest_charger_distance_m=_to_float_or_none(row["nearest_charger_dist_m"]),
            road_type=str(row["road_type"]),
            parking_available=bool(row["parking_available"]),
            nearest_mall_distance_m=_to_float_or_none(row["nearest_mall_dist_m"]),
            warnings=warnings_list,
        )

        features.append(CandidateFeature(
            geometry=PointGeometry(coordinates=[geom.x, geom.y]),
            properties=properties,
        ))

    return features


def _to_float_or_none(v) -> float | None:
    """Convert NaN / None to None, otherwise return float."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None
