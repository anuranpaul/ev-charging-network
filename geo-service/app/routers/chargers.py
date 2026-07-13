"""
GET /chargers router — returns all existing EV chargers for a city.
"""

from __future__ import annotations

import logging
import json
from fastapi import APIRouter, HTTPException, status, Response

from app.core.dataset_loader import registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chargers"])

_SUPPORTED_CITIES: frozenset[str] = frozenset(
    {"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}
)

@router.get(
    "/chargers",
    summary="Get all existing EV chargers for a city",
    description="Returns a GeoJSON FeatureCollection of all EV charger locations in the city.",
)
async def get_chargers(city: str) -> Response:
    if not city or city not in _SUPPORTED_CITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"City '{city}' is not supported.",
                "supported_cities": sorted(_SUPPORTED_CITIES),
            },
        )

    # Load datasets (cached after first call)
    datasets = registry.load(city.lower())

    # Convert the UTM EPSG:32643 GeoDataFrame back to WGS-84 (EPSG:4326) for GeoJSON output
    chargers_wgs84 = datasets.ev_chargers.to_crs(epsg=4326)

    # Convert to GeoJSON string and return as a direct JSON Response
    geojson_str = chargers_wgs84.to_json()
    return Response(content=geojson_str, media_type="application/json")
