"""
Shared pytest fixtures for the geo-service test suite.
Sets the required environment variables so the lifespan hook does not exit.
"""

import os
import pytest
from fastapi.testclient import TestClient


# Set required env vars before importing the app so the startup check passes.
os.environ.setdefault("DATA_DIR", "/tmp/test-data")
os.environ.setdefault("DEFAULT_CRS_EPSG", "32643")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # quieter logs during tests


@pytest.fixture(scope="session")
def client() -> TestClient:
    from app.main import app
    with TestClient(app) as c:
        yield c
