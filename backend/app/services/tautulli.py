from __future__ import annotations

import re
from typing import Any

from .http import json_get, tidy_url

TMDB_RE = re.compile(r"(?:themoviedb|tmdb)(?:://|/)?(\d+)", re.I)
TVDB_RE = re.compile(r"(?:thetvdb|tvdb)(?:://(?:series/)?)?(\d+)", re.I)
IMDB_RE = re.compile(r"(tt\d{5,})", re.I)


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", False):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def rating_key_score(row: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    return (
        1 if row.get("in_library") else 0,
        1 if as_int(row.get("file_size")) > 0 or row.get("thumb") else 0,
        as_int(row.get("rating_key")),
        as_int(row.get("last_played")),
        as_int(row.get("play_count")),
        as_int(row.get("added_at")),
    )


def pick_live_rating_key(options: dict[str, dict[str, Any]], exists) -> str:
    rows = [row for row in options.values() if row.get("rating_key")]
    if not rows:
        return ""
    rows.sort(key=rating_key_score, reverse=True)
    winner = rows[0]
    needs_check = (
        len(rows) > 1
        or not winner.get("in_library")
        or (as_int(winner.get("file_size")) <= 0 and not winner.get("thumb"))
    )
    if not needs_check:
        return str(winner["rating_key"])
    for row in rows:
        key = str(row["rating_key"])
        if exists(key):
            return key
    return ""


def parse_ids(blob: Any) -> dict[str, Any]:
    text = blob if isinstance(blob, str) else " ".join(str(x) for x in (blob or []))
    tmdb = TMDB_RE.search(text or "")
    tvdb = TVDB_RE.search(text or "")
    imdb = IMDB_RE.search(text or "")
    return {
        "tmdb_id": int(tmdb.group(1)) if tmdb else 0,
        "tvdb_id": int(tvdb.group(1)) if tvdb else 0,
        "imdb_id": imdb.group(1) if imdb else "",
    }


class Tautulli:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.api_key = api_key

    def _cmd(self, cmd: str, **params: Any) -> Any:
        payload = json_get(
            f"{self.url}/api/v2",
            params={"apikey": self.api_key, "cmd": cmd, **params},
        )
        response = (payload or {}).get("response") or {}
        if response.get("result") == "error":
            raise RuntimeError(response.get("message") or "Tautulli error")
        return response.get("data")

    def test(self) -> str:
        data = self._cmd("arnold")
        return str(data or "ok")

    def libraries(self) -> list[dict[str, Any]]:
        data = self._cmd("get_libraries") or []
        return data if isinstance(data, list) else []

    def library_media(self, section_id: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        length = 1000
        while True:
            data = self._cmd("get_library_media_info", section_id=section_id, start=start, length=length) or {}
            chunk = data.get("data") if isinstance(data, dict) else data
            chunk = chunk or []
            rows.extend(chunk)
            if len(chunk) < length:
                break
            start += length
        return rows

    def history(self, on_progress=None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        length = 1000
        while True:
            data = self._cmd("get_history", start=start, length=length) or {}
            chunk = data.get("data") if isinstance(data, dict) else data
            chunk = chunk or []
            rows.extend(chunk)
            if on_progress:
                on_progress(len(rows))
            if len(chunk) < length:
                break
            start += length
            if start > 100_000:
                break
        return rows

    def users(self) -> list[dict[str, Any]]:
        data = self._cmd("get_users") or []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            rows = data.get("data") or data.get("users") or []
            return rows if isinstance(rows, list) else []
        return []

    def metadata(self, rating_key: str) -> dict[str, Any] | None:
        try:
            data = self._cmd("get_metadata", rating_key=str(rating_key))
        except Exception:
            return None
        if isinstance(data, dict) and (data.get("rating_key") or data.get("title") or data.get("guid")):
            return data
        return None

    def rating_map(self) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        for lib in self.libraries():
            section_id = lib.get("section_id")
            if section_id is None:
                continue
            for row in self.library_media(section_id):
                media_type = row.get("media_type") or lib.get("section_type") or ""
                if str(media_type).lower() in {"season", "episode", "track", "album", "photo"}:
                    continue
                ids = parse_ids(row.get("guid") or row.get("guids") or "")
                rating_key = str(row.get("rating_key") or "")
                if rating_key:
                    mapping[rating_key] = {
                        **ids,
                        "title": row.get("title") or "",
                        "year": row.get("year"),
                        "media_type": media_type,
                        "play_count": as_int(row.get("play_count")),
                        "last_played": as_int(row.get("last_played")),
                        "added_at": as_int(row.get("added_at")),
                        "file_size": as_int(row.get("file_size")),
                        "thumb": row.get("thumb") or "",
                        "in_library": True,
                    }
        return mapping
