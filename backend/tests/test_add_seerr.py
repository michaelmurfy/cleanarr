import pytest

from app.db import connect
from app.services.http import ServiceError


@pytest.fixture
def unmatched_row():
    def insert(kind="no_seerr", tmdb_id=603, media_type="movie", title="The Matrix"):
        with connect() as conn:
            conn.execute(
                "INSERT INTO unmatched (source, media_type, title, kind, tmdb_id) VALUES (?, ?, ?, ?, ?)",
                ("radarr", media_type, title, kind, tmdb_id),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    yield insert
    with connect() as conn:
        conn.execute("DELETE FROM unmatched")


class FakeSeerr:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def request_media(self, tmdb_id, media_type):
        self.calls.append((tmdb_id, media_type))
        if self.error:
            raise self.error
        return {"id": 1}


def test_add_seerr_requests_the_title_and_drops_the_row(auth_client, unmatched_row, monkeypatch):
    row_id = unmatched_row(media_type="tv", tmdb_id=1396, title="Breaking Bad")
    fake = FakeSeerr()
    monkeypatch.setattr("app.actions.seerr", lambda: fake)

    response = auth_client.post("/api/unmatched/add-seerr", json={"ids": [row_id]})

    assert response.status_code == 200
    assert response.json()["results"] == [{"title": "Breaking Bad", "ok": True}]
    assert fake.calls == [(1396, "tv")]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 0


def test_add_seerr_treats_a_duplicate_request_as_success(auth_client, unmatched_row, monkeypatch):
    row_id = unmatched_row()
    monkeypatch.setattr(
        "app.actions.seerr",
        lambda: FakeSeerr(ServiceError("http", "already exists", 409)),
    )

    response = auth_client.post("/api/unmatched/add-seerr", json={"ids": [row_id]})

    assert response.json()["results"][0]["ok"] is True
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 0


def test_add_seerr_reports_failures_and_keeps_the_row(auth_client, unmatched_row, monkeypatch):
    row_id = unmatched_row()
    monkeypatch.setattr(
        "app.actions.seerr",
        lambda: FakeSeerr(ServiceError("http", "boom", 500)),
    )

    result = auth_client.post("/api/unmatched/add-seerr", json={"ids": [row_id]}).json()["results"][0]

    assert result["ok"] is False
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 1


def test_add_seerr_ignores_stale_rows_and_rows_without_a_tmdb_id(auth_client, unmatched_row, monkeypatch):
    stale_id = unmatched_row(kind="seerr_missing", title="Stale")
    no_tmdb_id = unmatched_row(tmdb_id=0, title="No ids")
    fake = FakeSeerr()
    monkeypatch.setattr("app.actions.seerr", lambda: fake)

    results = auth_client.post(
        "/api/unmatched/add-seerr", json={"ids": [stale_id, no_tmdb_id]}
    ).json()["results"]

    assert fake.calls == []
    assert results == [{"title": "No ids", "ok": False, "error": "No TMDB id to request"}]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM unmatched").fetchone()[0] == 2


def test_add_seerr_rejects_an_empty_selection(auth_client, monkeypatch):
    monkeypatch.setattr("app.actions.seerr", lambda: FakeSeerr())
    assert auth_client.post("/api/unmatched/add-seerr", json={}).status_code == 400


def test_add_seerr_needs_seerr_configured(auth_client, monkeypatch):
    monkeypatch.setattr("app.actions.seerr", lambda: None)
    assert auth_client.post("/api/unmatched/add-seerr", json={"ids": [1]}).status_code == 400
