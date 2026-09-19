from __future__ import annotations

from app.actions import delete_item
from app.db import connect
from app.services import clients


class FakeRadarr:
    def __init__(self, name: str, deleted: list[tuple[str, int]]):
        self.name = name
        self.deleted = deleted

    def delete(self, movie_id: int, delete_files: bool = True, exclude: bool = False) -> None:
        self.deleted.append((self.name, movie_id, delete_files, exclude))


def _movie(**overrides):
    row = {
        "id": 1,
        "media_type": "movie",
        "tmdb_id": 42,
        "tvdb_id": 0,
        "title": "Dual Copy",
        "radarr_id": None,
        "radarr_4k_id": None,
        "seerr_media_id": None,
    }
    row.update(overrides)
    return row


def test_settings_include_radarr_4k_keys(auth_client):
    values = auth_client.get("/api/settings").json()["values"]
    assert "radarr_4k_url" in values
    assert values["radarr_4k_api_key"] == ""


def test_radarr_4k_probe_reports_not_configured(auth_client):
    body = auth_client.post("/api/settings/test", json={"service": "radarr_4k"}).json()
    assert body["ok"] is False
    assert body["configured"] is False


def test_delete_removes_from_both_radarr_instances(monkeypatch):
    deleted: list[tuple] = []
    primary = FakeRadarr("radarr", deleted)
    four_k = FakeRadarr("radarr_4k", deleted)
    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute(
            """
            INSERT INTO media (id, media_type, tmdb_id, title, radarr_id, radarr_4k_id, size_bytes)
            VALUES (9, 'movie', 42, 'Dual Copy', 11, 22, 0)
            """
        )

    result = delete_item(
        _movie(id=9, radarr_id=11, radarr_4k_id=22),
        delete_files=True,
        blacklist=False,
        actor="tester",
        radarr_client=primary,
        radarr_4k_client=four_k,
    )
    assert result["ok"] is True
    assert deleted == [("radarr", 11, True, False), ("radarr_4k", 22, True, False)]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM media WHERE id = 9").fetchone()["n"] == 0


def test_delete_works_with_only_radarr_4k_id():
    deleted: list[tuple] = []
    four_k = FakeRadarr("radarr_4k", deleted)
    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute(
            """
            INSERT INTO media (id, media_type, tmdb_id, title, radarr_4k_id, size_bytes)
            VALUES (10, 'movie', 99, 'UHD Only', 55, 0)
            """
        )

    result = delete_item(
        _movie(id=10, title="UHD Only", tmdb_id=99, radarr_4k_id=55),
        delete_files=True,
        blacklist=False,
        actor="tester",
        radarr_4k_client=four_k,
    )
    assert result["ok"] is True
    assert deleted == [("radarr_4k", 55, True, False)]


def test_radarr_4k_client_uses_dedicated_settings(monkeypatch):
    monkeypatch.setenv("RADARR_4K_URL", "http://radarr-4k:7878")
    monkeypatch.setenv("RADARR_4K_API_KEY", "uhd-key")
    client = clients.radarr_4k()
    assert client is not None
    assert client.url == "http://radarr-4k:7878"
    assert client.headers["X-Api-Key"] == "uhd-key"
