from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from typing import Any

from .art import warm_cache
from .db import connect
from .logs import add_log
from .match import CatalogIndex, parse_year
from .services.arr import pick_rating, poster_from
from .services.clients import radarr, seerr, sonarr, tautulli, tracearr
from .services.tautulli import parse_ids

_lock = threading.Lock()
_job: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "step": "",
    "current": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
}


def job_status() -> dict[str, Any]:
    data = dict(_job)
    total = int(data.get("total") or 0)
    current = int(data.get("current") or 0)
    if data.get("status") == "running" and total:
        data["percent"] = min(100, int((current * 100) / total))
    elif data.get("status") == "running":
        data["percent"] = None
    else:
        data["percent"] = 100 if data.get("status") == "idle" else 0
    return data


def restore_job() -> None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_state WHERE id = 1").fetchone()
    if not row:
        return
    data = dict(row)
    if data.get("status") == "running":
        _set_job(
            status="idle",
            message="Previous sync was interrupted",
            step="",
            current=0,
            total=0,
            finished_at=int(time.time()),
        )
        add_log("Previous sync was interrupted", level="warn", category="sync", action="interrupted")
        return
    _job.update(
        {
            "status": data.get("status") or "idle",
            "message": data.get("message") or "",
            "step": data.get("step") or "",
            "current": data.get("progress_current") or 0,
            "total": data.get("progress_total") or 0,
            "started_at": data.get("started_at"),
            "finished_at": data.get("finished_at"),
        }
    )


def _set_job(**kwargs: Any) -> None:
    _job.update(kwargs)
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_state
            SET status = ?, message = ?, started_at = ?, finished_at = ?,
                step = ?, progress_current = ?, progress_total = ?
            WHERE id = 1
            """,
            (
                _job.get("status"),
                _job.get("message"),
                _job.get("started_at"),
                _job.get("finished_at"),
                _job.get("step") or "",
                int(_job.get("current") or 0),
                int(_job.get("total") or 0),
            ),
        )


def start_sync() -> dict[str, Any]:
    with _lock:
        if _job.get("status") == "running":
            return job_status()
        _set_job(
            status="running",
            message="Starting…",
            step="start",
            current=0,
            total=0,
            started_at=int(time.time()),
            finished_at=None,
        )
    add_log("Library sync started", category="sync", action="start")
    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()
    return job_status()


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


def _alt_titles(item: dict[str, Any]) -> list[str]:
    titles = [
        item.get("originalTitle") or item.get("original_title") or "",
        item.get("sortTitle") or item.get("sort_title") or "",
        item.get("cleanTitle") or item.get("clean_title") or "",
    ]
    for alt in item.get("alternateTitles") or item.get("alternate_titles") or []:
        if isinstance(alt, dict):
            titles.append(alt.get("title") or alt.get("alternateTitle") or "")
        elif alt:
            titles.append(str(alt))
    return [title for title in titles if title]


def _progress(message: str, *, step: str, current: int = 0, total: int = 0) -> None:
    _set_job(message=message, step=step, current=current, total=total)


def _run_sync() -> None:
    try:
        catalog: dict[tuple[str, int, int], dict[str, Any]] = {}
        index = CatalogIndex()

        def upsert_base(item: dict[str, Any], extra_titles: list[str] | None = None) -> tuple[str, int, int]:
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
                "rating": None,
                "rating_votes": 0,
                "rating_source": "",
            }
            for field in current:
                if item.get(field) not in (None, "", 0, []):
                    current[field] = item[field]
            catalog[key] = current
            index.add(key, current, extra_titles or [])
            return key

        _progress("Loading Radarr…", step="radarr")
        if client := radarr():
            movies = client.movies()
            total = len(movies)
            _progress(f"Loading Radarr… 0/{total}", step="radarr", current=0, total=total)
            for i, movie in enumerate(movies, 1):
                rating, votes, source = pick_rating(movie.get("ratings"))
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
                        "rating": rating,
                        "rating_votes": votes,
                        "rating_source": source,
                    },
                    _alt_titles(movie),
                )
                if i == total or i % 75 == 0:
                    _progress(f"Loading Radarr… {i}/{total}", step="radarr", current=i, total=total)
            add_log(f"Loaded {total} movies from Radarr", category="sync", action="radarr")

        _progress("Loading Sonarr…", step="sonarr")
        if client := sonarr():
            shows = client.series()
            total = len(shows)
            _progress(f"Loading Sonarr… 0/{total}", step="sonarr", current=0, total=total)
            for i, show in enumerate(shows, 1):
                stats = show.get("statistics") or {}
                rating, votes, source = pick_rating(show.get("ratings"))
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
                        "rating": rating,
                        "rating_votes": votes,
                        "rating_source": source,
                    },
                    _alt_titles(show),
                )
                if i == total or i % 40 == 0:
                    _progress(f"Loading Sonarr… {i}/{total}", step="sonarr", current=i, total=total)
            add_log(f"Loaded {total} series from Sonarr", category="sync", action="sonarr")

        _progress("Loading Seerr requests…", step="seerr")
        if client := seerr():
            for req in client.requests():
                media = req.get("media") or {}
                media_type = _media_type(media.get("mediaType") or req.get("type"), "movie")
                tmdb_id = int(media.get("tmdbId") or req.get("tmdbId") or 0)
                tvdb_id = int(media.get("tvdbId") or 0)
                key = (media_type, tmdb_id, tvdb_id if media_type == "tv" else 0)
                if key not in catalog:
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

        def attach_play(key: tuple[str, int, int] | None, event: dict[str, Any], unmatched: set[str]) -> None:
            if key:
                plays[key].append(event)
                return
            label = event.get("title") or "Unknown title"
            year = event.get("year")
            unmatched.add(f"{label}{f' ({year})' if year else ''}")

        def log_unmatched(source: str, unmatched: set[str], matched: int, total: int) -> None:
            add_log(
                f"Matched {matched}/{total} {source} plays",
                category="sync",
                action=f"{source}_match",
                detail={"matched": matched, "total": total, "unmatched": total - matched},
            )
            if not unmatched:
                return
            sample = sorted(unmatched)[:40]
            add_log(
                f"{len(unmatched)} unmatched {source} titles (articles like “The” are ignored; check IDs if this looks wrong)",
                level="warn",
                category="match",
                action="unmatched",
                detail={"source": source, "titles": sample, "count": len(unmatched)},
            )

        _progress("Loading Tautulli history…", step="tautulli")
        if client := tautulli():
            try:
                _progress("Loading Tautulli library IDs…", step="tautulli")
                rating_map = client.rating_map()
            except Exception as exc:
                rating_map = {}
                add_log(f"Tautulli library map failed: {exc}", level="warn", category="sync", action="tautulli")

            def tautulli_progress(fetched: int) -> None:
                _progress(f"Fetching Tautulli history… {fetched}", step="tautulli", current=fetched, total=0)

            rows = client.history(on_progress=tautulli_progress)
            total = len(rows)
            unmatched: set[str] = set()
            matched = 0
            _progress(f"Matching Tautulli history… 0/{total}", step="tautulli", current=0, total=total)
            for i, row in enumerate(rows, 1):
                media_type = _media_type(row.get("media_type"), "movie")
                rating_key = str(
                    row.get("grandparent_rating_key")
                    if media_type == "tv"
                    else row.get("rating_key") or ""
                )
                ids = rating_map.get(rating_key) or parse_ids(row.get("guid") or row.get("guids") or "")
                mapped = rating_map.get(rating_key) or {}
                title = (
                    row.get("grandparent_title")
                    if media_type == "tv"
                    else row.get("title") or row.get("full_title") or ""
                )
                titles = [
                    title,
                    row.get("full_title") or "",
                    row.get("grandparent_title") or "",
                    mapped.get("title") or "",
                ]
                year = ids.get("year") or mapped.get("year") or row.get("year") or parse_year(row.get("full_title") or "")
                key = index.resolve(
                    media_type,
                    int(ids.get("tmdb_id") or mapped.get("tmdb_id") or 0),
                    int(ids.get("tvdb_id") or mapped.get("tvdb_id") or 0),
                    str(ids.get("imdb_id") or mapped.get("imdb_id") or ""),
                    titles,
                    year,
                )
                if key:
                    matched += 1
                attach_play(
                    key,
                    {
                        "user": _user_name(row) or row.get("friendly_name") or "Unknown",
                        "watched_at": _unix(row.get("date")),
                        "source": "tautulli",
                        "title": title or mapped.get("title") or row.get("full_title") or "Unknown",
                        "year": parse_year(year),
                    },
                    unmatched,
                )
                if i == total or i % 250 == 0:
                    _progress(f"Matching Tautulli history… {i}/{total}", step="tautulli", current=i, total=total)
            log_unmatched("Tautulli", unmatched, matched, total)

        _progress("Loading Tracearr history…", step="tracearr")
        if client := tracearr():
            def tracearr_progress(fetched: int) -> None:
                _progress(f"Fetching Tracearr history… {fetched}", step="tracearr", current=fetched, total=0)

            rows = client.history(on_progress=tracearr_progress)
            total = len(rows)
            unmatched = set()
            matched = 0
            _progress(f"Matching Tracearr history… 0/{total}", step="tracearr", current=0, total=total)
            for i, row in enumerate(rows, 1):
                media = row.get("media") or {}
                media_type = _media_type(
                    row.get("mediaType") or row.get("media_type") or media.get("type"),
                    "movie",
                )
                show_title = (
                    row.get("showTitle")
                    or row.get("grandparentTitle")
                    or media.get("showTitle")
                    or ""
                )
                title = show_title if media_type == "tv" and show_title else (
                    show_title
                    or row.get("title")
                    or media.get("title")
                    or row.get("name")
                    or media.get("name")
                    or ""
                )
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
                imdb_id = row.get("imdbId") or row.get("imdb_id") or media.get("imdbId") or ""
                year = row.get("year") or media.get("year") or row.get("productionYear")
                key = index.resolve(
                    media_type,
                    int(tmdb_id or 0),
                    int(tvdb_id or 0),
                    str(imdb_id or ""),
                    [
                        title,
                        row.get("originalTitle") or "",
                        media.get("originalTitle") or "",
                        row.get("sortTitle") or "",
                        media.get("name") or "",
                    ],
                    year,
                )
                if key:
                    matched += 1
                attach_play(
                    key,
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
                        "title": title or "Unknown",
                        "year": parse_year(year),
                    },
                    unmatched,
                )
                if i == total or i % 250 == 0:
                    _progress(f"Matching Tracearr history… {i}/{total}", step="tracearr", current=i, total=total)
            log_unmatched("Tracearr", unmatched, matched, total)

        _progress("Deduping watch history…", step="save")
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

        _progress(f"Saving {len(records)} titles…", step="save", current=1, total=1)
        with connect() as conn:
            conn.execute("DELETE FROM media")
            conn.executemany(
                """
                INSERT INTO media (
                    media_type, tmdb_id, tvdb_id, imdb_id, title, year, poster_url, size_bytes,
                    radarr_id, sonarr_id, seerr_media_id, requested_by, requested_at,
                    last_watched_at, play_count, watcher_count, watchers_json, sources_json, path, title_slug,
                    rating, rating_votes, rating_source
                ) VALUES (
                    :media_type, :tmdb_id, :tvdb_id, :imdb_id, :title, :year, :poster_url, :size_bytes,
                    :radarr_id, :sonarr_id, :seerr_media_id, :requested_by, :requested_at,
                    :last_watched_at, :play_count, :watcher_count, :watchers_json, :sources_json, :path, :title_slug,
                    :rating, :rating_votes, :rating_source
                )
                """,
                records,
            )
        watched = sum(1 for row in records if row["play_count"])
        message = f"Synced {len(records)} titles ({watched} with watch history)"
        _set_job(status="idle", message=message, step="", current=0, total=0, finished_at=int(time.time()))
        add_log(message, category="sync", action="complete", detail={"titles": len(records), "watched": watched})
        warm_cache()
    except Exception as exc:
        _set_job(status="error", message=str(exc), step="", current=0, total=0, finished_at=int(time.time()))
        add_log(str(exc), level="error", category="sync", action="error")
