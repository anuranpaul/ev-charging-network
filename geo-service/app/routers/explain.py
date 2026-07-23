"""
POST /explain router — AI-powered candidate explanation.

Design reference: design.md §AI Enhancement 2: AI-Powered Explanation

Builds a structured prompt from candidate properties and calls the
configured LLM provider. Returns a human-readable explanation suitable
for stakeholder presentations.

Fallback: if the LLM call fails, returns a deterministic template-based
explanation with confidence="fallback".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.llm_provider import llm, MockProvider
from app.core.scorer import WEIGHTS_BY_TYPE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CandidateExplainInput(BaseModel):
    """Factor data for the candidate to explain."""

    score: int = Field(..., ge=0, le=100)
    factor_scores: dict[str, int] = Field(
        ..., description="Factor name → 0–100 score"
    )
    population_1km: int = Field(default=0, ge=0)
    nearest_charger_distance_m: float | None = None
    road_type: str = "unknown"
    parking_available: bool = False
    nearest_mall_distance_m: float | None = None
    coordinates: list[float] = Field(default_factory=list)


class ExplainRequest(BaseModel):
    """POST /explain request body."""

    city: str
    chargerType: str = Field(..., alias="chargerType")
    rank: int = Field(..., ge=1)
    candidate: CandidateExplainInput

    model_config = {"populate_by_name": True}


class ExplainResponse(BaseModel):
    """POST /explain response."""

    explanation: str
    confidence: str = Field(
        ..., description="'high' when LLM generated, 'fallback' on failure"
    )
    generated_at: str
    model: str

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

EXPLAIN_SYSTEM_PROMPT = """You are an EV infrastructure planning analyst.
Given a scored candidate location for EV charger placement, produce a
concise plain-language explanation of its score suitable for a city
planning committee. Reference specific factor values and explain their
practical significance. Do not exceed 200 words."""

EXPLAIN_USER_TEMPLATE = """City: {city}
Charger type: {charger_type} (weights: {weights_summary})
Rank: #{rank}
Overall score: {score}/100

Factor breakdown:
- Population density (1 km): {pop_score}/100 ({pop_raw:,} residents)
- Nearest existing charger: {charger_score}/100 ({charger_dist_desc})
- Road proximity: {road_score}/100 (road type: {road_type})
- Parking availability: {parking_score}/100 (available: {parking_avail})
- Mall proximity: {mall_score}/100 ({mall_dist_desc})

Explain why this location received its score, highlighting the strongest
and weakest factors in practical terms."""


def _build_user_prompt(req: ExplainRequest) -> str:
    """Populate the user prompt template with candidate data."""
    c = req.candidate
    fs = c.factor_scores

    # Build a weights summary string for the charger type
    weights = WEIGHTS_BY_TYPE.get(req.chargerType, WEIGHTS_BY_TYPE["FAST"])
    weights_summary = ", ".join(
        f"{k} {int(v * 100)}%" for k, v in weights.items()
    )

    # Distance descriptions
    charger_dist_desc = (
        f"{c.nearest_charger_distance_m:.0f} m"
        if c.nearest_charger_distance_m is not None
        else "none within search radius"
    )
    mall_dist_desc = (
        f"{c.nearest_mall_distance_m:.0f} m"
        if c.nearest_mall_distance_m is not None
        else "none within 500 m"
    )

    return EXPLAIN_USER_TEMPLATE.format(
        city=req.city,
        charger_type=req.chargerType,
        weights_summary=weights_summary,
        rank=req.rank,
        score=c.score,
        pop_score=fs.get("population", 0),
        pop_raw=c.population_1km,
        charger_score=fs.get("charger_distance", 0),
        charger_dist_desc=charger_dist_desc,
        road_score=fs.get("road_proximity", 0),
        road_type=c.road_type,
        parking_score=fs.get("parking", 0),
        parking_avail=c.parking_available,
        mall_score=fs.get("mall_proximity", 0),
        mall_dist_desc=mall_dist_desc,
    )


def _build_fallback_explanation(req: ExplainRequest) -> str:
    """Deterministic fallback when LLM is unavailable."""
    c = req.candidate
    fs = c.factor_scores

    # Find strongest and weakest
    sorted_factors = sorted(fs.items(), key=lambda x: x[1], reverse=True)
    strongest_name, strongest_val = sorted_factors[0] if sorted_factors else ("unknown", 0)
    weakest_name, weakest_val = sorted_factors[-1] if sorted_factors else ("unknown", 0)

    return (
        f"Score {c.score}/100 for {req.chargerType.replace('_', ' ').lower()} "
        f"placement in {req.city}. "
        f"Strongest factor: {strongest_name.replace('_', ' ')} ({strongest_val}/100). "
        f"Weakest factor: {weakest_name.replace('_', ' ')} ({weakest_val}/100)."
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/explain",
    response_model=ExplainResponse,
    summary="Generate a human-readable explanation for a candidate's score",
    description=(
        "Given a candidate's factor scores and metadata, produces a concise "
        "natural-language explanation suitable for stakeholder presentations. "
        "Uses the configured LLM provider (LLM_PROVIDER env var). "
        "Returns a deterministic fallback when the LLM is unavailable. "
        "(AI Enhancement 2)"
    ),
)
async def explain_candidate(req: ExplainRequest, request: Request) -> ExplainResponse:
    correlation_id = getattr(request.state, "correlation_id", "N/A")
    model_name = "mock"

    # Determine model name from env
    import os
    provider_name = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider_name == "openai":
        model_name = os.getenv("LLM_MODEL", "gpt-4o-mini")
    elif provider_name == "bedrock":
        model_name = os.getenv("LLM_MODEL", "claude-3-haiku")
    else:
        model_name = "mock"

    user_prompt = _build_user_prompt(req)

    try:
        explanation = await llm.generate(
            system=EXPLAIN_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=300,
        )
        confidence = "high" if not isinstance(llm, MockProvider) else "mock"

        logger.info(
            "explanation generated",
            extra={
                "correlation_id": correlation_id,
                "city": req.city,
                "charger_type": req.chargerType,
                "rank": req.rank,
                "confidence": confidence,
                "model": model_name,
            },
        )

        return ExplainResponse(
            explanation=explanation,
            confidence=confidence,
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            model=model_name,
        )

    except Exception as exc:
        logger.warning(
            "LLM call failed — returning fallback explanation",
            extra={
                "correlation_id": correlation_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )

        return ExplainResponse(
            explanation=_build_fallback_explanation(req),
            confidence="fallback",
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            model=model_name,
        )
