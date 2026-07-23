"""
app/core/anomaly_detector.py — ChargeWise India Geo Service

Automated data quality scanner that flags anomalies in loaded GeoJSON
datasets before they silently degrade scoring accuracy.

Design reference: design.md §AI Enhancement 1: Anomaly Detection on Input Data

Detection rules
---------------
  DUPLICATE_CLUSTER      — DBSCAN on projected points; flag clusters with
                           identical name/operator tags.
  SUSPICIOUS_UNIFORM_POP — >80% of non-zero population cells share a value.
  INVALID_GEOMETRY       — Shapely is_valid check on every feature.
  IMPLAUSIBLE_DENSITY    — Population > 200 000 / km² in a grid cell.
  ZERO_AREA_POLYGON      — Polygons with area == 0 after projection.
  ORPHAN_ROAD_SEGMENT    — LineString features with length < 5 m.

Performance constraint: full scan for one city (nine layers, ~5 000 total
features) must complete in < 2 s using vectorised operations only.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from app.models.schemas import AnomalyFinding, AnomalyReport, AnomalySeverity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DBSCAN parameters for duplicate cluster detection
_DBSCAN_EPS_M: float = 25.0        # max distance between points in a cluster
_DBSCAN_MIN_SAMPLES: int = 3       # min points to form a cluster

# Population anomaly thresholds
_UNIFORM_POP_THRESHOLD: float = 0.80   # fraction of cells sharing same value
_MAX_POP_DENSITY_PER_KM2: float = 200_000.0  # physical impossibility threshold

# Road segment minimum length
_MIN_ROAD_LENGTH_M: float = 5.0

# Layers eligible for duplicate cluster detection
_POINT_LAYERS_FOR_CLUSTERING: list[str] = ["ev_chargers", "fuel_stations"]

# Layers eligible for zero-area polygon detection
_POLYGON_LAYERS: list[str] = ["parking", "malls", "ward_boundaries"]


# ---------------------------------------------------------------------------
# AnomalyDetector
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """
    Stateless scanner — instantiated with a city name and its loaded
    CityDatasets object. Runs all detection rules and returns a
    consolidated AnomalyReport.

    Usage::

        from app.core.dataset_loader import CityDatasets
        detector = AnomalyDetector("bengaluru", datasets)
        report = detector.scan()
    """

    def __init__(self, city: str, datasets: Any) -> None:
        self._city = city
        self._datasets = datasets

    def scan(self) -> AnomalyReport:
        """Run all detection rules and return a consolidated report."""
        t0 = time.perf_counter()
        findings: list[AnomalyFinding] = []
        layers_scanned = 0

        # Iterate all layer fields on CityDatasets (excluding missing_layers)
        layer_names = [
            "ev_chargers", "roads", "parking", "malls",
            "metro_stations", "tech_parks", "fuel_stations",
            "ward_boundaries", "population_grid",
        ]

        for layer_name in layer_names:
            gdf: gpd.GeoDataFrame = getattr(self._datasets, layer_name)
            if len(gdf) == 0:
                continue
            layers_scanned += 1

            # Rule: INVALID_GEOMETRY — applies to all layers
            findings.extend(self._detect_invalid_geometries(gdf, layer_name))

            # Rule: DUPLICATE_CLUSTER — point layers only
            if layer_name in _POINT_LAYERS_FOR_CLUSTERING:
                findings.extend(self._detect_duplicate_clusters(gdf, layer_name))

            # Rule: ZERO_AREA_POLYGON — polygon layers only
            if layer_name in _POLYGON_LAYERS:
                findings.extend(self._detect_zero_area_polygons(gdf, layer_name))

            # Rule: ORPHAN_ROAD_SEGMENT — roads only
            if layer_name == "roads":
                findings.extend(self._detect_orphan_road_segments(gdf))

            # Population-specific rules
            if layer_name == "population_grid":
                findings.extend(self._detect_uniform_population(gdf))
                findings.extend(self._detect_implausible_density(gdf))

        duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        report = AnomalyReport(
            scanned_at=datetime.now(tz=timezone.utc).isoformat(),
            total_findings=len(findings),
            findings=findings,
            layers_scanned=layers_scanned,
            scan_duration_ms=duration_ms,
        )

        logger.info(
            "anomaly scan complete",
            extra={
                "city": self._city,
                "total_findings": len(findings),
                "layers_scanned": layers_scanned,
                "duration_ms": duration_ms,
            },
        )

        return report

    # ------------------------------------------------------------------
    # Rule: INVALID_GEOMETRY
    # ------------------------------------------------------------------

    def _detect_invalid_geometries(
        self, gdf: gpd.GeoDataFrame, layer: str
    ) -> list[AnomalyFinding]:
        """Shapely is_valid check on every feature."""
        invalid_mask = ~gdf.geometry.is_valid
        if not invalid_mask.any():
            return []

        invalid_indices = gdf.index[invalid_mask].tolist()

        # Build per-feature messages using explain_validity (vectorised where possible)
        messages: list[str] = []
        for idx in invalid_indices[:10]:  # cap detail messages at 10
            from shapely.validation import explain_validity
            reason = explain_validity(gdf.geometry.iloc[idx])
            messages.append(f"Feature {idx}: {reason}")

        return [
            AnomalyFinding(
                rule_id="INVALID_GEOMETRY",
                layer=layer,
                city=self._city,
                severity=AnomalySeverity.ERROR,
                message=(
                    f"{len(invalid_indices)} invalid geometries detected"
                    + (f" (e.g. {messages[0]})" if messages else "")
                ),
                affected_features=invalid_indices[:100],  # cap at 100
            )
        ]

    # ------------------------------------------------------------------
    # Rule: DUPLICATE_CLUSTER
    # ------------------------------------------------------------------

    def _detect_duplicate_clusters(
        self, gdf: gpd.GeoDataFrame, layer: str
    ) -> list[AnomalyFinding]:
        """
        DBSCAN on projected centroids; flag clusters where features share
        identical name or operator tags.
        """
        if len(gdf) < _DBSCAN_MIN_SAMPLES:
            return []

        try:
            from sklearn.cluster import DBSCAN
        except ImportError:
            logger.debug("scikit-learn not available; skipping DUPLICATE_CLUSTER rule")
            return []

        # Extract coordinates as a numpy array for DBSCAN
        coords = np.column_stack([
            gdf.geometry.centroid.x,
            gdf.geometry.centroid.y,
        ])

        clustering = DBSCAN(
            eps=_DBSCAN_EPS_M,
            min_samples=_DBSCAN_MIN_SAMPLES,
            metric="euclidean",
        ).fit(coords)

        labels = clustering.labels_
        # -1 means noise (not in any cluster)
        cluster_ids = set(labels) - {-1}
        if not cluster_ids:
            return []

        findings: list[AnomalyFinding] = []

        # Check each cluster for identical name/operator tags
        name_col = "name" if "name" in gdf.columns else None
        operator_col = "operator" if "operator" in gdf.columns else None

        for cluster_id in cluster_ids:
            mask = labels == cluster_id
            cluster_indices = gdf.index[mask].tolist()

            # Check if features in this cluster share a name or operator
            is_duplicate = False
            if name_col:
                names = gdf.loc[mask, name_col].dropna().unique()
                if len(names) == 1:
                    is_duplicate = True
            if not is_duplicate and operator_col:
                operators = gdf.loc[mask, operator_col].dropna().unique()
                if len(operators) == 1:
                    is_duplicate = True

            if is_duplicate:
                centroid = gdf.geometry.iloc[cluster_indices[0]].centroid
                findings.append(
                    AnomalyFinding(
                        rule_id="DUPLICATE_CLUSTER",
                        layer=layer,
                        city=self._city,
                        severity=AnomalySeverity.WARNING,
                        message=(
                            f"Cluster of {len(cluster_indices)} features within "
                            f"{_DBSCAN_EPS_M} m sharing identical tags"
                        ),
                        affected_features=cluster_indices,
                        geometry={
                            "type": "Point",
                            "coordinates": [centroid.x, centroid.y],
                        },
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # Rule: SUSPICIOUS_UNIFORM_POP
    # ------------------------------------------------------------------

    def _detect_uniform_population(
        self, pop_grid: gpd.GeoDataFrame
    ) -> list[AnomalyFinding]:
        """Flag grids where > 80% of non-zero cells share a single value."""
        if "population" not in pop_grid.columns:
            return []

        pop_values = pop_grid["population"]
        non_zero = pop_values[pop_values > 0]

        if len(non_zero) == 0:
            return []

        # Find the most common non-zero value
        value_counts = non_zero.value_counts()
        most_common_value = value_counts.index[0]
        most_common_count = value_counts.iloc[0]
        fraction = most_common_count / len(non_zero)

        if fraction <= _UNIFORM_POP_THRESHOLD:
            return []

        affected = pop_grid.index[pop_values == most_common_value].tolist()

        return [
            AnomalyFinding(
                rule_id="SUSPICIOUS_UNIFORM_POP",
                layer="population_grid",
                city=self._city,
                severity=AnomalySeverity.ERROR,
                message=(
                    f"{fraction:.0%} of non-zero population cells share the "
                    f"value {most_common_value} — possible data corruption"
                ),
                affected_features=affected[:100],
            )
        ]

    # ------------------------------------------------------------------
    # Rule: IMPLAUSIBLE_DENSITY
    # ------------------------------------------------------------------

    def _detect_implausible_density(
        self, pop_grid: gpd.GeoDataFrame
    ) -> list[AnomalyFinding]:
        """Grid cells with population > 200 000 / km²."""
        if "population" not in pop_grid.columns:
            return []

        # Compute area in km² (CRS is EPSG:32643, so area is in m²)
        areas_km2 = pop_grid.geometry.area / 1_000_000.0
        # Avoid division by zero for degenerate geometries
        areas_km2 = areas_km2.replace(0, np.nan)

        density = pop_grid["population"] / areas_km2
        implausible_mask = density > _MAX_POP_DENSITY_PER_KM2

        if not implausible_mask.any():
            return []

        affected = pop_grid.index[implausible_mask].tolist()

        return [
            AnomalyFinding(
                rule_id="IMPLAUSIBLE_DENSITY",
                layer="population_grid",
                city=self._city,
                severity=AnomalySeverity.WARNING,
                message=(
                    f"{len(affected)} grid cells exceed {_MAX_POP_DENSITY_PER_KM2:,.0f} "
                    f"pop/km² — physically implausible"
                ),
                affected_features=affected[:100],
            )
        ]

    # ------------------------------------------------------------------
    # Rule: ZERO_AREA_POLYGON
    # ------------------------------------------------------------------

    def _detect_zero_area_polygons(
        self, gdf: gpd.GeoDataFrame, layer: str
    ) -> list[AnomalyFinding]:
        """Polygons with area == 0 after projection to EPSG:32643."""
        # Only check polygon/multipolygon geometries
        is_polygon = gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
        polygons = gdf[is_polygon]

        if len(polygons) == 0:
            return []

        zero_area_mask = polygons.geometry.area == 0
        if not zero_area_mask.any():
            return []

        affected = polygons.index[zero_area_mask].tolist()

        return [
            AnomalyFinding(
                rule_id="ZERO_AREA_POLYGON",
                layer=layer,
                city=self._city,
                severity=AnomalySeverity.ERROR,
                message=f"{len(affected)} polygons have zero area after projection",
                affected_features=affected[:100],
            )
        ]

    # ------------------------------------------------------------------
    # Rule: ORPHAN_ROAD_SEGMENT
    # ------------------------------------------------------------------

    def _detect_orphan_road_segments(
        self, roads_gdf: gpd.GeoDataFrame
    ) -> list[AnomalyFinding]:
        """LineString features with length < 5 m (digitisation artifacts)."""
        is_line = roads_gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])
        lines = roads_gdf[is_line]

        if len(lines) == 0:
            return []

        short_mask = lines.geometry.length < _MIN_ROAD_LENGTH_M
        if not short_mask.any():
            return []

        affected = lines.index[short_mask].tolist()

        return [
            AnomalyFinding(
                rule_id="ORPHAN_ROAD_SEGMENT",
                layer="roads",
                city=self._city,
                severity=AnomalySeverity.INFO,
                message=(
                    f"{len(affected)} road segments shorter than "
                    f"{_MIN_ROAD_LENGTH_M} m — likely digitisation artifacts"
                ),
                affected_features=affected[:100],
            )
        ]
