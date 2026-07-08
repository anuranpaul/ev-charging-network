"""
Tests for POST /recommendation (Requirement 4).

Fixture strategy
----------------
``real_client`` — backed by the real geo-service/data directory (Bengaluru
    data present).  Used for all tests that exercise the pipeline end-to-end.

``client``      — backed by an empty DATA_DIR.  Used for validation, error-
    path, and correlation-ID tests that do not invoke the scoring pipeline.

Covers:
- Happy path: valid request returns 200 + valid FeatureCollection
- Schema contract: every required field is present and typed correctly
- Validation: missing fields → 422 (Pydantic), unsupported city → 422
- Radius bounds: below min and above max → 422
- Unsupported chargerType → 422
- Correlation ID propagation (Requirement 11 AC-5)
"""

import pytest

ENDPOINT = "/recommendation"

VALID_PAYLOAD = {
    "city": "Bengaluru",
    "chargerType": "DC_FAST",
    "radius": 1000,
}


# ---------------------------------------------------------------------------
# Happy path  (real_client — Bengaluru data present)
# ---------------------------------------------------------------------------


def test_valid_request_returns_200(real_client):
    response = real_client.post(ENDPOINT, json=VALID_PAYLOAD)
    assert response.status_code == 200


def test_response_is_geojson_feature_collection(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)
    assert len(body["features"]) > 0


def test_response_metadata_echoes_request(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    assert body["city"] == VALID_PAYLOAD["city"]
    assert body["chargerType"] == VALID_PAYLOAD["chargerType"]
    assert body["radius"] == VALID_PAYLOAD["radius"]
    assert isinstance(body["total_candidates"], int)
    assert body["total_candidates"] == len(body["features"])


def test_feature_schema_is_correct(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    feature = body["features"][0]

    # GeoJSON structure
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    coords = feature["geometry"]["coordinates"]
    assert len(coords) == 2
    assert isinstance(coords[0], float)  # longitude
    assert isinstance(coords[1], float)  # latitude

    # Properties required by Requirement 5 AC-6 and Requirement 6 AC-3
    props = feature["properties"]
    assert isinstance(props["rank"], int)
    assert 0 <= props["score"] <= 100
    assert isinstance(props["population_1km"], int)
    assert isinstance(props["road_type"], str)
    assert isinstance(props["parking_available"], bool)
    assert isinstance(props["warnings"], list)

    # nearest_charger_distance_m may be float or None (Req 5 AC-6)
    ncd = props["nearest_charger_distance_m"]
    assert ncd is None or isinstance(ncd, (int, float))


def test_factor_scores_present_and_bounded(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    for feature in body["features"]:
        fs = feature["properties"]["factor_scores"]
        for key in ("population", "charger_distance", "road_proximity",
                    "parking", "mall_proximity"):
            assert key in fs, f"factor_scores missing key: {key}"
            assert 0 <= fs[key] <= 100, f"factor {key} out of [0,100]: {fs[key]}"


def test_candidates_ordered_by_descending_score(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    scores = [f["properties"]["score"] for f in body["features"]]
    assert scores == sorted(scores, reverse=True), (
        "candidates must be sorted by descending score"
    )


def test_ranks_are_sequential_from_one(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    ranks = [f["properties"]["rank"] for f in body["features"]]
    assert ranks == list(range(1, len(ranks) + 1)), "ranks must be 1, 2, 3, ..."


def test_coordinates_are_wgs84(real_client):
    """All returned coordinates must look like WGS-84 degrees, not UTM metres."""
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    for feature in body["features"]:
        lng, lat = feature["geometry"]["coordinates"]
        # WGS-84 Bengaluru area: roughly lng 77–78, lat 12–14
        assert 70.0 <= lng <= 85.0, f"longitude {lng} looks like UTM, not WGS-84"
        assert 8.0 <= lat <= 20.0, f"latitude {lat} looks like UTM, not WGS-84"


def test_total_candidates_matches_feature_count(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    assert body["total_candidates"] == len(body["features"])


def test_warnings_is_list_of_strings(real_client):
    body = real_client.post(ENDPOINT, json=VALID_PAYLOAD).json()
    for feature in body["features"]:
        warnings = feature["properties"]["warnings"]
        assert isinstance(warnings, list)
        for w in warnings:
            assert isinstance(w, str)


def test_radius_boundary_values_accepted(real_client):
    for radius in (250, 10_000):
        payload = {**VALID_PAYLOAD, "radius": radius}
        response = real_client.post(ENDPOINT, json=payload)
        assert response.status_code == 200, (
            f"Expected 200 for radius={radius}, got {response.status_code}"
        )


def test_bengaluru_returns_200(real_client):
    """Bengaluru has real data and must always return 200."""
    response = real_client.post(ENDPOINT, json=VALID_PAYLOAD)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Correlation ID — Requirement 11 AC-5
# (Uses real_client so the full pipeline runs and the header propagates.)
# ---------------------------------------------------------------------------


def test_response_echoes_correlation_id(real_client):
    cid = "test-corr-id-abc123"
    response = real_client.post(
        ENDPOINT,
        json=VALID_PAYLOAD,
        headers={"X-Correlation-ID": cid},
    )
    assert response.headers.get("X-Correlation-ID") == cid


def test_response_generates_correlation_id_when_absent(real_client):
    response = real_client.post(ENDPOINT, json=VALID_PAYLOAD)
    assert "X-Correlation-ID" in response.headers
    assert len(response.headers["X-Correlation-ID"]) > 0


# ---------------------------------------------------------------------------
# Validation — Requirement 4 AC-3: 400 / 422 for bad inputs
# (Uses ``client`` — no real data needed, Pydantic catches these before
#  the pipeline is invoked.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload,description", [
    ({}, "completely empty body"),
    ({"chargerType": "DC_FAST", "radius": 1000}, "missing city"),
    ({"city": "Bengaluru", "radius": 1000}, "missing chargerType"),
    ({"city": "Bengaluru", "chargerType": "DC_FAST"}, "missing radius"),
])
def test_missing_required_fields_returns_422(client, payload, description):
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 422, f"expected 422 for: {description}"


@pytest.mark.parametrize("charger_type", ["ULTRA", "slow", "fast", "dc_fast", ""])
def test_invalid_charger_type_returns_422(client, charger_type):
    payload = {**VALID_PAYLOAD, "chargerType": charger_type}
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("radius", [0, -1, 249, 10_001, 99_999])
def test_radius_out_of_bounds_returns_422(client, radius):
    payload = {**VALID_PAYLOAD, "radius": radius}
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 422


def test_blank_city_returns_422(client):
    payload = {**VALID_PAYLOAD, "city": "   "}
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Unsupported city — Requirement 4 AC-4: 422 + supported city list
# ---------------------------------------------------------------------------


def test_unsupported_city_returns_422(client):
    payload = {**VALID_PAYLOAD, "city": "Tokyo"}
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 422


def test_unsupported_city_response_contains_supported_list(client):
    payload = {**VALID_PAYLOAD, "city": "Tokyo"}
    detail = client.post(ENDPOINT, json=payload).json()["detail"]
    assert "supported_cities" in detail
    supported = detail["supported_cities"]
    assert "Bengaluru" in supported
