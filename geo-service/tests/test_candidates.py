"""
tests/test_candidates.py

Unit tests for app/core/candidates.py.

Coverage:
- Primary path: non-empty parking GDFs produce one candidate per polygon,
  at the centroid coordinate.
- Fallback path: empty parking → deterministic grid.
- Grid determinism: same bbox across repeated calls yields identical output
  (same points, same order).
- Grid independence from input order: the grid does not depend on anything
  other than city_bbox.
- Grid geometry: all points lie within bbox, step spacing is correct.
- CRS: output is always EPSG:32643.
- Edge cases: single parking polygon; parking with Point (degenerate)
  geometry; bbox whose width/height is an exact multiple of GRID_STEP_M.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon, box

from app.core.candidates import GRID_STEP_M, TARGET_EPSG, generate_candidates

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# A realistic UTM 43N bounding box roughly enclosing central Bengaluru.
# Units are metres (EPSG:32643).
_BBOX_STANDARD = box(700_000, 1_420_000, 704_000, 1_424_000)   # 4 km × 4 km
_BBOX_SMALL    = box(700_000, 1_420_000, 700_600, 1_420_600)   # 600 m × 600 m
_BBOX_EXACT    = box(700_000, 1_420_000, 701_500, 1_421_500)   # exact 3×3 steps


def _make_parking_gdf(*polygons: Polygon) -> gpd.GeoDataFrame:
    """Construct a parking GeoDataFrame with EPSG:32643 from the given polygons."""
    return gpd.GeoDataFrame(
        geometry=list(polygons),
        crs=f"EPSG:{TARGET_EPSG}",
    )


def _make_datasets(parking: gpd.GeoDataFrame) -> Any:
    """Minimal CityDatasets-like object exposing only the parking layer."""
    return SimpleNamespace(parking=parking)


def _empty_parking() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=TARGET_EPSG))


# A small square parking polygon centred at (701_000, 1_421_000) in UTM 43N.
_PARKING_POLY_A = Polygon([
    (700_950, 1_420_950),
    (701_050, 1_420_950),
    (701_050, 1_421_050),
    (700_950, 1_421_050),
    (700_950, 1_420_950),
])

_PARKING_POLY_B = Polygon([
    (702_000, 1_422_000),
    (702_200, 1_422_000),
    (702_200, 1_422_200),
    (702_000, 1_422_200),
    (702_000, 1_422_000),
])


# ---------------------------------------------------------------------------
# Primary path: parking centroids
# ---------------------------------------------------------------------------

class TestParkingPath:
    """generate_candidates uses parking centroids when parking is non-empty."""

    def test_returns_geodataframe(self) -> None:
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_candidate_count_equals_parking_polygon_count(self) -> None:
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A, _PARKING_POLY_B))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert len(result) == 2

    def test_single_parking_polygon_yields_one_candidate(self) -> None:
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert len(result) == 1

    def test_candidate_is_centroid_of_parking_polygon(self) -> None:
        """The candidate point must be at the exact centroid of the polygon."""
        poly = _PARKING_POLY_A
        expected = poly.centroid
        datasets = _make_datasets(_make_parking_gdf(poly))
        result = generate_candidates(datasets, _BBOX_STANDARD)

        candidate: Point = result.geometry.iloc[0]
        assert candidate.x == pytest.approx(expected.x, abs=1e-6)
        assert candidate.y == pytest.approx(expected.y, abs=1e-6)

    def test_all_geometries_are_points(self) -> None:
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A, _PARKING_POLY_B))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        for geom in result.geometry:
            assert geom.geom_type == "Point"

    def test_crs_is_target_epsg(self) -> None:
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert result.crs is not None
        assert result.crs.to_epsg() == TARGET_EPSG

    def test_index_is_reset_to_range(self) -> None:
        """Index must be a simple 0..N-1 RangeIndex."""
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A, _PARKING_POLY_B))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert list(result.index) == list(range(len(result)))

    def test_degenerate_point_geometry_in_parking(self) -> None:
        """
        If a 'parking polygon' is actually a Point (degenerate input),
        the centroid of a Point is the Point itself.
        """
        point_geom = Point(701_000, 1_421_000)
        parking = gpd.GeoDataFrame(
            geometry=[point_geom],
            crs=f"EPSG:{TARGET_EPSG}",
        )
        datasets = _make_datasets(parking)
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert len(result) == 1
        assert result.geometry.iloc[0].x == pytest.approx(point_geom.x, abs=1e-6)

    def test_many_parking_polygons(self) -> None:
        """Stress-check with 50 parking polygons."""
        polys = [
            Polygon([
                (700_000 + i * 40,       1_420_000),
                (700_000 + i * 40 + 30,  1_420_000),
                (700_000 + i * 40 + 30,  1_420_030),
                (700_000 + i * 40,       1_420_030),
                (700_000 + i * 40,       1_420_000),
            ])
            for i in range(50)
        ]
        datasets = _make_datasets(_make_parking_gdf(*polys))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert len(result) == 50


# ---------------------------------------------------------------------------
# Fallback path: deterministic grid
# ---------------------------------------------------------------------------

class TestGridFallback:
    """generate_candidates uses a grid when parking is empty."""

    def test_returns_geodataframe(self) -> None:
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_SMALL)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_crs_is_target_epsg(self) -> None:
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_SMALL)
        assert result.crs is not None
        assert result.crs.to_epsg() == TARGET_EPSG

    def test_all_geometries_are_points(self) -> None:
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_SMALL)
        for geom in result.geometry:
            assert geom.geom_type == "Point"

    def test_index_is_reset_to_range(self) -> None:
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_SMALL)
        assert list(result.index) == list(range(len(result)))

    def test_grid_non_empty(self) -> None:
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert len(result) > 0

    def test_all_points_within_bbox_bounds(self) -> None:
        """Every grid point must be within (or on the edge of) the bounding box."""
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_STANDARD)
        minx, miny, maxx, maxy = _BBOX_STANDARD.bounds

        for geom in result.geometry:
            assert minx <= geom.x <= maxx + 1e-6, (
                f"Point x={geom.x} out of bbox [{minx}, {maxx}]"
            )
            assert miny <= geom.y <= maxy + 1e-6, (
                f"Point y={geom.y} out of bbox [{miny}, {maxy}]"
            )

    def test_grid_starts_at_minx_miny(self) -> None:
        """First point in the grid must be at exactly (minx, miny)."""
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_SMALL)
        minx, miny, _, _ = _BBOX_SMALL.bounds

        first: Point = result.geometry.iloc[0]
        assert first.x == pytest.approx(minx, abs=1e-6)
        assert first.y == pytest.approx(miny, abs=1e-6)

    def test_grid_step_spacing_in_x(self) -> None:
        """Consecutive points in the same row must be exactly GRID_STEP_M apart."""
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_STANDARD)

        # Find two adjacent points with the same y (same row)
        minx, miny, _, _ = _BBOX_STANDARD.bounds
        row0 = [g for g in result.geometry if abs(g.y - miny) < 1e-6]
        row0.sort(key=lambda p: p.x)

        assert len(row0) >= 2, "Expected at least two points in the first row"
        for p1, p2 in zip(row0, row0[1:]):
            assert abs(p2.x - p1.x - GRID_STEP_M) < 1e-6, (
                f"x-spacing {p2.x - p1.x:.2f} != GRID_STEP_M {GRID_STEP_M}"
            )

    def test_grid_step_spacing_in_y(self) -> None:
        """Consecutive points in the same column must be exactly GRID_STEP_M apart."""
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_STANDARD)

        minx, miny, _, _ = _BBOX_STANDARD.bounds
        col0 = [g for g in result.geometry if abs(g.x - minx) < 1e-6]
        col0.sort(key=lambda p: p.y)

        assert len(col0) >= 2, "Expected at least two points in the first column"
        for p1, p2 in zip(col0, col0[1:]):
            assert abs(p2.y - p1.y - GRID_STEP_M) < 1e-6, (
                f"y-spacing {p2.y - p1.y:.2f} != GRID_STEP_M {GRID_STEP_M}"
            )

    def test_grid_exact_multiple_bbox(self) -> None:
        """
        When bbox dimensions are exact multiples of GRID_STEP_M, the point
        count must be exactly (width/step + 1) × (height/step + 1).
        _BBOX_EXACT = 1500 m × 1500 m → 4 × 4 = 16 points.
        """
        minx, miny, maxx, maxy = _BBOX_EXACT.bounds
        width  = maxx - minx   # 1500 m
        height = maxy - miny   # 1500 m
        expected_x = int(math.floor(width  / GRID_STEP_M)) + 1   # 4
        expected_y = int(math.floor(height / GRID_STEP_M)) + 1   # 4
        expected_count = expected_x * expected_y                  # 16

        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_EXACT)
        assert len(result) == expected_count

    def test_grid_point_count_matches_formula(self) -> None:
        """
        For an arbitrary bbox the candidate count must equal
        (floor((maxx-minx)/step)+1) × (floor((maxy-miny)/step)+1).
        """
        bbox = _BBOX_STANDARD
        minx, miny, maxx, maxy = bbox.bounds
        expected = (
            (int(math.floor((maxx - minx) / GRID_STEP_M)) + 1)
            * (int(math.floor((maxy - miny) / GRID_STEP_M)) + 1)
        )

        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, bbox)
        assert len(result) == expected


# ---------------------------------------------------------------------------
# Grid determinism — design.md Property 5 note
#
# "The same city and bounding polygon always produce the same candidate set
#  in the same order."
# ---------------------------------------------------------------------------

class TestGridDeterminism:
    """
    The fallback grid must produce identical output across repeated calls
    for the same city bounding polygon.
    """

    def _coords(self, gdf: gpd.GeoDataFrame) -> list[tuple[float, float]]:
        """Extract (x, y) tuples from the geometry column in index order."""
        return [(g.x, g.y) for g in gdf.geometry]

    def test_repeated_calls_same_count(self) -> None:
        """Two calls with the same bbox must return the same number of points."""
        datasets = _make_datasets(_empty_parking())
        r1 = generate_candidates(datasets, _BBOX_STANDARD)
        r2 = generate_candidates(datasets, _BBOX_STANDARD)
        assert len(r1) == len(r2)

    def test_repeated_calls_identical_coordinates(self) -> None:
        """
        Two calls with the same bbox must return points with identical
        (x, y) values in the same order (design.md Candidate Generation).
        """
        datasets = _make_datasets(_empty_parking())
        r1 = generate_candidates(datasets, _BBOX_STANDARD)
        r2 = generate_candidates(datasets, _BBOX_STANDARD)

        coords1 = self._coords(r1)
        coords2 = self._coords(r2)

        assert coords1 == coords2, (
            "Grid is not deterministic: coordinates differed between calls"
        )

    def test_repeated_calls_identical_coordinates_small_bbox(self) -> None:
        """Determinism holds for a small bbox as well."""
        datasets = _make_datasets(_empty_parking())
        r1 = generate_candidates(datasets, _BBOX_SMALL)
        r2 = generate_candidates(datasets, _BBOX_SMALL)
        assert self._coords(r1) == self._coords(r2)

    def test_repeated_calls_identical_coordinates_exact_bbox(self) -> None:
        """Determinism holds when bbox dimensions are exact multiples of step."""
        datasets = _make_datasets(_empty_parking())
        r1 = generate_candidates(datasets, _BBOX_EXACT)
        r2 = generate_candidates(datasets, _BBOX_EXACT)
        assert self._coords(r1) == self._coords(r2)

    def test_independent_datasets_same_grid(self) -> None:
        """
        The grid must not depend on the datasets object — two separate
        _empty_parking() instances with the same bbox must produce the same
        grid (the grid is purely a function of city_bbox).
        """
        ds_a = _make_datasets(_empty_parking())
        ds_b = _make_datasets(_empty_parking())
        r_a = generate_candidates(ds_a, _BBOX_STANDARD)
        r_b = generate_candidates(ds_b, _BBOX_STANDARD)

        assert self._coords(r_a) == self._coords(r_b)

    def test_different_bbox_yields_different_grid(self) -> None:
        """Different bounding boxes must produce different grids."""
        datasets = _make_datasets(_empty_parking())
        r_std   = generate_candidates(datasets, _BBOX_STANDARD)
        r_small = generate_candidates(datasets, _BBOX_SMALL)
        # The point counts alone will differ; coords also differ.
        assert self._coords(r_std) != self._coords(r_small)

    def test_row_major_order(self) -> None:
        """
        Points must be in row-major order: y (outer loop) advances slowest,
        x (inner loop) advances fastest.  Concretely: for the first two
        points, they share the same y (first row) and differ in x.
        """
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_SMALL)
        assert len(result) >= 2

        p0: Point = result.geometry.iloc[0]
        p1: Point = result.geometry.iloc[1]
        # Both in the first row → same y
        assert p0.y == pytest.approx(p1.y, abs=1e-6), (
            "Expected row-major order: first two points should share y coordinate"
        )
        # x of second point is one step ahead
        assert p1.x == pytest.approx(p0.x + GRID_STEP_M, abs=1e-6), (
            "Expected row-major order: second point x should be p0.x + GRID_STEP_M"
        )

    def test_five_independent_calls_all_identical(self) -> None:
        """Running five independent calls must all return the same coordinates."""
        datasets = _make_datasets(_empty_parking())
        results = [generate_candidates(datasets, _BBOX_STANDARD) for _ in range(5)]
        ref_coords = self._coords(results[0])
        for i, r in enumerate(results[1:], start=1):
            assert self._coords(r) == ref_coords, (
                f"Call {i + 1} produced different coordinates from call 1"
            )


# ---------------------------------------------------------------------------
# CRS contract (both paths)
# ---------------------------------------------------------------------------

class TestCRS:
    """Output CRS must always be EPSG:32643 regardless of path taken."""

    def test_parking_path_crs(self) -> None:
        datasets = _make_datasets(_make_parking_gdf(_PARKING_POLY_A))
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert result.crs.to_epsg() == TARGET_EPSG

    def test_grid_path_crs(self) -> None:
        datasets = _make_datasets(_empty_parking())
        result = generate_candidates(datasets, _BBOX_STANDARD)
        assert result.crs.to_epsg() == TARGET_EPSG

    def test_crs_not_none(self) -> None:
        for datasets in (
            _make_datasets(_make_parking_gdf(_PARKING_POLY_A)),
            _make_datasets(_empty_parking()),
        ):
            result = generate_candidates(datasets, _BBOX_STANDARD)
            assert result.crs is not None
