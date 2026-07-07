"""
app/core/dataset_loader.py — ChargeWise India Geo Service

Loads the nine GeoJSON layers for a given city from $DATA_DIR/{city}/,
reprojects every successfully-loaded GeoDataFrame to EPSG:32643, and
provides a health() method returning per-dataset record counts and
last-load timestamps.

Design constraints (design.md §Data Models / CRS Strategy):
  - Files on disk are WGS-84 (EPSG:4326).
  - After load, every GeoDataFrame is projected to EPSG:32643 (UTM 43N)
    so that all downstream spatial operations (buffer, sjoin_nearest, …)
    use metric distances.
  - A missing or un-parseable file is never a fatal error; instead its
    layer name is appended to CityDatasets.missing_layers and the
    corresponding GeoDataFrame field is an empty frame with the correct CRS.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd

from app.models.schemas import DataHealthResponse, DatasetHealth

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_EPSG: int = 32643   # UTM Zone 43N — metres, optimal for Indian cities
SOURCE_EPSG: int = 4326    # WGS-84 — standard GeoJSON on disk

# Ordered list of (field_name, filename) pairs.
# Order is preserved: the first item is "most critical" purely for readability.
_LAYERS: list[tuple[str, str]] = [
    ("ev_chargers",      "ev_chargers.geojson"),
    ("roads",            "roads.geojson"),
    ("parking",          "parking.geojson"),
    ("malls",            "malls.geojson"),
    ("metro_stations",   "metro_stations.geojson"),
    ("tech_parks",       "tech_parks.geojson"),
    ("fuel_stations",    "fuel_stations.geojson"),
    ("ward_boundaries",  "ward_boundaries.geojson"),
    ("population_grid",  "population_grid.geojson"),
]

_LAYER_NAMES: list[str] = [name for name, _ in _LAYERS]


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

def _empty_gdf() -> gpd.GeoDataFrame:
    """Return an empty GeoDataFrame already projected to TARGET_EPSG."""
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=TARGET_EPSG))


@dataclass
class CityDatasets:
    """
    Container for all nine spatial layers belonging to a single city.

    Every GeoDataFrame field is guaranteed to be projected to EPSG:32643.
    When a file was missing or failed to parse, the corresponding field
    holds an empty GeoDataFrame and the layer name appears in missing_layers.
    """

    ev_chargers:      gpd.GeoDataFrame
    roads:            gpd.GeoDataFrame   # motorway / trunk / primary / secondary
    parking:          gpd.GeoDataFrame
    malls:            gpd.GeoDataFrame
    metro_stations:   gpd.GeoDataFrame
    tech_parks:       gpd.GeoDataFrame
    fuel_stations:    gpd.GeoDataFrame
    ward_boundaries:  gpd.GeoDataFrame
    population_grid:  gpd.GeoDataFrame
    missing_layers:   list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal per-dataset health tracking
# ---------------------------------------------------------------------------

@dataclass
class _DatasetMeta:
    """Bookkeeping for a single loaded (or failed) dataset."""

    city:          str
    layer_name:    str
    record_count:  int                  # 0 when missing / parse error
    last_loaded_at: datetime | None     # None when never successfully loaded
    status:        str                  # "ok" | "missing"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class DatasetRegistry:
    """
    Singleton-style registry that holds loaded GeoDataFrames per city.

    Usage::

        registry = DatasetRegistry()
        datasets = registry.load("bengaluru")
        health_resp = registry.health()

    The registry is intentionally *not* a module-level singleton so that
    tests can instantiate isolated copies without shared state.
    """

    def __init__(self) -> None:
        # city (lower) -> CityDatasets
        self._cache: dict[str, CityDatasets] = {}
        # "{city}/{layer_name}" -> _DatasetMeta
        self._meta: dict[str, _DatasetMeta] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, city: str) -> CityDatasets:
        """
        Load (or return cached) datasets for *city*.

        Parameters
        ----------
        city:
            City name as a directory name under $DATA_DIR (case-insensitive;
            normalised to lowercase before path construction).

        Returns
        -------
        CityDatasets
            All nine GeoDataFrames in EPSG:32643.  Missing files result in
            empty frames and their names in ``missing_layers``.
        """
        city_key = city.lower()
        if city_key in self._cache:
            logger.debug("dataset_registry cache hit", extra={"city": city_key})
            return self._cache[city_key]

        datasets = self._load_city(city_key)
        self._cache[city_key] = datasets
        return datasets

    def health(self) -> DataHealthResponse:
        """
        Return a DataHealthResponse reflecting current registry state.

        The response is built from ``_meta`` entries recorded during the
        most-recent ``load()`` call for each city.  Cities that have never
        been loaded are absent from the datasets dict.

        City availability is:
        - "available"   — no missing layers
        - "partial"     — ≥1 layer missing
        - "unavailable" — no layers loaded at all (city not in cache)
        """
        datasets_out: dict[str, DatasetHealth] = {}
        city_availability: dict[str, str] = {}

        for key, meta in self._meta.items():
            datasets_out[key] = DatasetHealth(
                record_count=meta.record_count,
                last_loaded_at=(
                    meta.last_loaded_at.isoformat()
                    if meta.last_loaded_at is not None
                    else "null"
                ),
                status=meta.status,   # type: ignore[arg-type]
            )

        # Derive city-level availability from meta
        cities_seen: dict[str, list[_DatasetMeta]] = {}
        for meta in self._meta.values():
            cities_seen.setdefault(meta.city, []).append(meta)

        for city_key, metas in cities_seen.items():
            missing_count = sum(1 for m in metas if m.status == "missing")
            total = len(metas)
            if missing_count == 0:
                city_availability[city_key.capitalize()] = "available"
            elif missing_count < total:
                city_availability[city_key.capitalize()] = "partial"
            else:
                city_availability[city_key.capitalize()] = "unavailable"

        return DataHealthResponse(
            datasets=datasets_out,
            city_availability=city_availability,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_city(self, city_key: str) -> CityDatasets:
        data_dir = os.environ.get("DATA_DIR", "data")
        city_dir = Path(data_dir) / city_key

        loaded: dict[str, gpd.GeoDataFrame] = {}
        missing: list[str] = []

        for layer_name, filename in _LAYERS:
            gdf, success = self._load_layer(city_key, city_dir, layer_name, filename)
            loaded[layer_name] = gdf
            if not success:
                missing.append(layer_name)

        return CityDatasets(
            ev_chargers=loaded["ev_chargers"],
            roads=loaded["roads"],
            parking=loaded["parking"],
            malls=loaded["malls"],
            metro_stations=loaded["metro_stations"],
            tech_parks=loaded["tech_parks"],
            fuel_stations=loaded["fuel_stations"],
            ward_boundaries=loaded["ward_boundaries"],
            population_grid=loaded["population_grid"],
            missing_layers=missing,
        )

    def _load_layer(
        self,
        city_key: str,
        city_dir: Path,
        layer_name: str,
        filename: str,
    ) -> tuple[gpd.GeoDataFrame, bool]:
        """
        Load a single GeoJSON layer, reproject it, and record health metadata.

        Returns ``(gdf, True)`` on success, ``(empty_gdf, False)`` on any
        failure.  Failures are logged as structured ERROR records; they never
        propagate as exceptions.
        """
        meta_key = f"{city_key}/{layer_name}"
        path = city_dir / filename
        t0 = time.perf_counter()

        # --- file existence check -------------------------------------------
        if not path.exists():
            self._record_meta(city_key, layer_name, meta_key, 0, None, "missing")
            logger.error(
                "dataset file not found",
                extra={
                    "event":      "dataset_load_error",
                    "city":       city_key,
                    "layer":      layer_name,
                    "path":       str(path),
                    "error_type": "FileNotFound",
                },
            )
            return _empty_gdf(), False

        # --- parse + reproject -----------------------------------------------
        try:
            gdf = gpd.read_file(path)

            # Ensure CRS is set; GeoJSON that omits the CRS key defaults to
            # WGS-84, which geopandas sometimes leaves as None.
            if gdf.crs is None:
                gdf = gdf.set_crs(epsg=SOURCE_EPSG)

            # Reproject to target metric CRS (design.md §CRS Strategy)
            gdf = gdf.to_crs(epsg=TARGET_EPSG)

            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            now = datetime.now(tz=timezone.utc)
            self._record_meta(city_key, layer_name, meta_key, len(gdf), now, "ok")

            logger.info(
                "dataset loaded",
                extra={
                    "event":       "dataset_loaded",
                    "city":        city_key,
                    "layer":       layer_name,
                    "record_count": len(gdf),
                    "crs":         str(gdf.crs),
                    "duration_ms": duration_ms,
                },
            )
            return gdf, True

        except Exception as exc:
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            self._record_meta(city_key, layer_name, meta_key, 0, None, "missing")
            logger.error(
                "dataset failed to load",
                extra={
                    "event":       "dataset_load_error",
                    "city":        city_key,
                    "layer":       layer_name,
                    "path":        str(path),
                    "error_type":  type(exc).__name__,
                    "error":       str(exc),
                    "duration_ms": duration_ms,
                },
            )
            return _empty_gdf(), False

    def _record_meta(
        self,
        city_key: str,
        layer_name: str,
        meta_key: str,
        record_count: int,
        last_loaded_at: datetime | None,
        status: str,
    ) -> None:
        self._meta[meta_key] = _DatasetMeta(
            city=city_key,
            layer_name=layer_name,
            record_count=record_count,
            last_loaded_at=last_loaded_at,
            status=status,
        )


# ---------------------------------------------------------------------------
# Module-level singleton (used by FastAPI routers)
# ---------------------------------------------------------------------------

#: Application-wide registry instance.  Import and use this in routers:
#:
#:     from app.core.dataset_loader import registry
#:     datasets = registry.load(request.city.lower())
registry: DatasetRegistry = DatasetRegistry()
