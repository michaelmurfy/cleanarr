"""Reviewing Seerr requests that were attached to a library title by a title guess."""

import pytest

from app.db import connect, ignored_matches, match_ignore_key, set_setting


@pytest.fixture
def linked_row():
    def insert(**overrides):
        fields = {
            "media_type": "movie",
            "tmdb_id": 555604,
            "tvdb_id": 0,
            "title": "Guillermo del Toro's Pinocchio",
            "year": 2022,
            "seerr_media_id": 99,
            "seerr_tmdb_id": 111,
            "seerr_match_via": "title_alt",
            "seerr_title": "Pinocchio (1940)",
            "requested_by": "Alex",
            "requested_at": "2024-01-01",
        }
        fields.update(overrides)
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with connect() as conn:
            conn.execute(f"INSERT INTO media ({columns}) VALUES ({marks})", tuple(fields.values()))
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    yield insert
    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM match_ignored")


def test_title_guesses_are_listed_for_review(auth_client, linked_row):
    linked_row()
    linked_row(tmdb_id=603, title="The Matrix", seerr_tmdb_id=603, seerr_match_via="tmdb", seerr_title="The Matrix")

    items = auth_client.get("/api/matches/review").json()["items"]

    assert [item["title"] for item in items] == ["Guillermo del Toro's Pinocchio"]
    assert items[0]["seerr_title"] == "Pinocchio (1940)"
    assert items[0]["via"] == "title_alt"
    stats = auth_client.get("/api/unmatched").json()["stats"]
    assert stats["review"] == 1
    assert stats["actionable"] == 1


def test_unlink_clears_seerr_and_blocks_the_pair(auth_client, linked_row):
    item_id = linked_row()

    body = auth_client.post(f"/api/matches/{item_id}/unlink", json={}).json()

    assert body == {"ok": True, "remaining": 0}
    with connect() as conn:
        row = dict(conn.execute("SELECT * FROM media WHERE id = ?", (item_id,)).fetchone())
    assert row["seerr_media_id"] is None
    assert row["requested_by"] == ""
    assert row["seerr_match_via"] == ""
    assert match_ignore_key("movie", 111, 0, 555604, 0) in ignored_matches()
    assert auth_client.get("/api/matches/review").json()["items"] == []


def test_keep_confirms_without_blocking(auth_client, linked_row):
    item_id = linked_row()

    body = auth_client.post(f"/api/matches/{item_id}/keep", json={}).json()

    assert body["remaining"] == 0
    assert auth_client.get("/api/matches/review").json()["items"] == []
    assert match_ignore_key("movie", 111, 0, 555604, 0) not in ignored_matches()
    assert match_ignore_key("movie", 111, 0, 555604, 0) in ignored_matches("keep")
    with connect() as conn:
        assert conn.execute("SELECT requested_by FROM media WHERE id = ?", (item_id,)).fetchone()[0] == "Alex"


def test_undo_puts_the_title_back_up_for_review(auth_client, linked_row):
    item_id = linked_row()
    auth_client.post(f"/api/matches/{item_id}/keep", json={})
    decision = auth_client.get("/api/matches/decisions").json()["items"][0]
    assert decision["action"] == "keep"
    assert decision["seerr_title"] == "Pinocchio (1940)"

    assert auth_client.delete(f"/api/matches/decisions/{decision['id']}").status_code == 200
    assert len(auth_client.get("/api/matches/review").json()["items"]) == 1


def test_library_links_only_when_actually_linked(auth_client, linked_row):
    set_setting("seerr_url", "http://seerr.local")
    set_setting("seerr_external_url", "http://seerr.local")
    set_setting("tautulli_url", "http://tautulli.local")
    set_setting("tautulli_external_url", "http://tautulli.local")
    item_id = linked_row()

    items = auth_client.get("/api/library").json()["items"]
    row = next(item for item in items if item["id"] == item_id)
    assert "seerr" in row["links"]
    assert "tautulli" not in row["links"]
