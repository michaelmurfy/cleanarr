import pytest

from app.db import connect


@pytest.fixture
def rows():
    def insert(kind, *, title, seerr_state="", tmdb_id=0, media_type="movie"):
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO unmatched (source, media_type, title, kind, seerr_state, tmdb_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("seerr", media_type, title, kind, seerr_state, tmdb_id),
            )

    with connect() as conn:
        conn.execute("DELETE FROM unmatched")
    yield insert
    with connect() as conn:
        conn.execute("DELETE FROM unmatched")


def test_deleted_rows_are_reported_but_stay_out_of_the_actionable_count(auth_client, rows):
    rows("seerr_missing", title="Stale Film", seerr_state="available")
    rows("no_seerr", title="Library Only", seerr_state="absent")
    rows("seerr_deleted", title="Already Gone", seerr_state="deleted")

    stats = auth_client.get("/api/unmatched").json()["stats"]

    assert stats["count"] == 3
    # A title Seerr already deleted can just be requested again, so it is not a gap to fix.
    assert stats["actionable"] == 2
    assert stats["seerr_deleted"] == 1
    assert stats["seerr_missing"] == 1
    assert stats["no_seerr"] == 1


def test_deleted_rows_carry_their_state_and_can_be_filtered(auth_client, rows):
    rows("seerr_missing", title="Stale Film", seerr_state="orphan")
    rows("seerr_deleted", title="Already Gone", seerr_state="deleted", tmdb_id=603)

    body = auth_client.get("/api/unmatched", params={"kind": "seerr_deleted"}).json()

    assert [row["title"] for row in body["items"]] == ["Already Gone"]
    assert body["items"][0]["seerr_state"] == "deleted"


def test_the_library_unmatched_stat_ignores_deleted_seerr_history(auth_client, rows):
    rows("seerr_deleted", title="Already Gone", seerr_state="deleted")
    assert auth_client.get("/api/library").json()["stats"]["unmatched"] == 0

    rows("seerr_missing", title="Stale Film", seerr_state="available")
    assert auth_client.get("/api/library").json()["stats"]["unmatched"] == 1


def test_clearing_stale_seerr_never_touches_deleted_history(auth_client, rows, monkeypatch):
    rows("seerr_deleted", title="Already Gone", seerr_state="deleted")

    class FakeSeerr:
        def delete_media(self, media_id):  # pragma: no cover - must never be called
            raise AssertionError("deleted history should not be cleared")

    monkeypatch.setattr("app.actions.seerr", lambda: FakeSeerr())

    response = auth_client.post("/api/unmatched/clear-seerr", json={"all_stale": True})

    assert response.status_code == 200
    assert response.json()["results"] == []
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 1
