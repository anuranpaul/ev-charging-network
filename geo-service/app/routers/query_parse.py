"""
POST /query/parse router — Natural Language Query Interface.

Design reference: design.md §AI Enhancement 4

Accepts a natural language query string, calls the configured LLM to
extract structured parameters (city, chargerType, radius, spatial_filters),
validates them against the city registry, and returns a ParsedQuery response.

Supports multi-turn clarification via a conversation history field.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.llm_provider import llm
from app.models.schemas import ChargerType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ConversationTurn(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str


class QueryParseRequest(BaseModel):
    """POST /query/parse request body."""

    query: str = Field(..., min_length=1, max_length=500)
    locale: str = Field(default="en", description="'en' or 'hi'")
    conversation: list[ConversationTurn] = Field(
        default_factory=list,
        description="Prior conversation turns for multi-turn clarification (max 3)",
    )


class SpatialFilter(BaseModel):
    """A spatial constraint extracted from the NL query."""

    type: str = Field(..., description="near_layer | near_road | in_area")
    layer: str | None = None
    road_name: str | None = None
    area_description: str | None = None
    max_distance_m: int | None = None


class ParsedQuery(BaseModel):
    """Structured parameters extracted from the NL query."""

    city: str | None = None
    chargerType: str | None = None
    radius: int = 1500
    spatial_filters: list[SpatialFilter] = Field(default_factory=list)
    sort_preference: str | None = "score_desc"
    limit: int | None = None


class QueryParseResponse(BaseModel):
    """POST /query/parse response."""

    parsed: ParsedQuery
    confidence: float = Field(..., ge=0.0, le=1.0)
    clarification_needed: bool = False
    clarification_prompt: str | None = None
    raw_interpretation: str | None = None


# ---------------------------------------------------------------------------
# Supported values (for prompt context and validation)
# ---------------------------------------------------------------------------

SUPPORTED_CITIES = ["Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"]
SUPPORTED_LAYERS = [
    "ev_chargers", "fuel_stations", "roads",
    "parking", "metro_stations", "malls", "tech_parks",
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

NL_PARSE_SYSTEM_PROMPT = """You are a query parser for an EV charging station placement tool.
Extract structured parameters from the user's natural language query.

Available cities: {cities}
Available charger types: SLOW, FAST, DC_FAST
Radius range: 250–10000 metres (default 1500 if not specified)
Available layers for spatial filters: {layers}

Output ONLY a valid JSON object with these fields (no markdown, no explanation):
{{
  "city": string or null,
  "chargerType": "SLOW" | "FAST" | "DC_FAST" | null,
  "radius": integer,
  "spatial_filters": [
    {{"type": "near_layer", "layer": string, "max_distance_m": integer}},
    {{"type": "near_road", "road_name": string, "max_distance_m": integer}},
    {{"type": "in_area", "area_description": string}}
  ],
  "sort_preference": "score_desc" | "distance_asc" | null,
  "limit": integer or null,
  "clarification_needed": boolean,
  "clarification_prompt": string or null,
  "raw_interpretation": string
}}

If any required field cannot be confidently extracted, set it to null and
set clarification_needed to true with an appropriate prompt.
Only output the JSON object. No other text."""


def _build_system_prompt() -> str:
    return NL_PARSE_SYSTEM_PROMPT.format(
        cities=", ".join(SUPPORTED_CITIES),
        layers=", ".join(SUPPORTED_LAYERS),
    )


def _build_user_prompt(req: QueryParseRequest) -> str:
    """Build the user prompt including conversation history."""
    parts: list[str] = []

    if req.conversation:
        parts.append("Previous conversation:")
        for turn in req.conversation[-3:]:  # max 3 turns
            parts.append(f"  {turn.role}: {turn.content}")
        parts.append("")

    parts.append(f"<user_query>{req.query}</user_query>")

    if req.locale != "en":
        parts.append(f"(User's locale: {req.locale})")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_llm_response(raw: str) -> QueryParseResponse:
    """
    Parse the LLM's JSON output into a validated QueryParseResponse.

    Falls back to a clarification response if parsing fails.
    """
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return QueryParseResponse(
            parsed=ParsedQuery(),
            confidence=0.0,
            clarification_needed=True,
            clarification_prompt="I couldn't understand your query. Could you rephrase it?",
            raw_interpretation=None,
        )

    # Build spatial filters
    spatial_filters: list[SpatialFilter] = []
    for sf in data.get("spatial_filters", []):
        if isinstance(sf, dict):
            spatial_filters.append(SpatialFilter(
                type=sf.get("type", "near_layer"),
                layer=sf.get("layer"),
                road_name=sf.get("road_name"),
                area_description=sf.get("area_description"),
                max_distance_m=sf.get("max_distance_m"),
            ))

    parsed = ParsedQuery(
        city=data.get("city"),
        chargerType=data.get("chargerType"),
        radius=data.get("radius", 1500),
        spatial_filters=spatial_filters,
        sort_preference=data.get("sort_preference", "score_desc"),
        limit=data.get("limit"),
    )

    # Validate city if provided
    city_valid = parsed.city is None or parsed.city in SUPPORTED_CITIES
    clarification_needed = data.get("clarification_needed", False)

    if not city_valid:
        clarification_needed = True

    # Clamp radius to valid range
    if parsed.radius < 250:
        parsed.radius = 250
    elif parsed.radius > 10000:
        parsed.radius = 10000

    return QueryParseResponse(
        parsed=parsed,
        confidence=0.85 if not clarification_needed else 0.3,
        clarification_needed=clarification_needed,
        clarification_prompt=data.get("clarification_prompt"),
        raw_interpretation=data.get("raw_interpretation"),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/query/parse",
    response_model=QueryParseResponse,
    summary="Parse a natural language query into structured parameters",
    description=(
        "Accepts a free-text query describing where to place EV chargers, "
        "extracts structured parameters (city, chargerType, radius, spatial "
        "filters), and validates them. Supports English and Hindi. "
        "Returns clarification_needed=true when the query is ambiguous. "
        "(AI Enhancement 4)"
    ),
)
async def parse_query(req: QueryParseRequest, request: Request) -> QueryParseResponse:
    correlation_id = getattr(request.state, "correlation_id", "N/A")

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(req)

    try:
        raw_response = await llm.generate(
            system=system_prompt,
            user=user_prompt,
            max_tokens=300,
        )

        logger.info(
            "NL query parsed",
            extra={
                "correlation_id": correlation_id,
                "query_length": len(req.query),
                "locale": req.locale,
                "conversation_turns": len(req.conversation),
            },
        )

        return _parse_llm_response(raw_response)

    except Exception as exc:
        logger.warning(
            "NL query parse failed — LLM error",
            extra={
                "correlation_id": correlation_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )

        return QueryParseResponse(
            parsed=ParsedQuery(),
            confidence=0.0,
            clarification_needed=True,
            clarification_prompt=(
                "I'm having trouble processing your query right now. "
                "Please use the form fields above instead."
            ),
            raw_interpretation=None,
        )
