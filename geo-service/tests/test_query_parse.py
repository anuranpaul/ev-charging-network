"""
Tests for POST /query/parse — AI Enhancement 4.

Uses the mock LLM provider which returns the user prompt as-is (not valid
JSON), triggering the fallback clarification response. Also tests the
response parsing logic with pre-built JSON.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.query_parse import _parse_llm_response, QueryParseResponse

client = TestClient(app)


# ---------------------------------------------------------------------------
# Tests: endpoint behaviour with mock provider
# ---------------------------------------------------------------------------


class TestQueryParseEndpoint:
    def test_valid_request_returns_200(self):
        resp = client.post("/query/parse", json={
            "query": "Find DC fast charger spots in Bengaluru near tech parks",
            "locale": "en",
        })
        assert resp.status_code == 200

    def test_response_has_required_fields(self):
        resp = client.post("/query/parse", json={
            "query": "slow chargers in Mumbai",
        })
        data = resp.json()

        assert "parsed" in data
        assert "confidence" in data
        assert "clarification_needed" in data

    def test_empty_query_returns_422(self):
        resp = client.post("/query/parse", json={"query": ""})
        assert resp.status_code == 422

    def test_mock_provider_triggers_clarification(self):
        """Mock provider returns non-JSON, so parser falls back to clarification."""
        resp = client.post("/query/parse", json={
            "query": "best charger spots",
        })
        data = resp.json()
        # Mock provider output won't be valid JSON, so parser should ask for clarification
        assert data["clarification_needed"] is True

    def test_conversation_history_accepted(self):
        resp = client.post("/query/parse", json={
            "query": "The one in south Mumbai",
            "conversation": [
                {"role": "user", "content": "Show me slow chargers"},
                {"role": "assistant", "content": "Which city?"},
            ],
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: response parsing logic
# ---------------------------------------------------------------------------


class TestParseLogic:
    def test_valid_json_is_parsed(self):
        raw = """{
            "city": "Bengaluru",
            "chargerType": "DC_FAST",
            "radius": 2000,
            "spatial_filters": [
                {"type": "near_layer", "layer": "tech_parks", "max_distance_m": 500}
            ],
            "sort_preference": "score_desc",
            "limit": null,
            "clarification_needed": false,
            "clarification_prompt": null,
            "raw_interpretation": "DC fast chargers near tech parks in Bengaluru"
        }"""

        result = _parse_llm_response(raw)

        assert result.parsed.city == "Bengaluru"
        assert result.parsed.chargerType == "DC_FAST"
        assert result.parsed.radius == 2000
        assert len(result.parsed.spatial_filters) == 1
        assert result.parsed.spatial_filters[0].layer == "tech_parks"
        assert result.clarification_needed is False
        assert result.confidence > 0.5

    def test_json_in_code_fence_is_parsed(self):
        raw = """```json
        {"city": "Mumbai", "chargerType": "SLOW", "radius": 1500,
         "spatial_filters": [], "clarification_needed": false,
         "raw_interpretation": "Slow chargers in Mumbai"}
        ```"""

        result = _parse_llm_response(raw)
        assert result.parsed.city == "Mumbai"

    def test_invalid_json_triggers_clarification(self):
        result = _parse_llm_response("this is not json at all")
        assert result.clarification_needed is True
        assert result.confidence == 0.0

    def test_radius_is_clamped(self):
        raw = '{"city": "Pune", "radius": 50000, "clarification_needed": false, "raw_interpretation": "test"}'
        result = _parse_llm_response(raw)
        assert result.parsed.radius == 10000

    def test_unknown_city_triggers_clarification(self):
        raw = '{"city": "Delhi", "chargerType": "FAST", "radius": 1500, "clarification_needed": false, "raw_interpretation": "test"}'
        result = _parse_llm_response(raw)
        # Delhi is not in SUPPORTED_CITIES
        assert result.clarification_needed is True
