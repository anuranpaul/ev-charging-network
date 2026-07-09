"""
GET /data-health router — dataset load status.

Requirement 3 AC-4 & AC-6:
- Returns per-dataset record counts and last-load timestamps.
- Marks a city as "partial" if one or more required datasets are absent.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.dataset_loader import registry
from app.models.schemas import DataHealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])


@router.get(
    "/data-health",
    response_model=DataHealthResponse,
    summary="Dataset load status for all loaded cities",
    description=(
        "Returns per-dataset record counts and ISO-8601 last-load timestamps "
        "for every city whose datasets have been loaded into memory. "
        "A city is marked 'partial' when one or more required layers are absent. "
        "(Requirement 3 AC-4 & AC-6)"
    ),
)
async def data_health() -> DataHealthResponse:
    return registry.health()
