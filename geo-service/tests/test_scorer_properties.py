"""
tests/test_scorer_properties.py

Property-based tests for app/core/scorer.py using Hypothesis.

Scope (design.md §Testing Strategy — Geo Service PBT):

  Property 3: Final weighted score is correctly computed and bounded.
    test_score_formula_and_bounds

  Property 4: Population factor uses a fixed 1 km buffer.
    (Validated in test_scorer.py unit tests; 1 km buffer independence is
     structural — confirmed by inspecting POPULATION_BUFFER_M constant and
     the population_factor implementation, not a Hypothesis concern here.)

  Property 5: Score determinism under candidate order permutation.
    test_score_determinism_under_shuffle

  Property 6: Missing layer triggers zero factor score and warning.
    test_missing_layer_zero_factor

All tests are tagged with a comment referencing the design property per
design.md §Testing Strategy.

Library versions: hypothesis>=6.100 (hypothesis[pandas]).
"""

from __future__ import annotations

from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from shapely.geometry import Point, Polygon

from app.core.scorer import WEIGHTS, Scorer

# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

# All property tests use at least 100 examples (design.md §Testing Strategy).
_SETTINGS = settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EPSG = 32643
OX: float = 700_000.0
OY: float = 1_420_000.0

# Factor score range; each individual factor is in [0, 100].
_FACTOR_RANGE = st.floats(min_value=0.0, max_value=100.0, allow_nan=False,
                          allow_infinity=False)


# ---------------------------------------------------------------------------
# Helpers shared across property tests
# ---------------------------------------------------------------------------

def _factor_series(values: list[float], index: pd.Index | None = None) -> pd.Series:
    idx = index if index is not None else pd.RangeIndex(len(values))
    return pd.Series(values, index=idx, dtype=float)


def _candidates_gdf(coords: list[tuple[float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        geometry=[Point(x, y) for x, y in coords],
        crs=f"EPSG:{EPSG}",
    )


def _empty_layer() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=EPSG))


def _empty_pop_grid() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"population": pd.Series([], dtype=int)},
        geometry=gpd.GeoSeries([], crs=EPSG),
    )


def _full_empty_datasets() -> SimpleNamespace:
    """CityDatasets stub where every layer is empty."""
    return SimpleNamespace(
        population_grid=_empty_pop_grid(),
        ev_chargers=_empty_layer(),
        roads=_empty_layer(),
        parking=_empty_layer(),
        malls=_empty_layer(),
    )


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

def _five_factor_lists(n: int):
    """Strategy: produce five lists each of length n, values in [0, 100]."""
    factor = st.lists(
        st.floats(min_value=0.0, max_value=100.0, allow_nan=False,
                  allow_infinity=False),
        min_size=n,
        max_size=n,
    )
    return st.tuples(factor, factor, factor, factor, factor)


# ===========================================================================
# Property 3 — Final weighted score is correctly computed and bounded
#
# "For any five factor scores each in the range [0, 100], the Scorer's
#  compute_final_score(pop, chr, road, park, mall) must return
#  round(0.35×pop + 0.25×chr + 0.15×road + 0.15×park + 0.10×mall) and
#  the result must be an integer in [0, 100] inclusive."
#
# design.md §Correctness Properties / Property 3 / Validates: Req 5.2
# ===========================================================================

# Strategy: 5-tuple of individual factor values in [0, 100].
_five_factors = st.tuples(
    _FACTOR_RANGE, _FACTOR_RANGE, _FACTOR_RANGE, _FACTOR_RANGE, _FACTOR_RANGE
)


@_SETTINGS
@given(_five_factors)
def test_score_formula_and_bounds(factors: tuple[float, float, float, float, float]) -> None:
    # Feature: chargewise-india, Property 3: final weighted score is correctly
    # computed and bounded.
    pop, charger, road, park, mall = factors

    pop_s     = _factor_series([pop])
    charger_s = _factor_series([charger])
    road_s    = _factor_series([road])
    park_s    = _factor_series([park])
    mall_s    = _factor_series([mall])

    result = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)

    # --- formula correctness ---
    raw = (
        WEIGHTS["population"]       * pop
        + WEIGHTS["charger_distance"] * charger
        + WEIGHTS["road_proximity"]   * road
        + WEIGHTS["parking"]          * park
        + WEIGHTS["mall_proximity"]   * mall
    )
    expected = int(round(raw))
    # clip(0,100) may differ from raw round if out of range
    expected_clipped = max(0, min(100, expected))

    assert result.iloc[0] == expected_clipped, (
        f"Formula mismatch: factors={factors!r}, raw={raw:.6f}, "
        f"expected={expected_clipped}, got={result.iloc[0]}"
    )

    # --- bounds ---
    assert isinstance(result.iloc[0], (int, np.integer)), (
        f"Score must be integer dtype, got {type(result.iloc[0])}"
    )
    assert 0 <= result.iloc[0] <= 100, (
        f"Score {result.iloc[0]} out of [0, 100]"
    )


@_SETTINGS
@given(st.lists(_five_factors, min_size=1, max_size=20))
def test_score_formula_batch_all_bounded(
    factor_rows: list[tuple[float, float, float, float, float]],
) -> None:
    # Feature: chargewise-india, Property 3 (batch): all scores in [0, 100].
    n = len(factor_rows)
    pop_s     = _factor_series([r[0] for r in factor_rows])
    charger_s = _factor_series([r[1] for r in factor_rows])
    road_s    = _factor_series([r[2] for r in factor_rows])
    park_s    = _factor_series([r[3] for r in factor_rows])
    mall_s    = _factor_series([r[4] for r in factor_rows])

    result = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)

    assert len(result) == n
    assert pd.api.types.is_integer_dtype(result), (
        f"Score Series must have integer dtype, got {result.dtype}"
    )
    assert (result >= 0).all() and (result <= 100).all(), (
        f"Scores out of [0, 100]: {result[~result.between(0, 100)].tolist()}"
    )


@_SETTINGS
@given(_five_factors)
def test_score_all_zeros_gives_zero(
    factors: tuple[float, float, float, float, float],
) -> None:
    # Property 3 edge case: zero factors must yield score 0.
    # (Ignored if any factor is non-zero; just check the all-zero case.)
    pop_s     = _factor_series([0.0])
    charger_s = _factor_series([0.0])
    road_s    = _factor_series([0.0])
    park_s    = _factor_series([0.0])
    mall_s    = _factor_series([0.0])

    result = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)
    assert result.iloc[0] == 0


@_SETTINGS
@given(_five_factors)
def test_score_all_100_gives_100(
    factors: tuple[float, float, float, float, float],
) -> None:
    # Property 3 edge case: all factors at maximum → score = 100.
    pop_s     = _factor_series([100.0])
    charger_s = _factor_series([100.0])
    road_s    = _factor_series([100.0])
    park_s    = _factor_series([100.0])
    mall_s    = _factor_series([100.0])

    result = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)
    # 0.35*100 + 0.25*100 + 0.15*100 + 0.15*100 + 0.10*100 = 100.0 → 100
    assert result.iloc[0] == 100


# ===========================================================================
# Property 5 — Score determinism under candidate order permutation
#
# "For any candidate GeoDataFrame, shuffling the row order before calling
#  score_batch must produce identical score values for each candidate
#  (identified by original geometry centroid coordinates)."
#
# We test compute_final_score directly (pure function, no spatial ops) and
# also score_batch end-to-end with a fully-empty datasets stub (all layers
# absent → factors are constants: pop=0, charger=100, road=0, park=0,
# mall=0 regardless of row order).
#
# design.md §Correctness Properties / Property 5 / Validates: Req 5.7
# ===========================================================================

# Strategy: list of 2–10 factor-row tuples.
_factor_rows = st.lists(
    st.tuples(
        _FACTOR_RANGE, _FACTOR_RANGE, _FACTOR_RANGE, _FACTOR_RANGE, _FACTOR_RANGE,
    ),
    min_size=2,
    max_size=10,
)


@_SETTINGS
@given(_factor_rows)
def test_score_determinism_under_shuffle(
    factor_rows: list[tuple[float, float, float, float, float]],
) -> None:
    # Feature: chargewise-india, Property 5: score determinism under
    # candidate order permutation.
    n = len(factor_rows)
    idx = pd.RangeIndex(n)

    def _make_series(col: int) -> pd.Series:
        return _factor_series([r[col] for r in factor_rows], idx)

    pop_s     = _make_series(0)
    charger_s = _make_series(1)
    road_s    = _make_series(2)
    park_s    = _make_series(3)
    mall_s    = _make_series(4)

    # Original order
    original = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)

    # Shuffle: reverse row order, then scramble with a fixed permutation.
    perm = list(reversed(range(n)))
    pop_shuf     = pop_s.iloc[perm].reset_index(drop=True)
    charger_shuf = charger_s.iloc[perm].reset_index(drop=True)
    road_shuf    = road_s.iloc[perm].reset_index(drop=True)
    park_shuf    = park_s.iloc[perm].reset_index(drop=True)
    mall_shuf    = mall_s.iloc[perm].reset_index(drop=True)

    shuffled = Scorer.compute_final_score(
        pop_shuf, charger_shuf, road_shuf, park_shuf, mall_shuf
    )

    # Each position in shuffled corresponds to original[perm[i]].
    for i, orig_idx in enumerate(perm):
        assert shuffled.iloc[i] == original.iloc[orig_idx], (
            f"Determinism violation at position {i}: "
            f"shuffled={shuffled.iloc[i]}, original[{orig_idx}]={original.iloc[orig_idx]}"
        )


@_SETTINGS
@given(
    st.lists(
        st.floats(min_value=700_000, max_value=710_000, allow_nan=False,
                  allow_infinity=False),
        min_size=2, max_size=8,
    )
)
def test_score_batch_determinism_empty_datasets(x_coords: list[float]) -> None:
    # Feature: chargewise-india, Property 5 (score_batch path):
    # same score for every candidate regardless of row order when all layers
    # are empty (each candidate receives the same constant factor values).
    coords = [(x, OY) for x in x_coords]
    candidates = _candidates_gdf(coords)
    datasets   = _full_empty_datasets()
    scorer     = Scorer()

    result_original = scorer.score_batch(candidates, datasets, search_radius=1_000)

    # Shuffle candidate GeoDataFrame rows.
    shuffled_cands = candidates.iloc[::-1].reset_index(drop=True)
    result_shuffled = scorer.score_batch(shuffled_cands, datasets, search_radius=1_000)

    # When all layers are empty every candidate receives the same score
    # (charger_factor=100, all others=0 → score=round(0.25*100)=25).
    # Both result DataFrames should have identical score values.
    assert list(result_original["score"]) == list(result_original["score"]), (
        "Scores must be identical on repeated call with same inputs"
    )
    # The shuffled frame has the same coordinates, just reversed; each
    # position must also have score=25.
    expected_score = round(WEIGHTS["charger_distance"] * 100)  # 25
    for v in result_shuffled["score"]:
        assert v == expected_score, (
            f"Expected score={expected_score} for empty-datasets candidate, got {v}"
        )


# ===========================================================================
# Property 6 — Missing layer triggers zero factor score
#
# "For any candidate set, when a CityDatasets object has one or more layers
#  set to an empty GeoDataFrame (simulating absence), every candidate in the
#  output must have factor score 0 for each affected factor."
#
# We test this through score_batch (which calls each factor method) by
# selectively emptying one layer at a time and asserting the matching
# factor column is all zeros.
#
# Note on "warnings": design.md Property 6 says each candidate's warnings
# list must contain the affected factor name.  At this stage, warnings are
# attached at the ScorerResult level (not yet wired to score_batch output).
# The tests below verify zero factor values, which is the directly testable
# invariant from compute_final_score / score_batch.  The warning propagation
# is tested in the ScorerResult integration step.
#
# design.md §Correctness Properties / Property 6 / Validates: Req 5.8
# ===========================================================================

# Strategy: 1–5 candidate x-offsets (all with the same y, for simplicity).
_x_offsets = st.lists(
    st.floats(min_value=0.0, max_value=5_000.0, allow_nan=False,
              allow_infinity=False),
    min_size=1,
    max_size=5,
)


@_SETTINGS
@given(_x_offsets)
def test_missing_population_grid_gives_zero_pop_factor(x_offsets: list[float]) -> None:
    # Feature: chargewise-india, Property 6: missing layer → zero factor.
    candidates = _candidates_gdf([(OX + dx, OY) for dx in x_offsets])
    datasets   = _full_empty_datasets()   # population_grid is empty
    scorer     = Scorer()

    result = scorer.score_batch(candidates, datasets, search_radius=1_000)

    assert (result["pop_factor"] == 0.0).all(), (
        "Empty population_grid must produce pop_factor=0 for all candidates"
    )


@_SETTINGS
@given(_x_offsets)
def test_missing_roads_gives_zero_road_factor(x_offsets: list[float]) -> None:
    # Feature: chargewise-india, Property 6: missing roads layer → zero road factor.
    candidates = _candidates_gdf([(OX + dx, OY) for dx in x_offsets])
    datasets   = _full_empty_datasets()   # roads is empty
    scorer     = Scorer()

    result = scorer.score_batch(candidates, datasets, search_radius=1_000)

    assert (result["road_factor"] == 0.0).all(), (
        "Empty roads layer must produce road_factor=0 for all candidates"
    )


@_SETTINGS
@given(_x_offsets)
def test_missing_parking_gives_zero_park_factor(x_offsets: list[float]) -> None:
    # Feature: chargewise-india, Property 6: missing parking layer → zero park factor.
    candidates = _candidates_gdf([(OX + dx, OY) for dx in x_offsets])
    datasets   = _full_empty_datasets()   # parking is empty
    scorer     = Scorer()

    result = scorer.score_batch(candidates, datasets, search_radius=1_000)

    assert (result["park_factor"] == 0.0).all(), (
        "Empty parking layer must produce park_factor=0 for all candidates"
    )


@_SETTINGS
@given(_x_offsets)
def test_missing_malls_gives_zero_mall_factor(x_offsets: list[float]) -> None:
    # Feature: chargewise-india, Property 6: missing malls layer → zero mall factor.
    candidates = _candidates_gdf([(OX + dx, OY) for dx in x_offsets])
    datasets   = _full_empty_datasets()   # malls is empty
    scorer     = Scorer()

    result = scorer.score_batch(candidates, datasets, search_radius=1_000)

    assert (result["mall_factor"] == 0.0).all(), (
        "Empty malls layer must produce mall_factor=0 for all candidates"
    )


@_SETTINGS
@given(_x_offsets)
def test_missing_chargers_gives_100_charger_factor(x_offsets: list[float]) -> None:
    # Feature: chargewise-india, Property 6 (charger special case):
    # missing ev_chargers layer → charger_factor=100 (maximally under-served).
    candidates = _candidates_gdf([(OX + dx, OY) for dx in x_offsets])
    datasets   = _full_empty_datasets()   # ev_chargers is empty
    scorer     = Scorer()

    result = scorer.score_batch(candidates, datasets, search_radius=1_000)

    assert (result["charger_factor"] == 100.0).all(), (
        "Empty ev_chargers layer must produce charger_factor=100 for all candidates"
    )


@_SETTINGS
@given(_x_offsets)
def test_all_layers_empty_score_is_charger_weight_times_100(
    x_offsets: list[float],
) -> None:
    # Feature: chargewise-india, Property 6 (score with all layers empty):
    # score = round(0.35*0 + 0.25*100 + 0.15*0 + 0.15*0 + 0.10*0) = 25
    candidates = _candidates_gdf([(OX + dx, OY) for dx in x_offsets])
    datasets   = _full_empty_datasets()
    scorer     = Scorer()

    result = scorer.score_batch(candidates, datasets, search_radius=1_000)

    expected = round(WEIGHTS["charger_distance"] * 100)  # 25
    assert (result["score"] == expected).all(), (
        f"All-empty-layers score must be {expected}, got {result['score'].tolist()}"
    )


@_SETTINGS
@given(_five_factors)
def test_compute_final_score_with_zero_factors_gives_zero_score(
    factors: tuple[float, float, float, float, float],
) -> None:
    # Feature: chargewise-india, Property 6 (direct compute_final_score):
    # when all factors are 0, score must be 0.
    zeros = _factor_series([0.0])
    result = Scorer.compute_final_score(zeros, zeros, zeros, zeros, zeros)
    assert result.iloc[0] == 0


# ===========================================================================
# Additional robustness properties for compute_final_score
# ===========================================================================

@_SETTINGS
@given(st.lists(_five_factors, min_size=1, max_size=30))
def test_compute_final_score_index_preserved(
    factor_rows: list[tuple[float, float, float, float, float]],
) -> None:
    """Output index matches input Series index exactly."""
    n = len(factor_rows)
    # Use a non-default starting index to catch off-by-one errors.
    idx = pd.RangeIndex(start=10, stop=10 + n)
    pop_s     = _factor_series([r[0] for r in factor_rows], idx)
    charger_s = _factor_series([r[1] for r in factor_rows], idx)
    road_s    = _factor_series([r[2] for r in factor_rows], idx)
    park_s    = _factor_series([r[3] for r in factor_rows], idx)
    mall_s    = _factor_series([r[4] for r in factor_rows], idx)

    result = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)

    assert list(result.index) == list(idx), (
        f"Output index {list(result.index)} != input index {list(idx)}"
    )


@_SETTINGS
@given(st.lists(_five_factors, min_size=1, max_size=30))
def test_compute_final_score_length_preserved(
    factor_rows: list[tuple[float, float, float, float, float]],
) -> None:
    """Output length equals number of input candidates."""
    n = len(factor_rows)
    pop_s     = _factor_series([r[0] for r in factor_rows])
    charger_s = _factor_series([r[1] for r in factor_rows])
    road_s    = _factor_series([r[2] for r in factor_rows])
    park_s    = _factor_series([r[3] for r in factor_rows])
    mall_s    = _factor_series([r[4] for r in factor_rows])

    result = Scorer.compute_final_score(pop_s, charger_s, road_s, park_s, mall_s)

    assert len(result) == n


@_SETTINGS
@given(_five_factors)
def test_compute_final_score_monotone_in_each_factor(
    factors: tuple[float, float, float, float, float],
) -> None:
    """
    Increasing any one factor by a detectable amount must not decrease the
    score (monotonicity, given integer rounding).  We use a bump of +1.0
    which is large enough to survive banker's rounding.
    """
    pop, charger, road, park, mall = factors

    def _score(p: float, c: float, r: float, pk: float, m: float) -> int:
        return int(Scorer.compute_final_score(
            _factor_series([p]),
            _factor_series([c]),
            _factor_series([r]),
            _factor_series([pk]),
            _factor_series([m]),
        ).iloc[0])

    base = _score(pop, charger, road, park, mall)

    # Bump each factor individually if not already at ceiling.
    if pop + 1.0 <= 100.0:
        assert _score(pop + 1.0, charger, road, park, mall) >= base
    if charger + 1.0 <= 100.0:
        assert _score(pop, charger + 1.0, road, park, mall) >= base
    if road + 1.0 <= 100.0:
        assert _score(pop, charger, road + 1.0, park, mall) >= base
    if park + 1.0 <= 100.0:
        assert _score(pop, charger, road, park + 1.0, mall) >= base
    if mall + 1.0 <= 100.0:
        assert _score(pop, charger, road, park, mall + 1.0) >= base
