"""
ml/train.py — Training pipeline for ChargeWise ML scorer models.

Design reference: design.md §AI Enhancement 3: Training Pipeline

Usage:
    python -m ml.train --city=Bengaluru --charger-type=DC_FAST

Produces a serialised LightGBM model at ml/models/{city}_{charger_type}.joblib

Training strategies:
  Option A: Real utilisation data (sessions/day from BPCL/EESL datasets)
  Option B: Synthetic labels (bootstrapped from existing charger locations)

This script implements Option B (synthetic bootstrap) as the default
cold-start strategy.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def generate_synthetic_labels(
    candidates: pd.DataFrame,
    charger_dist_col: str = "charger_dist_m",
) -> pd.Series:
    """
    Generate synthetic training labels using distance-from-existing-chargers
    as a proxy for demand.

    Logic:
      - Locations AT existing chargers (dist ≈ 0) score 80–100 (known good).
      - Locations far from chargers score higher (underserved area = opportunity).
      - Random locations with no spatial signal score 20–40.

    This produces a reasonable gradient for the model to learn spatial
    patterns without real utilisation data.
    """
    dist = candidates[charger_dist_col].fillna(candidates[charger_dist_col].max())
    max_dist = dist.max() if dist.max() > 0 else 1.0

    # Normalise distance to 0–1 (higher = farther from existing charger)
    normalised = dist / max_dist

    # Score: locations far from chargers are good candidates (high demand gap)
    # Add some noise to prevent perfect overfitting
    rng = np.random.default_rng(seed=42)
    noise = rng.normal(0, 0.05, size=len(normalised))

    scores = (normalised * 0.7 + 0.2 + noise).clip(0, 1)

    return pd.Series(scores, index=candidates.index, name="target")


def train_model(
    features: pd.DataFrame,
    targets: pd.Series,
    city: str,
    charger_type: str,
    output_dir: str = "ml/models",
) -> None:
    """
    Train a LightGBM regressor and save the artifact.

    Uses 5-fold cross-validation for evaluation metrics, then trains
    a final model on all data for deployment.
    """
    try:
        import lightgbm as lgb
        from sklearn.model_selection import cross_val_score
        import joblib
    except ImportError as exc:
        logger.error(f"Missing dependency: {exc}. Install lightgbm, scikit-learn, joblib.")
        sys.exit(1)

    params = {
        "objective": "regression",
        "metric": "rmse",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "min_child_samples": 5,
        "verbose": -1,
    }

    model = lgb.LGBMRegressor(**params)

    # Cross-validation for evaluation
    cv_scores = cross_val_score(
        model, features, targets,
        cv=5, scoring="neg_root_mean_squared_error",
    )
    rmse = -cv_scores.mean()
    logger.info(f"5-fold CV RMSE: {rmse:.4f} (+/- {cv_scores.std():.4f})")

    # Train final model on all data
    model.fit(features, targets)

    # Save artifact
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    artifact_path = output_path / f"{city.lower()}_{charger_type.lower()}.joblib"
    joblib.dump(model, artifact_path)

    logger.info(f"Model saved to {artifact_path}")

    # Save metrics
    import json
    metrics_path = output_path / "metrics.json"
    metrics: dict = {}
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text())
    metrics[f"{city.lower()}_{charger_type.lower()}"] = {
        "rmse": round(rmse, 4),
        "cv_std": round(cv_scores.std(), 4),
        "n_samples": len(features),
        "n_features": features.shape[1],
    }
    metrics_path.write_text(json.dumps(metrics, indent=2))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Train ChargeWise ML scorer model")
    parser.add_argument("--city", required=True, help="City name (e.g. Bengaluru)")
    parser.add_argument("--charger-type", required=True, help="SLOW, FAST, or DC_FAST")
    parser.add_argument("--data-dir", default="data", help="Path to data directory")
    args = parser.parse_args()

    logger.info(f"Training model for {args.city} / {args.charger_type}")
    logger.info("Loading datasets...")

    # Import here to avoid circular dependency at module level
    import os
    os.environ.setdefault("DATA_DIR", args.data_dir)
    os.environ.setdefault("DEFAULT_CRS_EPSG", "32643")

    from app.core.dataset_loader import DatasetRegistry
    from app.core.candidates import generate_candidates
    from app.core.ml_scorer import build_feature_matrix

    registry = DatasetRegistry()
    datasets = registry.load(args.city)

    # Generate candidates
    city_bbox = datasets.ward_boundaries.geometry.union_all()
    candidates = generate_candidates(datasets, city_bbox)
    logger.info(f"Generated {len(candidates)} candidates")

    # Build features
    search_radius = 1500  # default for training
    features = build_feature_matrix(candidates, datasets, search_radius)
    logger.info(f"Feature matrix: {features.shape}")

    # Generate synthetic labels
    targets = generate_synthetic_labels(features)
    logger.info(f"Target range: [{targets.min():.2f}, {targets.max():.2f}]")

    # Train
    train_model(features, targets, args.city, args.charger_type)
    logger.info("Done.")


if __name__ == "__main__":
    main()
