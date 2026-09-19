import time

import pytest

from app import sync
from app.db import connect, set_setting

DAY = 24 * 3600


@pytest.fixture(autouse=True)
def library():
    """Empty library and default auto-delete settings, restored after each test."""
    def reset():
        with connect() as conn:
            conn.execute("DELETE FROM media")
            conn.execute("DELETE FROM whitelist")
        set_setting("auto_delete_enabled", "0")
        set_setting("auto_delete_max_per_run", "10")
        set_setting("auto_delete_stale_days", "365")

    reset()
    yield
    reset()


def add_title(title, *, plays=0, last_watched=None, added_days_ago=400, availability="downloaded", radarr_id=1):
    now = int(time.time())
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO media (media_type, tmdb_id, title, play_count, last_watched_at,
                               added_at, availability, radarr_id, size_bytes)
            VALUES ('movie', ?, ?, ?, ?, ?, ?, ?, 1000000000)
            """,
            (
                abs(hash(title)) % 100000,
                title,
                plays,
                last_watched,
                None if added_days_ago is None else int(time.time()) - added_days_ago * DAY,
                availability,
                radarr_id,
            ),
        )
    return now


def titles(rows):
    return sorted(row["title"] for row in rows)


def test_only_never_watched_titles_past_the_cutoff_are_candidates(library):
    add_title("Old And Unwatched", added_days_ago=400)
    add_title("Added Last Week", added_days_ago=7)
    add_title("Watched Once", plays=3, last_watched=int(time.time()) - 400 * DAY)
    add_title("Not On Disk", availability="requested")

    assert titles(sync.auto_delete_candidates(365, 50)) == ["Old And Unwatched"]


def test_titles_without_an_added_date_are_never_candidates(library):
    # added_at is NULL until a sync backfills it, so an old library deletes nothing.
    add_title("No Added Date", added_days_ago=None)
    assert sync.auto_delete_candidates(365, 50) == []


def test_whitelisted_titles_are_skipped(library):
    add_title("Protected Film", added_days_ago=400)
    add_title("Ordinary Film", added_days_ago=400)
    with connect() as conn:
        conn.execute(
            "INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at) "
            "VALUES ('title', 'any', 0, 'protected', '', 0)"
        )
    assert titles(sync.auto_delete_candidates(365, 50)) == ["Ordinary Film"]


def test_candidates_stop_at_the_cap_oldest_first(library):
    for days in (500, 400, 900, 800):
        add_title(f"Film {days}", added_days_ago=days)
    picked = sync.auto_delete_candidates(365, 2)
    assert titles(picked) == ["Film 800", "Film 900"]


def test_run_auto_delete_is_a_no_op_when_disabled(library, monkeypatch):
    set_setting("auto_delete_enabled", "0")
    add_title("Old And Unwatched", added_days_ago=400)

    def fail(*args, **kwargs):
        raise AssertionError("delete_item must not be called while disabled")

    monkeypatch.setattr(sync, "delete_item", fail)
    assert sync.run_auto_delete() == {"enabled": False, "deleted": 0, "failed": 0, "capped": False}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_run_auto_delete_deletes_candidates_and_reports_the_cap(library, monkeypatch):
    set_setting("auto_delete_enabled", "1")
    set_setting("auto_delete_max_per_run", "2")
    set_setting("auto_delete_stale_days", "365")
    for days in (400, 500, 600):
        add_title(f"Film {days}", added_days_ago=days)

    deleted = []

    def fake_delete(item, **kwargs):
        deleted.append(item["title"])
        assert kwargs["delete_files"] is True
        assert kwargs["blacklist"] is False
        assert kwargs["actor"] == "automatic"
        with connect() as conn:
            conn.execute("DELETE FROM media WHERE id = ?", (item["id"],))
        return {"title": item["title"], "ok": True}

    monkeypatch.setattr(sync, "delete_item", fake_delete)
    result = sync.run_auto_delete()

    assert result == {"enabled": True, "deleted": 2, "failed": 0, "capped": True}
    assert sorted(deleted) == ["Film 500", "Film 600"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_a_failed_delete_does_not_stop_the_run(library, monkeypatch):
    set_setting("auto_delete_enabled", "1")
    set_setting("auto_delete_max_per_run", "10")
    add_title("Breaks", added_days_ago=500)
    add_title("Works", added_days_ago=400)

    def fake_delete(item, **kwargs):
        if item["title"] == "Breaks":
            raise RuntimeError("Radarr said no")
        return {"title": item["title"], "ok": True}

    monkeypatch.setattr(sync, "delete_item", fake_delete)
    result = sync.run_auto_delete()
    assert result["deleted"] == 1
    assert result["failed"] == 1


def test_settings_are_clamped_and_fall_back(monkeypatch):
    set_setting("auto_delete_max_per_run", "9999")
    set_setting("auto_delete_stale_days", "not a number")
    config = sync.auto_delete_settings()
    assert config["max_per_run"] == 500
    assert config["stale_days"] == 365


def test_init_db_adds_added_at_to_an_existing_library():
    from app.db import init_db

    with connect() as conn:
        conn.execute("ALTER TABLE media DROP COLUMN added_at")
        assert "added_at" not in {row["name"] for row in conn.execute("PRAGMA table_info(media)")}

    init_db()

    with connect() as conn:
        assert "added_at" in {row["name"] for row in conn.execute("PRAGMA table_info(media)")}


def test_manual_sync_never_auto_deletes_but_scheduled_does(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            started.append((kwargs or {}).get("auto_delete"))

        def start(self):
            pass

    monkeypatch.setattr(sync.threading, "Thread", FakeThread)
    sync.reset_job()
    sync.start_sync()
    sync.reset_job()
    sync.start_sync(scheduled=True)
    sync.reset_job()

    assert started == [False, True]
