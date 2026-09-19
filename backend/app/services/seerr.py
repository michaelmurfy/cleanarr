from __future__ import annotations

from typing import Any

from .http import json_get, json_request, tidy_url

# Overseerr / Jellyseerr media + request status enums.
MEDIA_AVAILABLE = {4, 5}
MEDIA_IN_FLIGHT = {2, 3}
MEDIA_BLOCKED = {6}  # blacklisted / blocklisted in Seerr and Jellyseerr
MEDIA_DELETED = {7}
REQUEST_PENDING = {1}
REQUEST_APPROVED = {2}
REQUEST_OPEN = REQUEST_PENDING | REQUEST_APPROVED
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
        "blacklisted": 6,
        "blocklisted": 6,
        "blacklist": 6,
        "blocklist": 6,
        "deleted": 7,
        "approved": 2,
        "declined": 3,
        "failed": 4,
        "completed": 5,
    }
    return names.get(text.replace(" ", ""))


def media_blocked(media: dict[str, Any] | None) -> bool:
    """True when Seerr has blocklisted the title so it should not be treated as stale."""
    media = media or {}
    for field in ("status", "status4k"):
        if _status_num(media.get(field)) in MEDIA_BLOCKED:
            return True
    if media.get("isBlacklisted") or media.get("isBlocklisted"):
        return True
    for field in ("blacklist", "blocklist"):
        value = media.get(field)
        if value not in (None, False, "", [], {}):
            return True
    return False


def media_deleted(media: dict[str, Any] | None) -> bool:
    """True when Seerr has already cleared the title (status 7), so there is nothing left to reconcile."""
    media = media or {}
    statuses = [_status_num(media.get(field)) for field in ("status", "status4k")]
    if any(status in MEDIA_AVAILABLE for status in statuses):
        return False
    return any(status in MEDIA_DELETED for status in statuses)


def media_available(media: dict[str, Any] | None) -> bool:
    """True when Seerr's own status says the title is (partially) available."""
    media = media or {}
    return any(_status_num((media or {}).get(field)) in MEDIA_AVAILABLE for field in ("status", "status4k"))


def media_claimed(media: dict[str, Any] | None) -> bool:
    """True when Seerr still treats the title as present in the *arr library.

    mediaAddedAt is deliberately not evidence: Jellyseerr defaults it to the row's insert
    time, so every media row carries one regardless of whether the file still exists.
    """
    media = media or {}
    if media_blocked(media) or media_deleted(media):
        return False
    for field in ("status", "status4k"):
        if _status_num(media.get(field)) in MEDIA_AVAILABLE:
            return True
    if media.get("externalServiceId") or media.get("externalServiceId4k"):
        return True
    return False


def media_in_flight(media: dict[str, Any] | None) -> bool:
    media = media or {}
    return _status_num(media.get("status")) in MEDIA_IN_FLIGHT and not media_claimed(media)


def request_is_open(requests: list[Any] | None) -> bool:
    for req in requests or []:
        if not isinstance(req, dict):
            continue
        if _status_num(req.get("status")) in REQUEST_OPEN:
            return True
    return False


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

    def detail(self, media_type: str, tmdb_id: int) -> dict[str, Any]:
        """TMDB-backed title lookup; /api/v1/media rows carry no title of their own."""
        path = "tv" if media_type == "tv" else "movie"
        data = json_get(f"{self.url}/api/v1/{path}/{int(tmdb_id)}", headers=self.headers)
        return data if isinstance(data, dict) else {}

    def users(self) -> list[dict[str, Any]]:
        return self._paged("/api/v1/user")

    def blocklist(self) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for path in ("/api/v1/blocklist", "/api/v1/blacklist"):
            try:
                return self._paged(path)
            except Exception as exc:
                last_error = exc
                if getattr(exc, "status", None) in {404, 405}:
                    continue
        if last_error and getattr(last_error, "status", None) not in {404, 405}:
            raise last_error
        return []

    def delete_media(self, media_id: int) -> None:
        json_request("DELETE", f"{self.url}/api/v1/media/{media_id}", headers=self.headers)

    def request_media(self, tmdb_id: int, media_type: str) -> dict[str, Any]:
        """Create an auto-approved request so Seerr starts tracking a title the *arrs already hold."""
        body: dict[str, Any] = {
            "mediaType": "tv" if media_type == "tv" else "movie",
            "mediaId": int(tmdb_id),
        }
        if media_type == "tv":
            body["seasons"] = "all"
        data = json_request("POST", f"{self.url}/api/v1/request", headers=self.headers, json=body)
        return data if isinstance(data, dict) else {}

    def blacklist(self, tmdb_id: int, media_type: str, title: str) -> None:
        json_request(
            "POST",
            f"{self.url}/api/v1/blacklist",
            headers=self.headers,
            json={"tmdbId": tmdb_id, "mediaType": media_type, "title": title},
        )
