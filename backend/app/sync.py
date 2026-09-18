from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from typing import Any

from .db import connect
from .services.arr import poster_from
from .services.clients import radarr, seerr, sonarr, tautulli, tracearr
from .services.tautulli import parse_ids

_lock = threading.Lock()
_job: dict[str, Any] = {"status": "idle", "message": "", "started_at": None, "finished_at": None}


def job_status() -> dict[str, Any]:
    return dict(_job)


def _set_job(**kwargs: Any) -> None:
    _job.update(kwargs)
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_state
            SET status = ?, message = ?, started_at = ?, finished_at = ?
            WHERE id = 1
            """,
            (_job.get("status"), _job.get("message"), _job.get("started_at"), _job.get("finished_at")),
        )


def start_sync() -> dict[str, Any]:
    with _lock:
        if _job.get("status") == "running":
            return job_status()
        _set_job(status="running", message="Starting…", started_at=int(time.time()), finished_at=None)
    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()
    return job_status()


def _norm(title: str) -> str:
    value = (title or "").lower()
    value = re.sub(r"\s*\(\d{4}\)\s*", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _unix(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts if ts > 1_000_000_000 else ts
    text = str(value)
    if text.isdigit():
        return int(text)
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _user_name(row: dict[str, Any]) -> str:
    user = row.get("user") or row.get("friendly_name") or row.get("username") or ""
    if isinstance(user, dict):
        return (
            user.get("displayName")
            or user.get("username")
            or user.get("name")
            or user.get("email")
            or ""
        )
    return str(user or "").strip()


def _media_type(raw: str | None, fallback: str = "movie") -> str:
    value = (raw or fallback or "").lower()
    if value in {"episode", "show", "series", "tv", "season"}:
        return "tv"
    if value in {"movie", "movies"}:
        return "movie"
    return fallback


def _run_sync() -> None:
    try:
        catalog: dict[tuple[str, int, int], dict[str, Any]] = {}
        title_index: dict[tuple[str, str, int | None], tuple[str, int, int]] = {}

        def upsert_base(item: dict[str, Any]) -> tuple[str, int, int]:
            media_type = item["media_type"]
            tmdb_id = int(item.get("tmdb_id") or 0)
            tvdb_id = int(item.get("tvdb_id") or 0)
            key = (media_type, tmdb_id, tvdb_id)
            current = catalog.get(key) or {
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "tvdb_id": tvdb_id,
                "imdb_id": "",
                "title": "",
                "year": None,
                "poster_url": "",
                "size_bytes": 0,
                "radarr_id": None,
                "sonarr_id": None,
                "seerr_media_id": None,
                "requested_by": "",
                "requested_at": "",
                "path": "",
                "title_slug": "",
            }
            for field in current:
                if item.get(field) not in (None, "", 0, []):
                    current[field] = item[field]
            catalog[key] = current
            title_index[(_norm(current["title"]), media_type, current.get("year"))] = key
            title_index[(_norm(current["title"]), media_type, None)] = key
            return key

        _set_job(message="Loading Radarr…")
        if client := radarr():
            for movie in client.movies():
                upsert_base(
                    {
                        "media_type": "movie",
                        "tmdb_id": movie.get("tmdbId") or 0,
                        "tvdb_id": 0,
                        "imdb_id": movie.get("imdbId") or "",
                        "title": movie.get("title") or "",
                        "year": movie.get("year"),
                        "poster_url": poster_from(movie.get("images")),
                        "size_bytes": movie.get("sizeOnDisk") or 0,
                        "radarr_id": movie.get("id"),
                        "path": movie.get("path") or "",
                        "title_slug": movie.get("titleSlug") or "",
                    }
                )

        _set_job(message="Loading Sonarr…")
        if client := sonarr():
            for show in client.series():
                stats = show.get("statistics") or {}
                upsert_base(
                    {
                        "media_type": "tv",
                        "tmdb_id": show.get("tmdbId") or 0,
                        "tvdb_id": show.get("tvdbId") or 0,
                        "imdb_id": show.get("imdbId") or "",
                        "title": show.get("title") or "",
                        "year": show.get("year"),
                        "poster_url": poster_from(show.get("images")),
                        "size_bytes": stats.get("sizeOnDisk") or show.get("sizeOnDisk") or 0,
                        "sonarr_id": show.get("id"),
                        "path": show.get("path") or "",
                        "title_slug": show.get("titleSlug") or "",
                    }
                )

        _set_job(message="Loading Seerr requests…")
        if client := seerr():
            for req in client.requests():
                media = req.get("media") or {}
                media_type = _media_type(media.get("mediaType") or req.get("type"), "movie")
                tmdb_id = int(media.get("tmdbId") or req.get("tmdbId") or 0)
                tvdb_id = int(media.get("tvdbId") or 0)
                key = (media_type, tmdb_id, tvdb_id if media_type == "tv" else 0)
                if key not in catalog:
                    # Try tmdb-only match for TV
                    alt = next((k for k in catalog if k[0] == media_type and tmdb_id and k[1] == tmdb_id), None)
                    key = alt or key
                    if key not in catalog:
                        continue
                requested_by = _user_name(req.get("requestedBy") or {})
                catalog[key]["requested_by"] = requested_by
                catalog[key]["requested_at"] = req.get("createdAt") or ""
                catalog[key]["seerr_media_id"] = media.get("id") or catalog[key]["seerr_media_id"]
            for media in client.media():
                media_type = _media_type(media.get("mediaType"), "movie")
                tmdb_id = int(media.get("tmdbId") or 0)
                tvdb_id = int(media.get("tvdbId") or 0)
                key = next(
                    (
                        k
                        for k in catalog
                        if k[0] == media_type and ((tmdb_id and k[1] == tmdb_id) or (tvdb_id and k[2] == tvdb_id))
                    ),
                    None,
                )
                if key:
                    catalog[key]["seerr_media_id"] = media.get("id")

        plays: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
        rating_map: dict[str, dict[str, Any]] = {}

        def resolve_key(meta: dict[str, Any]) -> tuple[str, int, int] | None:
            media_type = _media_type(meta.get("media_type"), "movie")
            tmdb_id = int(meta.get("tmdb_id") or 0)
            tvdb_id = int(meta.get("tvdb_id") or 0)
            if tmdb_id:
                match = next((k for k in catalog if k[0] == media_type and k[1] == tmdb_id), None)
                if match:
                    return match
            if tvdb_id:
                match = next((k for k in catalog if k[0] == media_type and k[2] == tvdb_id), None)
                if match:
                    return match
            title = _norm(meta.get("title") or "")
            year = meta.get("year")
            if title:
                return title_index.get((title, media_type, year)) or title_index.get((title, media_type, None))
            return None

        _set_job(message="Loading Tautulli history…")
        if client := tautulli():
            try:
                rating_map = client.rating_map()
            except Exception:
                rating_map = {}
            for row in client.history():
                media_type = _media_type(row.get("media_type"), "movie")
                rating_key = str(
                    row.get("grandparent_rating_key")
                    if media_type == "tv"
                    else row.get("rating_key") or ""
                )
                ids = rating_map.get(rating_key) or parse_ids(row.get("guid") or row.get("guids") or "")
                title = (
                    row.get("grandparent_title")
                    if media_type == "tv"
                    else row.get("title") or row.get("full_title") or ""
                )
                year = ids.get("year") or row.get("year")
                key = resolve_key(
                    {
                        "media_type": media_type,
                        "tmdb_id": ids.get("tmdb_id") or 0,
                        "tvdb_id": ids.get("tvdb_id") or 0,
                        "title": title,
                        "year": int(year) if str(year or "").isdigit() else None,
                    }
                )
                if not key:
                    continue
                plays[key].append(
                    {
                        "user": _user_name(row) or row.get("friendly_name") or "Unknown",
                        "watched_at": _unix(row.get("date")),
                        "source": "tautulli",
                    }
                )

        _set_job(message="Loading Tracearr history…")
        if client := tracearr():
            for row in client.history():
                media = row.get("media") or {}
                media_type = _media_type(
                    row.get("mediaType") or row.get("media_type") or media.get("type"),
                    "movie",
                )
                title = (
                    row.get("showTitle")
                    or row.get("grandparentTitle")
                    or media.get("showTitle")
                    or row.get("title")
                    or media.get("title")
                    or ""
                )
                if media_type == "tv" and row.get("showTitle"):
                    title = row.get("showTitle")
                tmdb_id = (
                    row.get("showTmdbId")
                    or row.get("show_tmdb_id")
                    or row.get("tmdbId")
                    or row.get("tmdb_id")
                    or media.get("tmdbId")
                    or 0
                )
                tvdb_id = (
                    row.get("showTvdbId")
                    or row.get("tvdbId")
                    or row.get("tvdb_id")
                    or media.get("tvdbId")
                    or 0
                )
                year = row.get("year") or media.get("year")
                key = resolve_key(
                    {
                        "media_type": media_type,
                        "tmdb_id": int(tmdb_id or 0),
                        "tvdb_id": int(tvdb_id or 0),
                        "title": title,
                        "year": int(year) if str(year or "").isdigit() else None,
                    }
                )
                if not key:
                    continue
                plays[key].append(
                    {
                        "user": _user_name(row) or "Unknown",
                        "watched_at": _unix(
                            row.get("viewedAt")
                            or row.get("watchedAt")
                            or row.get("startedAt")
                            or row.get("createdAt")
                            or row.get("date")
                        ),
                        "source": "tracearr",
                    }
                )

        _set_job(message="Deduping watch history…")
        records = []
        for key, item in catalog.items():
            events = plays.get(key) or []
            seen: set[tuple[str, int]] = set()
            unique_events: list[dict[str, Any]] = []
            for event in events:
                user = re.sub(r"\s+", " ", (event.get("user") or "Unknown").strip())
                ts = event.get("watched_at") or 0
                bucket = (ts or 0) // 7200
                stamp = (user.lower(), bucket)
                if stamp in seen:
                    continue
                seen.add(stamp)
                unique_events.append({**event, "user": user})
            watchers: dict[str, dict[str, Any]] = {}
            sources = set()
            last_watched = None
            for event in unique_events:
                user = event["user"]
                watchers.setdefault(user, {"user": user, "plays": 0, "last_watched_at": None})
                watchers[user]["plays"] += 1
                ts = event.get("watched_at")
                if ts and (watchers[user]["last_watched_at"] or 0) < ts:
                    watchers[user]["last_watched_at"] = ts
                if ts and (last_watched or 0) < ts:
                    last_watched = ts
                if event.get("source"):
                    sources.add(event["source"])
            records.append(
                {
                    **item,
                    "last_watched_at": last_watched,
                    "play_count": len(unique_events),
                    "watcher_count": len(watchers),
                    "watchers_json": json.dumps(sorted(watchers.values(), key=lambda x: -x["plays"])),
                    "sources_json": json.dumps(sorted(sources)),
                }
            )

        with connect() as conn:
            conn.execute("DELETE FROM media")
            conn.executemany(
                """
                INSERT INTO media (
                    media_type, tmdb_id, tvdb_id, imdb_id, title, year, poster_url, size_bytes,
                    radarr_id, sonarr_id, seerr_media_id, requested_by, requested_at,
                    last_watched_at, play_count, watcher_count, watchers_json, sources_json, path, title_slug
                ) VALUES (
                    :media_type, :tmdb_id, :tvdb_id, :imdb_id, :title, :year, :poster_url, :size_bytes,
                    :radarr_id, :sonarr_id, :seerr_media_id, :requested_by, :requested_at,
                    :last_watched_at, :play_count, :watcher_count, :watchers_json, :sources_json, :path, :title_slug
                )
                """,
                records,
            )
        _set_job(status="idle", message=f"Synced {len(records)} titles", finished_at=int(time.time()))
    except Exception as exc:
        _set_job(status="error", message=str(exc), finished_at=int(time.time()))
