from __future__ import annotations

import json
import re
from typing import Any

from .http import json_get, json_request, tidy_url

SKIP_LIBRARIES = {"music", "musicvideos", "books", "photos", "boxsets", "playlists", "homevideos"}
TMDB_RE = re.compile(r"(?:tmdb|themoviedb)[:\-]?(\d+)", re.I)
TVDB_RE = re.compile(r"(?:tvdb|thetvdb)[:\-]?(\d+)", re.I)
IMDB_RE = re.compile(r"(tt\d{5,})", re.I)
HISTORY_ROW_CAP = 200_000


def provider_ids(item: dict[str, Any]) -> dict[str, Any]:
    """Pull TMDB/TVDB/IMDB out of Jellyfin-style ProviderIds or a guid blob."""
    providers = _pick(item, "ProviderIds", "providerIds", "provider_ids") or {}
    if not isinstance(providers, dict):
        providers = {}
    lower = {str(key).lower(): value for key, value in providers.items()}
    tmdb = lower.get("tmdb") or lower.get("themoviedb") or 0
    tvdb = lower.get("tvdb") or lower.get("thetvdb") or 0
    imdb = lower.get("imdb") or ""
    blob = " ".join(str(v) for v in (_pick(item, "Guid", "guid", "ExternalUrls", "Path") or "", *providers.values()))
    if not tmdb:
        match = TMDB_RE.search(blob)
        tmdb = match.group(1) if match else 0
    if not tvdb:
        match = TVDB_RE.search(blob)
        tvdb = match.group(1) if match else 0
    if not imdb:
        match = IMDB_RE.search(blob)
        imdb = match.group(1) if match else ""
    try:
        tmdb_i = int(tmdb or 0)
    except (TypeError, ValueError):
        tmdb_i = 0
    try:
        tvdb_i = int(tvdb or 0)
    except (TypeError, ValueError):
        tvdb_i = 0
    return {"tmdb_id": tmdb_i, "tvdb_id": tvdb_i, "imdb_id": str(imdb or "")}


def _pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    lower = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lower.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def play_title(row: dict[str, Any]) -> tuple[str, str]:
    series = str(_pick(row, "SeriesName", "seriesName", "series_name") or "").strip()
    episode = str(_pick(row, "NowPlayingItemName", "Name", "name", "title") or "").strip()
    if series:
        return "tv", series
    return "movie", episode


def expand_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        nested = row.get("results")
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except Exception:
                nested = None
        if isinstance(nested, list) and nested:
            for item in nested:
                if isinstance(item, dict):
                    merged = {**row, **item}
                    merged.pop("results", None)
                    out.append(merged)
            continue
        out.append(row)
    return out


class Jellystat:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.history_truncated = False
        # Only x-api-token: sending it twice trips Jellystat's duplicate-header
        # check (403), and an Authorization header makes it reject the request (401).
        self.headers = {
            "x-api-token": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return json_get(f"{self.url}{path}", headers=self.headers, params=params)

    def _post(self, path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        return json_request("POST", f"{self.url}{path}", headers=self.headers, params=params, json=payload or {})

    def test(self) -> str:
        data = self._get("/api/getLibraries")
        if isinstance(data, list):
            return f"{len(data)} libraries"
        return "ok"

    def libraries(self) -> list[dict[str, Any]]:
        data = self._get("/api/getLibraries") or []
        return data if isinstance(data, list) else []

    def library_items(self, library_id: Any) -> list[dict[str, Any]]:
        data = self._post("/api/getLibraryItems", {"libraryid": library_id}) or []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            chunk = data.get("results") or data.get("data") or data.get("items") or []
            return chunk if isinstance(chunk, list) else []
        return []

    def users(self) -> list[dict[str, Any]]:
        try:
            data = self._get("/stats/getAllUserActivity") or []
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def library_map(self) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        for lib in self.libraries():
            library_id = _pick(lib, "Id", "id")
            collection = str(_pick(lib, "CollectionType", "collectionType") or "").lower()
            if collection in SKIP_LIBRARIES or library_id in (None, ""):
                continue
            fallback = "tv" if collection in {"tvshows", "tv", "series"} else "movie"
            try:
                items = self.library_items(library_id)
            except Exception:
                continue
            for item in items:
                item_id = str(_pick(item, "Id", "id") or "")
                if not item_id:
                    continue
                kind = str(_pick(item, "Type", "type") or "").lower()
                if kind in {"season", "episode", "audio", "musicalbum", "musicvideo", "book", "photo"}:
                    continue
                media_type = "tv" if kind in {"series", "show", "tv"} else fallback if kind else fallback
                if kind == "movie":
                    media_type = "movie"
                ids = provider_ids(item)
                mapping[item_id] = {
                    "title": str(_pick(item, "Name", "name") or ""),
                    "year": _pick(item, "ProductionYear", "productionYear", "year"),
                    "media_type": media_type,
                    **ids,
                }
        return mapping

    def history(self, on_progress=None) -> list[dict[str, Any]]:
        self.history_truncated = False
        last_error: Exception | None = None
        for path, expand in (
            ("/stats/getPlaybackActivity", False),
            ("/api/getHistory", True),
        ):
            try:
                rows = self._paged(path, on_progress=on_progress)
                if rows:
                    return expand_history(rows) if expand else rows
            except Exception as exc:
                last_error = exc
        try:
            rows = self._paged_post("/api/getHistory", on_progress=on_progress)
            if rows:
                return expand_history(rows)
        except Exception as exc:
            last_error = exc
        if last_error:
            raise last_error
        return []

    def _paged(self, path: str, on_progress=None, size: int = 200) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        for _ in range(500):
            data = self._get(path, params={"page": page, "size": size})
            chunk, pages = _page_chunk(data)
            rows.extend(chunk)
            if on_progress:
                on_progress(len(rows))
            if not chunk or page >= pages or len(chunk) < size:
                break
            page += 1
            if len(rows) >= HISTORY_ROW_CAP:
                self.history_truncated = True
                break
        return rows

    def _paged_post(self, path: str, on_progress=None, size: int = 200) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        for _ in range(500):
            data = self._post(path, {"page": page, "size": size})
            chunk, pages = _page_chunk(data)
            rows.extend(chunk)
            if on_progress:
                on_progress(len(rows))
            if not chunk or page >= pages or len(chunk) < size:
                break
            page += 1
            if len(rows) >= HISTORY_ROW_CAP:
                self.history_truncated = True
                break
        return rows


def _page_chunk(data: Any) -> tuple[list[dict[str, Any]], int]:
    if data is None:
        return [], 1
    if isinstance(data, list):
        return data, 1
    chunk = data.get("results") or data.get("data") or data.get("items") or data.get("history") or []
    if not isinstance(chunk, list):
        chunk = []
    pages = int(data.get("pages") or data.get("total_pages") or 1)
    try:
        current = int(data.get("current_page") or data.get("page") or 1)
    except (TypeError, ValueError):
        current = 1
    if pages < current:
        pages = current
    return chunk, max(1, pages)
