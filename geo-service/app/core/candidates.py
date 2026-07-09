"""
app/core/candidates.py — ChargeWise India Geo Service

Generates the candidate set that the Scorer will rank.

Two paths (design.md §Scoring Algorithm / Candidate Generation):

Primary path
  Return the centroid of every parking polygon.  Each centroid is a Point
  in EPSG:32643, derived directly from ``datasets.parking``.

Fallback path (parking layer empty or absent)
  Generate a deterministic uniform grid at ~500 m spacing across
  ``city_bbox``.  The grid is produced by iterating fixed-step integer
  offsets from the bounding box's (minx, miny) corner — no randomness, no
  hashing — so the same bounding polygon always yields the same candidate
  set in the same order (design.md Property 5, Candidate Generation note).

All returned Points are in EPSG:32643.

Public interface
---------------
    generate_candidates(datasets, city_bbox) -> gpd.GeoDataFrame

    The returned GeoDataFrame has:
      • a geometry column of shapely Point objects in EPSG:32643
      • a RangeIndex reset to 0..N-1
      • CRS == EPSG:32643

Design notes
-----------
* ``city_bbox`` must be a shapely geometry or any object that exposes
  ``.bounds`` returning ``(minx, miny, maxx, maxy)`` in EPSG:32643 metres.
  Typically this is the union of ``datasets.ward_boundaries.geometry`` or a
  pre-computed bounding box polygon stored on disk.

* GRID_STEP_M is intentionally a module constant (not a parameter) so the
  grid is reproducible without callers needing to track it.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, MultiPoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_EPSG: int = 32643          # UTM Zone 43N — must match dataset_loader
GRID_STEP_M: float = 500.0        # metres between grid points (design.md ~500 m)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_candidates(
    datasets: Any,          # CityDatasets — typed loosely to avoid a circular import
    city_bbox: Any,         # shapely geometry or object with .bounds in EPSG:32643
) -> gpd.GeoDataFrame:
    """
    Return a GeoDataFrame of candidate Point locations in EPSG:32643.

    Primary path
    ~~~~~~~~~~~~
    When ``datasets.parking`` is non-empty, each parking polygon centroid
    becomes a candidate.  The centroids are already in EPSG:32643 because
    the DatasetRegistry reprojects every layer at load time.

    Fallback path
    ~~~~~~~~~~~~~
    When ``datasets.parking`` is empty (layer missing or no features), a
    deterministic uniform grid is generated across ``city_bbox`` at
    ``GRID_STEP_M`` spacing.  The grid starts at the exact (minx, miny)
    corner of the bounding box and advances in equal steps along both axes,
    guaranteeing identical output for the same input geometry on every call.

    Parameters
    ----------
    datasets:
        A ``CityDatasets`` instance.  Only ``datasets.parking`` is consumed.
    city_bbox:
        A Shapely geometry (or any object with a ``.bounds`` property) whose
        bounding box is in EPSG:32643 metres.  Used only when the fallback
        path is triggered.

    Returns
    -------
    gpd.GeoDataFrame
        Candidate Point geometries, CRS == EPSG:32643, index reset to 0..N-1.
        Guaranteed non-empty as long as ``city_bbox`` has positive area.
    """
    parking: gpd.GeoDataFrame = datasets.parking

    if len(parking) > 0:
        return _candidates_from_parking(parking)

    logger.warning(
        "parking layer empty — falling back to deterministic grid",
        extra={"grid_step_m": GRID_STEP_M},
    )
    return _candidates_from_grid(city_bbox)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _candidates_from_parking(parking: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return parking polygon centroids as candidate Points.

    Polygons that already are Points (degenerate geometry) are included as-is.
    The CRS is preserved from the input GeoDataFrame (EPSG:32643).
    """
    centroids: gpd.GeoSeries = parking.geometry.centroid

    gdf = gpd.GeoDataFrame(geometry=centroids, crs=parking.crs)
    gdf = gdf.reset_index(drop=True)

    logger.info(
        "candidates generated from parking centroids",
        extra={"candidate_count": len(gdf)},
    )
    return gdf


def _candidates_from_grid(city_bbox: Any) -> gpd.GeoDataFrame:
    """Return a deterministic uniform grid of Points over *city_bbox*.

    Grid construction
    ~~~~~~~~~~~~~~~~~
    Uses vectorised numpy meshgrid — no Python-level Point() loop.
    Given the bounding box (minx, miny, maxx, maxy) in EPSG:32643 metres:

    1. Build 1-D arrays of x and y coordinates via ``np.arange``.
    2. ``np.meshgrid`` produces all (x, y) pairs at once.
    3. ``gpd.points_from_xy`` converts the flat coordinate arrays to a
       GeoSeries without any Python-level iteration.

    Points are emitted in row-major order (y outer, x inner) — same
    sequence as the previous list-comprehension approach, so the output
    is identical and deterministic for the same bbox.
    """
    minx, miny, maxx, maxy = city_bbox.bounds

    xs = np.arange(minx, maxx + GRID_STEP_M, GRID_STEP_M)
    ys = np.arange(miny, maxy + GRID_STEP_M, GRID_STEP_M)

    # meshgrid in 'ij' indexing then flatten so y is outer (row-major).
    xx, yy = np.meshgrid(xs, ys)          # shape (n_y, n_x)
    coords_x = xx.ravel()
    coords_y = yy.ravel()

    geometry = gpd.points_from_xy(coords_x, coords_y)
    gdf = gpd.GeoDataFrame(geometry=geometry, crs=f"EPSG:{TARGET_EPSG}")
    gdf = gdf.reset_index(drop=True)

    logger.info(
        "candidates generated from deterministic grid",
        extra={
            "candidate_count": len(gdf),
            "x_steps": len(xs),
            "y_steps": len(ys),
            "grid_step_m": GRID_STEP_M,
            "bbox": (minx, miny, maxx, maxy),
        },
    )
    return gdf
