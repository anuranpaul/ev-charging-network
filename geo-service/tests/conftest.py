"""
Shared pytest fixtures for the geo-service test suite.

Two client fixtures are provided:

``client`` (session-scoped)
    Points DATA_DIR at an empty temp directory.  Used for validation,
    error-path, and correlation-ID tests that do not need real spatial data.

``real_client`` (session-scoped)
    Points DATA_DIR at the real ``data/`` directory (Bengaluru data present).
    Used for happy-path pipeline tests that exercise the full scoring stack.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Set required env vars before importing the app so the startup check passes.
os.environ.setdefault("DATA_DIR", "/tmp/test-data")
os.environ.setdefault("DEFAULT_CRS_EPSG", "32643")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # quieter logs during tests

# Absolute path to the real data directory (Bengaluru data is present here).
_REAL_DATA_DIR = str(
    Path(__file__).parent.parent / "data"
)


@pytest.fixture(scope="session")
def client() -> TestClient:
    """TestClient backed by an empty DATA_DIR (no real spatial files)."""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def real_client(tmp_path_factory) -> TestClient:
    """
    TestClient backed by the real geo-service/data directory.

    Uses a fresh DatasetRegistry so the real_client session is isolated from
    the empty-data ``client`` session above.  Monkeypatches the module-level
    ``registry`` singleton in dataset_loader so the router picks it up.
    """
    import importlib

    # Point DATA_DIR at the real data for the duration of this fixture.
    old_data_dir = os.environ.get("DATA_DIR", "")
    os.environ["DATA_DIR"] = _REAL_DATA_DIR

    # Re-import the registry with the new DATA_DIR so cached state is fresh.
    from app.core import dataset_loader as dl
    fresh_registry = dl.DatasetRegistry()
    old_registry = dl.registry
    dl.registry = fresh_registry

    # Also patch the router's reference (it imports registry at module load).
    import app.routers.recommendation as rec_mod
    old_rec_registry = rec_mod.registry
    rec_mod.registry = fresh_registry

    from app.main import app
    with TestClient(app) as c:
        yield c

    # Restore everything.
    os.environ["DATA_DIR"] = old_data_dir
    dl.registry = old_registry
    rec_mod.registry = old_rec_registry
