"""
GET /analysis router — spatial statistics for a city.

Requirement 7 AC-1–5:
  Returns:
    - total_candidates    int
    - score_mean          float (0-100)
    - score_median        float (0-100)
    - score_p90           float (0-100, 90th percentile)
    - coverage_pct        float (0-100, % of city bbox covered by ≥1 candidate within 500 m)
    - ward_stats          list — per-ward candidate count and mean score

Coverage computation (AC-1):
  Union all candidate 500 m buffers → intersect with city_bbox polygon →
  intersection_area / city_bbox_area × 100.
"""

from __future__ import annotations

import logging
import time
import types

import geopandas as gpd
import numpy as np
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from shapely.ops import unary_union

from app.core.candidates import generate_candidates
from app.core.dataset_loader import registry
from app.core.scorer import Scorer
from app.models.schemas import ChargerType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])

_SUPPORTED_CITIES: frozenset[str] = frozenset(
    {"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}
)
_ARTERIAL_ROAD_TYPES: frozenset[str] = frozenset({"motorway", "trunk", "primary"})
_COVERAGE_BUFFER_M: float = 500.0


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class WardStat(BaseModel):
    ward_name: str
    candidate_count: int
    mean_score: float = Field(..., ge=0, le=100)


class AnalysisResponse(BaseModel):
    city: str
    charger_type: str = Field(..., alias="chargerType")
    total_candidates: int
    score_mean: float = Field(..., ge=0, le=100)
    score_median: float = Field(..., ge=0, le=100)
    score_p90: float = Field(..., ge=0, le=100)
    coverage_pct: float = Field(
        ..., ge=0, le=100,
        description="% of city bounding polygon area covered by ≥1 candidate within 500 m"
    )
    ward_stats: list[WardStat]

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------

@router.get(
    "/analysis",
    response_model=AnalysisResponse,
    summary="Spatial statistics for a city",
    description=(
        "Returns score distribution (mean, median, p90), area coverage percentage, "
        "and per-ward candidate counts for the given city and charger type. "
        "(Requirement 7 AC-1–5)"
    ),
)
async def get_analysis(
    city: str,
    chargerType: str,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
) -> AnalysisResponse:
    # --- validate city ---
    if city not in _SUPPORTED_CITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"City '{city}' is not supported.",
                "supported_cities": sorted(_SUPPORTED_CITIES),
            },
        )

    # --- validate chargerType ---
    try:
        charger_type_enum = ChargerType(chargerType)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"chargerType '{chargerType}' is not recognised.",
                "valid_values": [e.value for e in ChargerType],
            },
        )

    cid = x_correlation_id or "MISSING"
    log_ctx = {"correlation_id": cid, "city": city, "charger_type": chargerType}
    logger.info("analysis request received", extra=log_ctx)
    t_start = time.perf_counter()

    # --- load datasets (cached) ---
    datasets = registry.load(city.lower())

    # --- derive city bbox ---
    if not datasets.ward_boundaries.empty:
        city_bbox_geom = unary_union(datasets.ward_boundaries.geometry.convex_hull)
    else:
        all_geoms = []
        for layer_name in ("ev_chargers", "roads", "parking", "malls", "population_grid"):
            layer = getattr(datasets, layer_name, None)
            if layer is not None and not layer.empty:
                all_geoms.extend(layer.geometry.tolist())
        if not all_geoms:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"message": "No spatial data available for city."},
            )
        city_bbox_geom = unary_union(all_geoms).convex_hull

    # --- generate candidates ---
    candidates_gdf = generate_candidates(datasets, city_bbox_geom)

    # --- filter arterial roads + score ---
    roads = datasets.roads
    if not roads.empty and "highway" in roads.columns:
        arterial = roads[roads["highway"].isin(_ARTERIAL_ROAD_TYPES)].copy()
    else:
        arterial = roads

    scoring_ds = types.SimpleNamespace(
        population_grid=datasets.population_grid,
        ev_chargers=datasets.ev_chargers,
        roads=arterial,
        parking=datasets.parking,
        malls=datasets.malls,
    )

    # Default radius for analysis — use 1500 m (mid-range)
    scores_df = Scorer().score_batch(candidates_gdf, scoring_ds, search_radius=1500)
    scores = scores_df["score"].values.astype(float)

    # --- score distribution ---
    score_mean   = float(np.mean(scores))
    score_median = float(np.median(scores))
    score_p90    = float(np.percentile(scores, 90))

    # --- coverage percentage (Req 7 AC-1) ---
    # Buffer every candidate by 500 m, union all, intersect with city bbox
    cand_buffered = candidates_gdf.geometry.buffer(_COVERAGE_BUFFER_M)
    union_coverage = unary_union(cand_buffered)
    intersection   = union_coverage.intersection(city_bbox_geom)
    city_area      = city_bbox_geom.area
    coverage_pct   = float((intersection.area / city_area * 100)) if city_area > 0 else 0.0
    coverage_pct   = min(coverage_pct, 100.0)

    # --- per-ward statistics (Req 7 AC-2) ---
    ward_stats: list[WardStat] = []
    if not datasets.ward_boundaries.empty:
        # sjoin candidates → wards
        wards = datasets.ward_boundaries.copy().reset_index(drop=True)
        name_col = next(
            (c for c in wards.columns if c.lower() in ("name", "ward_name", "wardname")),
            None,
        )
        joined = gpd.sjoin(
            candidates_gdf[["geometry"]].assign(score=scores_df["score"].values),
            wards[["geometry"] + ([name_col] if name_col else [])],
            how="left",
            predicate="within",
        )
        for ward_idx, group in joined.groupby("index_right"):
            w_name = str(wards.at[ward_idx, name_col]) if name_col else f"ward_{ward_idx}"
            ward_stats.append(WardStat(
                ward_name=w_name,
                candidate_count=len(group),
                mean_score=round(float(group["score"].mean()), 1),
            ))
    ward_stats.sort(key=lambda w: w.candidate_count, reverse=True)

    logger.info(
        "analysis complete",
        extra={
            **log_ctx,
            "total_candidates": len(candidates_gdf),
            "coverage_pct": round(coverage_pct, 2),
            "duration_ms": round((time.perf_counter() - t_start) * 1000, 2),
        },
    )

    return AnalysisResponse(
        city=city,
        chargerType=charger_type_enum.value,
        total_candidates=len(candidates_gdf),
        score_mean=round(score_mean, 1),
        score_median=round(score_median, 1),
        score_p90=round(score_p90, 1),
        coverage_pct=round(coverage_pct, 2),
        ward_stats=ward_stats,
    )
