"""
POST /recommendation router — real pipeline implementation.

Replaces the previous stub with the full geospatial pipeline:
  1. Load city datasets via DatasetRegistry.
  2. Derive city bounding box from ward_boundaries (or fallback heuristic).
  3. Generate candidates via generate_candidates().
  4. Score all candidates via Scorer.score_batch().
  5. Assemble per-candidate detail fields (population_1km,
     nearest_charger_distance_m, road_type, parking_available,
     nearest_mall_distance_m, warnings).
  6. Sort descending by score (ties broken by original index, ascending)
     and assign 1-based ranks.
  7. Serialise to GeoJSON FeatureCollection (WGS-84 output).

Correlation ID (Requirement 11)
---------------------------------
Every structured log record inside this handler carries ``correlation_id``
so that individual pipeline steps can be traced across log aggregators.

CRS contract
--------------
All spatial operations run in EPSG:32643.  Candidate geometries are
converted back to EPSG:4326 (WGS-84) before serialisation.
"""

from __future__ import annotations

import logging
import time

import geopandas as gpd
import pandas as pd
from fastapi import APIRouter, Header, HTTPException, status
from shapely.geometry import shape

from app.core.candidates import generate_candidates
from app.core.dataset_loader import registry
from app.core.scorer import (
    POPULATION_BUFFER_M,
    ROAD_PROXIMITY_M,
    MALL_PROXIMITY_M,
    POPULATION_NORMALISER,
    Scorer,
)
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

# Arterial road classes used for the road-proximity factor
# (design.md §Road proximity factor).
_ARTERIAL_ROAD_TYPES: frozenset[str] = frozenset(
    {"motorway", "trunk", "primary"}
)

# Supported cities (Requirement 4 AC-4)
_SUPPORTED_CITIES: frozenset[str] = frozenset(
    {"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}
)

# Target CRS used by all spatial layers after DatasetRegistry.load()
_TARGET_EPSG: int = 32643
_SOURCE_EPSG: int = 4326   # WGS-84 for GeoJSON output

# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Return ranked EV charger candidate locations",
    description=(
        "Loads city datasets, generates candidate locations from parking "
        "centroids (grid fallback), scores them across five geospatial "
        "factors, and returns a ranked GeoJSON FeatureCollection."
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
    # Step 0 — validate city (Requirement 4 AC-4)
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
    # Step 1 — load datasets (Requirement 11: log each step)
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
    # Step 2 — derive city bounding box in EPSG:32643
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
    # Step 5 — score batch using filtered datasets
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    scorer = Scorer()

    # Build a modified datasets-like object with filtered roads
    import types
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
    # Step 6 — compute per-candidate detail fields
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    detail_df = _compute_candidate_details(
        candidates_gdf, datasets, arterial_roads, scores_df, request.radius
    )
    logger.info(
        "candidate details computed",
        extra={
            **log_ctx,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )

    # ------------------------------------------------------------------
    # Step 7 — sort, rank, reproject to WGS-84
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    features = _build_features(candidates_gdf, scores_df, detail_df, datasets)
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
    """
    Return a Shapely geometry whose ``.bounds`` covers the city in EPSG:32643.

    Priority:
    1. Union of ward_boundaries polygons (most accurate).
    2. Union of all non-empty layer geometries (fallback when ward_boundaries
       is absent or empty).
    3. Final guard: if no layer has any geometry, raise 503.
    """
    from shapely.ops import unary_union

    if not datasets.ward_boundaries.empty:
        # Use convex_hull per geometry before unioning to avoid
        # TopologyException on self-intersecting ward boundary polygons.
        safe_geoms = datasets.ward_boundaries.geometry.convex_hull
        return unary_union(safe_geoms)

    # Gather bounds from all non-empty layers
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
            detail={
                "message": "No spatial data available for city — all layers missing.",
            },
        )

    return unary_union(all_geoms).convex_hull


def _filter_arterial_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return only motorway / trunk / primary road features."""
    if roads.empty or "highway" not in roads.columns:
        return roads  # empty or no highway attribute → pass through as-is

    mask = roads["highway"].isin(_ARTERIAL_ROAD_TYPES)
    return roads[mask].copy()


def _compute_candidate_details(
    candidates_gdf: gpd.GeoDataFrame,
    datasets,
    arterial_roads: gpd.GeoDataFrame,
    scores_df: pd.DataFrame,
    search_radius: int,
) -> pd.DataFrame:
    """
    Compute the auxiliary properties required by CandidateProperties:
      - population_1km       (int)
      - nearest_charger_distance_m  (float | None)
      - road_type            (str)
      - parking_available    (bool)
      - nearest_mall_distance_m  (float | None)
      - warnings             (list[str])

    Returns a DataFrame indexed identically to ``candidates_gdf``.
    """
    idx = candidates_gdf.index

    # --- population_1km -------------------------------------------------
    # Re-use the population factor numerics: reverse-engineer the sum from
    # the factor value, or recompute via buffer + sjoin directly (cheaper
    # than factor, no clip needed for raw sum).
    population_1km = _compute_population_1km(candidates_gdf, datasets.population_grid)

    # --- nearest_charger_distance_m -------------------------------------
    charger_dist = _compute_nearest_distance(
        candidates_gdf, datasets.ev_chargers, search_radius,
        dist_col="charger_dist_m",
    )

    # --- road_type -------------------------------------------------------
    road_type_series = _compute_nearest_road_type(candidates_gdf, arterial_roads)

    # --- parking_available -----------------------------------------------
    parking_avail = _compute_parking_available(candidates_gdf, datasets.parking)

    # --- nearest_mall_distance_m ----------------------------------------
    mall_dist = _compute_nearest_distance(
        candidates_gdf, datasets.malls, max_distance=None,
        dist_col="mall_dist_m",
    )

    # --- warnings --------------------------------------------------------
    warnings_series = _compute_warnings(datasets)
    # Broadcast the same warnings list to every candidate row
    warnings_col = pd.Series(
        [warnings_series] * len(candidates_gdf), index=idx
    )

    return pd.DataFrame(
        {
            "population_1km":             population_1km,
            "nearest_charger_distance_m": charger_dist,
            "road_type":                  road_type_series,
            "parking_available":          parking_avail,
            "nearest_mall_distance_m":    mall_dist,
            "warnings":                   warnings_col,
        },
        index=idx,
    )


def _compute_population_1km(
    candidates: gpd.GeoDataFrame,
    population_grid: gpd.GeoDataFrame,
) -> pd.Series:
    """Return total population within POPULATION_BUFFER_M of each candidate."""
    if population_grid.empty:
        return pd.Series(0, index=candidates.index, dtype=int)

    buffers = gpd.GeoDataFrame(
        geometry=candidates.geometry.buffer(POPULATION_BUFFER_M),
        crs=candidates.crs,
    )
    joined = gpd.sjoin(
        buffers,
        population_grid[["geometry", "population"]],
        how="left",
        predicate="intersects",
    )
    raw = (
        joined.groupby(joined.index)["population"]
        .sum()
        .reindex(candidates.index, fill_value=0)
    )
    return raw.astype(int)


def _compute_nearest_distance(
    candidates: gpd.GeoDataFrame,
    layer: gpd.GeoDataFrame,
    max_distance: int | float | None,
    dist_col: str,
) -> pd.Series:
    """
    Return the distance (metres) to the nearest feature in ``layer``.

    Returns ``None`` (Python None, not NaN) for candidates where no feature
    exists within ``max_distance`` (or where the layer is empty).
    """
    if layer.empty:
        return pd.Series([None] * len(candidates), index=candidates.index)

    kwargs: dict = {"how": "left", "distance_col": dist_col}
    if max_distance is not None:
        kwargs["max_distance"] = max_distance

    nearest = gpd.sjoin_nearest(
        candidates[["geometry"]],
        layer[["geometry"]].reset_index(drop=True),
        **kwargs,
    )

    # Deduplicate equidistant ties — keep closest.
    nearest_sorted = nearest.sort_values(dist_col, na_position="last")
    nearest = (
        nearest_sorted
        .loc[~nearest_sorted.index.duplicated(keep="first")]
        .reindex(candidates.index)
    )

    # Convert to Python float | None (NaN → None for JSON serialisation)
    def _nan_to_none(v):
        import math
        if v is None:
            return None
        try:
            return None if math.isnan(float(v)) else float(v)
        except (TypeError, ValueError):
            return None

    result = nearest[dist_col].apply(_nan_to_none)
    return result


def _compute_nearest_road_type(
    candidates: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
) -> pd.Series:
    """
    Return the OSM ``highway`` tag of the nearest arterial road to each
    candidate.  Returns ``"none"`` when the roads layer is empty or lacks a
    ``highway`` column.
    """
    if roads.empty:
        return pd.Series("none", index=candidates.index)

    cols = ["geometry"]
    if "highway" in roads.columns:
        cols.append("highway")

    nearest = gpd.sjoin_nearest(
        candidates[["geometry"]],
        roads[cols].reset_index(drop=True),
        how="left",
        distance_col="_road_d",
    )
    # Deduplicate
    nearest_sorted = nearest.sort_values("_road_d", na_position="last")
    nearest = (
        nearest_sorted
        .loc[~nearest_sorted.index.duplicated(keep="first")]
        .reindex(candidates.index)
    )

    if "highway" in nearest.columns:
        road_type = nearest["highway"].fillna("none")
    else:
        road_type = pd.Series("none", index=candidates.index)

    return road_type


def _compute_parking_available(
    candidates: gpd.GeoDataFrame,
    parking: gpd.GeoDataFrame,
) -> pd.Series:
    """
    Return True for candidates that intersect any parking polygon, False
    otherwise.  Uses the same inner-join + .index.unique() approach as
    Scorer.parking_factor to avoid double-counting.
    """
    if parking.empty:
        return pd.Series(False, index=candidates.index)

    matched = gpd.sjoin(
        candidates[["geometry"]],
        parking[["geometry"]],
        how="inner",
        predicate="intersects",
    )
    matched_idx = matched.index.unique()
    return pd.Series(
        candidates.index.isin(matched_idx),
        index=candidates.index,
    )


def _compute_warnings(datasets) -> list[str]:
    """
    Return the list of missing-layer factor names (design.md §Missing Layer
    Handling, Req 5 AC-8).  A missing layer is one that appears in
    ``datasets.missing_layers``.

    The mapping from layer name → factor name follows design.md:
      population_grid → "population"
      ev_chargers     → "charger_distance"
      roads           → "road_proximity"
      parking         → "parking"
      malls           → "mall_proximity"
    """
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
    detail_df: pd.DataFrame,
    datasets,
) -> list[CandidateFeature]:
    """
    Sort candidates by score descending (ties by original index ascending),
    assign 1-based ranks, reproject to WGS-84, and build the list of
    ``CandidateFeature`` objects.

    design.md §Determinism:
      "sorted descending by score, ascending by original index to break ties"
    """
    # Reproject candidate geometries to WGS-84 for GeoJSON output
    candidates_wgs84: gpd.GeoDataFrame = candidates_gdf.to_crs(epsg=_SOURCE_EPSG)

    # Build a combined DataFrame for sorting
    combined = scores_df[["score"]].copy()
    combined["original_index"] = range(len(combined))

    # Sort: primary = score descending, secondary = original_index ascending
    combined_sorted = combined.sort_values(
        ["score", "original_index"],
        ascending=[False, True],
    )

    features: list[CandidateFeature] = []
    for rank, row_idx in enumerate(combined_sorted.index, start=1):
        score_row    = scores_df.loc[row_idx]
        detail_row   = detail_df.loc[row_idx]
        geom         = candidates_wgs84.geometry.loc[row_idx]

        # Factor scores (int, clipped to [0,100])
        factor_scores = FactorScores(
            population=      int(score_row["pop_factor"]),
            charger_distance=int(score_row["charger_factor"]),
            road_proximity=  int(score_row["road_factor"]),
            parking=         int(score_row["park_factor"]),
            mall_proximity=  int(score_row["mall_factor"]),
        )

        # nearest_charger_distance_m and nearest_mall_distance_m may be None
        charger_dist = detail_row["nearest_charger_distance_m"]
        mall_dist    = detail_row["nearest_mall_distance_m"]

        def _safe_float(v) -> float | None:
            if v is None:
                return None
            try:
                f = float(v)
                return None if f != f else f  # NaN check: NaN != NaN
            except (TypeError, ValueError):
                return None

        properties = CandidateProperties(
            rank=rank,
            score=int(score_row["score"]),
            factor_scores=factor_scores,
            population_1km=int(detail_row["population_1km"]),
            nearest_charger_distance_m=_safe_float(charger_dist),
            road_type=str(detail_row["road_type"]),
            parking_available=bool(detail_row["parking_available"]),
            nearest_mall_distance_m=_safe_float(mall_dist),
            warnings=list(detail_row["warnings"]),
        )

        feature = CandidateFeature(
            geometry=PointGeometry(coordinates=[geom.x, geom.y]),
            properties=properties,
        )
        features.append(feature)

    return features
