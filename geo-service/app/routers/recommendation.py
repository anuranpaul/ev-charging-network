"""
POST /recommendation router — stub implementation.

Returns a hardcoded GeoJSON FeatureCollection so the API contract is
verifiable end-to-end before any GeoPandas logic is wired in.

GeoPandas scoring (Requirement 5) will be introduced in a subsequent task.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, status

from app.models.schemas import (
    CandidateFeature,
    CandidateProperties,
    ChargerType,
    FactorScores,
    PointGeometry,
    RecommendationRequest,
    RecommendationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendation", tags=["recommendation"])

# ---------------------------------------------------------------------------
# Supported city registry
# Requirement 4 AC-4: return 422 for unsupported cities.
# ---------------------------------------------------------------------------

_SUPPORTED_CITIES: frozenset[str] = frozenset(
    {"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}
)

# ---------------------------------------------------------------------------
# Mock data factory
# Hardcoded stubs that match the locked CandidateFeature schema exactly.
# Coordinates are real Bengaluru locations so the Frontend map renders them
# at recognisable positions during development.
# ---------------------------------------------------------------------------

_MOCK_CANDIDATES: list[dict] = [
    {
        "lng": 77.5946,
        "lat": 12.9716,
        "rank": 1,
        "score": 91,
        "population_1km": 48_200,
        "nearest_charger_distance_m": 1_240.5,
        "road_type": "primary",
        "parking_available": True,
        "nearest_mall_distance_m": 380.0,
        "factors": {"population": 96, "charger_distance": 82, "road_proximity": 100, "parking": 100, "mall_proximity": 100},
    },
    {
        "lng": 77.6101,
        "lat": 12.9352,
        "rank": 2,
        "score": 78,
        "population_1km": 35_000,
        "nearest_charger_distance_m": 2_100.0,
        "road_type": "trunk",
        "parking_available": True,
        "nearest_mall_distance_m": 620.0,
        "factors": {"population": 70, "charger_distance": 100, "road_proximity": 100, "parking": 100, "mall_proximity": 0},
    },
    {
        "lng": 77.5800,
        "lat": 13.0012,
        "rank": 3,
        "score": 65,
        "population_1km": 22_000,
        "nearest_charger_distance_m": None,
        "road_type": "motorway",
        "parking_available": False,
        "nearest_mall_distance_m": None,
        "factors": {"population": 44, "charger_distance": 100, "road_proximity": 100, "parking": 0, "mall_proximity": 0},
    },
]


def _build_mock_response(request: RecommendationRequest) -> RecommendationResponse:
    """Assemble the hardcoded FeatureCollection from the mock seed data."""
    features: list[CandidateFeature] = []

    for raw in _MOCK_CANDIDATES:
        feature = CandidateFeature(
            geometry=PointGeometry(coordinates=[raw["lng"], raw["lat"]]),
            properties=CandidateProperties(
                rank=raw["rank"],
                score=raw["score"],
                factor_scores=FactorScores(**raw["factors"]),
                population_1km=raw["population_1km"],
                nearest_charger_distance_m=raw["nearest_charger_distance_m"],
                road_type=raw["road_type"],
                parking_available=raw["parking_available"],
                nearest_mall_distance_m=raw["nearest_mall_distance_m"],
                warnings=[],
            ),
        )
        features.append(feature)

    return RecommendationResponse(
        features=features,
        city=request.city,
        chargerType=request.charger_type,
        radius=request.radius,
        total_candidates=len(features),
    )


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Return ranked EV charger candidate locations",
    description=(
        "Accepts city, chargerType, and radius. "
        "Currently returns hardcoded mock data. "
        "GeoPandas spatial scoring will be wired in a subsequent task."
    ),
)
async def get_recommendations(
    request: RecommendationRequest,
    x_correlation_id: str | None = Header(
        default=None,
        alias="X-Correlation-ID",
        description="Propagated from the API Server (Requirement 11 AC-4)",
    ),
) -> RecommendationResponse:
    # Requirement 4 AC-4: reject unsupported cities.
    if request.city not in _SUPPORTED_CITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"City '{request.city}' is not supported.",
                "supported_cities": sorted(_SUPPORTED_CITIES),
            },
        )

    log_ctx = {
        "correlation_id": x_correlation_id or "MISSING",
        "city": request.city,
        "charger_type": request.charger_type,
        "radius": request.radius,
    }
    logger.info("recommendation request received", extra=log_ctx)

    response = _build_mock_response(request)

    logger.info(
        "recommendation response dispatched",
        extra={**log_ctx, "candidate_count": response.total_candidates},
    )
    return response
