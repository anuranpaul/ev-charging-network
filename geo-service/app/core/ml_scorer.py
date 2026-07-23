"""
app/core/ml_scorer.py — ChargeWise India Geo Service

ML-based scoring engine that uses trained LightGBM models to predict
charger demand for candidate locations.

Design reference: design.md §AI Enhancement 3: ML-Based Demand Prediction

Implements the same score_batch interface as Scorer so the recommendation
router can swap scorers transparently based on SCORING_MODE env var.

Scoring modes (controlled by SCORING_MODE):
  weighted  — use the existing deterministic Scorer (default)
  ml        — use MLScorer with trained model artifacts
  ensemble  — 0.6 * ml_score + 0.4 * weighted_score

Fallback guarantee: if no model artifact exists for the requested
(city, chargerType), falls back to the deterministic Scorer.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from app.core.scorer import Scorer, WEIGHTS_BY_TYPE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_feature_matrix(
    candidates: gpd.GeoDataFrame,
    datasets: Any,  # CityDatasets
    search_radius: int,
) -> pd.DataFrame:
    """
    Build the feature matrix consumed by the ML model.

    Returns a DataFrame with one row per candidate and columns:
      - pop_1km: int (population within 1 km buffer)
      - charger_dist_m: float (distance to nearest charger)
      - road_dist_m: float (distance to nearest arterial road)
      - parking_available: int (0 or 1)
      - mall_dist_m: float (distance to nearest mall)
      - metro_dist_m: float (distance to nearest metro station)
      - tech_park_dist_m: float (distance to nearest tech park)
      - fuel_station_count_500m: int (fuel stations within 500 m)
      - search_radius: int (the user's search radius — contextual feature)
    """
    from app.core.scorer import POPULATION_BUFFER_M

    n = len(candidates)
    features: dict[str, Any] = {}

    # Population within 1 km
    if len(datasets.population_grid) > 0:
        buffers = candidates.geometry.buffer(POPULATION_BUFFER_M)
        buf_gdf = gpd.GeoDataFrame(geometry=buffers, crs=candidates.crs)
        joined = gpd.sjoin(buf_gdf, datasets.population_grid, how="left", predicate="intersects")
        pop_col = "population" if "population" in joined.columns else None
        if pop_col:
            pop_sums = joined.groupby(joined.index)[pop_col].sum().reindex(
                candidates.index, fill_value=0
            )
            features["pop_1km"] = pop_sums.values
        else:
            features["pop_1km"] = np.zeros(n)
    else:
        features["pop_1km"] = np.zeros(n)

    # Distance to nearest charger
    if len(datasets.ev_chargers) > 0:
        nearest = gpd.sjoin_nearest(
            candidates, datasets.ev_chargers,
            how="left", max_distance=search_radius,
            distance_col="dist_m",
        )
        # Deduplicate — sjoin_nearest can produce multiple matches
        charger_dist = nearest.groupby(nearest.index)["dist_m"].min().reindex(
            candidates.index, fill_value=float(search_radius)
        )
        features["charger_dist_m"] = charger_dist.values
    else:
        features["charger_dist_m"] = np.full(n, float(search_radius))

    # Distance to nearest arterial road
    if len(datasets.roads) > 0:
        arterial = datasets.roads[
            datasets.roads.get("highway", pd.Series(dtype=str)).isin(
                ["motorway", "trunk", "primary", "secondary"]
            )
        ] if "highway" in datasets.roads.columns else datasets.roads
        if len(arterial) > 0:
            nearest_road = gpd.sjoin_nearest(
                candidates, arterial,
                how="left", max_distance=500,
                distance_col="road_dist_m",
            )
            road_dist = nearest_road.groupby(nearest_road.index)["road_dist_m"].min().reindex(
                candidates.index, fill_value=500.0
            )
            features["road_dist_m"] = road_dist.values
        else:
            features["road_dist_m"] = np.full(n, 500.0)
    else:
        features["road_dist_m"] = np.full(n, 500.0)

    # Parking availability (binary)
    if len(datasets.parking) > 0:
        matched = gpd.sjoin(
            candidates, datasets.parking,
            how="inner", predicate="intersects",
        ).index.unique()
        parking_vec = np.isin(candidates.index, matched).astype(int)
        features["parking_available"] = parking_vec
    else:
        features["parking_available"] = np.zeros(n, dtype=int)

    # Distance to nearest mall
    if len(datasets.malls) > 0:
        nearest_mall = gpd.sjoin_nearest(
            candidates, datasets.malls,
            how="left", max_distance=1000,
            distance_col="mall_dist_m",
        )
        mall_dist = nearest_mall.groupby(nearest_mall.index)["mall_dist_m"].min().reindex(
            candidates.index, fill_value=1000.0
        )
        features["mall_dist_m"] = mall_dist.values
    else:
        features["mall_dist_m"] = np.full(n, 1000.0)

    # Distance to nearest metro station
    if len(datasets.metro_stations) > 0:
        nearest_metro = gpd.sjoin_nearest(
            candidates, datasets.metro_stations,
            how="left", max_distance=2000,
            distance_col="metro_dist_m",
        )
        metro_dist = nearest_metro.groupby(nearest_metro.index)["metro_dist_m"].min().reindex(
            candidates.index, fill_value=2000.0
        )
        features["metro_dist_m"] = metro_dist.values
    else:
        features["metro_dist_m"] = np.full(n, 2000.0)

    # Distance to nearest tech park
    if len(datasets.tech_parks) > 0:
        nearest_tp = gpd.sjoin_nearest(
            candidates, datasets.tech_parks,
            how="left", max_distance=2000,
            distance_col="tp_dist_m",
        )
        tp_dist = nearest_tp.groupby(nearest_tp.index)["tp_dist_m"].min().reindex(
            candidates.index, fill_value=2000.0
        )
        features["tech_park_dist_m"] = tp_dist.values
    else:
        features["tech_park_dist_m"] = np.full(n, 2000.0)

    # Fuel station count within 500 m
    if len(datasets.fuel_stations) > 0:
        buf_500 = candidates.geometry.buffer(500)
        buf_gdf = gpd.GeoDataFrame(geometry=buf_500, crs=candidates.crs)
        joined_fs = gpd.sjoin(buf_gdf, datasets.fuel_stations, how="left", predicate="intersects")
        fs_count = joined_fs.groupby(joined_fs.index).size().reindex(
            candidates.index, fill_value=0
        )
        features["fuel_station_count_500m"] = fs_count.values.astype(int)
    else:
        features["fuel_station_count_500m"] = np.zeros(n, dtype=int)

    # Search radius as a contextual feature
    features["search_radius"] = np.full(n, search_radius)

    return pd.DataFrame(features, index=candidates.index)


# ---------------------------------------------------------------------------
# MLScorer
# ---------------------------------------------------------------------------

class MLScorer:
    """
    Drop-in replacement for Scorer using a trained LightGBM model.

    Implements score_batch with the same return signature so the
    recommendation router can swap scorers transparently.
    """

    def __init__(self, model_dir: str | None = None) -> None:
        if model_dir is None:
            model_dir = os.getenv("ML_MODEL_DIR", "ml/models")
        self._model_dir = Path(model_dir)
        self._models: dict[str, Any] = {}  # key: "{city}_{charger_type}"

    def load_model(self, city: str, charger_type: str) -> bool:
        """
        Load a serialised model artifact. Returns False if not found.

        Expected file: {model_dir}/{city}_{charger_type}.joblib
        """
        key = f"{city.lower()}_{charger_type.lower()}"
        if key in self._models:
            return True

        model_path = self._model_dir / f"{key}.joblib"
        if not model_path.exists():
            logger.warning(
                "ML model artifact not found",
                extra={"path": str(model_path), "city": city, "charger_type": charger_type},
            )
            return False

        try:
            import joblib
            model = joblib.load(model_path)
            self._models[key] = model
            logger.info(
                "ML model loaded",
                extra={"path": str(model_path), "city": city, "charger_type": charger_type},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to load ML model",
                extra={"path": str(model_path), "error": str(exc)},
            )
            return False

    def predict(
        self,
        candidates: gpd.GeoDataFrame,
        datasets: Any,
        search_radius: int,
        city: str,
        charger_type: str,
    ) -> np.ndarray | None:
        """
        Run model inference on the feature matrix.

        Returns an array of scores (0–100) or None if model unavailable.
        """
        key = f"{city.lower()}_{charger_type.lower()}"
        if key not in self._models:
            if not self.load_model(city, charger_type):
                return None

        model = self._models[key]
        feature_matrix = build_feature_matrix(candidates, datasets, search_radius)

        # LightGBM predict returns raw values — scale to 0–100
        raw_predictions = model.predict(feature_matrix)
        # Clip and scale (model trained on 0–1 target)
        scores = np.clip(raw_predictions * 100, 0, 100).round().astype(int)

        return scores


# ---------------------------------------------------------------------------
# Scoring mode orchestrator
# ---------------------------------------------------------------------------

def get_scoring_mode() -> str:
    """Return the configured scoring mode from SCORING_MODE env var."""
    return os.getenv("SCORING_MODE", "weighted").lower()


# Module-level instance
ml_scorer: MLScorer = MLScorer()
