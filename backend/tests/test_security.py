import hashlib
import hmac
import time
from pathlib import Path

import pytest


def test_static_route_refuses_paths_outside_the_bundle(tmp_path, monkeypatch):
    """The SPA catch-all took attacker-controlled paths; /data and .env must stay unreachable."""
    from app import main

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>")
    (static / "favicon.svg").write_text("<svg />")
    (static / ".env").write_text("SEERR_API_KEY=secret")
    (tmp_path / "outside.txt").write_text("private")
    monkeypatch.setattr(main, "STATIC_DIR", static)

    assert main._static_file("favicon.svg") == static / "favicon.svg"
    for escape in (
        "../outside.txt",
        "../../etc/hostname",
        "/etc/hostname",
        "..%2f..%2fetc%2fhostname".replace("%2f", "/"),
        ".env",
        "",
    ):
        assert main._static_file(escape) is None, escape


def test_api_responses_carry_hardening_headers(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_cross_site_writes_are_rejected(auth_client):
    blocked = auth_client.post("/api/sync", headers={"Sec-Fetch-Site": "cross-site"})
    assert blocked.status_code == 403
    same_origin = auth_client.get("/api/sync", headers={"Sec-Fetch-Site": "same-origin"})
    assert same_origin.status_code == 200


def test_repeated_bad_passwords_lock_the_account_out(client):
    from app.security import login_throttle

    login_throttle.reset()
    client.cookies.clear()
    try:
        for _ in range(login_throttle.max_attempts):
            assert client.post("/api/auth/login", json={"username": "tester", "password": "nope"}).status_code == 401
        locked = client.post("/api/auth/login", json={"username": "tester", "password": "nope"})
        assert locked.status_code == 429
        assert locked.headers["retry-after"]
        # A correct password does not get a free pass while locked out.
        assert client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"}).status_code == 429
    finally:
        login_throttle.reset()


def test_successful_login_clears_the_failure_count(client):
    from app.security import login_throttle

    login_throttle.reset()
    client.cookies.clear()
    try:
        for _ in range(login_throttle.max_attempts - 1):
            client.post("/api/auth/login", json={"username": "tester", "password": "nope"})
        assert client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"}).status_code == 200
        assert client.post("/api/auth/login", json={"username": "tester", "password": "nope"}).status_code == 401
    finally:
        login_throttle.reset()
        client.post("/api/auth/logout")


def _signed(username: str, issued: int) -> str:
    from app import auth

    payload = f"{username}:{issued}:deadbeef"
    sig = hmac.new(auth._session_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def test_sessions_expire(client):
    from app import auth

    now = int(time.time())
    assert auth.read_session(_signed("tester", now)) == "tester"
    assert auth.read_session(_signed("tester", now - auth.SESSION_MAX_AGE - 60)) is None
    assert auth.read_session(_signed("tester", now + 3600)) is None
    assert auth.read_session("tester:not-a-number:deadbeef:00") is None
    assert auth.read_session(None) is None


def test_changing_the_password_invalidates_old_sessions(client):
    from app import auth

    token = _signed("tester", int(time.time()))
    assert auth.read_session(token) == "tester"
    try:
        auth.set_credentials("tester", "another-password")
        assert auth.read_session(token) is None
    finally:
        auth.bootstrap_auth()


def test_short_passwords_are_refused(auth_client):
    from fastapi import HTTPException

    from app import auth

    with pytest.raises(HTTPException) as caught:
        auth.set_credentials("tester", "short")
    assert caught.value.status_code == 400
    auth.bootstrap_auth()


def test_redact_masks_keys_and_credentialed_urls(monkeypatch):
    from app import security

    monkeypatch.setattr(security, "_configured_secrets", lambda: ["supersecretkey123"])
    assert "supersecretkey123" not in security.redact("GET https://tautulli/api/v2?apikey=supersecretkey123 failed")
    assert security.redact("http://host/api?apikey=abc123def456 returned 401").endswith("returned 401")
    assert "abc123def456" not in security.redact("http://host/api?apikey=abc123def456 returned 401")
    assert "X-Api-Key: ***" in security.redact("X-Api-Key: 0123456789abcdef")
    assert security.redact("") == ""
    assert security.redact(None) == ""


def test_logs_never_persist_a_configured_api_key(auth_client, monkeypatch):
    from app import logs, security

    monkeypatch.setattr(security, "_configured_secrets", lambda: ["leaky-key-value"])
    logs.add_log("Seerr failed with leaky-key-value", category="system", action="test")
    items = auth_client.get("/api/logs", params={"q": "Seerr failed"}).json()["items"]
    assert items
    assert "leaky-key-value" not in items[0]["message"]


def test_log_listing_reports_level_counts(auth_client):
    from app import logs

    logs.add_log("Security test warning", level="warn", category="system", action="test")
    body = auth_client.get("/api/logs", params={"q": "Security test warning"}).json()
    assert body["levels"]["warn"] >= 1
    assert set(body["levels"]) == {"info", "warn", "error"}


def test_unknown_api_paths_do_not_fall_through_to_the_app_shell(client):
    assert client.get("/api/definitely-not-a-route").status_code == 404
