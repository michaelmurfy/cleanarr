import sqlite3

import pytest

from app.actions import Whitelist, is_protected
from app.db import connect, get_setting, set_setting


def test_settings_cache_sees_writes_from_other_connections(client):
    set_setting("bench_probe", "one")
    assert get_setting("bench_probe") == "one"

    with connect() as conn:
        conn.execute("UPDATE settings SET value = 'two' WHERE key = 'bench_probe'")
    assert get_setting("bench_probe") == "two"

    with connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'bench_probe'")
    assert get_setting("bench_probe", "gone") == "gone"


def test_connect_closes_on_exit(client):
    with connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_prepared_whitelist_matches_like_the_plain_rules():
    rows = [
        {"media_type": "tv", "match_type": "title", "tmdb_id": 0, "pattern": " Stargate "},
        {"media_type": "any", "match_type": "id", "tmdb_id": 157336, "pattern": "157336"},
        {"media_type": "movie", "match_type": "title", "tmdb_id": 0, "pattern": "Back to the Future"},
    ]
    rules = Whitelist(rows)
    cases = [
        ("Stargate SG-1", "tv", 0, rows[0]),
        ("Stargate", "movie", 0, None),
        ("Interstellar", "movie", 157336, rows[1]),
        ("Back to the Future Part II", "movie", 105, rows[2]),
        ("Back to the Future", "tv", 0, None),
    ]
    for title, media_type, tmdb, expected in cases:
        assert rules.match(title, media_type, tmdb) is expected
        assert is_protected(title, media_type, tmdb, rows) is expected


def test_password_change_rejects_old_session_cookie(auth_client):
    from app import auth

    assert auth_client.get("/api/auth/me").status_code == 200
    try:
        auth.set_credentials("tester", "a-brand-new-password")
        # The session key is derived from cached settings, so this also proves
        # the cache picked up the new hash before the next request.
        assert auth_client.get("/api/auth/me").status_code == 401
    finally:
        auth.bootstrap_auth()
        auth_client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"})
