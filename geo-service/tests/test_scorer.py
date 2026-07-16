"""
tests/test_scorer.py

Unit tests for app/core/scorer.py — all five spatial factors.

All geometry is synthetic and hand-crafted in EPSG:32643 (UTM Zone 43N,
metres) so that expected values can be verified by inspection without
running any spatial library.

Coordinate system
-----------------
Tests operate in a fictional metric space anchored near Bengaluru's UTM
zone (easting ~700 000, northing ~1 420 000).  All distances are in metres.

Test layout
-----------
TestPopulationFactorBasic          — 3 candidates, known pop sums
TestPopulationFactorBoundaries     — clip, exact normaliser, zero
TestPopulationFactorEmptyGrid      — missing layer → zeros
TestPopulationFactorBuffer         — fixed 1 000 m regardless of search_radius
TestPopulationFactorIndexAlignment — index preservation
TestChargerDistanceFactorBasic     — 3 candidates, known distances
TestChargerDistanceFactorBoundary  — exact radius, 0 m, just-in, just-out
TestChargerDistanceFactorEmptyChargers — missing layer → 100s
TestChargerDistanceFactorIndexAlignment
TestChargerDistanceFactorDeduplication
TestRoadProximityFactorBasic       — binary 0/100, ROAD_PROXIMITY_M threshold
TestRoadProximityFactorBoundary    — exactly at threshold, just-in, just-out
TestRoadProximityFactorEmptyRoads  — missing layer → zeros
TestRoadProximityFactorIndexAlignment
TestParkingFactorBasic             — intersecting / non-intersecting candidates
TestParkingFactorOverlapping       — candidate touching two overlapping polygons
                                     must still count as exactly one match (→ 100,
                                     not a duplicated row)
TestParkingFactorBoundary          — point on polygon boundary, fully outside
TestParkingFactorEmptyParking      — missing layer → zeros
TestParkingFactorIndexAlignment    — index preservation
TestMallProximityFactorBasic       — binary 0/100, MALL_PROXIMITY_M threshold
TestMallProximityFactorBoundary    — exactly at threshold, just-in, just-out
TestMallProximityFactorEmptyMalls  — missing layer → zeros
TestMallProximityFactorIndexAlignment
TestScoreBatch                     — 5-factor weighted score
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from app.core.scorer import (
    MALL_PROXIMITY_M,
    POPULATION_BUFFER_M,
    POPULATION_NORMALISER,
    ROAD_PROXIMITY_M,
    Scorer,
    WEIGHTS,
    WEIGHTS_BY_TYPE,
)

# ---------------------------------------------------------------------------
# CRS & shared constants
# ---------------------------------------------------------------------------

EPSG = 32643   # must match scorer.TARGET_EPSG (via dataset_loader constant)

# Origin point used as the anchor for all synthetic geometry.
# Roughly central Bengaluru in UTM 43N.
OX: float = 700_000.0
OY: float = 1_420_000.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _candidates(*coords: tuple[float, float]) -> gpd.GeoDataFrame:
    """Build a candidate GeoDataFrame (Points) in EPSG:32643."""
    return gpd.GeoDataFrame(
        geometry=[Point(x, y) for x, y in coords],
        crs=f"EPSG:{EPSG}",
    )


def _square_cell(cx: float, cy: float, half: float, population: int) -> dict:
    """Return a dict describing a square grid cell centred at (cx, cy)."""
    return {
        "geometry": Polygon([
            (cx - half, cy - half),
            (cx + half, cy - half),
            (cx + half, cy + half),
            (cx - half, cy + half),
            (cx - half, cy - half),
        ]),
        "population": population,
    }


def _pop_grid(*cells: dict) -> gpd.GeoDataFrame:
    """Build a population_grid GeoDataFrame (Polygon + population column)."""
    return gpd.GeoDataFrame(cells, crs=f"EPSG:{EPSG}")


def _empty_pop_grid() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"population": gpd.pd.Series([], dtype=int)},
        geometry=gpd.GeoSeries([], crs=EPSG),
    )


def _chargers(*coords: tuple[float, float]) -> gpd.GeoDataFrame:
    """Build an ev_chargers GeoDataFrame (Points) in EPSG:32643."""
    return gpd.GeoDataFrame(
        geometry=[Point(x, y) for x, y in coords],
        crs=f"EPSG:{EPSG}",
    )


def _empty_chargers() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=EPSG))


def _roads(*coords_pairs: tuple[tuple[float, float], tuple[float, float]]) -> gpd.GeoDataFrame:
    """Build a roads GeoDataFrame (LineStrings) in EPSG:32643."""
    from shapely.geometry import LineString
    return gpd.GeoDataFrame(
        geometry=[LineString([a, b]) for a, b in coords_pairs],
        crs=f"EPSG:{EPSG}",
    )


def _road_point(*coords: tuple[float, float]) -> gpd.GeoDataFrame:
    """Build a roads GeoDataFrame from Points (simplest geometry for proximity tests)."""
    return gpd.GeoDataFrame(
        geometry=[Point(x, y) for x, y in coords],
        crs=f"EPSG:{EPSG}",
    )


def _empty_roads() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=EPSG))


def _malls(*coords: tuple[float, float]) -> gpd.GeoDataFrame:
    """Build a malls GeoDataFrame (Points) in EPSG:32643."""
    return gpd.GeoDataFrame(
        geometry=[Point(x, y) for x, y in coords],
        crs=f"EPSG:{EPSG}",
    )


def _empty_malls() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=EPSG))


def _parking_poly(*polygons: Polygon) -> gpd.GeoDataFrame:
    """Build a parking GeoDataFrame (Polygons) in EPSG:32643."""
    return gpd.GeoDataFrame(geometry=list(polygons), crs=f"EPSG:{EPSG}")


def _empty_parking() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=EPSG))


def _square_parking(cx: float, cy: float, half: float) -> Polygon:
    """Return a square Polygon centred at (cx, cy) with given half-width."""
    return Polygon([
        (cx - half, cy - half),
        (cx + half, cy - half),
        (cx + half, cy + half),
        (cx - half, cy + half),
        (cx - half, cy - half),
    ])


def _datasets(
    population_grid: gpd.GeoDataFrame,
    ev_chargers: gpd.GeoDataFrame | None = None,
    roads: gpd.GeoDataFrame | None = None,
    parking: gpd.GeoDataFrame | None = None,
    malls: gpd.GeoDataFrame | None = None,
) -> SimpleNamespace:
    """Minimal CityDatasets stub."""
    return SimpleNamespace(
        population_grid=population_grid,
        ev_chargers=ev_chargers if ev_chargers is not None else _empty_chargers(),
        roads=roads if roads is not None else _empty_roads(),
        parking=parking if parking is not None else _empty_parking(),
        malls=malls if malls is not None else _empty_malls(),
    )


# ---------------------------------------------------------------------------
# Shared scorer instance
# ---------------------------------------------------------------------------

@pytest.fixture()
def scorer() -> Scorer:
    return Scorer()


# ---------------------------------------------------------------------------
# TestPopulationFactorBasic
#
# Geometry layout
# ~~~~~~~~~~~~~~~
# Three candidates placed at:
#   C0 = (OX,        OY)          — origin
#   C1 = (OX+2000,   OY)          — 2 km east of origin
#   C2 = (OX+5000,   OY+5000)     — far away, no grid cells nearby
#
# Three population grid cells, each a 200 m × 200 m square (half=100 m):
#   G0: centred at (OX+300, OY),  pop=10 000   — within 1 km of C0 only
#   G1: centred at (OX+300, OY),  pop=20 000   — same area, second cell
#       (also within 1 km of C0)
#   G2: centred at (OX+2200, OY), pop=30 000   — within 1 km of C1 only
#
# Expected population sums (by hand):
#   C0: G0 (10 000) + G1 (20 000) = 30 000  → factor = 30 000/50 000*100 = 60.0
#   C1: G2 (30 000)                         → factor = 30 000/50 000*100 = 60.0
#   C2: (nothing within 1 km)               → factor = 0.0
#
# Buffer radius check:
#   G0 centre at (OX+300, OY); buffer is 1 000 m from C0=(OX, OY).
#   Distance from C0 to G0 centre = 300 m < 1 000 m → definitely intersects.
#   G2 centre at (OX+2200, OY); distance from C0 = 2 200 m > 1 000 m → no.
#   G2 centre at (OX+2200, OY); distance from C1=(OX+2000, OY) = 200 m → yes.
# ---------------------------------------------------------------------------

class TestPopulationFactorBasic:

    @pytest.fixture()
    def setup(self) -> dict:
        candidates = _candidates(
            (OX,        OY),           # C0
            (OX+2_000,  OY),           # C1
            (OX+5_000,  OY+5_000),     # C2
        )
        grid = _pop_grid(
            _square_cell(OX+300,   OY,  100, 10_000),   # G0
            _square_cell(OX+300,   OY,  100, 20_000),   # G1  (same area as G0)
            _square_cell(OX+2_200, OY,  100, 30_000),   # G2
        )
        return {"candidates": candidates, "grid": grid}

    def test_returns_series(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.population_factor(setup["candidates"], setup["grid"])
        assert isinstance(result, pd.Series)

    def test_length_matches_candidates(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.population_factor(setup["candidates"], setup["grid"])
        assert len(result) == len(setup["candidates"])

    def test_c0_expected_factor(self, scorer: Scorer, setup: dict) -> None:
        """C0 intersects G0+G1 → pop_sum=30 000 → factor=60.0."""
        result = scorer.population_factor(setup["candidates"], setup["grid"])
        expected = 30_000 / POPULATION_NORMALISER * 100   # 60.0
        assert result.iloc[0] == pytest.approx(expected, abs=0.01)

    def test_c1_expected_factor(self, scorer: Scorer, setup: dict) -> None:
        """C1 intersects G2 only → pop_sum=30 000 → factor=60.0."""
        result = scorer.population_factor(setup["candidates"], setup["grid"])
        expected = 30_000 / POPULATION_NORMALISER * 100   # 60.0
        assert result.iloc[1] == pytest.approx(expected, abs=0.01)

    def test_c2_expected_factor_zero(self, scorer: Scorer, setup: dict) -> None:
        """C2 is 5+ km from every grid cell → factor=0.0."""
        result = scorer.population_factor(setup["candidates"], setup["grid"])
        assert result.iloc[2] == pytest.approx(0.0, abs=0.01)

    def test_all_values_in_range(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.population_factor(setup["candidates"], setup["grid"])
        assert (result >= 0.0).all()
        assert (result <= 100.0).all()


# ---------------------------------------------------------------------------
# TestPopulationFactorBoundaries
#
# Tests the clip-to-100 behaviour and the exact-boundary case.
# ---------------------------------------------------------------------------

class TestPopulationFactorBoundaries:

    def test_high_population_capped_at_100(self, scorer: Scorer) -> None:
        """pop_sum >= 50 000 must yield factor == 100.0 (clip upper=100)."""
        candidates = _candidates((OX, OY))
        # Single cell with 60 000 population — more than the normaliser.
        grid = _pop_grid(_square_cell(OX + 100, OY, 200, 60_000))
        result = scorer.population_factor(candidates, grid)
        assert result.iloc[0] == pytest.approx(100.0, abs=0.01)

    def test_exact_normaliser_value_gives_100(self, scorer: Scorer) -> None:
        """pop_sum == POPULATION_NORMALISER (50 000) must yield factor == 100.0."""
        candidates = _candidates((OX, OY))
        grid = _pop_grid(_square_cell(OX + 100, OY, 200, int(POPULATION_NORMALISER)))
        result = scorer.population_factor(candidates, grid)
        assert result.iloc[0] == pytest.approx(100.0, abs=0.01)

    def test_zero_population_candidate_gives_0(self, scorer: Scorer) -> None:
        """Candidate with no intersecting cells must yield factor == 0.0."""
        candidates = _candidates((OX + 9_000, OY + 9_000))   # far from everything
        grid = _pop_grid(_square_cell(OX, OY, 100, 25_000))
        result = scorer.population_factor(candidates, grid)
        assert result.iloc[0] == pytest.approx(0.0, abs=0.01)

    def test_never_exceeds_100_with_huge_population(self, scorer: Scorer) -> None:
        """Even with 1 000 000 population the factor must not exceed 100."""
        candidates = _candidates((OX, OY))
        grid = _pop_grid(_square_cell(OX, OY, 500, 1_000_000))
        result = scorer.population_factor(candidates, grid)
        assert result.iloc[0] <= 100.0

    def test_factor_never_below_zero(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY))
        grid = _pop_grid(_square_cell(OX + 5_000, OY, 100, 10_000))
        result = scorer.population_factor(candidates, grid)
        assert (result >= 0.0).all()


# ---------------------------------------------------------------------------
# TestPopulationFactorEmptyGrid
#
# When population_grid is empty (missing layer) every candidate must
# receive factor == 0.  This is the missing-layer fallback (design.md
# §Missing Layer Handling, Req 5 AC-8).
# ---------------------------------------------------------------------------

class TestPopulationFactorEmptyGrid:

    def test_empty_grid_returns_series(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1000, OY))
        result = scorer.population_factor(candidates, _empty_pop_grid())
        assert isinstance(result, pd.Series)

    def test_empty_grid_all_zeros(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1000, OY), (OX+2000, OY))
        result = scorer.population_factor(candidates, _empty_pop_grid())
        assert (result == 0.0).all()

    def test_empty_grid_length_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1000, OY))
        result = scorer.population_factor(candidates, _empty_pop_grid())
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestPopulationFactorBuffer
#
# Verifies that the buffer radius used is exactly POPULATION_BUFFER_M
# (1 000 m) — independent of any search_radius argument.
#
# A grid cell centred 999 m east of the candidate (and 1 m inside the
# buffer boundary after accounting for the cell's own half-width of 1 m)
# must be picked up; a cell centred 1 001 m away (entirely outside) must not.
#
# Geometry:
#   Candidate at (OX, OY).
#   "Inside" cell: centre at (OX + 990, OY), half=10 m
#       → cell spans [OX+980, OX+1000] in x.  The 1 000 m buffer from
#         (OX, OY) has its eastern edge at exactly OX+1000, so the cell's
#         eastern face just touches the buffer edge → intersects.
#   "Outside" cell: centre at (OX + 1_010, OY), half=1 m
#       → cell spans [OX+1009, OX+1011].  Nearest point to candidate is
#         OX+1009, which is 1 009 m away — outside the 1 000 m buffer.
# ---------------------------------------------------------------------------

class TestPopulationFactorBuffer:

    def test_cell_inside_1km_buffer_is_included(self, scorer: Scorer) -> None:
        """A grid cell whose nearest edge is within 1 000 m must contribute."""
        candidates = _candidates((OX, OY))
        # Cell centre at OX+990, half=10 → easternmost edge at OX+1000.
        # A 1 000 m circular buffer from (OX, OY) reaches to OX+1000 on the
        # x-axis, so the cell is intersected.
        grid = _pop_grid(_square_cell(OX + 990, OY, 10, 5_000))
        result = scorer.population_factor(candidates, grid)
        assert result.iloc[0] > 0.0, (
            "Expected grid cell inside 1 km buffer to contribute to factor"
        )

    def test_cell_outside_1km_buffer_is_excluded(self, scorer: Scorer) -> None:
        """A grid cell entirely beyond 1 000 m must not contribute."""
        candidates = _candidates((OX, OY))
        # Cell centre at OX+1010, half=1 → nearest edge at OX+1009 (1 009 m
        # from the candidate) — outside the 1 000 m buffer.
        grid = _pop_grid(_square_cell(OX + 1_010, OY, 1, 50_000))
        result = scorer.population_factor(candidates, grid)
        assert result.iloc[0] == pytest.approx(0.0, abs=0.01), (
            "Expected grid cell outside 1 km buffer to be excluded from factor"
        )

    def test_buffer_is_1000m_not_search_radius(self, scorer: Scorer) -> None:
        """
        Verify buffer independence from search_radius by calling score_batch
        with a large search_radius (5 000 m) and confirming that a cell at
        1 500 m is NOT included (it would be if search_radius were used).
        """
        candidates = _candidates((OX, OY))
        # Cell is 1 500 m from candidate — outside 1 km buffer, inside 5 km.
        grid = _pop_grid(_square_cell(OX + 1_500, OY, 10, 50_000))
        datasets = _datasets(grid)
        results_df = Scorer().score_batch(candidates, datasets, search_radius=5_000)
        assert results_df["pop_factor"].iloc[0] == pytest.approx(0.0, abs=0.01), (
            "Buffer must be fixed at 1 000 m regardless of search_radius"
        )


# ---------------------------------------------------------------------------
# TestPopulationFactorIndexAlignment
#
# The output Series must align with whatever index the input candidates
# GeoDataFrame carries — including non-default or non-contiguous indexes.
# ---------------------------------------------------------------------------

class TestPopulationFactorIndexAlignment:

    def test_output_index_matches_input_index(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+2_000, OY), (OX+4_000, OY))
        grid = _pop_grid(_square_cell(OX + 100, OY, 100, 10_000))
        result = scorer.population_factor(candidates, grid)
        assert list(result.index) == list(candidates.index)

    def test_non_default_index_preserved(self, scorer: Scorer) -> None:
        """Index starting at 10 must be carried through unchanged."""
        base = _candidates((OX, OY), (OX+2_000, OY))
        candidates = base.set_index(pd.Index([10, 20]))
        grid = _pop_grid(_square_cell(OX + 100, OY, 100, 10_000))
        result = scorer.population_factor(candidates, grid)
        assert list(result.index) == [10, 20]

    def test_non_contiguous_index_preserved(self, scorer: Scorer) -> None:
        """Gaps in the index must be preserved (e.g. after filtering rows)."""
        base = _candidates((OX, OY), (OX+1_000, OY), (OX+2_000, OY))
        # Drop middle row → non-contiguous index [0, 2]
        candidates = base.drop(index=1)
        grid = _pop_grid(_square_cell(OX + 100, OY, 100, 5_000))
        result = scorer.population_factor(candidates, grid)
        assert list(result.index) == [0, 2]

    def test_single_candidate_index_is_scalar(self, scorer: Scorer) -> None:
        """Single-row input → single-element Series with index [0]."""
        candidates = _candidates((OX, OY))
        grid = _pop_grid(_square_cell(OX + 100, OY, 100, 10_000))
        result = scorer.population_factor(candidates, grid)
        assert len(result) == 1
        assert list(result.index) == [0]


# ---------------------------------------------------------------------------
# TestScoreBatch
#
# score_batch now combines all five factors.
#
# Hand-computed expected score for the reference scenario:
#   pop_sum = 25 000          → pop_factor    = 50.0
#   charger at 800 m, R=1000  → charger_factor = 80.0
#   road at 100 m (≤200)      → road_factor   = 100.0
#   candidate inside parking  → park_factor   = 100.0
#   mall at 300 m (≤500)      → mall_factor   = 100.0
#   score = round(0.35*50 + 0.25*80 + 0.15*100 + 0.15*100 + 0.10*100)
#         = round(17.5 + 20.0 + 15.0 + 15.0 + 10.0)
#         = round(77.5) = 78   (Python banker's rounding)
# ---------------------------------------------------------------------------

class TestScoreBatch:

    def test_returns_dataframe(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 25_000)),
            ev_chargers=_chargers((OX + 800, OY)),
            roads=_road_point((OX + 100, OY)),
            parking=_parking_poly(_square_parking(OX, OY, 50)),
            malls=_malls((OX + 300, OY)),
        )
        result = scorer.score_batch(candidates, datasets, search_radius=1_000)
        assert isinstance(result, pd.DataFrame)

    def test_columns_present(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 25_000)),
            ev_chargers=_chargers((OX + 800, OY)),
            roads=_road_point((OX + 100, OY)),
            parking=_parking_poly(_square_parking(OX, OY, 50)),
            malls=_malls((OX + 300, OY)),
        )
        result = scorer.score_batch(candidates, datasets, search_radius=1_000)
        for col in (
            "pop_factor", "charger_factor", "road_factor",
            "park_factor", "mall_factor", "score",
        ):
            assert col in result.columns, f"missing column: {col}"

    def test_index_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+2_000, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 10_000)),
            ev_chargers=_chargers((OX + 500, OY)),
        )
        result = scorer.score_batch(candidates, datasets, search_radius=1_000)
        assert list(result.index) == list(candidates.index)

    def test_length_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+2_000, OY), (OX+4_000, OY))
        datasets = _datasets(_empty_pop_grid())
        result = scorer.score_batch(candidates, datasets, search_radius=1_000)
        assert len(result) == 3

    def test_pop_factor_column_matches_direct_call(self, scorer: Scorer) -> None:
        """score_batch pop_factor column must equal population_factor() output."""
        candidates = _candidates((OX, OY), (OX+3_000, OY))
        grid = _pop_grid(_square_cell(OX + 100, OY, 100, 20_000))
        datasets = _datasets(grid)
        batch = scorer.score_batch(candidates, datasets, search_radius=1_000)
        direct = scorer.population_factor(candidates, grid)
        pd.testing.assert_series_equal(
            batch["pop_factor"].reset_index(drop=True),
            direct.reset_index(drop=True),
            check_names=False,
        )

    def test_charger_factor_column_matches_direct_call(self, scorer: Scorer) -> None:
        """score_batch charger_factor must equal charger_distance_factor() output."""
        candidates = _candidates((OX, OY), (OX+3_000, OY))
        chargers   = _chargers((OX + 500, OY), (OX+2_800, OY))
        datasets   = _datasets(_empty_pop_grid(), ev_chargers=chargers)
        batch  = scorer.score_batch(candidates, datasets, search_radius=1_000)
        direct = scorer.charger_distance_factor(candidates, chargers, 1_000)
        pd.testing.assert_series_equal(
            batch["charger_factor"].reset_index(drop=True),
            direct.reset_index(drop=True),
            check_names=False,
        )

    def test_road_factor_column_matches_direct_call(self, scorer: Scorer) -> None:
        """score_batch road_factor must equal road_proximity_factor() output."""
        candidates = _candidates((OX, OY), (OX+3_000, OY))
        roads_gdf  = _road_point((OX + 100, OY))
        datasets   = _datasets(_empty_pop_grid(), roads=roads_gdf)
        batch  = scorer.score_batch(candidates, datasets, search_radius=1_000)
        direct = scorer.road_proximity_factor(candidates, roads_gdf)
        pd.testing.assert_series_equal(
            batch["road_factor"].reset_index(drop=True),
            direct.reset_index(drop=True),
            check_names=False,
        )

    def test_park_factor_column_matches_direct_call(self, scorer: Scorer) -> None:
        """score_batch park_factor must equal parking_factor() output."""
        candidates  = _candidates((OX, OY), (OX+3_000, OY))
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        datasets    = _datasets(_empty_pop_grid(), parking=parking_gdf)
        batch  = scorer.score_batch(candidates, datasets, search_radius=1_000)
        direct = scorer.parking_factor(candidates, parking_gdf)
        pd.testing.assert_series_equal(
            batch["park_factor"].reset_index(drop=True),
            direct.reset_index(drop=True),
            check_names=False,
        )

    def test_mall_factor_column_matches_direct_call(self, scorer: Scorer) -> None:
        """score_batch mall_factor must equal mall_proximity_factor() output."""
        candidates = _candidates((OX, OY), (OX+3_000, OY))
        malls_gdf  = _malls((OX + 300, OY))
        datasets   = _datasets(_empty_pop_grid(), malls=malls_gdf)
        batch  = scorer.score_batch(candidates, datasets, search_radius=1_000)
        direct = scorer.mall_proximity_factor(candidates, malls_gdf)
        pd.testing.assert_series_equal(
            batch["mall_factor"].reset_index(drop=True),
            direct.reset_index(drop=True),
            check_names=False,
        )

    def test_score_is_integer_dtype(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 10_000)),
            ev_chargers=_chargers((OX + 500, OY)),
        )
        result = scorer.score_batch(candidates, datasets, search_radius=1_000)
        assert pd.api.types.is_integer_dtype(result["score"])

    def test_score_bounded_0_to_100(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+2_000, OY), (OX+10_000, OY))
        datasets = _datasets(
            _pop_grid(
                _square_cell(OX + 100, OY, 100, 100_000),
                _square_cell(OX+2_100, OY, 100, 25_000),
            ),
            ev_chargers=_chargers((OX + 50, OY)),
            roads=_road_point((OX + 50, OY)),
            parking=_parking_poly(_square_parking(OX, OY, 200)),
            malls=_malls((OX + 100, OY)),
        )
        result = scorer.score_batch(candidates, datasets, search_radius=2_000)
        assert (result["score"] >= 0).all()
        assert (result["score"] <= 100).all()

    def test_score_five_factor_weighted_correctly_fast(self, scorer: Scorer) -> None:
        """
        FAST weights: pop=35%, charger=25%, road=15%, parking=15%, mall=10%

        pop_sum=25 000           → pop_factor    = 50.0
        charger at 800 m, R=1000 → charger_factor = 80.0
        road at 100 m (≤200)     → road_factor   = 100.0
        candidate inside parking → park_factor   = 100.0
        mall at 300 m (≤500)     → mall_factor   = 100.0
        score = round(0.35*50 + 0.25*80 + 0.15*100 + 0.15*100 + 0.10*100)
              = round(17.5 + 20.0 + 15.0 + 15.0 + 10.0) = round(77.5) = 78
        """
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 25_000)),
            ev_chargers=_chargers((OX + 800, OY)),
            roads=_road_point((OX + 100, OY)),
            parking=_parking_poly(_square_parking(OX, OY, 50)),
            malls=_malls((OX + 300, OY)),
        )
        result = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="FAST"
        )
        w = WEIGHTS_BY_TYPE["FAST"]
        expected_score = round(
            w["population"]       * 50.0
            + w["charger_distance"] * 80.0
            + w["road_proximity"]   * 100.0
            + w["parking"]          * 100.0
            + w["mall_proximity"]   * 100.0
        )   # round(77.5) = 78
        assert result["score"].iloc[0] == expected_score

    def test_score_five_factor_weighted_correctly_dc_fast(self, scorer: Scorer) -> None:
        """
        DC_FAST weights: pop=20%, charger=25%, road=35%, parking=10%, mall=10%

        Same candidate geometry as the FAST test — different weights must
        produce a different score.

        score = round(0.20*50 + 0.25*80 + 0.35*100 + 0.10*100 + 0.10*100)
              = round(10.0 + 20.0 + 35.0 + 10.0 + 10.0) = round(85.0) = 85
        """
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 25_000)),
            ev_chargers=_chargers((OX + 800, OY)),
            roads=_road_point((OX + 100, OY)),
            parking=_parking_poly(_square_parking(OX, OY, 50)),
            malls=_malls((OX + 300, OY)),
        )
        result = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="DC_FAST"
        )
        w = WEIGHTS_BY_TYPE["DC_FAST"]
        expected_score = round(
            w["population"]       * 50.0
            + w["charger_distance"] * 80.0
            + w["road_proximity"]   * 100.0
            + w["parking"]          * 100.0
            + w["mall_proximity"]   * 100.0
        )   # round(85.0) = 85
        assert result["score"].iloc[0] == expected_score

    def test_score_five_factor_weighted_correctly_slow(self, scorer: Scorer) -> None:
        """
        SLOW weights: pop=45%, charger=20%, road=5%, parking=20%, mall=10%

        Same candidate geometry as the FAST test — different weights must
        produce a different score.

        score = round(0.45*50 + 0.20*80 + 0.05*100 + 0.20*100 + 0.10*100)
              = round(22.5 + 16.0 + 5.0 + 20.0 + 10.0) = round(73.5) = 74
        """
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 25_000)),
            ev_chargers=_chargers((OX + 800, OY)),
            roads=_road_point((OX + 100, OY)),
            parking=_parking_poly(_square_parking(OX, OY, 50)),
            malls=_malls((OX + 300, OY)),
        )
        result = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="SLOW"
        )
        w = WEIGHTS_BY_TYPE["SLOW"]
        expected_score = round(
            w["population"]       * 50.0
            + w["charger_distance"] * 80.0
            + w["road_proximity"]   * 100.0
            + w["parking"]          * 100.0
            + w["mall_proximity"]   * 100.0
        )   # round(73.5) = 74
        assert result["score"].iloc[0] == expected_score

    def test_all_layers_empty_score_uses_charger_weight_fast(
        self, scorer: Scorer
    ) -> None:
        """
        FAST: charger_distance weight = 0.25
        Empty pop_grid  → pop_factor    = 0
        Empty chargers  → charger_factor = 100
        Empty roads     → road_factor   = 0
        Empty parking   → park_factor   = 0
        Empty malls     → mall_factor   = 0
        score = round(0.25 * 100) = 25
        """
        candidates = _candidates((OX, OY))
        datasets   = _datasets(_empty_pop_grid())
        result     = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="FAST"
        )
        expected = round(WEIGHTS_BY_TYPE["FAST"]["charger_distance"] * 100)   # 25
        assert result["score"].iloc[0] == expected

    def test_all_layers_empty_score_uses_charger_weight_dc_fast(
        self, scorer: Scorer
    ) -> None:
        """
        DC_FAST: charger_distance weight = 0.25 → same 25 as FAST for
        the charger-only case (both are 0.25), but SLOW differs at 0.20.
        """
        candidates = _candidates((OX, OY))
        datasets   = _datasets(_empty_pop_grid())
        result     = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="DC_FAST"
        )
        expected = round(WEIGHTS_BY_TYPE["DC_FAST"]["charger_distance"] * 100)  # 25
        assert result["score"].iloc[0] == expected

    def test_all_layers_empty_score_uses_charger_weight_slow(
        self, scorer: Scorer
    ) -> None:
        """
        SLOW: charger_distance weight = 0.20
        score = round(0.20 * 100) = 20  (differs from FAST/DC_FAST = 25)
        """
        candidates = _candidates((OX, OY))
        datasets   = _datasets(_empty_pop_grid())
        result     = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="SLOW"
        )
        expected = round(WEIGHTS_BY_TYPE["SLOW"]["charger_distance"] * 100)  # 20
        assert result["score"].iloc[0] == expected



# ---------------------------------------------------------------------------
# TestWeightsByType
#
# Validates:
#   1. Each weight table in WEIGHTS_BY_TYPE sums to exactly 1.0 (100%).
#   2. The WEIGHTS backward-compat alias is identical to the FAST table.
#   3. Calling score_batch with different charger types on the same candidate
#      set produces different scores whenever the weight tables would cause
#      different weighted sums — confirming charger_type is not ignored.
# ---------------------------------------------------------------------------

class TestWeightsByType:

    def test_fast_weights_sum_to_100_percent(self) -> None:
        """FAST weight table must sum to exactly 1.0."""
        total = sum(WEIGHTS_BY_TYPE["FAST"].values())
        assert total == pytest.approx(1.0, abs=1e-9), (
            f"FAST weights sum to {total}, expected 1.0"
        )

    def test_dc_fast_weights_sum_to_100_percent(self) -> None:
        """DC_FAST weight table must sum to exactly 1.0."""
        total = sum(WEIGHTS_BY_TYPE["DC_FAST"].values())
        assert total == pytest.approx(1.0, abs=1e-9), (
            f"DC_FAST weights sum to {total}, expected 1.0"
        )

    def test_slow_weights_sum_to_100_percent(self) -> None:
        """SLOW weight table must sum to exactly 1.0."""
        total = sum(WEIGHTS_BY_TYPE["SLOW"].values())
        assert total == pytest.approx(1.0, abs=1e-9), (
            f"SLOW weights sum to {total}, expected 1.0"
        )

    def test_weights_alias_is_fast_table(self) -> None:
        """Backward-compat WEIGHTS alias must be identical to WEIGHTS_BY_TYPE['FAST']."""
        assert WEIGHTS is WEIGHTS_BY_TYPE["FAST"], (
            "WEIGHTS alias must point at the same object as WEIGHTS_BY_TYPE['FAST']"
        )

    def test_dc_fast_and_fast_produce_different_scores(self, scorer: Scorer) -> None:
        """
        DC_FAST weights road_proximity heavily (35%) vs FAST (15%), so a
        candidate that is near a road but has low population must score
        higher under DC_FAST than FAST.

        Geometry:
          - Low population (pop_sum=5 000  → pop_factor=10.0)
          - Near a road (road_factor=100.0)
          - No charger within radius (charger_factor=100.0)
          - No parking (park_factor=0.0)
          - No mall (mall_factor=0.0)

        FAST:    round(0.35*10 + 0.25*100 + 0.15*100 + 0.15*0 + 0.10*0)
               = round(3.5 + 25.0 + 15.0) = round(43.5) = 44
        DC_FAST: round(0.20*10 + 0.25*100 + 0.35*100 + 0.10*0 + 0.10*0)
               = round(2.0 + 25.0 + 35.0) = round(62.0) = 62

        The two scores must differ.
        """
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 5_000)),
            ev_chargers=_empty_chargers(),          # no charger → factor=100
            roads=_road_point((OX + 100, OY)),      # near road → factor=100
            parking=_empty_parking(),
            malls=_empty_malls(),
        )
        result_fast = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="FAST"
        )
        result_dc_fast = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="DC_FAST"
        )
        assert result_fast["score"].iloc[0] != result_dc_fast["score"].iloc[0], (
            "DC_FAST and FAST must produce different scores when road factor "
            "is dominant and population factor is low"
        )
        # DC_FAST prioritises roads (35%) over FAST (15%) → higher score here
        assert result_dc_fast["score"].iloc[0] > result_fast["score"].iloc[0]

    def test_slow_and_fast_produce_different_scores(self, scorer: Scorer) -> None:
        """
        SLOW weights population heavily (45%) vs FAST (35%), so a candidate
        in a very dense area but far from roads must score higher under SLOW.

        Geometry:
          - High population (pop_sum=40 000 → pop_factor=80.0)
          - No road within threshold (road_factor=0.0)
          - No charger within radius (charger_factor=100.0)
          - Candidate inside parking (park_factor=100.0)
          - No mall (mall_factor=0.0)

        FAST: round(0.35*80 + 0.25*100 + 0.15*0 + 0.15*100 + 0.10*0)
            = round(28 + 25 + 0 + 15 + 0) = 68
        SLOW: round(0.45*80 + 0.20*100 + 0.05*0 + 0.20*100 + 0.10*0)
            = round(36 + 20 + 0 + 20 + 0) = 76

        The two scores must differ.
        """
        candidates = _candidates((OX, OY))
        datasets = _datasets(
            _pop_grid(_square_cell(OX + 100, OY, 100, 40_000)),
            ev_chargers=_empty_chargers(),
            roads=_empty_roads(),
            parking=_parking_poly(_square_parking(OX, OY, 50)),
            malls=_empty_malls(),
        )
        result_fast = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="FAST"
        )
        result_slow = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="SLOW"
        )
        assert result_fast["score"].iloc[0] != result_slow["score"].iloc[0], (
            "SLOW and FAST must produce different scores when population is "
            "high and road factor is zero"
        )
        # SLOW weights population (45%) more than FAST (35%) → higher score
        assert result_slow["score"].iloc[0] > result_fast["score"].iloc[0]

    def test_all_types_produce_same_score_when_all_factors_equal(
        self, scorer: Scorer
    ) -> None:
        """
        When all five factor scores are identical the final score equals
        that value regardless of weights (since weights sum to 1.0).
        All three charger types must yield the same score.

        Factor value 60.0 for all five:
          score = round(1.0 * 60.0) = 60 for any weight table.
        """
        # pop_sum = 30 000 → pop_factor = 60.0
        # charger at 600 m, R=1000 → charger_factor = 60.0
        # road at 100 m → road_factor = 100.0 (binary — use a low road factor instead)
        # For perfect equality use compute_final_score directly.
        s60 = pd.Series([60.0])
        for ctype in ("SLOW", "FAST", "DC_FAST"):
            result = Scorer.compute_final_score(s60, s60, s60, s60, s60, charger_type=ctype)
            assert result.iloc[0] == 60, (
                f"Expected score=60 for charger_type={ctype!r} when all "
                f"factors=60.0, got {result.iloc[0]}"
            )

    def test_unknown_charger_type_falls_back_to_fast(self, scorer: Scorer) -> None:
        """An unrecognised charger_type string must silently use FAST weights."""
        candidates = _candidates((OX, OY))
        datasets   = _datasets(_empty_pop_grid())
        result_unknown = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="UNKNOWN_TYPE"
        )
        result_fast = scorer.score_batch(
            candidates, datasets, search_radius=1_000, charger_type="FAST"
        )
        assert result_unknown["score"].iloc[0] == result_fast["score"].iloc[0]


# ---------------------------------------------------------------------------
# TestChargerDistanceFactorBasic
#
# Geometry layout
# ~~~~~~~~~~~~~~~
# Three candidates:
#   C0 = (OX,       OY)        — close to charger E0
#   C1 = (OX+2000,  OY)        — close to charger E1
#   C2 = (OX+5000,  OY+5000)   — no charger within search_radius (2 000 m)
#
# Two chargers:
#   E0 at (OX+400,  OY)  — dist from C0 = 400 m
#   E1 at (OX+2300, OY)  — dist from C1 = 300 m
#
# search_radius = 2 000 m
#
# Expected charger factors (by hand):
#   C0: dist=400  → 400/2000*100 = 20.0
#   C1: dist=300  → 300/2000*100 = 15.0
#   C2: no charger within 2 000 m → NaN → filled with 100.0
#
# Distance from C2=(OX+5000, OY+5000) to E0=(OX+400, OY):
#   sqrt((4600)²+(5000)²) ≈ 6799 m  > 2000 m  ✓
# Distance from C2 to E1=(OX+2300, OY):
#   sqrt((2700)²+(5000)²) ≈ 5681 m  > 2000 m  ✓
# ---------------------------------------------------------------------------

class TestChargerDistanceFactorBasic:

    SEARCH_RADIUS = 2_000

    @pytest.fixture()
    def setup(self) -> dict:
        candidates = _candidates(
            (OX,       OY),           # C0
            (OX+2_000, OY),           # C1
            (OX+5_000, OY+5_000),     # C2
        )
        chargers = _chargers(
            (OX+400,   OY),           # E0
            (OX+2_300, OY),           # E1
        )
        return {"candidates": candidates, "chargers": chargers}

    def test_returns_series(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.charger_distance_factor(
            setup["candidates"], setup["chargers"], self.SEARCH_RADIUS
        )
        assert isinstance(result, pd.Series)

    def test_length_matches_candidates(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.charger_distance_factor(
            setup["candidates"], setup["chargers"], self.SEARCH_RADIUS
        )
        assert len(result) == 3

    def test_c0_expected_factor(self, scorer: Scorer, setup: dict) -> None:
        """C0 nearest charger at 400 m → factor = 400/2000*100 = 20.0."""
        result = scorer.charger_distance_factor(
            setup["candidates"], setup["chargers"], self.SEARCH_RADIUS
        )
        expected = 400 / self.SEARCH_RADIUS * 100   # 20.0
        assert result.iloc[0] == pytest.approx(expected, abs=0.01)

    def test_c1_expected_factor(self, scorer: Scorer, setup: dict) -> None:
        """C1 nearest charger at 300 m → factor = 300/2000*100 = 15.0."""
        result = scorer.charger_distance_factor(
            setup["candidates"], setup["chargers"], self.SEARCH_RADIUS
        )
        expected = 300 / self.SEARCH_RADIUS * 100   # 15.0
        assert result.iloc[1] == pytest.approx(expected, abs=0.01)

    def test_c2_no_charger_in_range_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C2 has no charger within search_radius → factor == 100.0."""
        result = scorer.charger_distance_factor(
            setup["candidates"], setup["chargers"], self.SEARCH_RADIUS
        )
        assert result.iloc[2] == pytest.approx(100.0, abs=0.01)

    def test_all_values_in_range(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.charger_distance_factor(
            setup["candidates"], setup["chargers"], self.SEARCH_RADIUS
        )
        assert (result >= 0.0).all()
        assert (result <= 100.0).all()


# ---------------------------------------------------------------------------
# TestChargerDistanceFactorBoundary
#
# The critical boundary cases:
#
# 1. Charger at EXACTLY search_radius distance
#    distance == search_radius → factor == 100.0
#    (sjoin_nearest includes points at max_distance; factor = R/R*100 = 100)
#
# 2. Charger at 0 m (co-located with candidate)
#    factor == 0.0
#
# 3. Charger just inside radius (search_radius - 1 m) → included, factor < 100
#
# 4. Charger just outside radius (search_radius + 1 m) → NaN → factor == 100
# ---------------------------------------------------------------------------

class TestChargerDistanceFactorBoundary:

    SEARCH_RADIUS = 1_000

    def test_charger_at_exact_search_radius_gives_100(self, scorer: Scorer) -> None:
        """
        Charger placed exactly search_radius metres from the candidate.

        sjoin_nearest with max_distance=search_radius INCLUDES points at
        that exact distance.  Normalised: R/R * 100 = 100.0.
        """
        candidates = _candidates((OX, OY))
        # Place charger exactly search_radius east — Euclidean distance == 1 000 m.
        chargers = _chargers((OX + self.SEARCH_RADIUS, OY))
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert result.iloc[0] == pytest.approx(100.0, abs=0.01), (
            "Charger at exactly search_radius must produce factor == 100"
        )

    def test_charger_at_zero_distance_gives_0(self, scorer: Scorer) -> None:
        """Co-located charger → dist = 0 → factor = 0/R*100 = 0.0."""
        candidates = _candidates((OX, OY))
        chargers   = _chargers((OX, OY))   # same coordinates
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert result.iloc[0] == pytest.approx(0.0, abs=0.01)

    def test_charger_just_inside_radius_is_included(self, scorer: Scorer) -> None:
        """Charger at search_radius - 1 m must be included (factor < 100)."""
        candidates = _candidates((OX, OY))
        chargers   = _chargers((OX + self.SEARCH_RADIUS - 1, OY))
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert result.iloc[0] < 100.0, (
            "Charger just inside radius should produce factor < 100"
        )
        assert result.iloc[0] > 0.0

    def test_charger_just_outside_radius_gives_100(self, scorer: Scorer) -> None:
        """
        Charger at search_radius + 1 m is outside max_distance so
        sjoin_nearest returns NaN → filled to 100.0.
        """
        candidates = _candidates((OX, OY))
        chargers   = _chargers((OX + self.SEARCH_RADIUS + 1, OY))
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert result.iloc[0] == pytest.approx(100.0, abs=0.01), (
            "Charger just outside radius should be treated as absent → factor 100"
        )

    def test_factor_never_exceeds_100(self, scorer: Scorer) -> None:
        """Factor must not exceed 100 even for chargers right at the boundary."""
        candidates = _candidates((OX, OY), (OX + 500, OY))
        chargers   = _chargers((OX + self.SEARCH_RADIUS, OY))
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert (result <= 100.0).all()

    def test_factor_never_below_zero(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY))
        chargers   = _chargers((OX, OY))
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert (result >= 0.0).all()

    def test_small_search_radius_excludes_distant_charger(self, scorer: Scorer) -> None:
        """
        With a small search_radius, a charger that would be included under a
        larger radius is correctly excluded and the factor fills to 100.
        """
        candidates = _candidates((OX, OY))
        chargers   = _chargers((OX + 600, OY))   # 600 m away
        # Radius 500 m: charger is outside → 100
        result_small = scorer.charger_distance_factor(candidates, chargers, 500)
        # Radius 1000 m: charger is inside → 60
        result_large = scorer.charger_distance_factor(candidates, chargers, 1_000)
        assert result_small.iloc[0] == pytest.approx(100.0, abs=0.01)
        assert result_large.iloc[0] == pytest.approx(60.0,  abs=0.01)


# ---------------------------------------------------------------------------
# TestChargerDistanceFactorEmptyChargers
#
# When ev_chargers is empty (missing layer) every candidate is maximally
# under-served → factor == 100 for all (design.md §Missing Layer Handling).
# ---------------------------------------------------------------------------

class TestChargerDistanceFactorEmptyChargers:

    def test_empty_chargers_returns_series(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1_000, OY))
        result = scorer.charger_distance_factor(candidates, _empty_chargers(), 1_000)
        assert isinstance(result, pd.Series)

    def test_empty_chargers_all_100(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1_000, OY), (OX+2_000, OY))
        result = scorer.charger_distance_factor(candidates, _empty_chargers(), 1_000)
        assert (result == 100.0).all()

    def test_empty_chargers_length_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1_000, OY))
        result = scorer.charger_distance_factor(candidates, _empty_chargers(), 1_000)
        assert len(result) == 2

    def test_empty_chargers_index_preserved(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+1_000, OY))
        result = scorer.charger_distance_factor(candidates, _empty_chargers(), 1_000)
        assert list(result.index) == list(candidates.index)


# ---------------------------------------------------------------------------
# TestChargerDistanceFactorIndexAlignment
# ---------------------------------------------------------------------------

class TestChargerDistanceFactorIndexAlignment:

    def test_output_index_matches_input_index(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+500, OY), (OX+1_500, OY))
        chargers   = _chargers((OX+100, OY))
        result = scorer.charger_distance_factor(candidates, chargers, 1_000)
        assert list(result.index) == list(candidates.index)

    def test_non_default_index_preserved(self, scorer: Scorer) -> None:
        base = _candidates((OX, OY), (OX+500, OY))
        candidates = base.set_index(pd.Index([5, 10]))
        chargers   = _chargers((OX+200, OY))
        result = scorer.charger_distance_factor(candidates, chargers, 1_000)
        assert list(result.index) == [5, 10]

    def test_non_contiguous_index_preserved(self, scorer: Scorer) -> None:
        base = _candidates((OX, OY), (OX+500, OY), (OX+1_500, OY))
        candidates = base.drop(index=1)   # index [0, 2]
        chargers   = _chargers((OX+100, OY))
        result = scorer.charger_distance_factor(candidates, chargers, 1_000)
        assert list(result.index) == [0, 2]


# ---------------------------------------------------------------------------
# TestChargerDistanceFactorDeduplication
#
# sjoin_nearest may return multiple rows when several chargers are
# equidistant.  The factor must still return exactly one row per candidate.
# ---------------------------------------------------------------------------

class TestChargerDistanceFactorDeduplication:

    SEARCH_RADIUS = 2_000

    def test_two_equidistant_chargers_one_row_per_candidate(
        self, scorer: Scorer
    ) -> None:
        """
        Two chargers both 500 m from the candidate (one north, one east).
        The result must have exactly len(candidates) rows.
        """
        candidates = _candidates((OX, OY))
        chargers   = _chargers(
            (OX + 500, OY),    # 500 m east
            (OX,       OY + 500),   # 500 m north
        )
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert len(result) == 1

    def test_many_chargers_all_in_range_one_row_per_candidate(
        self, scorer: Scorer
    ) -> None:
        """Five chargers all within range; each of 3 candidates gets one row."""
        candidates = _candidates(
            (OX,       OY),
            (OX+1_000, OY),
            (OX+2_000, OY),
        )
        chargers = _chargers(
            *(  (OX + 100 * i, OY + 100 * i) for i in range(5)  )
        )
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        assert len(result) == len(candidates)

    def test_deduplication_picks_nearest_distance(self, scorer: Scorer) -> None:
        """
        When equidistant chargers exist, the reported factor must correspond
        to the closest charger (not an arbitrary or duplicated one).

        Candidate at (OX, OY).
        Charger A at (OX+300, OY) — 300 m.
        Charger B at (OX-300, OY) — 300 m (equidistant).
        Charger C at (OX+200, OY) — 200 m (closer).

        Expected factor: 200 / SEARCH_RADIUS * 100 = 10.0.
        """
        candidates = _candidates((OX, OY))
        chargers   = _chargers(
            (OX + 300, OY),
            (OX - 300, OY),
            (OX + 200, OY),   # closest
        )
        result = scorer.charger_distance_factor(
            candidates, chargers, self.SEARCH_RADIUS
        )
        expected = 200 / self.SEARCH_RADIUS * 100   # 10.0
        assert result.iloc[0] == pytest.approx(expected, abs=0.01)


# ===========================================================================
# Road proximity factor tests
# ===========================================================================
#
# road_proximity_factor is binary:
#   - 100 if nearest road is within ROAD_PROXIMITY_M (200 m)
#   - 0   otherwise
#
# Tests use Point geometries for roads to keep distance arithmetic simple
# (the factor only cares about nearest-distance, not road shape).
# ===========================================================================

# ---------------------------------------------------------------------------
# TestRoadProximityFactorBasic
#
# Three candidates:
#   C0 = (OX,       OY)      — road R0 at (OX+100, OY), dist=100 m  ≤ 200 → 100
#   C1 = (OX+1000,  OY)      — road R1 at (OX+1150, OY), dist=150 m ≤ 200 → 100
#   C2 = (OX+3000,  OY)      — no road within 200 m                         → 0
# ---------------------------------------------------------------------------

class TestRoadProximityFactorBasic:

    @pytest.fixture()
    def setup(self) -> dict:
        candidates = _candidates(
            (OX,        OY),    # C0
            (OX+1_000,  OY),    # C1
            (OX+3_000,  OY),    # C2 — far from all roads
        )
        roads_gdf = _road_point(
            (OX + 100,   OY),    # R0 — 100 m from C0
            (OX+1_150,   OY),    # R1 — 150 m from C1
        )
        return {"candidates": candidates, "roads": roads_gdf}

    def test_returns_series(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.road_proximity_factor(setup["candidates"], setup["roads"])
        assert isinstance(result, pd.Series)

    def test_length_matches_candidates(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.road_proximity_factor(setup["candidates"], setup["roads"])
        assert len(result) == 3

    def test_c0_in_range_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C0: road at 100 m ≤ ROAD_PROXIMITY_M (200) → factor = 100."""
        result = scorer.road_proximity_factor(setup["candidates"], setup["roads"])
        assert result.iloc[0] == pytest.approx(100.0)

    def test_c1_in_range_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C1: road at 150 m ≤ 200 → factor = 100."""
        result = scorer.road_proximity_factor(setup["candidates"], setup["roads"])
        assert result.iloc[1] == pytest.approx(100.0)

    def test_c2_out_of_range_gives_0(self, scorer: Scorer, setup: dict) -> None:
        """C2: nearest road >200 m away → factor = 0."""
        result = scorer.road_proximity_factor(setup["candidates"], setup["roads"])
        assert result.iloc[2] == pytest.approx(0.0)

    def test_values_are_only_0_or_100(self, scorer: Scorer, setup: dict) -> None:
        """Road factor is strictly binary — no intermediate values."""
        result = scorer.road_proximity_factor(setup["candidates"], setup["roads"])
        for v in result:
            assert v in (0.0, 100.0), f"Unexpected non-binary value: {v}"


# ---------------------------------------------------------------------------
# TestRoadProximityFactorBoundary
#
# The threshold is ROAD_PROXIMITY_M = 200 m.
# ---------------------------------------------------------------------------

class TestRoadProximityFactorBoundary:

    def test_road_at_exact_threshold_gives_100(self, scorer: Scorer) -> None:
        """Road exactly ROAD_PROXIMITY_M metres away must be included → 100."""
        candidates = _candidates((OX, OY))
        roads_gdf  = _road_point((OX + ROAD_PROXIMITY_M, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert result.iloc[0] == pytest.approx(100.0), (
            f"Road at exactly {ROAD_PROXIMITY_M} m must give factor 100"
        )

    def test_road_just_inside_threshold_gives_100(self, scorer: Scorer) -> None:
        """Road at threshold - 1 m → 100."""
        candidates = _candidates((OX, OY))
        roads_gdf  = _road_point((OX + ROAD_PROXIMITY_M - 1, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert result.iloc[0] == pytest.approx(100.0)

    def test_road_just_outside_threshold_gives_0(self, scorer: Scorer) -> None:
        """Road at threshold + 1 m → beyond max_distance → NaN → 0."""
        candidates = _candidates((OX, OY))
        roads_gdf  = _road_point((OX + ROAD_PROXIMITY_M + 1, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert result.iloc[0] == pytest.approx(0.0), (
            f"Road just outside {ROAD_PROXIMITY_M} m must give factor 0"
        )

    def test_road_at_zero_distance_gives_100(self, scorer: Scorer) -> None:
        """Road co-located with candidate → 0 m distance → 100."""
        candidates = _candidates((OX, OY))
        roads_gdf  = _road_point((OX, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert result.iloc[0] == pytest.approx(100.0)

    def test_factor_never_intermediate(self, scorer: Scorer) -> None:
        """Mix of in-range and out-of-range candidates — all values must be 0 or 100."""
        candidates = _candidates(
            (OX,          OY),   # road at 50 m → 100
            (OX+1_000,    OY),   # road at 1 050 m → 0
        )
        roads_gdf = _road_point((OX + 50, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        for v in result:
            assert v in (0.0, 100.0)


# ---------------------------------------------------------------------------
# TestRoadProximityFactorEmptyRoads
# ---------------------------------------------------------------------------

class TestRoadProximityFactorEmptyRoads:

    def test_empty_roads_returns_series(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+100, OY))
        result = scorer.road_proximity_factor(candidates, _empty_roads())
        assert isinstance(result, pd.Series)

    def test_empty_roads_all_zeros(self, scorer: Scorer) -> None:
        """Missing roads layer → factor = 0 for all candidates."""
        candidates = _candidates((OX, OY), (OX+100, OY), (OX+200, OY))
        result = scorer.road_proximity_factor(candidates, _empty_roads())
        assert (result == 0.0).all()

    def test_empty_roads_length_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+100, OY))
        result = scorer.road_proximity_factor(candidates, _empty_roads())
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestRoadProximityFactorIndexAlignment
# ---------------------------------------------------------------------------

class TestRoadProximityFactorIndexAlignment:

    def test_output_index_matches_input_index(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+500, OY))
        roads_gdf  = _road_point((OX + 50, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert list(result.index) == list(candidates.index)

    def test_non_default_index_preserved(self, scorer: Scorer) -> None:
        base = _candidates((OX, OY), (OX+500, OY))
        candidates = base.set_index(pd.Index([7, 14]))
        roads_gdf  = _road_point((OX + 50, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert list(result.index) == [7, 14]

    def test_non_contiguous_index_preserved(self, scorer: Scorer) -> None:
        base = _candidates((OX, OY), (OX+100, OY), (OX+500, OY))
        candidates = base.drop(index=1)
        roads_gdf  = _road_point((OX + 50, OY))
        result = scorer.road_proximity_factor(candidates, roads_gdf)
        assert list(result.index) == [0, 2]


# ===========================================================================
# Mall proximity factor tests
# ===========================================================================
#
# mall_proximity_factor is binary:
#   - 100 if nearest mall is within MALL_PROXIMITY_M (500 m)
#   - 0   otherwise
#
# Structurally identical to road tests; threshold differs (500 m vs 200 m).
# ===========================================================================

# ---------------------------------------------------------------------------
# TestMallProximityFactorBasic
#
# Three candidates:
#   C0 = (OX,       OY)      — mall M0 at (OX+300, OY), dist=300 m ≤ 500 → 100
#   C1 = (OX+2000,  OY)      — mall M1 at (OX+2400, OY), dist=400 m ≤ 500 → 100
#   C2 = (OX+5000,  OY)      — no mall within 500 m                         → 0
# ---------------------------------------------------------------------------

class TestMallProximityFactorBasic:

    @pytest.fixture()
    def setup(self) -> dict:
        candidates = _candidates(
            (OX,        OY),    # C0
            (OX+2_000,  OY),    # C1
            (OX+5_000,  OY),    # C2 — far from all malls
        )
        malls_gdf = _malls(
            (OX + 300,   OY),    # M0 — 300 m from C0
            (OX+2_400,   OY),    # M1 — 400 m from C1
        )
        return {"candidates": candidates, "malls": malls_gdf}

    def test_returns_series(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.mall_proximity_factor(setup["candidates"], setup["malls"])
        assert isinstance(result, pd.Series)

    def test_length_matches_candidates(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.mall_proximity_factor(setup["candidates"], setup["malls"])
        assert len(result) == 3

    def test_c0_in_range_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C0: mall at 300 m ≤ MALL_PROXIMITY_M (500) → factor = 100."""
        result = scorer.mall_proximity_factor(setup["candidates"], setup["malls"])
        assert result.iloc[0] == pytest.approx(100.0)

    def test_c1_in_range_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C1: mall at 400 m ≤ 500 → factor = 100."""
        result = scorer.mall_proximity_factor(setup["candidates"], setup["malls"])
        assert result.iloc[1] == pytest.approx(100.0)

    def test_c2_out_of_range_gives_0(self, scorer: Scorer, setup: dict) -> None:
        """C2: nearest mall >500 m → factor = 0."""
        result = scorer.mall_proximity_factor(setup["candidates"], setup["malls"])
        assert result.iloc[2] == pytest.approx(0.0)

    def test_values_are_only_0_or_100(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.mall_proximity_factor(setup["candidates"], setup["malls"])
        for v in result:
            assert v in (0.0, 100.0), f"Unexpected non-binary value: {v}"


# ---------------------------------------------------------------------------
# TestMallProximityFactorBoundary
#
# The threshold is MALL_PROXIMITY_M = 500 m.
# ---------------------------------------------------------------------------

class TestMallProximityFactorBoundary:

    def test_mall_at_exact_threshold_gives_100(self, scorer: Scorer) -> None:
        """Mall exactly MALL_PROXIMITY_M metres away must be included → 100."""
        candidates = _candidates((OX, OY))
        malls_gdf  = _malls((OX + MALL_PROXIMITY_M, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert result.iloc[0] == pytest.approx(100.0), (
            f"Mall at exactly {MALL_PROXIMITY_M} m must give factor 100"
        )

    def test_mall_just_inside_threshold_gives_100(self, scorer: Scorer) -> None:
        """Mall at threshold - 1 m → 100."""
        candidates = _candidates((OX, OY))
        malls_gdf  = _malls((OX + MALL_PROXIMITY_M - 1, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert result.iloc[0] == pytest.approx(100.0)

    def test_mall_just_outside_threshold_gives_0(self, scorer: Scorer) -> None:
        """Mall at threshold + 1 m → beyond max_distance → NaN → 0."""
        candidates = _candidates((OX, OY))
        malls_gdf  = _malls((OX + MALL_PROXIMITY_M + 1, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert result.iloc[0] == pytest.approx(0.0), (
            f"Mall just outside {MALL_PROXIMITY_M} m must give factor 0"
        )

    def test_mall_at_zero_distance_gives_100(self, scorer: Scorer) -> None:
        """Mall co-located with candidate → 0 m → 100."""
        candidates = _candidates((OX, OY))
        malls_gdf  = _malls((OX, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert result.iloc[0] == pytest.approx(100.0)

    def test_factor_never_intermediate(self, scorer: Scorer) -> None:
        candidates = _candidates(
            (OX,        OY),   # mall at 200 m → 100
            (OX+2_000,  OY),   # mall at 1 800 m → 0
        )
        malls_gdf = _malls((OX + 200, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        for v in result:
            assert v in (0.0, 100.0)

    def test_road_threshold_does_not_apply_to_malls(self, scorer: Scorer) -> None:
        """
        A mall at 300 m must score 100 (within MALL_PROXIMITY_M=500),
        even though 300 > ROAD_PROXIMITY_M=200.
        This guards against accidentally using the wrong constant.
        """
        assert MALL_PROXIMITY_M > ROAD_PROXIMITY_M, "sanity: 500 > 200"
        candidates = _candidates((OX, OY))
        # Place mall between the two thresholds: 300 m
        # > ROAD_PROXIMITY_M (200) but ≤ MALL_PROXIMITY_M (500) → must give 100
        malls_gdf = _malls((OX + 300, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert result.iloc[0] == pytest.approx(100.0), (
            "Mall at 300 m should score 100 with MALL_PROXIMITY_M=500"
        )


# ---------------------------------------------------------------------------
# TestMallProximityFactorEmptyMalls
# ---------------------------------------------------------------------------

class TestMallProximityFactorEmptyMalls:

    def test_empty_malls_returns_series(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+100, OY))
        result = scorer.mall_proximity_factor(candidates, _empty_malls())
        assert isinstance(result, pd.Series)

    def test_empty_malls_all_zeros(self, scorer: Scorer) -> None:
        """Missing malls layer → factor = 0 for all candidates."""
        candidates = _candidates((OX, OY), (OX+100, OY), (OX+200, OY))
        result = scorer.mall_proximity_factor(candidates, _empty_malls())
        assert (result == 0.0).all()

    def test_empty_malls_length_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+100, OY))
        result = scorer.mall_proximity_factor(candidates, _empty_malls())
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestMallProximityFactorIndexAlignment
# ---------------------------------------------------------------------------

class TestMallProximityFactorIndexAlignment:

    def test_output_index_matches_input_index(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+600, OY))
        malls_gdf  = _malls((OX + 100, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert list(result.index) == list(candidates.index)

    def test_non_default_index_preserved(self, scorer: Scorer) -> None:
        base = _candidates((OX, OY), (OX+600, OY))
        candidates = base.set_index(pd.Index([3, 9]))
        malls_gdf  = _malls((OX + 100, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert list(result.index) == [3, 9]

    def test_non_contiguous_index_preserved(self, scorer: Scorer) -> None:
        base = _candidates((OX, OY), (OX+100, OY), (OX+600, OY))
        candidates = base.drop(index=1)
        malls_gdf  = _malls((OX + 50, OY))
        result = scorer.mall_proximity_factor(candidates, malls_gdf)
        assert list(result.index) == [0, 2]


# ===========================================================================
# Parking factor tests
# ===========================================================================
#
# parking_factor is binary:
#   - 100 if the candidate Point intersects any parking Polygon
#   - 0   otherwise
#
# The critical correctness invariant is the double-counting guard:
# a candidate that touches two *overlapping* polygons must still receive
# exactly 100 (one match), not appear twice in intermediate results.
# This is enforced by sjoin(how="inner") + .index.unique() + explicit
# .reindex — the tests below verify that path specifically.
# ===========================================================================

# ---------------------------------------------------------------------------
# TestParkingFactorBasic
#
# Three candidates:
#   C0 = (OX, OY)           — inside parking polygon P0
#   C1 = (OX+1000, OY)      — inside parking polygon P1
#   C2 = (OX+5000, OY)      — outside all parking polygons
#
# Two parking polygons (100 m × 100 m squares, half=50):
#   P0 centred at (OX, OY)        — contains C0
#   P1 centred at (OX+1000, OY)   — contains C1
#
# Expected parking factors (by hand):
#   C0 → 100.0
#   C1 → 100.0
#   C2 → 0.0
# ---------------------------------------------------------------------------

class TestParkingFactorBasic:

    @pytest.fixture()
    def setup(self) -> dict:
        candidates = _candidates(
            (OX,        OY),    # C0 — inside P0
            (OX+1_000,  OY),    # C1 — inside P1
            (OX+5_000,  OY),    # C2 — outside all polygons
        )
        parking_gdf = _parking_poly(
            _square_parking(OX,        OY,  50),   # P0
            _square_parking(OX+1_000,  OY,  50),   # P1
        )
        return {"candidates": candidates, "parking": parking_gdf}

    def test_returns_series(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.parking_factor(setup["candidates"], setup["parking"])
        assert isinstance(result, pd.Series)

    def test_length_matches_candidates(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.parking_factor(setup["candidates"], setup["parking"])
        assert len(result) == 3

    def test_c0_inside_polygon_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C0 is inside P0 → parking_factor = 100."""
        result = scorer.parking_factor(setup["candidates"], setup["parking"])
        assert result.iloc[0] == pytest.approx(100.0)

    def test_c1_inside_polygon_gives_100(self, scorer: Scorer, setup: dict) -> None:
        """C1 is inside P1 → parking_factor = 100."""
        result = scorer.parking_factor(setup["candidates"], setup["parking"])
        assert result.iloc[1] == pytest.approx(100.0)

    def test_c2_outside_all_gives_0(self, scorer: Scorer, setup: dict) -> None:
        """C2 is outside all parking polygons → parking_factor = 0."""
        result = scorer.parking_factor(setup["candidates"], setup["parking"])
        assert result.iloc[2] == pytest.approx(0.0)

    def test_values_are_only_0_or_100(self, scorer: Scorer, setup: dict) -> None:
        result = scorer.parking_factor(setup["candidates"], setup["parking"])
        for v in result:
            assert v in (0.0, 100.0), f"Unexpected non-binary value: {v}"


# ---------------------------------------------------------------------------
# TestParkingFactorOverlapping
#
# THIS IS THE CRITICAL DOUBLE-COUNTING TEST (design.md §Parking factor /
# Req 13 AC-1):
#
# A candidate that sits inside TWO overlapping parking polygons must still
# yield factor == 100, not produce two rows that would corrupt index
# alignment or inflate the result.
#
# Geometry:
#   C0 = (OX, OY)
#   P0 = square centred at (OX, OY), half=100 m   — contains C0
#   P1 = square centred at (OX+50, OY), half=100 m — also contains C0
#       (P0 and P1 overlap because |50| < 100+100)
#
# A naive left-join would yield two rows for C0 (one per polygon match).
# The correct implementation (inner join + .index.unique() + reindex)
# collapses them to one entry with value 100.
# ---------------------------------------------------------------------------

class TestParkingFactorOverlapping:

    def test_two_overlapping_polygons_gives_100_not_200(
        self, scorer: Scorer
    ) -> None:
        """
        Candidate inside two overlapping polygons → factor == 100 (not > 100).
        This guards against the sjoin double-counting bug.
        """
        candidates = _candidates((OX, OY))
        parking_gdf = _parking_poly(
            _square_parking(OX,      OY, 100),   # P0 — contains (OX, OY)
            _square_parking(OX + 50, OY, 100),   # P1 — also contains (OX, OY)
        )
        result = scorer.parking_factor(candidates, parking_gdf)
        assert result.iloc[0] == pytest.approx(100.0), (
            "Expected factor=100, not >100 — inner join + unique() must prevent double-counting"
        )

    def test_two_overlapping_polygons_output_length_is_one(
        self, scorer: Scorer
    ) -> None:
        """Output Series must have exactly one row per candidate."""
        candidates = _candidates((OX, OY))
        parking_gdf = _parking_poly(
            _square_parking(OX,      OY, 100),
            _square_parking(OX + 50, OY, 100),
        )
        result = scorer.parking_factor(candidates, parking_gdf)
        assert len(result) == 1, (
            "Inner join + unique() must collapse duplicate rows to one per candidate"
        )

    def test_three_overlapping_polygons_still_100(self, scorer: Scorer) -> None:
        """Even three overlapping polygons around the same point → 100."""
        candidates = _candidates((OX, OY))
        parking_gdf = _parking_poly(
            _square_parking(OX,       OY,  80),
            _square_parking(OX + 40,  OY,  80),
            _square_parking(OX - 40,  OY,  80),
        )
        result = scorer.parking_factor(candidates, parking_gdf)
        assert result.iloc[0] == pytest.approx(100.0)
        assert len(result) == 1

    def test_mixed_overlapping_and_non_overlapping_candidates(
        self, scorer: Scorer
    ) -> None:
        """
        C0 is inside two overlapping polygons → 100 (one row, not two).
        C1 is outside all polygons → 0.
        C2 is inside exactly one polygon → 100.
        Total output rows must equal 3.
        """
        candidates = _candidates(
            (OX,        OY),          # C0 — inside P0 and P1
            (OX+5_000,  OY),          # C1 — outside all
            (OX+2_000,  OY),          # C2 — inside P2 only
        )
        parking_gdf = _parking_poly(
            _square_parking(OX,        OY,  100),   # P0 — overlaps with P1, contains C0
            _square_parking(OX + 50,   OY,  100),   # P1 — overlaps with P0, contains C0
            _square_parking(OX+2_000,  OY,   50),   # P2 — contains C2
        )
        result = scorer.parking_factor(candidates, parking_gdf)
        assert len(result) == 3, "Output must have one row per candidate"
        assert result.iloc[0] == pytest.approx(100.0)   # C0 — inside two polygons
        assert result.iloc[1] == pytest.approx(0.0)     # C1 — outside all
        assert result.iloc[2] == pytest.approx(100.0)   # C2 — inside one polygon

    def test_index_not_duplicated_after_overlapping_join(
        self, scorer: Scorer
    ) -> None:
        """The output index must have no duplicated values."""
        candidates = _candidates((OX, OY), (OX+2_000, OY))
        parking_gdf = _parking_poly(
            _square_parking(OX,      OY, 100),
            _square_parking(OX + 50, OY, 100),   # overlaps with first, both contain C0
        )
        result = scorer.parking_factor(candidates, parking_gdf)
        assert not result.index.duplicated().any(), (
            "Output index must have no duplicates after overlapping polygon join"
        )


# ---------------------------------------------------------------------------
# TestParkingFactorBoundary
# ---------------------------------------------------------------------------

class TestParkingFactorBoundary:

    def test_point_on_polygon_boundary_gives_100(self, scorer: Scorer) -> None:
        """
        A Point on the boundary of a Polygon is considered to intersect it
        in Shapely's DE-9IM model → factor should be 100.
        """
        # Place candidate exactly on the eastern edge of the parking square.
        # P0: x ∈ [OX-50, OX+50], y ∈ [OY-50, OY+50].
        # Candidate at (OX+50, OY) — exactly on the eastern edge.
        candidates = _candidates((OX + 50, OY))
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        result = scorer.parking_factor(candidates, parking_gdf)
        assert result.iloc[0] == pytest.approx(100.0), (
            "Point on polygon boundary should intersect → factor 100"
        )

    def test_point_just_outside_polygon_gives_0(self, scorer: Scorer) -> None:
        """A Point strictly outside the polygon → 0."""
        candidates = _candidates((OX + 51, OY))   # 1 m beyond the edge
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        result = scorer.parking_factor(candidates, parking_gdf)
        assert result.iloc[0] == pytest.approx(0.0)

    def test_point_at_polygon_centre_gives_100(self, scorer: Scorer) -> None:
        """A Point at the exact centroid of the parking polygon → 100."""
        candidates = _candidates((OX, OY))
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        result = scorer.parking_factor(candidates, parking_gdf)
        assert result.iloc[0] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# TestParkingFactorEmptyParking
# ---------------------------------------------------------------------------

class TestParkingFactorEmptyParking:

    def test_empty_parking_returns_series(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+100, OY))
        result = scorer.parking_factor(candidates, _empty_parking())
        assert isinstance(result, pd.Series)

    def test_empty_parking_all_zeros(self, scorer: Scorer) -> None:
        """Missing parking layer → factor = 0 for all candidates."""
        candidates = _candidates((OX, OY), (OX+100, OY), (OX+200, OY))
        result = scorer.parking_factor(candidates, _empty_parking())
        assert (result == 0.0).all()

    def test_empty_parking_length_matches_candidates(self, scorer: Scorer) -> None:
        candidates = _candidates((OX, OY), (OX+100, OY))
        result = scorer.parking_factor(candidates, _empty_parking())
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestParkingFactorIndexAlignment
# ---------------------------------------------------------------------------

class TestParkingFactorIndexAlignment:

    def test_output_index_matches_input_index(self, scorer: Scorer) -> None:
        candidates  = _candidates((OX, OY), (OX+200, OY), (OX+5_000, OY))
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        result = scorer.parking_factor(candidates, parking_gdf)
        assert list(result.index) == list(candidates.index)

    def test_non_default_index_preserved(self, scorer: Scorer) -> None:
        """Index [5, 10] must survive the inner-join + reindex path."""
        base = _candidates((OX, OY), (OX+5_000, OY))
        candidates = base.set_index(pd.Index([5, 10]))
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        result = scorer.parking_factor(candidates, parking_gdf)
        assert list(result.index) == [5, 10]

    def test_non_contiguous_index_preserved(self, scorer: Scorer) -> None:
        """After drop → non-contiguous [0, 2]; reindex must restore both."""
        base = _candidates((OX, OY), (OX+100, OY), (OX+5_000, OY))
        candidates = base.drop(index=1)   # index [0, 2]
        parking_gdf = _parking_poly(_square_parking(OX, OY, 50))
        result = scorer.parking_factor(candidates, parking_gdf)
        assert list(result.index) == [0, 2]

    def test_index_alignment_with_overlapping_polygons(self, scorer: Scorer) -> None:
        """
        Even when a candidate matches multiple overlapping polygons, the
        output index must exactly match the input index — no extra rows.
        """
        base = _candidates((OX, OY), (OX+5_000, OY))
        candidates = base.set_index(pd.Index([7, 99]))
        parking_gdf = _parking_poly(
            _square_parking(OX,      OY, 100),
            _square_parking(OX + 50, OY, 100),   # overlaps, both contain index-7 candidate
        )
        result = scorer.parking_factor(candidates, parking_gdf)
        assert list(result.index) == [7, 99]
        assert len(result) == 2
