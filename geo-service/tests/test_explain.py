"""
Tests for POST /explain — AI Enhancement 2.

Verifies the explain endpoint works with the mock LLM provider and
produces valid responses with correct structure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

VALID_EXPLAIN_REQUEST = {
    "city": "Bengaluru",
    "chargerType": "DC_FAST",
    "rank": 3,
    "candidate": {
        "score": 82,
        "factor_scores": {
            "population": 68,
            "charger_distance": 92,
            "road_proximity": 100,
            "parking": 100,
            "mall_proximity": 0,
        },
        "population_1km": 34210,
        "nearest_charger_distance_m": 2340.0,
        "road_type": "trunk",
        "parking_available": True,
        "nearest_mall_distance_m": None,
        "coordinates": [77.6123, 12.9345],
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExplainEndpoint:
    def test_valid_request_returns_200(self):
        resp = client.post("/explain", json=VALID_EXPLAIN_REQUEST)
        assert resp.status_code == 200

    def test_response_has_expected_fields(self):
        resp = client.post("/explain", json=VALID_EXPLAIN_REQUEST)
        data = resp.json()

        assert "explanation" in data
        assert "confidence" in data
        assert "generated_at" in data
        assert "model" in data

    def test_mock_provider_returns_non_empty_explanation(self):
        resp = client.post("/explain", json=VALID_EXPLAIN_REQUEST)
        data = resp.json()

        assert len(data["explanation"]) > 0
        assert data["confidence"] == "mock"
        assert data["model"] == "mock"

    def test_explanation_references_score(self):
        resp = client.post("/explain", json=VALID_EXPLAIN_REQUEST)
        data = resp.json()

        # The mock provider should include factor information
        explanation = data["explanation"]
        assert "100" in explanation or "score" in explanation.lower()

    def test_missing_candidate_returns_422(self):
        incomplete = {"city": "Bengaluru", "chargerType": "DC_FAST", "rank": 1}
        resp = client.post("/explain", json=incomplete)
        assert resp.status_code == 422

    def test_invalid_score_returns_422(self):
        bad_request = dict(VALID_EXPLAIN_REQUEST)
        bad_request["candidate"] = dict(VALID_EXPLAIN_REQUEST["candidate"])
        bad_request["candidate"]["score"] = 150  # out of range
        resp = client.post("/explain", json=bad_request)
        assert resp.status_code == 422
