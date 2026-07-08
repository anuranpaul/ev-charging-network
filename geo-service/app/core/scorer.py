"""
app/core/scorer.py — ChargeWise India Geo Service

Spatial scoring engine.  The ``Scorer`` class computes a weighted 0–100
score for each candidate location using five geospatial factors.

All five factors are fully implemented.

Design references
-----------------
  design.md §Scoring Algorithm / Factor Computation
  design.md §Correctness Properties 3, 4, 5, 6

Factor weights (design.md §Factor Computation)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  population        35 %
  charger_distance  25 %
  road_proximity    15 %
  parking           15 %
  mall_proximity    10 %

CRS contract
~~~~~~~~~~~~
All inputs must be in EPSG:32643 (UTM Zone 43N, metres).
The DatasetRegistry guarantees this; callers must not pass WGS-84 data.
"""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring constants  (design.md §Factor Computation)
# ---------------------------------------------------------------------------

POPULATION_BUFFER_M: float = 1_000.0    # fixed — NOT search_radius (Req 5 AC-4)
POPULATION_NORMALISER: float = 50_000.0  # design.md §Population factor (35%)
ROAD_PROXIMITY_M: float = 200.0          # design.md §Road proximity factor (15%)
MALL_PROXIMITY_M: float = 500.0          # design.md §Mall proximity factor (10%)

WEIGHTS: dict[str, float] = {
    "population":       0.35,
    "charger_distance": 0.25,
    "road_proximity":   0.15,
    "parking":          0.15,
    "mall_proximity":   0.10,
}


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class Scorer:
    """
    Vectorised spatial scorer for EV charger candidate locations.

    All spatial operations use GeoPandas batch operations — no row-by-row
    Python loops for spatial predicates (design.md §Scoring Algorithm).

    Usage::

        scorer = Scorer()
        pop_factor     = scorer.population_factor(candidates_gdf, population_grid_gdf)
        charger_factor = scorer.charger_distance_factor(candidates_gdf, ev_chargers_gdf, search_radius)
        road_factor    = scorer.road_proximity_factor(candidates_gdf, roads_gdf)
        park_factor    = scorer.parking_factor(candidates_gdf, parking_gdf)
        mall_factor    = scorer.mall_proximity_factor(candidates_gdf, malls_gdf)

        # Combine five per-candidate factor Series into a final score Series:
        scores = Scorer.compute_final_score(pop_factor, charger_factor,
                                            road_factor, park_factor, mall_factor)

    ``score_batch`` orchestrates all of the above end-to-end.
    """

    # ------------------------------------------------------------------
    # Population factor  (design.md §Population factor / 35%)
    # ------------------------------------------------------------------

    def population_factor(
        self,
        candidates: gpd.GeoDataFrame,
        population_grid: gpd.GeoDataFrame,
    ) -> pd.Series:
        """
        Compute the population factor (0–100) for each candidate.

        Algorithm (design.md §Population factor)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        1. Buffer every candidate point by exactly ``POPULATION_BUFFER_M``
           (1 000 m).  This buffer is **fixed** and independent of the
           caller's ``search_radius`` (Requirement 5 AC-4).
        2. Spatial-join candidate buffers with population grid cells using
           ``predicate="intersects"``.  Each candidate buffer may match
           zero or many grid cells.
        3. Sum the ``population`` column per candidate index.
        4. Normalise: ``pop_sum / POPULATION_NORMALISER`` (50 000).
        5. Clip to [0, 100] — prevents a densely-populated candidate from
           exceeding the scale maximum.
        6. Reindex onto ``candidates.index`` filling missing entries with 0
           (candidates with no intersecting grid cells have zero population).

        Parameters
        ----------
        candidates:
            GeoDataFrame of Point geometries in EPSG:32643.
        population_grid:
            GeoDataFrame with a ``population`` column (int/float) and
            Polygon geometries in EPSG:32643.  May be empty (missing layer),
            in which case every candidate receives a factor score of 0.

        Returns
        -------
        pd.Series
            Float values in [0.0, 100.0], indexed identically to
            ``candidates``.  One value per candidate row.
        """
        # Fast-path: missing layer → zero factor for all candidates.
        if population_grid.empty:
            logger.debug(
                "population_factor: population_grid is empty — returning zeros",
                extra={"candidate_count": len(candidates)},
            )
            return pd.Series(0.0, index=candidates.index)

        # Step 1 — buffer each candidate by exactly 1 000 m.
        buffers: gpd.GeoSeries = candidates.geometry.buffer(POPULATION_BUFFER_M)
        buf_gdf = gpd.GeoDataFrame(geometry=buffers, crs=candidates.crs)

        # Step 2 — spatial join: which grid cells does each buffer intersect?
        # "left" keeps every candidate even when no grid cell is found.
        joined = gpd.sjoin(
            buf_gdf,
            population_grid[["geometry", "population"]],
            how="left",
            predicate="intersects",
        )

        # Step 3 — sum population per candidate (left-hand index).
        # groupby uses the left frame's index; fill_value=0 covers candidates
        # that had no intersecting grid cells (sjoin produces NaN for them).
        pop_sums: pd.Series = (
            joined.groupby(joined.index)["population"]
            .sum()
            .reindex(candidates.index, fill_value=0)
        )

        # Step 4+5 — normalise and clip.
        pop_factor: pd.Series = (pop_sums / POPULATION_NORMALISER * 100).clip(
            upper=100.0
        )

        logger.debug(
            "population_factor computed",
            extra={
                "candidate_count": len(candidates),
                "non_zero_count":  int((pop_factor > 0).sum()),
                "max_factor":      float(pop_factor.max()),
            },
        )

        return pop_factor

    # ------------------------------------------------------------------
    # Charger distance factor  (design.md §Charger distance factor / 25%)
    # ------------------------------------------------------------------

    def charger_distance_factor(
        self,
        candidates: gpd.GeoDataFrame,
        ev_chargers: gpd.GeoDataFrame,
        search_radius: int,
    ) -> pd.Series:
        """
        Compute the charger-distance factor (0–100) for each candidate.

        Algorithm (design.md §Charger distance factor)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        1. ``sjoin_nearest`` finds the nearest existing EV charger to each
           candidate, up to ``max_distance=search_radius`` metres.
           Candidates beyond that distance receive NaN in the distance column.
        2. Normalise: ``dist_m / search_radius * 100``.
           - A charger at distance 0  → factor  0  (already served).
           - A charger at ``search_radius`` → factor 100 (furthest acceptable).
        3. Clip to [0, 100] — guards against floating-point edge cases where
           ``sjoin_nearest`` returns a distance marginally larger than
           ``max_distance`` due to floating-point rounding.
        4. Fill NaN with 100 — no charger within ``search_radius`` means
           this location is the most under-served, so it scores the maximum.

        ``sjoin_nearest`` may return duplicate rows when multiple chargers
        are equidistant.  We keep only the first match per candidate
        (minimum distance) by deduplicating on the left-hand index before
        building the factor series.

        Parameters
        ----------
        candidates:
            GeoDataFrame of Point geometries in EPSG:32643.
        ev_chargers:
            GeoDataFrame of Point (or any) geometries in EPSG:32643
            representing existing charger locations.  May be empty
            (missing layer), in which case every candidate receives
            factor == 100 (maximally under-served).
        search_radius:
            Maximum distance in metres within which a charger is considered
            "nearby".  Candidates farther than this receive factor == 100.

        Returns
        -------
        pd.Series
            Integer values in [0, 100], indexed identically to
            ``candidates``.  One value per candidate row.
        """
        # Fast-path: no existing chargers → every location is maximally
        # under-served, so every candidate gets the maximum factor of 100.
        if ev_chargers.empty:
            logger.debug(
                "charger_distance_factor: ev_chargers is empty — returning 100s",
                extra={"candidate_count": len(candidates)},
            )
            return pd.Series(100.0, index=candidates.index)

        # Step 1 — find the nearest charger to each candidate within
        # search_radius.  how="left" keeps every candidate row; candidates
        # with no charger within max_distance get NaN in "dist_m".
        # Reset the right-hand index so non-unique OSM IDs in the data
        # file cannot propagate into the join result.
        nearest = gpd.sjoin_nearest(
            candidates[["geometry"]],
            ev_chargers[["geometry"]].reset_index(drop=True),
            how="left",
            max_distance=search_radius,
            distance_col="dist_m",
        )

        # Deduplicate: when multiple chargers are equidistant sjoin_nearest
        # emits one row per tie.  Sort first, then deduplicate on the sorted
        # frame so "keep='first'" retains the minimum distance row.
        nearest_sorted = nearest.sort_values("dist_m", na_position="last")
        nearest = (
            nearest_sorted
            .loc[~nearest_sorted.index.duplicated(keep="first")]
            .reindex(candidates.index)   # restore original order
        )

        # Steps 2–4 — normalise, clip, fill.
        charger_factor: pd.Series = (
            (nearest["dist_m"] / search_radius * 100)
            .clip(upper=100.0)
            .fillna(100.0)
        )

        logger.debug(
            "charger_distance_factor computed",
            extra={
                "candidate_count":  len(candidates),
                "no_charger_count": int(nearest["dist_m"].isna().sum()),
                "min_factor":       float(charger_factor.min()),
                "max_factor":       float(charger_factor.max()),
            },
        )

        return charger_factor

    # ------------------------------------------------------------------
    # Road proximity factor  (design.md §Road proximity factor / 15%)
    # ------------------------------------------------------------------

    def road_proximity_factor(
        self,
        candidates: gpd.GeoDataFrame,
        roads: gpd.GeoDataFrame,
    ) -> pd.Series:
        """
        Compute the road-proximity factor (0 or 100) for each candidate.

        Algorithm (design.md §Road proximity factor)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Binary: a candidate scores 100 if there is an arterial road within
        ``ROAD_PROXIMITY_M`` metres, 0 otherwise.

        1. ``sjoin_nearest`` with ``max_distance=ROAD_PROXIMITY_M``.
           Candidates with no road within that distance get NaN.
        2. Factor = 100 where distance is not NaN, 0 where it is NaN.

        The design filters to ``highway`` values in
        ``{"motorway", "trunk", "primary"}`` before this call; this method
        receives whatever GeoDataFrame the caller supplies and does not
        filter itself, keeping responsibilities clean.

        ``sjoin_nearest`` can produce duplicate rows when roads are
        equidistant; deduplication on the candidate index is applied to
        ensure one row per candidate.

        Parameters
        ----------
        candidates:
            GeoDataFrame of Point geometries in EPSG:32643.
        roads:
            GeoDataFrame of (filtered) road geometries in EPSG:32643.
            May be empty (missing layer), in which case every candidate
            receives factor == 0.

        Returns
        -------
        pd.Series
            Values are 0.0 or 100.0, indexed identically to ``candidates``.
        """
        if roads.empty:
            logger.debug(
                "road_proximity_factor: roads is empty — returning zeros",
                extra={"candidate_count": len(candidates)},
            )
            return pd.Series(0.0, index=candidates.index)

        nearest = gpd.sjoin_nearest(
            candidates[["geometry"]],
            roads[["geometry"]].reset_index(drop=True),
            how="left",
            max_distance=ROAD_PROXIMITY_M,
            distance_col="road_dist_m",
        )

        # Deduplicate: keep the first (and closest) match per candidate.
        nearest_sorted = nearest.sort_values("road_dist_m", na_position="last")
        nearest = (
            nearest_sorted
            .loc[~nearest_sorted.index.duplicated(keep="first")]
            .reindex(candidates.index)
        )

        # Binary: within threshold → 100, otherwise → 0.
        road_factor: pd.Series = nearest["road_dist_m"].notna().astype(float) * 100.0

        logger.debug(
            "road_proximity_factor computed",
            extra={
                "candidate_count": len(candidates),
                "in_range_count":  int((road_factor == 100.0).sum()),
            },
        )

        return road_factor

    # ------------------------------------------------------------------
    # Parking factor  (design.md §Parking factor / 15%)
    # ------------------------------------------------------------------

    def parking_factor(
        self,
        candidates: gpd.GeoDataFrame,
        parking: gpd.GeoDataFrame,
    ) -> pd.Series:
        """
        Compute the parking factor (0 or 100) for each candidate.

        Algorithm (design.md §Parking factor)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Binary: a candidate scores 100 if it intersects any parking polygon,
        0 otherwise.

        Why ``sjoin(..., how="inner")`` and not a left-join boolean:
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        A plain ``how="left"`` join returns one output row per
        (candidate, matching-polygon) pair.  When a candidate point lies
        inside two overlapping parking polygons it produces *two* rows for
        that candidate.  Any boolean check on that joined frame (e.g.
        ``notna()``) still gives the right 0/100 per row, but if the caller
        then tries to use the joined frame as a positional index into
        ``candidates`` the extra rows cause mis-alignment.

        Using ``how="inner"`` we collect only the matched candidate indexes,
        then call ``.index.unique()`` to collapse duplicates to a set of
        distinct candidate positions.  An explicit ``.reindex`` onto
        ``candidates.index`` with ``fill_value=0`` restores the full
        candidate set with proper alignment, regardless of how many parking
        polygons any single candidate overlaps.

        Parameters
        ----------
        candidates:
            GeoDataFrame of Point geometries in EPSG:32643.
        parking:
            GeoDataFrame of Polygon geometries in EPSG:32643 representing
            parking areas.  May be empty (missing layer), in which case
            every candidate receives factor == 0.

        Returns
        -------
        pd.Series
            Values are 0.0 or 100.0, indexed identically to ``candidates``.
        """
        if parking.empty:
            logger.debug(
                "parking_factor: parking is empty — returning zeros",
                extra={"candidate_count": len(candidates)},
            )
            return pd.Series(0.0, index=candidates.index)

        # Inner join: only rows where the candidate point intersects a
        # parking polygon are returned.  A candidate touching N polygons
        # appears N times — we only want to know *whether* it matched, not
        # how many times.
        matched = gpd.sjoin(
            candidates[["geometry"]],
            parking[["geometry"]],
            how="inner",
            predicate="intersects",
        )

        # Collapse duplicates: a candidate intersecting multiple polygons
        # should still count as a single match (design.md §Parking factor).
        matched_idx = matched.index.unique()

        # Build the binary factor series aligned to the original index.
        parking_factor: pd.Series = pd.Series(
            candidates.index.isin(matched_idx).astype(float) * 100.0,
            index=candidates.index,
        )

        logger.debug(
            "parking_factor computed",
            extra={
                "candidate_count":  len(candidates),
                "matched_count":    int(len(matched_idx)),
            },
        )

        return parking_factor

    # ------------------------------------------------------------------
    # Mall proximity factor  (design.md §Mall proximity factor / 10%)
    # ------------------------------------------------------------------

    def mall_proximity_factor(
        self,
        candidates: gpd.GeoDataFrame,
        malls: gpd.GeoDataFrame,
    ) -> pd.Series:
        """
        Compute the mall-proximity factor (0 or 100) for each candidate.

        Algorithm (design.md §Mall proximity factor)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Binary: a candidate scores 100 if there is a shopping mall within
        ``MALL_PROXIMITY_M`` metres, 0 otherwise.

        Structurally identical to ``road_proximity_factor`` but uses
        ``MALL_PROXIMITY_M`` as the threshold and ``malls`` as the feature
        layer.

        1. ``sjoin_nearest`` with ``max_distance=MALL_PROXIMITY_M``.
        2. Factor = 100 where distance is not NaN, 0 where it is NaN.
        3. Deduplicate on candidate index to guard against equidistant ties.

        Parameters
        ----------
        candidates:
            GeoDataFrame of Point geometries in EPSG:32643.
        malls:
            GeoDataFrame of mall geometries in EPSG:32643.
            May be empty (missing layer), in which case every candidate
            receives factor == 0.

        Returns
        -------
        pd.Series
            Values are 0.0 or 100.0, indexed identically to ``candidates``.
        """
        if malls.empty:
            logger.debug(
                "mall_proximity_factor: malls is empty — returning zeros",
                extra={"candidate_count": len(candidates)},
            )
            return pd.Series(0.0, index=candidates.index)

        nearest = gpd.sjoin_nearest(
            candidates[["geometry"]],
            malls[["geometry"]].reset_index(drop=True),
            how="left",
            max_distance=MALL_PROXIMITY_M,
            distance_col="mall_dist_m",
        )

        # Deduplicate: keep the first (and closest) match per candidate.
        nearest_sorted = nearest.sort_values("mall_dist_m", na_position="last")
        nearest = (
            nearest_sorted
            .loc[~nearest_sorted.index.duplicated(keep="first")]
            .reindex(candidates.index)
        )

        # Binary: within threshold → 100, otherwise → 0.
        mall_factor: pd.Series = nearest["mall_dist_m"].notna().astype(float) * 100.0

        logger.debug(
            "mall_proximity_factor computed",
            extra={
                "candidate_count": len(candidates),
                "in_range_count":  int((mall_factor == 100.0).sum()),
            },
        )

        return mall_factor

    # ------------------------------------------------------------------
    # compute_final_score — pure arithmetic kernel (design.md Property 3)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_final_score(
        pop_factor:     pd.Series,
        charger_factor: pd.Series,
        road_factor:    pd.Series,
        park_factor:    pd.Series,
        mall_factor:    pd.Series,
    ) -> pd.Series:
        """
        Combine five per-candidate factor Series into a final weighted score.

        This is the **pure arithmetic kernel** for design.md Property 3:

            score = round(
                0.35 * pop_factor
              + 0.25 * charger_factor
              + 0.15 * road_factor
              + 0.15 * park_factor
              + 0.10 * mall_factor
            )

        Post-processing:
          - ``round()`` uses Python/NumPy banker's rounding for 0.5 ties.
          - ``.astype(int)`` converts to integer dtype.
          - ``.clip(0, 100)`` guards against floating-point edge cases that
            could push the result infinitesimally outside [0, 100].

        All five input Series must share the same index (candidates index).
        No spatial operations are performed here; callers are responsible
        for supplying correctly-projected factor Series.

        Missing-layer handling (design.md §Missing Layer Handling, Req 5 AC-8):
          Each individual factor method already returns ``0`` when its layer
          is absent, so ``compute_final_score`` receives zeros for those
          factors automatically.  No additional logic is needed here.

        Parameters
        ----------
        pop_factor, charger_factor, road_factor, park_factor, mall_factor:
            Float Series in [0.0, 100.0], all sharing the same index.

        Returns
        -------
        pd.Series
            Integer values in [0, 100], same index as inputs.
        """
        score: pd.Series = (
            WEIGHTS["population"]       * pop_factor
            + WEIGHTS["charger_distance"] * charger_factor
            + WEIGHTS["road_proximity"]   * road_factor
            + WEIGHTS["parking"]          * park_factor
            + WEIGHTS["mall_proximity"]   * mall_factor
        ).round().astype(int).clip(0, 100)

        return score

    # ------------------------------------------------------------------
    # score_batch  (all five factors, delegates to compute_final_score)
    # ------------------------------------------------------------------

    def score_batch(
        self,
        candidates: gpd.GeoDataFrame,
        datasets: Any,          # CityDatasets — loosely typed to avoid circular import
        search_radius: int,
    ) -> pd.DataFrame:
        """
        Score a batch of candidates across all five factors.

        Orchestrates the five factor methods and combines them via
        ``compute_final_score``.  Returns a DataFrame aligned to
        ``candidates.index`` with columns:
          ``pop_factor``     — population factor (0–100 float)
          ``charger_factor`` — charger-distance factor (0–100 float)
          ``road_factor``    — road-proximity factor (0 or 100 float)
          ``park_factor``    — parking factor (0 or 100 float)
          ``mall_factor``    — mall-proximity factor (0 or 100 float)
          ``score``          — final weighted score (0–100 int)

        Missing layers in ``datasets`` are handled transparently: each
        factor method returns zeros (or 100 for charger_distance) when its
        layer is empty, and those zeros propagate through
        ``compute_final_score`` without special casing here.

        Parameters
        ----------
        candidates:
            GeoDataFrame of Point geometries in EPSG:32643.
        datasets:
            CityDatasets instance providing all five spatial layers.
        search_radius:
            Search radius in metres for the charger-distance factor.

        Returns
        -------
        pd.DataFrame
            Index matches ``candidates.index``.
            Columns: ``pop_factor``, ``charger_factor``, ``road_factor``,
            ``park_factor``, ``mall_factor``, ``score``.
        """
        pop_factor     = self.population_factor(candidates, datasets.population_grid)
        charger_factor = self.charger_distance_factor(
            candidates, datasets.ev_chargers, search_radius
        )
        road_factor    = self.road_proximity_factor(candidates, datasets.roads)
        park_factor    = self.parking_factor(candidates, datasets.parking)
        mall_factor    = self.mall_proximity_factor(candidates, datasets.malls)

        score = self.compute_final_score(
            pop_factor, charger_factor, road_factor, park_factor, mall_factor
        )

        return pd.DataFrame(
            {
                "pop_factor":     pop_factor,
                "charger_factor": charger_factor,
                "road_factor":    road_factor,
                "park_factor":    park_factor,
                "mall_factor":    mall_factor,
                "score":          score,
            },
            index=candidates.index,
        )
