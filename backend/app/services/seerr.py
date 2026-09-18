from __future__ import annotations

from typing import Any

from .http import json_get, json_request, tidy_url

# Overseerr / Jellyseerr media + request status enums.
MEDIA_AVAILABLE = {4, 5}
MEDIA_IN_FLIGHT = {2, 3}
REQUEST_PENDING = {1}
REQUEST_COUNTED = {2, 4, 5}  # approved, failed, completed


def _status_num(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip().lower()
    if text.isdigit():
        return int(text)
    names = {
        "unknown": 1,
        "pending": 2,
        "processing": 3,
        "partiallyavailable": 4,
        "partially-available": 4,
        "partial": 4,
        "available": 5,
        "approved": 2,
        "declined": 3,
        "failed": 4,
        "completed": 5,
    }
    return names.get(text.replace(" ", ""))


def media_claimed(media: dict[str, Any] | None) -> bool:
    """True when Seerr still treats the title as present in the *arr library."""
    media = media or {}
    for field in ("status", "status4k"):
        if _status_num(media.get(field)) in MEDIA_AVAILABLE:
            return True
    if media.get("externalServiceId") or media.get("externalServiceId4k"):
        return True
    if media.get("mediaAddedAt"):
        return True
    return False


def media_in_flight(media: dict[str, Any] | None) -> bool:
    media = media or {}
    return _status_num(media.get("status")) in MEDIA_IN_FLIGHT and not media_claimed(media)


def request_was_made(requests: list[Any] | None) -> bool:
    for req in requests or []:
        if not isinstance(req, dict):
            continue
        status = _status_num(req.get("status"))
        if status in REQUEST_COUNTED:
            return True
        if status not in REQUEST_PENDING | {3, None} and req.get("status") not in (None, ""):
            return True
    return False


class Seerr:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.headers = {"X-Api-Key": api_key}

    def test(self) -> str:
        data = json_get(f"{self.url}/api/v1/status", headers=self.headers)
        return str((data or {}).get("version") or "ok")

    def _paged(self, path: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skip = 0
        take = 100
        while True:
            params: dict[str, Any] = {"take": take, "skip": skip}
            if extra:
                params.update(extra)
            try:
                data = json_get(f"{self.url}{path}", headers=self.headers, params=params) or {}
            except Exception:
                if take > 20:
                    take = 20
                    continue
                raise
            if isinstance(data, list):
                rows.extend(data)
                break
            chunk = data.get("results") or data.get("data") or []
            if not isinstance(chunk, list):
                chunk = []
            rows.extend(chunk)
            info = data.get("pageInfo") or data.get("pagination") or {}
            total = int(info.get("results") or info.get("total") or 0)
            skip += len(chunk) if chunk else take
            if not chunk:
                break
            if total and len(rows) >= total:
                break
            if not total and len(chunk) < take:
                break
            if skip > 50_000:
                break
        return rows

    def requests(self) -> list[dict[str, Any]]:
        return self._paged("/api/v1/request", {"filter": "all", "sort": "added"})

    def media(self) -> list[dict[str, Any]]:
        return self._paged("/api/v1/media", {"filter": "all"})

    def users(self) -> list[dict[str, Any]]:
        return self._paged("/api/v1/user")

    def delete_media(self, media_id: int) -> None:
        json_request("DELETE", f"{self.url}/api/v1/media/{media_id}", headers=self.headers)

    def blacklist(self, tmdb_id: int, media_type: str, title: str) -> None:
        json_request(
            "POST",
            f"{self.url}/api/v1/blacklist",
            headers=self.headers,
            json={"tmdbId": tmdb_id, "mediaType": media_type, "title": title},
        )
