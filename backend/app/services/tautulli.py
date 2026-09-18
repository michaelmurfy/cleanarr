from __future__ import annotations

import re
from typing import Any

from .http import json_get, tidy_url

TMDB_RE = re.compile(r"(?:themoviedb|tmdb)(?:://|/)?(\d+)", re.I)
TVDB_RE = re.compile(r"(?:thetvdb|tvdb)(?:://(?:series/)?)?(\d+)", re.I)
IMDB_RE = re.compile(r"(tt\d{5,})", re.I)


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

    def history(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        length = 1000
        while True:
            data = self._cmd("get_history", start=start, length=length) or {}
            chunk = data.get("data") if isinstance(data, dict) else data
            chunk = chunk or []
            rows.extend(chunk)
            if len(chunk) < length:
                break
            start += length
            if start > 100_000:
                break
        return rows

    def rating_map(self) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        for lib in self.libraries():
            section_id = lib.get("section_id")
            if section_id is None:
                continue
            for row in self.library_media(section_id):
                ids = parse_ids(row.get("guid") or row.get("guids") or "")
                rating_key = str(row.get("rating_key") or "")
                if rating_key:
                    mapping[rating_key] = {
                        **ids,
                        "title": row.get("title") or "",
                        "year": row.get("year"),
                        "media_type": row.get("media_type") or lib.get("section_type") or "",
                    }
        return mapping
