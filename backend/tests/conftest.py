import os
import sys
import tempfile
from pathlib import Path

# app.config creates data dirs and app.db pins DB_PATH at import time, so the
# environment has to be set up before anything under app is imported.
_TMP = tempfile.mkdtemp(prefix="cleanarr-tests-")
os.environ["DATA_DIR"] = _TMP
os.environ["CLEANARR_USERNAME"] = "tester"
os.environ["CLEANARR_PASSWORD"] = "hunter2"
os.environ["CLEANARR_SECRET"] = "test-secret"
os.environ.pop("CLEANARR_HIDE_SETTINGS", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client):
    from app.auth import bootstrap_auth

    # Re-apply env credentials so earlier tests that wipe auth state cannot poison login.
    bootstrap_auth()
    response = client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"})
    assert response.status_code == 200
    yield client
    client.post("/api/auth/logout")
