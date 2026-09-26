from __future__ import annotations

import json

import pytest

from app.backup import apply_backup, build_backup
from app.db import connect, get_setting, set_setting


@pytest.fixture(autouse=True)
def clean_config():
    def reset():
        with connect() as conn:
            conn.execute("DELETE FROM settings")
            conn.execute("DELETE FROM whitelist")
            conn.execute("DELETE FROM unmatched_ignored")
            conn.execute("DELETE FROM match_ignored")
        # Re-bootstrap auth settings the login fixture expects to keep working across tests.
        from app.auth import bootstrap_auth

        bootstrap_auth()

    reset()
    yield
    reset()


def test_backup_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/settings/backup").status_code == 401
    assert client.post("/api/settings/restore", json={}).status_code == 401


def test_backup_includes_settings_and_whitelist_not_auth(auth_client):
    set_setting("radarr_url", "http://radarr.local:7878")
    set_setting("radarr_api_key", "secret-radarr-key")
    set_setting("auto_delete_enabled", "1")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at)
            VALUES ('title', 'any', 0, 'Keep Me', '', 1)
            """
        )

    response = auth_client.get("/api/settings/backup")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    assert response.headers.get("cache-control") == "no-store"
    payload = response.json()
    assert payload["format"] == "cleanarr-config"
    assert payload["version"] == 1
    assert payload["settings"]["radarr_url"] == "http://radarr.local:7878"
    assert payload["settings"]["radarr_api_key"] == "secret-radarr-key"
    assert payload["settings"]["auto_delete_enabled"] == "1"
    assert payload["whitelist"][0]["pattern"] == "Keep Me"
    assert "auth_password_hash" not in payload["settings"]
    assert "session_secret" not in payload["settings"]


def test_restore_applies_settings_and_replaces_whitelist(auth_client):
    set_setting("radarr_url", "http://old")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at)
            VALUES ('title', 'any', 0, 'Old Rule', '', 1)
            """
        )

    payload = {
        "format": "cleanarr-config",
        "version": 1,
        "settings": {
            "radarr_url": "http://new-radarr",
            "radarr_api_key": "new-key",
            "auto_delete_max_per_run": "25",
            "auth_password_hash": "should-never-apply",
            "session_secret": "nope",
        },
        "whitelist": [
            {"match_type": "title", "media_type": "movie", "tmdb_id": 0, "pattern": "New Rule", "note": "", "created_at": 2}
        ],
        "unmatched_ignored": [],
        "match_decisions": [],
    }
    body = auth_client.post("/api/settings/restore", content=json.dumps(payload)).json()
    assert body["ok"] is True
    assert body["settings_applied"] >= 3
    assert get_setting("radarr_url") == "http://new-radarr"
    assert get_setting("radarr_api_key") == "new-key"
    assert get_setting("auto_delete_max_per_run") == "25"
    assert get_setting("auth_password_hash") != "should-never-apply"
    with connect() as conn:
        patterns = [row["pattern"] for row in conn.execute("SELECT pattern FROM whitelist").fetchall()]
    assert patterns == ["New Rule"]


def test_restore_rejects_bad_format(auth_client):
    assert auth_client.post("/api/settings/restore", json={"format": "nope", "version": 1}).status_code == 400


def test_restore_rejects_oversized_body(auth_client):
    huge = json.dumps({"format": "cleanarr-config", "version": 1, "settings": {"note": "x" * 1_100_000}})
    assert auth_client.post("/api/settings/restore", content=huge).status_code == 400


def test_backup_skips_env_locked_keys(auth_client, monkeypatch):
    monkeypatch.setenv("RADARR_URL", "http://env-radarr")
    monkeypatch.setenv("RADARR_API_KEY", "env-key")
    set_setting("radarr_url", "http://db-radarr")
    set_setting("sonarr_url", "http://sonarr.local")
    payload = build_backup()
    assert "radarr_url" not in payload["settings"]
    assert "radarr_api_key" not in payload["settings"]
    assert "radarr_url" in payload["skipped_locked"]
    assert payload["settings"]["sonarr_url"] == "http://sonarr.local"


def test_restore_skips_locked_keys(auth_client, monkeypatch):
    monkeypatch.setenv("RADARR_URL", "http://env-radarr")
    result = apply_backup(
        {
            "format": "cleanarr-config",
            "version": 1,
            "settings": {"radarr_url": "http://attacker", "sonarr_url": "http://ok"},
            "whitelist": [],
        }
    )
    assert get_setting("radarr_url") != "http://attacker"
    assert get_setting("sonarr_url") == "http://ok"
    assert result["settings_skipped"] >= 1


def test_restore_fully_replaces_prior_config(auth_client):
    set_setting("radarr_url", "http://old-radarr")
    set_setting("jellystat_url", "http://old-jellystat")
    set_setting("jellystat_api_key", "old-jelly-key")
    set_setting("auto_delete_enabled", "1")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at)
            VALUES ('title', 'any', 0, 'Stale Rule', '', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO unmatched_ignored
                (kind, media_type, tmdb_id, tvdb_id, title_key, title, reason, created_at)
            VALUES ('no_seerr', 'movie', 1, 0, 'gone', 'Gone', '', 1)
            """
        )

    apply_backup(
        {
            "format": "cleanarr-config",
            "version": 1,
            "settings": {
                "radarr_url": "http://new-radarr",
                "radarr_api_key": "new-key",
            },
            "whitelist": [],
            "unmatched_ignored": [],
            "match_decisions": [],
        }
    )

    assert get_setting("radarr_url") == "http://new-radarr"
    assert get_setting("radarr_api_key") == "new-key"
    assert get_setting("jellystat_url") == ""
    assert get_setting("jellystat_api_key") == ""
    assert get_setting("auto_delete_enabled") == "0"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM whitelist").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM unmatched_ignored").fetchone()["n"] == 0
