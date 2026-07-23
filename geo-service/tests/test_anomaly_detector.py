"""
Tests for app/core/anomaly_detector.py — AI Enhancement 1.

Covers all six detection rules with targeted examples and edge cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from app.core.anomaly_detector import AnomalyDetector
from app.models.schemas import AnomalySeverity

TARGET_EPSG = 32643


# ---------------------------------------------------------------------------
# Fixtures — minimal CityDatasets stub
# ---------------------------------------------------------------------------


def _empty_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=TARGET_EPSG))


@dataclass
class FakeCityDatasets:
    """Minimal stub matching the fields AnomalyDetector accesses."""

    ev_chargers: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    roads: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    parking: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    malls: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    metro_stations: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    tech_parks: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    fuel_stations: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    ward_boundaries: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    population_grid: gpd.GeoDataFrame = field(default_factory=_empty_gdf)
    missing_layers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Test: INVALID_GEOMETRY
# ---------------------------------------------------------------------------


class TestInvalidGeometry:
    def test_valid_geometries_produce_no_findings(self):
        gdf = gpd.GeoDataFrame(
            geometry=[Point(500000, 1400000), Point(500100, 1400100)],
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(ev_chargers=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        invalid_findings = [f for f in report.findings if f.rule_id == "INVALID_GEOMETRY"]
        assert len(invalid_findings) == 0

    def test_self_intersecting_polygon_is_flagged(self):
        # Bowtie polygon — self-intersecting
        bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
        gdf = gpd.GeoDataFrame(
            geometry=[bowtie, Point(500000, 1400000)],
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(parking=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        invalid_findings = [f for f in report.findings if f.rule_id == "INVALID_GEOMETRY"]
        assert len(invalid_findings) == 1
        assert invalid_findings[0].severity == AnomalySeverity.ERROR
        assert 0 in invalid_findings[0].affected_features


# ---------------------------------------------------------------------------
# Test: DUPLICATE_CLUSTER
# ---------------------------------------------------------------------------


class TestDuplicateCluster:
    def test_no_clusters_when_points_are_spread(self):
        pytest.importorskip("sklearn")
        # Points 1 km apart — no cluster
        points = [Point(500000 + i * 1000, 1400000) for i in range(5)]
        gdf = gpd.GeoDataFrame(
            {"name": ["Station A", "Station B", "Station C", "Station D", "Station E"]},
            geometry=points,
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(ev_chargers=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        cluster_findings = [f for f in report.findings if f.rule_id == "DUPLICATE_CLUSTER"]
        assert len(cluster_findings) == 0

    def test_cluster_with_identical_names_is_flagged(self):
        pytest.importorskip("sklearn")
        # 4 points within 10 m, all named "Charger X"
        base_x, base_y = 500000, 1400000
        points = [
            Point(base_x, base_y),
            Point(base_x + 5, base_y + 5),
            Point(base_x + 10, base_y),
            Point(base_x + 5, base_y - 5),
        ]
        gdf = gpd.GeoDataFrame(
            {"name": ["Charger X"] * 4},
            geometry=points,
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(ev_chargers=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        cluster_findings = [f for f in report.findings if f.rule_id == "DUPLICATE_CLUSTER"]
        assert len(cluster_findings) >= 1
        assert cluster_findings[0].severity == AnomalySeverity.WARNING


# ---------------------------------------------------------------------------
# Test: SUSPICIOUS_UNIFORM_POP
# ---------------------------------------------------------------------------


class TestSuspiciousUniformPop:
    def test_diverse_population_values_no_finding(self):
        # All different values
        polys = [
            Polygon([(i * 100, 0), (i * 100 + 100, 0), (i * 100 + 100, 100), (i * 100, 100)])
            for i in range(10)
        ]
        gdf = gpd.GeoDataFrame(
            {"population": [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]},
            geometry=polys,
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(population_grid=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        uniform_findings = [f for f in report.findings if f.rule_id == "SUSPICIOUS_UNIFORM_POP"]
        assert len(uniform_findings) == 0

    def test_uniform_population_is_flagged(self):
        # 90% of cells share value 42
        polys = [
            Polygon([(i * 100, 0), (i * 100 + 100, 0), (i * 100 + 100, 100), (i * 100, 100)])
            for i in range(10)
        ]
        pops = [42] * 9 + [100]  # 90% share value 42
        gdf = gpd.GeoDataFrame(
            {"population": pops},
            geometry=polys,
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(population_grid=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        uniform_findings = [f for f in report.findings if f.rule_id == "SUSPICIOUS_UNIFORM_POP"]
        assert len(uniform_findings) == 1
        assert uniform_findings[0].severity == AnomalySeverity.ERROR


# ---------------------------------------------------------------------------
# Test: IMPLAUSIBLE_DENSITY
# ---------------------------------------------------------------------------


class TestImplausibleDensity:
    def test_normal_density_no_finding(self):
        # 1 km² polygon with 10000 people — normal density
        poly = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        gdf = gpd.GeoDataFrame(
            {"population": [10000]},
            geometry=[poly],
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(population_grid=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        density_findings = [f for f in report.findings if f.rule_id == "IMPLAUSIBLE_DENSITY"]
        assert len(density_findings) == 0

    def test_extreme_density_is_flagged(self):
        # 1 km² polygon with 300000 people — impossible
        poly = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        gdf = gpd.GeoDataFrame(
            {"population": [300000]},
            geometry=[poly],
            crs=TARGET_EPSG,
        )
        datasets = FakeCityDatasets(population_grid=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        density_findings = [f for f in report.findings if f.rule_id == "IMPLAUSIBLE_DENSITY"]
        assert len(density_findings) == 1
        assert density_findings[0].severity == AnomalySeverity.WARNING


# ---------------------------------------------------------------------------
# Test: ZERO_AREA_POLYGON
# ---------------------------------------------------------------------------


class TestZeroAreaPolygon:
    def test_normal_polygons_no_finding(self):
        poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
        gdf = gpd.GeoDataFrame(geometry=[poly], crs=TARGET_EPSG)
        datasets = FakeCityDatasets(parking=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        zero_findings = [f for f in report.findings if f.rule_id == "ZERO_AREA_POLYGON"]
        assert len(zero_findings) == 0

    def test_degenerate_polygon_is_flagged(self):
        # A "polygon" that's actually a line (zero area)
        degenerate = Polygon([(0, 0), (100, 0), (100, 0), (0, 0)])
        normal = Polygon([(200, 200), (300, 200), (300, 300), (200, 300)])
        gdf = gpd.GeoDataFrame(geometry=[degenerate, normal], crs=TARGET_EPSG)
        datasets = FakeCityDatasets(parking=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        zero_findings = [f for f in report.findings if f.rule_id == "ZERO_AREA_POLYGON"]
        assert len(zero_findings) == 1
        assert 0 in zero_findings[0].affected_features


# ---------------------------------------------------------------------------
# Test: ORPHAN_ROAD_SEGMENT
# ---------------------------------------------------------------------------


class TestOrphanRoadSegment:
    def test_normal_roads_no_finding(self):
        road = LineString([(0, 0), (1000, 0)])  # 1 km long
        gdf = gpd.GeoDataFrame(geometry=[road], crs=TARGET_EPSG)
        datasets = FakeCityDatasets(roads=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        orphan_findings = [f for f in report.findings if f.rule_id == "ORPHAN_ROAD_SEGMENT"]
        assert len(orphan_findings) == 0

    def test_very_short_segment_is_flagged(self):
        short_road = LineString([(0, 0), (3, 0)])  # 3 m — below threshold
        normal_road = LineString([(0, 100), (500, 100)])
        gdf = gpd.GeoDataFrame(geometry=[short_road, normal_road], crs=TARGET_EPSG)
        datasets = FakeCityDatasets(roads=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        orphan_findings = [f for f in report.findings if f.rule_id == "ORPHAN_ROAD_SEGMENT"]
        assert len(orphan_findings) == 1
        assert orphan_findings[0].severity == AnomalySeverity.INFO
        assert 0 in orphan_findings[0].affected_features


# ---------------------------------------------------------------------------
# Test: Full scan produces a valid AnomalyReport
# ---------------------------------------------------------------------------


class TestFullScan:
    def test_empty_datasets_produce_empty_report(self):
        datasets = FakeCityDatasets()
        detector = AnomalyDetector("empty_city", datasets)
        report = detector.scan()

        assert report.total_findings == 0
        assert report.layers_scanned == 0
        assert report.scan_duration_ms >= 0

    def test_scan_duration_is_recorded(self):
        poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
        gdf = gpd.GeoDataFrame(geometry=[poly], crs=TARGET_EPSG)
        datasets = FakeCityDatasets(parking=gdf)
        detector = AnomalyDetector("test_city", datasets)
        report = detector.scan()

        assert report.scan_duration_ms > 0
        assert report.scanned_at != ""
