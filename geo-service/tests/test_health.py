"""Tests for the GET /health endpoint (Requirement 9 AC-6)."""


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "geo-service"
