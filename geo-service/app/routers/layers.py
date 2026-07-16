"""
GET /layers/{layer_id} router — returns a GeoJSON FeatureCollection for any
supported infrastructure layer for a given city.

Mirrors the pattern established by GET /chargers:
  - loads datasets via the shared DatasetRegistry (cached after first call)
  - reprojects from the in-memory UTM 43N (EPSG:32643) back to WGS-84 (EPSG:4326)
  - returns the raw GeoJSON string as an application/json Response

Frontend layer IDs must match the _LAYER_MAP keys below, which in turn align
with the BASE_LAYERS ids defined in frontend/src/types/domain.ts.

Excluded from exposure:
  - ward_boundaries  — internal scoring / analysis layer, not rendered on the
                       client map.
  - population_grid  — very large (195 k rows), internal scoring use only.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status, Response

from app.core.dataset_loader import registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["layers"])

_SUPPORTED_CITIES: frozenset[str] = frozenset(
    {"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}
)

# Maps the URL path segment (layer_id) to the CityDatasets attribute name.
# Only layers that are safe to expose as GeoJSON to the frontend are listed.
_LAYER_MAP: dict[str, str] = {
    "ev_chargers":    "ev_chargers",
    "fuel_stations":  "fuel_stations",
    "roads":          "roads",
    "parking":        "parking",
    "metro_stations": "metro_stations",
    "malls":          "malls",
    "tech_parks":     "tech_parks",
}


@router.get(
    "/layers/{layer_id}",
    summary="Get a GeoJSON FeatureCollection for an infrastructure layer",
    description=(
        "Returns all features for the requested layer in the specified city "
        "as a GeoJSON FeatureCollection (WGS-84, EPSG:4326). "
        "Supported layer IDs: ev_chargers, fuel_stations, roads, parking, "
        "metro_stations, malls, tech_parks."
    ),
    responses={
        200: {"description": "GeoJSON FeatureCollection"},
        404: {"description": "Unknown layer_id"},
        422: {"description": "Unsupported city"},
    },
)
async def get_layer(layer_id: str, city: str) -> Response:
    # Validate layer_id
    if layer_id not in _LAYER_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Layer '{layer_id}' is not available.",
                "available_layers": sorted(_LAYER_MAP.keys()),
            },
        )

    # Validate city
    if not city or city not in _SUPPORTED_CITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"City '{city}' is not supported.",
                "supported_cities": sorted(_SUPPORTED_CITIES),
            },
        )

    # Load datasets (cached after first call per city)
    datasets = registry.load(city.lower())

    attr_name = _LAYER_MAP[layer_id]
    gdf = getattr(datasets, attr_name)

    if gdf.empty:
        logger.warning(
            "layer requested but no data available",
            extra={"layer_id": layer_id, "city": city},
        )
        # Return an empty FeatureCollection rather than 404 so the frontend
        # can gracefully show nothing without treating it as an error.
        empty_fc = '{"type":"FeatureCollection","features":[]}'
        return Response(content=empty_fc, media_type="application/json")

    # Reproject from UTM 43N back to WGS-84 for GeoJSON output
    gdf_wgs84 = gdf.to_crs(epsg=4326)
    geojson_str = gdf_wgs84.to_json()

    logger.info(
        "layer response dispatched",
        extra={
            "layer_id": layer_id,
            "city": city,
            "feature_count": len(gdf_wgs84),
        },
    )
    return Response(content=geojson_str, media_type="application/json")
