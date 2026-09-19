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
def bundle(tmp_path, monkeypatch):
    """Point the SPA route at a throwaway build.

    Without this the catch-all has no index.html to serve and 404s, so any test
    of it would pass for the wrong reason. The icons are the committed ones so
    the content types a dashboard sees are the real ones.
    """
    from app import main

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html><body>Cleanarr</body></html>")
    # A source checkout keeps .env a couple of levels up from the bundle.
    (static / ".env").write_text("SEERR_API_KEY=secret")
    icons = Path(__file__).resolve().parents[2] / "frontend" / "public"
    for name in ("favicon.ico", "favicon-32x32.png", "apple-touch-icon.png", "site.webmanifest"):
        (static / name).write_bytes((icons / name).read_bytes())
    monkeypatch.setattr(main, "STATIC_DIR", static)
    return static


@pytest.fixture
def auth_client(client):
    from app.auth import bootstrap_auth

    # Re-apply env credentials so earlier tests that wipe auth state cannot poison login.
    bootstrap_auth()
    response = client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"})
    assert response.status_code == 200
    yield client
    client.post("/api/auth/logout")
