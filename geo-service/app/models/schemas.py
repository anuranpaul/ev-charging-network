"""
Pydantic schemas for the ChargeWise India Geo Service.

All models align with the locked API contract defined in the requirements.
Request/response shapes are kept separate so they can evolve independently.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Domain enums
# ---------------------------------------------------------------------------


class ChargerType(str, Enum):
    SLOW = "SLOW"
    FAST = "FAST"
    DC_FAST = "DC_FAST"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RecommendationRequest(BaseModel):
    """
    POST /recommendation request body.

    Mirrors the contract in Requirement 4 AC-1:
    - city: non-empty string
    - chargerType: one of SLOW | FAST | DC_FAST
    - radius: positive integer, 250–10 000 m inclusive
    """

    city: str = Field(..., min_length=1, description="Supported Indian city name")
    charger_type: ChargerType = Field(
        ..., alias="chargerType", description="EV charger category"
    )
    radius: int = Field(
        ...,
        ge=250,
        le=10_000,
        description="Search radius in metres (250–10 000 inclusive)",
    )

    model_config = {"populate_by_name": True}

    @field_validator("city")
    @classmethod
    def city_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("city must not be blank")
        return v.strip()


# ---------------------------------------------------------------------------
# GeoJSON building blocks
# ---------------------------------------------------------------------------


class PointGeometry(BaseModel):
    """GeoJSON Point geometry."""

    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(
        ...,
        min_length=2,
        max_length=3,
        description="[longitude, latitude] in WGS-84",
    )


# ---------------------------------------------------------------------------
# Factor scores — five components that sum to the final weighted score
# ---------------------------------------------------------------------------


class FactorScores(BaseModel):
    """
    Individual 0–100 factor scores for a candidate location.

    Weights (Requirement 5 AC-1):
      population     35 %
      charger_distance 25 %
      road_proximity  15 %
      parking         15 %
      mall_proximity  10 %
    """

    population: int = Field(..., ge=0, le=100)
    charger_distance: int = Field(..., ge=0, le=100)
    road_proximity: int = Field(..., ge=0, le=100)
    parking: int = Field(..., ge=0, le=100)
    mall_proximity: int = Field(..., ge=0, le=100)


# ---------------------------------------------------------------------------
# Candidate location result
# ---------------------------------------------------------------------------


class CandidateProperties(BaseModel):
    """
    GeoJSON feature properties for a scored candidate location.

    Fields align with Requirement 5 AC-6 and Requirement 6 AC-3.
    """

    rank: int = Field(..., ge=1, description="1-based rank by descending score")
    score: int = Field(..., ge=0, le=100, description="Final weighted score (0–100)")
    factor_scores: FactorScores
    population_1km: int = Field(
        ..., ge=0, description="Estimated population within 1 km"
    )
    nearest_charger_distance_m: float | None = Field(
        None,
        description=(
            "Distance to the nearest existing charger in metres; "
            "null if none within Search_Radius"
        ),
    )
    road_type: str = Field(
        ..., description="OSM highway classification of the nearest arterial road"
    )
    parking_available: bool = Field(
        ..., description="True when a parking polygon intersects the candidate point"
    )
    nearest_mall_distance_m: float | None = Field(
        None, description="Distance to the nearest shopping mall in metres"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Factor names that fell back to 0 due to missing/empty spatial layers "
            "(Requirement 5 AC-8)"
        ),
    )


class CandidateFeature(BaseModel):
    """A single GeoJSON Feature wrapping a scored candidate."""

    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: CandidateProperties


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------


class RecommendationResponse(BaseModel):
    """
    POST /recommendation response body (Requirement 4 AC-2).

    Returns a GeoJSON FeatureCollection of ranked candidates, plus
    metadata consumed by the API Server for caching and the Frontend
    for rendering.
    """

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[CandidateFeature] = Field(
        ..., description="Candidates ordered by descending score (rank 1 = best)"
    )
    city: str
    charger_type: ChargerType = Field(..., alias="chargerType")
    radius: int
    total_candidates: int = Field(
        ..., description="Total number of candidates evaluated before ranking"
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Health / data-health models
# ---------------------------------------------------------------------------


class DatasetHealth(BaseModel):
    """Per-dataset health record returned by GET /data-health (Req 3 AC-4)."""

    record_count: int
    last_loaded_at: str = Field(..., description="ISO-8601 timestamp of last load")
    status: Literal["ok", "partial", "missing"] = "ok"


class DataHealthResponse(BaseModel):
    """
    GET /data-health response (Requirement 3 AC-4 & AC-6).

    Keys are dataset names; values carry record count + last-load timestamp.
    A city is flagged 'partial' when some of its required datasets are absent.
    """

    datasets: dict[str, DatasetHealth]
    city_availability: dict[str, Literal["available", "partial", "unavailable"]]


# ---------------------------------------------------------------------------
# Validation endpoint models (Requirement 10 AC-4 & AC-5)
# ---------------------------------------------------------------------------


class ValidationError(BaseModel):
    feature_index: int = Field(..., ge=0, description="Zero-based index of the invalid feature")
    message: str


class ValidateResponse(BaseModel):
    record_count: int
    crs: str = Field(..., description="EPSG authority string, e.g. 'EPSG:4326'")
    geometry_types: list[str]
    validation_errors: list[ValidationError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Generic error envelope
# ---------------------------------------------------------------------------


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    """Standard error envelope used across all 4xx/5xx responses."""

    errors: list[FieldError] = Field(default_factory=list)
    message: str | None = None


# ---------------------------------------------------------------------------
# Anomaly detection models (AI Enhancement 1)
# ---------------------------------------------------------------------------


class AnomalySeverity(str, Enum):
    """Severity levels for data quality findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AnomalyFinding(BaseModel):
    """A single data quality issue detected during anomaly scanning."""

    rule_id: str = Field(..., description="Detection rule identifier, e.g. DUPLICATE_CLUSTER")
    layer: str = Field(..., description="Layer name where the anomaly was found")
    city: str
    severity: AnomalySeverity
    message: str = Field(..., description="Human-readable description of the finding")
    affected_features: list[int] = Field(
        default_factory=list,
        description="Zero-based feature indices in the source GeoJSON",
    )
    geometry: dict[str, Any] | None = Field(
        None, description="Optional GeoJSON geometry for map visualisation"
    )


class AnomalyReport(BaseModel):
    """Consolidated anomaly scan results for a city's datasets."""

    scanned_at: str = Field(..., description="ISO-8601 timestamp of the scan")
    total_findings: int
    findings: list[AnomalyFinding] = Field(default_factory=list)
    layers_scanned: int
    scan_duration_ms: float


class DataHealthWithAnomalies(BaseModel):
    """
    Extended GET /data-health response that includes anomaly scan results.

    Returned when ?anomalies=true is passed, or after startup scan completes.
    """

    datasets: dict[str, DatasetHealth]
    city_availability: dict[str, Literal["available", "partial", "unavailable"]]
    anomalies: dict[str, AnomalyReport] = Field(
        default_factory=dict,
        description="Per-city anomaly reports, keyed by city name (lowercase)",
    )
