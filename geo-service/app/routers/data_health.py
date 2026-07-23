"""
GET /data-health router — dataset load status + optional anomaly scan.

Requirement 3 AC-4 & AC-6:
- Returns per-dataset record counts and last-load timestamps.
- Marks a city as "partial" if one or more required datasets are absent.

AI Enhancement 1 (design.md §Anomaly Detection):
- When ?anomalies=true is passed, runs the AnomalyDetector on all loaded
  cities and includes findings in the response.
- Anomaly reports from the startup scan are cached and returned without
  re-scanning unless ?anomalies=true forces a fresh scan.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from app.core.anomaly_detector import AnomalyDetector
from app.core.dataset_loader import registry
from app.models.schemas import (
    AnomalyReport,
    DataHealthResponse,
    DataHealthWithAnomalies,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])

# Cache anomaly reports from startup scan — keyed by city (lowercase).
_anomaly_cache: dict[str, AnomalyReport] = {}


def run_startup_anomaly_scan() -> None:
    """
    Called during lifespan startup after datasets are warmed.
    Scans all loaded cities and populates the anomaly cache.
    """
    for city_key, datasets in registry._cache.items():
        detector = AnomalyDetector(city_key, datasets)
        report = detector.scan()
        _anomaly_cache[city_key] = report
        if report.total_findings > 0:
            logger.warning(
                "startup anomaly scan found issues",
                extra={
                    "city": city_key,
                    "total_findings": report.total_findings,
                    "error_count": sum(
                        1 for f in report.findings if f.severity.value == "error"
                    ),
                },
            )


@router.get(
    "/data-health",
    response_model=DataHealthWithAnomalies,
    summary="Dataset load status for all loaded cities",
    description=(
        "Returns per-dataset record counts and ISO-8601 last-load timestamps "
        "for every city whose datasets have been loaded into memory. "
        "A city is marked 'partial' when one or more required layers are absent. "
        "Pass ?anomalies=true to include data quality scan results. "
        "(Requirement 3 AC-4 & AC-6, AI Enhancement 1)"
    ),
)
async def data_health(
    anomalies: Optional[bool] = Query(
        default=False,
        description="When true, run a fresh anomaly scan on all loaded cities",
    ),
) -> DataHealthWithAnomalies:
    base_response = registry.health()

    anomaly_reports: dict[str, AnomalyReport] = {}

    if anomalies:
        # Fresh scan requested — re-run detector on all cached cities
        for city_key, datasets in registry._cache.items():
            detector = AnomalyDetector(city_key, datasets)
            report = detector.scan()
            _anomaly_cache[city_key] = report
            anomaly_reports[city_key] = report
    else:
        # Return cached results from startup scan (if any)
        anomaly_reports = dict(_anomaly_cache)

    return DataHealthWithAnomalies(
        datasets=base_response.datasets,
        city_availability=base_response.city_availability,
        anomalies=anomaly_reports,
    )
