from __future__ import annotations

from typing import Any

from .http import json_get, json_request, tidy_url


class Seerr:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.headers = {"X-Api-Key": api_key}

    def test(self) -> str:
        data = json_get(f"{self.url}/api/v1/status", headers=self.headers)
        return str((data or {}).get("version") or "ok")

    def _paged(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skip = 0
        take = 50
        while True:
            data = json_get(
                f"{self.url}{path}",
                headers=self.headers,
                params={"take": take, "skip": skip, "filter": "all", "sort": "added"},
            ) or {}
            chunk = data.get("results") if isinstance(data, dict) else data
            chunk = chunk or []
            rows.extend(chunk)
            if len(chunk) < take:
                break
            skip += take
            if skip > 20_000:
                break
        return rows

    def requests(self) -> list[dict[str, Any]]:
        return self._paged("/api/v1/request")

    def media(self) -> list[dict[str, Any]]:
        return self._paged("/api/v1/media")

    def delete_media(self, media_id: int) -> None:
        json_request("DELETE", f"{self.url}/api/v1/media/{media_id}", headers=self.headers)

    def blacklist(self, tmdb_id: int, media_type: str, title: str) -> None:
        json_request(
            "POST",
            f"{self.url}/api/v1/blacklist",
            headers=self.headers,
            json={"tmdbId": tmdb_id, "mediaType": media_type, "title": title},
        )
