import pytest

from app.db import connect, ignore_key, ignored_unmatched


@pytest.fixture
def unmatched_row():
    def insert(kind="no_seerr", tmdb_id=0, tvdb_id=427415, title="Kath and Kim: Our Effluent Life"):
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO unmatched (source, media_type, title, kind, tmdb_id, tvdb_id, reason)
                VALUES ('sonarr', 'tv', ?, ?, ?, ?, 'In the library, but Seerr cannot track it: no TMDB id')
                """,
                (title, kind, tmdb_id, tvdb_id),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    yield insert
    with connect() as conn:
        conn.execute("DELETE FROM unmatched")
        conn.execute("DELETE FROM unmatched_ignored")


def test_ignore_moves_the_row_onto_the_ignore_list(auth_client, unmatched_row):
    row_id = unmatched_row()

    body = auth_client.post("/api/unmatched/ignore", json={"ids": [row_id]}).json()

    assert body == {"ignored": 1, "remaining": 0}
    items = auth_client.get("/api/unmatched/ignored").json()["items"]
    assert [item["title"] for item in items] == ["Kath and Kim: Our Effluent Life"]
    assert items[0]["tvdb_id"] == 427415


def test_an_ignored_row_is_skipped_on_the_next_sync(auth_client, unmatched_row):
    row_id = unmatched_row()
    auth_client.post("/api/unmatched/ignore", json={"ids": [row_id]})

    # The key sync rebuilds rows under, with no year, must hit the stored entry.
    assert ignore_key("no_seerr", "tv", 0, 427415, "Kath and Kim: Our Effluent Life") in ignored_unmatched()
    assert ignore_key("no_seerr", "tv", 0, 427415, "something else") not in ignored_unmatched()


def test_ignoring_twice_does_not_duplicate_the_entry(auth_client, unmatched_row):
    first = unmatched_row()
    auth_client.post("/api/unmatched/ignore", json={"ids": [first]})
    second = unmatched_row()
    auth_client.post("/api/unmatched/ignore", json={"ids": [second]})

    assert len(auth_client.get("/api/unmatched/ignored").json()["items"]) == 1


def test_unignore_brings_the_title_back_into_scope(auth_client, unmatched_row):
    row_id = unmatched_row()
    auth_client.post("/api/unmatched/ignore", json={"ids": [row_id]})
    item_id = auth_client.get("/api/unmatched/ignored").json()["items"][0]["id"]

    assert auth_client.delete(f"/api/unmatched/ignored/{item_id}").status_code == 200
    assert auth_client.get("/api/unmatched/ignored").json()["items"] == []
    assert ignored_unmatched() == set()


def test_unmatched_stats_report_the_ignored_count(auth_client, unmatched_row):
    row_id = unmatched_row()
    auth_client.post("/api/unmatched/ignore", json={"ids": [row_id]})

    assert auth_client.get("/api/unmatched").json()["stats"]["ignored"] == 1


def test_ignore_rejects_an_empty_selection(auth_client):
    assert auth_client.post("/api/unmatched/ignore", json={"ids": []}).status_code == 400


def test_unignore_unknown_id_is_a_404(auth_client):
    assert auth_client.delete("/api/unmatched/ignored/999999").status_code == 404


def test_clearing_the_library_keeps_the_ignore_list(auth_client, unmatched_row):
    row_id = unmatched_row()
    auth_client.post("/api/unmatched/ignore", json={"ids": [row_id]})

    assert auth_client.post("/api/settings/clear-library").status_code == 200
    assert len(auth_client.get("/api/unmatched/ignored").json()["items"]) == 1
