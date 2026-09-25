"""Rejecting a bad Seerr↔*arr attachment and keeping it rejected across syncs."""

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
            "requested_by": "Alex",
            "requested_at": "2024-01-01",
        }
        fields.update(overrides)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO media (
                    media_type, tmdb_id, tvdb_id, title, year,
                    seerr_media_id, seerr_tmdb_id, seerr_match_via, requested_by, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["media_type"],
                    fields["tmdb_id"],
                    fields["tvdb_id"],
                    fields["title"],
                    fields["year"],
                    fields["seerr_media_id"],
                    fields["seerr_tmdb_id"],
                    fields["seerr_match_via"],
                    fields["requested_by"],
                    fields["requested_at"],
                ),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    yield insert
    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM match_ignored")


def test_unlink_clears_seerr_and_records_ignore(auth_client, linked_row):
    item_id = linked_row()

    body = auth_client.post(f"/api/library/{item_id}/unlink-seerr", json={"reason": "Wrong Pinocchio"}).json()

    assert body["ok"] is True
    with connect() as conn:
        row = dict(conn.execute("SELECT * FROM media WHERE id = ?", (item_id,)).fetchone())
    assert row["seerr_media_id"] is None
    assert row["requested_by"] == ""
    assert row["seerr_match_via"] == ""
    assert row["seerr_tmdb_id"] == 0

    ignored = auth_client.get("/api/matches/ignored").json()["items"]
    assert len(ignored) == 1
    assert ignored[0]["seerr_tmdb_id"] == 111
    assert ignored[0]["library_tmdb_id"] == 555604
    assert ignored[0]["reason"] == "Wrong Pinocchio"
    assert match_ignore_key("movie", 111, 0, 555604, 0) in ignored_matches()


def test_unignore_match_removes_the_pair(auth_client, linked_row):
    item_id = linked_row()
    auth_client.post(f"/api/library/{item_id}/unlink-seerr", json={})
    ignored_id = auth_client.get("/api/matches/ignored").json()["items"][0]["id"]

    assert auth_client.delete(f"/api/matches/ignored/{ignored_id}").status_code == 200
    assert auth_client.get("/api/matches/ignored").json()["items"] == []


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
