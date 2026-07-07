"""
tests/test_dataset_loader.py

Unit tests for app/core/dataset_loader.py.

Coverage:
- Happy path: 9 files present → all GeoDataFrames projected to EPSG:32643,
  missing_layers empty.
- Partial: some files absent → absent layers in missing_layers, present
  layers still EPSG:32643.
- All missing: all files absent → all layer names in missing_layers, health()
  city_availability = "unavailable".
- Registry cache: second load() returns the same object (no re-read).
- health() structure: correct DataHealthResponse keys and types.
- Empty GeoDataFrame CRS: even a missing layer's placeholder has crs == 32643.
- Property 2 (design.md §Correctness): .crs.to_epsg() == 32643 on every
  loaded layer, regardless of geometry type.
- Property 1 (design.md §Correctness): GeoJSON round-trip preserves record
  count, geometry types, and coordinates within 1e-7 degrees.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LinearRing, LineString, Point, Polygon

# Env vars must be set before the app module is imported (conftest.py does
# this for the session, but we override DATA_DIR per test below).
os.environ.setdefault("DATA_DIR", "/tmp/test-data-loader")
os.environ.setdefault("DEFAULT_CRS_EPSG", "32643")

from app.core.dataset_loader import (  # noqa: E402
    CityDatasets,
    DatasetRegistry,
    TARGET_EPSG,
    _LAYER_NAMES,
    _empty_gdf,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WGS84_POINT = Point(77.5946, 12.9716)   # central Bengaluru, in WGS-84


def _make_geojson_file(path: Path) -> None:
    """Write a minimal valid GeoJSON FeatureCollection (1 Point, WGS-84)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [_WGS84_POINT.x, _WGS84_POINT.y],
                },
                "properties": {"name": "test"},
            }
        ],
    }
    path.write_text(json.dumps(fc))


def _all_layer_files(city_dir: Path) -> None:
    """Write a stub GeoJSON file for every expected layer."""
    from app.core.dataset_loader import _LAYERS
    for _, filename in _LAYERS:
        _make_geojson_file(city_dir / filename)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point DATA_DIR at a fresh temp directory and return it."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def fresh_registry() -> DatasetRegistry:
    """Return a new DatasetRegistry with no cached state."""
    return DatasetRegistry()


# ---------------------------------------------------------------------------
# Tests: happy path (all 9 files present)
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_all_layers_loaded(self, tmp_data_dir: Path, fresh_registry: DatasetRegistry) -> None:
        """All 9 files present → missing_layers is empty."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds = fresh_registry.load("bengaluru")

        assert isinstance(ds, CityDatasets)
        assert ds.missing_layers == [], f"Unexpected missing layers: {ds.missing_layers}"

    def test_all_gdfs_projected_to_32643(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """Every GeoDataFrame must be EPSG:32643 after load() returns."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds = fresh_registry.load("bengaluru")

        for layer_name in _LAYER_NAMES:
            gdf: gpd.GeoDataFrame = getattr(ds, layer_name)
            assert gdf.crs is not None, f"{layer_name} has no CRS"
            assert gdf.crs.to_epsg() == TARGET_EPSG, (
                f"{layer_name} CRS is {gdf.crs.to_epsg()!r}, expected {TARGET_EPSG}"
            )

    def test_record_counts_non_zero(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """Each loaded GeoDataFrame should have at least 1 row."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds = fresh_registry.load("bengaluru")

        for layer_name in _LAYER_NAMES:
            gdf = getattr(ds, layer_name)
            assert len(gdf) >= 1, f"{layer_name} is unexpectedly empty"

    def test_coordinates_are_in_utm_range(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """After reprojection the point coordinates must not look like WGS-84 degrees."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds = fresh_registry.load("bengaluru")
        gdf = ds.ev_chargers
        assert len(gdf) > 0
        # UTM easting for Bengaluru is ~700 000 m (not ~77 degrees)
        x = gdf.geometry.iloc[0].x
        assert x > 1000, f"Point x={x!r} looks like WGS-84 degrees, not UTM metres"


# ---------------------------------------------------------------------------
# Tests: partial datasets (some files missing)
# ---------------------------------------------------------------------------

class TestPartialDatasets:
    def test_missing_file_added_to_missing_layers(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """A missing file must appear in missing_layers, not raise."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        (city_dir / "ev_chargers.geojson").unlink()

        ds = fresh_registry.load("bengaluru")

        assert "ev_chargers" in ds.missing_layers

    def test_present_layers_still_loaded(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """Layers whose files exist must be loaded correctly despite others missing."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        (city_dir / "population_grid.geojson").unlink()
        (city_dir / "parking.geojson").unlink()

        ds = fresh_registry.load("bengaluru")

        assert "population_grid" in ds.missing_layers
        assert "parking" in ds.missing_layers
        # A present layer must still be EPSG:32643 and non-empty
        assert ds.ev_chargers.crs is not None
        assert ds.ev_chargers.crs.to_epsg() == TARGET_EPSG
        assert len(ds.ev_chargers) >= 1

    def test_missing_layer_gdf_is_empty_with_correct_crs(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """The placeholder GeoDataFrame for a missing layer must use EPSG:32643."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        (city_dir / "malls.geojson").unlink()

        ds = fresh_registry.load("bengaluru")

        assert len(ds.malls) == 0
        assert ds.malls.crs is not None
        assert ds.malls.crs.to_epsg() == TARGET_EPSG

    def test_parse_error_treated_as_missing(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """A corrupt GeoJSON file must add the layer to missing_layers without raising."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        (city_dir / "roads.geojson").write_text("this is not valid geojson !!!")

        ds = fresh_registry.load("bengaluru")

        assert "roads" in ds.missing_layers
        assert len(ds.roads) == 0


# ---------------------------------------------------------------------------
# Tests: all datasets absent (city directory empty / missing)
# ---------------------------------------------------------------------------

class TestAllMissing:
    def test_all_layers_missing_no_exception(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """Load on a city directory that has no files must not raise."""
        # city_dir deliberately NOT created
        ds = fresh_registry.load("mumbai")

        assert isinstance(ds, CityDatasets)
        assert set(ds.missing_layers) == set(_LAYER_NAMES)

    def test_health_city_availability_unavailable(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """A city with every layer missing must appear as 'unavailable' in health()."""
        fresh_registry.load("mumbai")
        resp = fresh_registry.health()

        assert resp.city_availability.get("Mumbai") == "unavailable"


# ---------------------------------------------------------------------------
# Tests: registry caching
# ---------------------------------------------------------------------------

class TestRegistryCache:
    def test_second_load_returns_same_object(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """load() must return the exact same CityDatasets object on the second call."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds1 = fresh_registry.load("bengaluru")
        ds2 = fresh_registry.load("bengaluru")

        assert ds1 is ds2, "Second load() should return the cached object"

    def test_city_name_is_normalised_to_lowercase(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """'Bengaluru', 'BENGALURU', and 'bengaluru' must all hit the same cache entry."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds_lower = fresh_registry.load("bengaluru")
        ds_title = fresh_registry.load("Bengaluru")
        ds_upper = fresh_registry.load("BENGALURU")

        assert ds_lower is ds_title
        assert ds_lower is ds_upper


# ---------------------------------------------------------------------------
# Tests: health() output
# ---------------------------------------------------------------------------

class TestHealthMethod:
    def test_health_returns_data_health_response(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        from app.models.schemas import DataHealthResponse
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        assert isinstance(resp, DataHealthResponse)

    def test_health_has_all_9_dataset_keys(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        assert len(resp.datasets) == 9
        expected_keys = {f"bengaluru/{name}" for name in _LAYER_NAMES}
        assert set(resp.datasets.keys()) == expected_keys

    def test_health_ok_layers_have_positive_record_count(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        for key, dh in resp.datasets.items():
            assert dh.status == "ok", f"{key} status is {dh.status!r}"
            assert dh.record_count >= 1, f"{key} record_count={dh.record_count}"

    def test_health_missing_layer_status(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        (city_dir / "tech_parks.geojson").unlink()
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        assert resp.datasets["bengaluru/tech_parks"].status == "missing"
        assert resp.datasets["bengaluru/tech_parks"].record_count == 0

    def test_health_city_availability_available(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        assert resp.city_availability.get("Bengaluru") == "available"

    def test_health_city_availability_partial(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        (city_dir / "parking.geojson").unlink()
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        assert resp.city_availability.get("Bengaluru") == "partial"

    def test_health_last_loaded_at_is_iso_string(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        fresh_registry.load("bengaluru")

        resp = fresh_registry.health()

        for key, dh in resp.datasets.items():
            if dh.status == "ok":
                # Should parse as ISO-8601 without raising
                from datetime import datetime
                datetime.fromisoformat(dh.last_loaded_at)


# ---------------------------------------------------------------------------
# Tests: _empty_gdf helper
# ---------------------------------------------------------------------------

class TestEmptyGdf:
    def test_empty_gdf_has_target_crs(self) -> None:
        gdf = _empty_gdf()
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == TARGET_EPSG

    def test_empty_gdf_has_zero_rows(self) -> None:
        gdf = _empty_gdf()
        assert len(gdf) == 0


# ---------------------------------------------------------------------------
# Property 2 (design.md §Correctness): CRS assertion on every loaded layer
#
# "For any GeoJSON file read by load_city_datasets(), the resulting
#  GeoDataFrame's .crs.to_epsg() must equal 32643 before the function
#  returns control to the caller."
#
# We test:
#   - Point geometries (natural for ev_chargers, metro_stations, …)
#   - LineString geometries (natural for roads)
#   - Polygon geometries (natural for parking, ward_boundaries, …)
# ---------------------------------------------------------------------------


# Synthetic WGS-84 fixtures used across both Property test classes.

# A tight triangle inside Bengaluru — three Point coordinates.
_PT_COORDS: list[tuple[float, float]] = [
    (77.5946, 12.9716),   # Majestic area
    (77.6101, 12.9352),   # Koramangala
    (77.5800, 13.0012),   # Yeshwanthpur
]

# A simple non-self-intersecting polygon around the city centre (4 + 1 ring).
_POLY_EXTERIOR: list[tuple[float, float]] = [
    (77.55, 12.95),
    (77.65, 12.95),
    (77.65, 13.00),
    (77.55, 13.00),
    (77.55, 12.95),  # close the ring
]


def _write_mixed_geojson(path: Path) -> None:
    """
    Write a FeatureCollection with one Point, one LineString, and one Polygon
    to *path*.  All coordinates are in WGS-84 (EPSG:4326).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": list(_PT_COORDS[0]),
                },
                "properties": {"kind": "point", "idx": 0},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(c) for c in _PT_COORDS],
                },
                "properties": {"kind": "linestring", "idx": 1},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(c) for c in _POLY_EXTERIOR]],
                },
                "properties": {"kind": "polygon", "idx": 2},
            },
        ],
    }
    path.write_text(json.dumps(fc))


class TestProperty2CRS:
    """
    Design doc Property 2:
      For any GeoJSON file read by load_city_datasets(), the resulting
      GeoDataFrame's .crs.to_epsg() must equal 32643 before the function
      returns control to the caller.

    Validates: design.md §Correctness Property 2 / Requirements 3.2
    """

    def test_point_layer_projected_to_32643(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """Point geometries (e.g. ev_chargers) must land in EPSG:32643."""
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)
        ds = fresh_registry.load("bengaluru")

        gdf = ds.ev_chargers
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == TARGET_EPSG, (
            f"ev_chargers CRS={gdf.crs.to_epsg()!r}, want {TARGET_EPSG}"
        )

    def test_linestring_layer_projected_to_32643(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        LineString geometries (roads) must be in EPSG:32643.
        Overwrite the stub roads.geojson with an actual LineString so the
        geometry type is exercised end-to-end.
        """
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        # Replace the stub with a proper LineString feature.
        roads_path = city_dir / "roads.geojson"
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(c) for c in _PT_COORDS],
                    },
                    "properties": {"highway": "primary"},
                }
            ],
        }
        roads_path.write_text(json.dumps(fc))

        ds = fresh_registry.load("bengaluru")

        gdf = ds.roads
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == TARGET_EPSG, (
            f"roads CRS={gdf.crs.to_epsg()!r}, want {TARGET_EPSG}"
        )
        assert gdf.geometry.iloc[0].geom_type == "LineString"

    def test_polygon_layer_projected_to_32643(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        Polygon geometries (parking, ward_boundaries, …) must be in EPSG:32643.
        """
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        # Replace the stub with a proper Polygon feature.
        parking_path = city_dir / "parking.geojson"
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[list(c) for c in _POLY_EXTERIOR]],
                    },
                    "properties": {"amenity": "parking"},
                }
            ],
        }
        parking_path.write_text(json.dumps(fc))

        ds = fresh_registry.load("bengaluru")

        gdf = ds.parking
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == TARGET_EPSG, (
            f"parking CRS={gdf.crs.to_epsg()!r}, want {TARGET_EPSG}"
        )
        assert gdf.geometry.iloc[0].geom_type == "Polygon"

    def test_all_nine_layers_projected_to_32643(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        Exhaustive check: every one of the nine CityDatasets fields must carry
        EPSG:32643 after load() returns, for any present file.
        This is the direct assertion of Property 2.
        """
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        ds = fresh_registry.load("bengaluru")

        failures: list[str] = []
        for layer_name in _LAYER_NAMES:
            gdf: gpd.GeoDataFrame = getattr(ds, layer_name)
            if gdf.crs is None or gdf.crs.to_epsg() != TARGET_EPSG:
                actual = gdf.crs.to_epsg() if gdf.crs else None
                failures.append(f"{layer_name}: got {actual!r}")

        assert not failures, (
            f"The following layers are NOT EPSG:{TARGET_EPSG}:\n"
            + "\n".join(f"  {f}" for f in failures)
        )

    def test_no_crs_in_file_still_reprojects_correctly(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        A GeoJSON file that omits the 'crs' key (common in community datasets)
        must still be treated as WGS-84 and reprojected to EPSG:32643.
        """
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        # Overwrite ev_chargers.geojson with a CRS-less FeatureCollection.
        no_crs_path = city_dir / "ev_chargers.geojson"
        fc_no_crs = {
            "type": "FeatureCollection",
            # No "crs" key — geopandas may leave .crs as None
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": list(_PT_COORDS[0]),
                    },
                    "properties": {"amenity": "charging_station"},
                }
            ],
        }
        no_crs_path.write_text(json.dumps(fc_no_crs))

        ds = fresh_registry.load("bengaluru")

        gdf = ds.ev_chargers
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == TARGET_EPSG, (
            f"CRS-less file was not reprojected: got {gdf.crs.to_epsg()!r}"
        )


# ---------------------------------------------------------------------------
# Property 1 (design.md §Correctness): GeoJSON round-trip fidelity
#
# "For any valid GeoJSON FeatureCollection containing Point, LineString,
#  or Polygon geometries, parsing it into a GeoDataFrame, serialising that
#  GeoDataFrame back to GeoJSON, and parsing again must yield a GeoDataFrame
#  with the same record count, the same geometry types for each feature, and
#  coordinate values within 1×10⁻⁷ degrees of the originals for every vertex."
#
# Strategy:
#   1. Write a synthetic FeatureCollection to disk (WGS-84).
#   2. Load it via DatasetRegistry (→ EPSG:32643, internal CRS).
#   3. Re-project back to WGS-84 and serialise to a second file.
#   4. Parse the second file fresh.
#   5. Assert record_count, geometry_types, and per-vertex coordinates.
# ---------------------------------------------------------------------------

_COORD_TOLERANCE: float = 1e-7   # degrees, per design doc Property 1


def _collect_coords(geom) -> list[tuple[float, float]]:
    """Recursively collect all (x, y) vertex tuples from any Shapely geometry."""
    if geom.geom_type == "Point":
        return [(geom.x, geom.y)]
    if geom.geom_type in ("LineString", "LinearRing"):
        return list(geom.coords)
    if geom.geom_type == "Polygon":
        return list(geom.exterior.coords)
    if geom.geom_type.startswith("Multi") or geom.geom_type == "GeometryCollection":
        coords: list[tuple[float, float]] = []
        for part in geom.geoms:
            coords.extend(_collect_coords(part))
        return coords
    return []


class TestProperty1RoundTrip:
    """
    Design doc Property 1:
      For any valid GeoJSON FeatureCollection, parsing → GeoDataFrame →
      serialise → parse must preserve record count, geometry types, and
      coordinate values within 1e-7 degrees.

    Validates: design.md §Correctness Property 1 / Requirements 3.5, 10.3
    """

    def _run_roundtrip(
        self,
        source_fc: dict,
        tmp_data_dir: Path,
        fresh_registry: DatasetRegistry,
        layer_filename: str = "ev_chargers.geojson",
        layer_attr: str = "ev_chargers",
    ) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        """
        Write *source_fc* → load via registry (EPSG:32643) → serialise back to
        WGS-84 GeoJSON → reload fresh.

        Returns (original_gdf_in_4326, reloaded_gdf_in_4326).
        """
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        # Write our synthetic source over the stub
        source_path = city_dir / layer_filename
        source_path.write_text(json.dumps(source_fc))

        # Step 1: load through registry (projects to EPSG:32643)
        ds = fresh_registry.load("bengaluru")
        gdf_projected: gpd.GeoDataFrame = getattr(ds, layer_attr)

        # Step 2: reproject back to WGS-84 and serialise to disk
        gdf_wgs84: gpd.GeoDataFrame = gdf_projected.to_crs(epsg=4326)
        round_trip_path = tmp_data_dir / f"rt_{layer_filename}"
        gdf_wgs84.to_file(round_trip_path, driver="GeoJSON")

        # Step 3: reload the serialised file fresh (bypasses registry cache)
        reloaded: gpd.GeoDataFrame = gpd.read_file(round_trip_path)

        # For coordinate comparison return both in WGS-84
        return gdf_wgs84, reloaded

    def test_roundtrip_record_count_preserved(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """Record count must be identical after the full round-trip."""
        source_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(c)},
                    "properties": {"idx": i},
                }
                for i, c in enumerate(_PT_COORDS)
            ],
        }
        original, reloaded = self._run_roundtrip(source_fc, tmp_data_dir, fresh_registry)

        assert len(reloaded) == len(original), (
            f"Record count changed: {len(original)} → {len(reloaded)}"
        )

    def test_roundtrip_geometry_types_preserved(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        The set of geometry type strings must be identical after round-trip.
        Uses a mixed FeatureCollection (Point + LineString + Polygon).
        """
        city_dir = tmp_data_dir / "bengaluru"
        _all_layer_files(city_dir)

        mixed_path = city_dir / "ev_chargers.geojson"
        _write_mixed_geojson(mixed_path)

        ds = fresh_registry.load("bengaluru")
        gdf_projected = ds.ev_chargers
        gdf_wgs84 = gdf_projected.to_crs(epsg=4326)

        rt_path = tmp_data_dir / "rt_mixed.geojson"
        gdf_wgs84.to_file(rt_path, driver="GeoJSON")
        reloaded = gpd.read_file(rt_path)

        original_types = set(gdf_wgs84.geometry.geom_type.tolist())
        reloaded_types = set(reloaded.geometry.geom_type.tolist())

        assert original_types == reloaded_types, (
            f"Geometry types changed: {original_types} → {reloaded_types}"
        )

    def test_roundtrip_point_coordinates_within_tolerance(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        After round-trip, every Point vertex must match the original to within
        1e-7 degrees (Property 1 tolerance).
        """
        source_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(c)},
                    "properties": {"idx": i},
                }
                for i, c in enumerate(_PT_COORDS)
            ],
        }
        original, reloaded = self._run_roundtrip(source_fc, tmp_data_dir, fresh_registry)

        for row_idx, (orig_geom, rt_geom) in enumerate(
            zip(original.geometry, reloaded.geometry)
        ):
            orig_coords = _collect_coords(orig_geom)
            rt_coords = _collect_coords(rt_geom)
            assert len(orig_coords) == len(rt_coords), (
                f"Row {row_idx}: vertex count changed "
                f"{len(orig_coords)} → {len(rt_coords)}"
            )
            for v_idx, ((ox, oy), (rx, ry)) in enumerate(
                zip(orig_coords, rt_coords)
            ):
                assert abs(ox - rx) <= _COORD_TOLERANCE, (
                    f"Row {row_idx} vertex {v_idx}: x changed by "
                    f"{abs(ox-rx):.2e} (>{_COORD_TOLERANCE:.0e})"
                )
                assert abs(oy - ry) <= _COORD_TOLERANCE, (
                    f"Row {row_idx} vertex {v_idx}: y changed by "
                    f"{abs(oy-ry):.2e} (>{_COORD_TOLERANCE:.0e})"
                )

    def test_roundtrip_linestring_coordinates_within_tolerance(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        LineString vertices must survive the round-trip within 1e-7 degrees.
        """
        source_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(c) for c in _PT_COORDS],
                    },
                    "properties": {"highway": "primary"},
                }
            ],
        }
        original, reloaded = self._run_roundtrip(
            source_fc, tmp_data_dir, fresh_registry,
            layer_filename="roads.geojson",
            layer_attr="roads",
        )

        orig_coords = _collect_coords(original.geometry.iloc[0])
        rt_coords = _collect_coords(reloaded.geometry.iloc[0])

        assert len(orig_coords) == len(rt_coords)
        for v_idx, ((ox, oy), (rx, ry)) in enumerate(zip(orig_coords, rt_coords)):
            assert abs(ox - rx) <= _COORD_TOLERANCE, (
                f"LineString vertex {v_idx} x: Δ={abs(ox-rx):.2e}"
            )
            assert abs(oy - ry) <= _COORD_TOLERANCE, (
                f"LineString vertex {v_idx} y: Δ={abs(oy-ry):.2e}"
            )

    def test_roundtrip_polygon_coordinates_within_tolerance(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        Polygon ring vertices must survive the round-trip within 1e-7 degrees.
        """
        source_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[list(c) for c in _POLY_EXTERIOR]],
                    },
                    "properties": {"amenity": "parking"},
                }
            ],
        }
        original, reloaded = self._run_roundtrip(
            source_fc, tmp_data_dir, fresh_registry,
            layer_filename="parking.geojson",
            layer_attr="parking",
        )

        orig_coords = _collect_coords(original.geometry.iloc[0])
        rt_coords = _collect_coords(reloaded.geometry.iloc[0])

        # GeoJSON closes rings; the last coord may equal the first — lengths
        # should still match, but we compare the unique non-closing vertices.
        # Strip the repeated closing vertex from both sides if present.
        if orig_coords and orig_coords[0] == orig_coords[-1]:
            orig_coords = orig_coords[:-1]
        if rt_coords and rt_coords[0] == rt_coords[-1]:
            rt_coords = rt_coords[:-1]

        assert len(orig_coords) == len(rt_coords), (
            f"Polygon vertex count changed: {len(orig_coords)} → {len(rt_coords)}"
        )
        for v_idx, ((ox, oy), (rx, ry)) in enumerate(zip(orig_coords, rt_coords)):
            assert abs(ox - rx) <= _COORD_TOLERANCE, (
                f"Polygon vertex {v_idx} x: Δ={abs(ox-rx):.2e}"
            )
            assert abs(oy - ry) <= _COORD_TOLERANCE, (
                f"Polygon vertex {v_idx} y: Δ={abs(oy-ry):.2e}"
            )

    def test_roundtrip_properties_preserved(
        self, tmp_data_dir: Path, fresh_registry: DatasetRegistry
    ) -> None:
        """
        Non-geometry properties (tags/attributes) must survive the round-trip
        unchanged — confirms nothing is accidentally dropped during CRS conversion.
        """
        source_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(_PT_COORDS[0])},
                    "properties": {"amenity": "charging_station", "name": "Test Charger"},
                }
            ],
        }
        original, reloaded = self._run_roundtrip(source_fc, tmp_data_dir, fresh_registry)

        assert "amenity" in reloaded.columns
        assert reloaded["amenity"].iloc[0] == "charging_station"
        assert reloaded["name"].iloc[0] == "Test Charger"

