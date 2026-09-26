def test_health_needs_no_auth(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"]


def test_protected_routes_reject_anonymous_callers(client):
    client.cookies.clear()
    for path in (
        "/api/auth/me",
        "/api/settings",
        "/api/settings/backup",
        "/api/sync",
        "/api/whitelist",
        "/api/library",
        "/api/unmatched",
        "/api/matches/review",
        "/api/matches/decisions",
    ):
        assert client.get(path).status_code == 401, path
    for path in ("/api/matches/1/unlink", "/api/matches/1/keep", "/api/settings/restore"):
        assert client.post(path, json={}).status_code == 401, path
    assert client.delete("/api/matches/decisions/1").status_code == 401


def test_login_rejects_bad_credentials(client):
    assert client.post("/api/auth/login", json={"username": "tester", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "hunter2"}).status_code == 401


def test_login_sets_session_cookie_and_me_returns_user(auth_client):
    assert auth_client.cookies.get("cleanarr_session")
    body = auth_client.get("/api/auth/me").json()
    assert body["username"] == "tester"
    assert body["using_default_password"] is False


def test_logout_clears_the_session(client):
    client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"})
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_tampered_session_cookie_is_rejected(client):
    client.cookies.clear()
    client.cookies.set("cleanarr_session", "tester:deadbeef:0000")
    assert client.get("/api/auth/me").status_code == 401


def test_settings_hides_api_keys(auth_client):
    values = auth_client.get("/api/settings").json()["values"]
    assert values["radarr_api_key"] == ""
    assert values["sync_interval_hours"] == "24"


def test_sync_interval_is_clamped(auth_client):
    auth_client.put("/api/settings", json={"values": {"sync_interval_hours": "500"}})
    assert auth_client.get("/api/settings").json()["values"]["sync_interval_hours"] == "168"
    auth_client.put("/api/settings", json={"values": {"sync_interval_hours": "abc"}})
    assert auth_client.get("/api/settings").json()["values"]["sync_interval_hours"] == "24"


def test_whitelist_round_trip(auth_client):
    created = auth_client.post("/api/whitelist", json={"pattern": "Matrix", "note": "keep"})
    assert created.status_code == 200

    items = auth_client.get("/api/whitelist").json()["items"]
    match = next(row for row in items if row["pattern"] == "Matrix")
    assert match["match_count"] == 0
    assert match["matches"] == []

    assert auth_client.delete(f"/api/whitelist/{match['id']}").status_code == 200
    remaining = auth_client.get("/api/whitelist").json()["items"]
    assert all(row["id"] != match["id"] for row in remaining)


def test_whitelist_lists_matching_library_titles(auth_client):
    from app.db import connect

    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM whitelist")
        conn.execute(
            "INSERT INTO media (media_type, tmdb_id, title, year) VALUES ('movie', 603, 'The Matrix', 1999)"
        )
        conn.execute(
            "INSERT INTO media (media_type, tmdb_id, title, year) VALUES ('movie', 604, 'The Matrix Reloaded', 2003)"
        )
        conn.execute(
            "INSERT INTO media (media_type, tmdb_id, title, year) VALUES ('movie', 157336, 'Interstellar', 2014)"
        )

    try:
        assert auth_client.post("/api/whitelist", json={"pattern": "Matrix", "note": "keep"}).status_code == 200
        assert auth_client.post(
            "/api/whitelist", json={"pattern": "157336", "match_type": "id", "tmdb_id": 157336}
        ).status_code == 200

        items = {row["pattern"]: row for row in auth_client.get("/api/whitelist").json()["items"]}
        matrix = items["Matrix"]
        assert matrix["match_count"] == 2
        assert [m["title"] for m in matrix["matches"]] == ["The Matrix", "The Matrix Reloaded"]

        interstellar = items["157336"]
        assert interstellar["match_count"] == 1
        assert interstellar["matches"][0]["title"] == "Interstellar"
        assert interstellar["matches"][0]["tmdb_id"] == 157336
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM media")
            conn.execute("DELETE FROM whitelist")


def test_whitelist_rejects_empty_pattern(auth_client):
    assert auth_client.post("/api/whitelist", json={"pattern": "   "}).status_code == 400


def test_unknown_service_probe_is_rejected(auth_client):
    assert auth_client.post("/api/settings/test", json={"service": "nope"}).status_code == 400


def test_unconfigured_service_probe_reports_not_configured(auth_client):
    body = auth_client.post("/api/settings/test", json={"service": "radarr"}).json()
    assert body["ok"] is False
    assert body["configured"] is False
    assert body["message"] == "Not configured"


def test_test_all_skips_unconfigured_services(auth_client):
    body = auth_client.post("/api/settings/test-all").json()
    assert body["results"] == []
    assert body["ok"] is False


def test_library_returns_empty_payload_before_a_sync(auth_client):
    body = auth_client.get("/api/library").json()
    assert body["items"] == []


def test_protected_titles_are_hidden_outside_the_protected_filter(auth_client):
    """A whitelisted title is never a deletion candidate, so every other view
    (including a plain search for it) should skip it; only the Protected filter
    still shows it."""
    from app.db import connect

    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM whitelist")
        conn.execute(
            "INSERT INTO media (media_type, tmdb_id, title, year, play_count) "
            "VALUES ('movie', 2157, 'Lost in Space', 1998, 0)"
        )
        conn.execute(
            "INSERT INTO media (media_type, tmdb_id, title, year, play_count) "
            "VALUES ('movie', 423, 'Jobs', 2013, 0)"
        )
        conn.execute(
            "INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at) "
            "VALUES ('id', 'any', 2157, '2157', '', 0)"
        )

    try:
        default = auth_client.get("/api/library").json()
        assert [i["title"] for i in default["items"]] == ["Jobs"]
        assert default["stats"]["count"] == 1
        assert default["stats"]["whitelisted"] == 1

        searched = auth_client.get("/api/library", params={"q": "Lost in Space"}).json()
        assert searched["items"] == []

        protected = auth_client.get("/api/library", params={"watched": "protected"}).json()
        assert [i["title"] for i in protected["items"]] == ["Lost in Space"]
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM media")
            conn.execute("DELETE FROM whitelist")


def test_auto_delete_defaults_to_off(auth_client):
    values = auth_client.get("/api/settings").json()["values"]
    assert values["auto_delete_enabled"] == "0"
    assert values["auto_delete_max_per_run"] == "10"
    assert values["auto_delete_stale_days"] == "365"


def test_auto_delete_settings_round_trip_and_clamp(auth_client):
    auth_client.put(
        "/api/settings",
        json={"values": {"auto_delete_enabled": "yes", "auto_delete_max_per_run": "9999", "auto_delete_stale_days": "90"}},
    )
    values = auth_client.get("/api/settings").json()["values"]
    assert values["auto_delete_enabled"] == "1"
    assert values["auto_delete_max_per_run"] == "500"
    assert values["auto_delete_stale_days"] == "90"

    auth_client.put("/api/settings", json={"values": {"auto_delete_enabled": "0"}})
    assert auth_client.get("/api/settings").json()["values"]["auto_delete_enabled"] == "0"


def test_successful_probe_detail_is_redacted(auth_client, monkeypatch):
    from app import main

    class Leaky:
        def test(self):
            return "connected to http://radarr.local/api/v3?apikey=supersecret123"

    monkeypatch.setitem(main.SERVICES, "radarr", lambda: Leaky())
    body = auth_client.post("/api/settings/test", json={"service": "radarr"}).json()

    assert body["ok"] is True
    assert "supersecret123" not in body["detail"]
