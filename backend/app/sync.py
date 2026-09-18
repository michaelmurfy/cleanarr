from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from typing import Any

from .art import warm_cache
from .db import connect, get_setting
from .identity import UserDirectory
from .logs import add_log
from .match import CatalogIndex, parse_year
from .services.arr import movie_availability, pick_rating, poster_from, series_availability
from .services.clients import radarr, seerr, sonarr, tautulli, tracearr
from .services.seerr import media_claimed, media_in_flight, request_is_open, request_was_made
from .services.tautulli import parse_ids

UNKNOWN_LABELS = {"unknown", "unknown title", "unknown request", "n/a", "none", "null"}

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


def _usable_title(title: Any, tmdb_id: int = 0) -> str | None:
    label = re.sub(r"\s+", " ", str(title or "")).strip()
    lowered = label.lower()
    if lowered in UNKNOWN_LABELS or lowered.startswith("unknown"):
        return None
    if label:
        return label
    return f"TMDB {int(tmdb_id)}" if tmdb_id else None


def _progress(message: str, *, step: str, current: int = 0, total: int = 0) -> None:
    _set_job(message=message, step=step, current=current, total=total)


def _run_sync() -> None:
    try:
        catalog: dict[tuple[str, int, int], dict[str, Any]] = {}
        index = CatalogIndex()
        directory = UserDirectory()
        unmatched_rows: dict[tuple, dict[str, Any]] = {}

        def note_unmatched(
            source: str,
            media_type: str,
            title: str,
            year: Any,
            *,
            reason: str,
            plays: int = 1,
            tmdb_id: int = 0,
            tvdb_id: int = 0,
            seerr_media_id: Any = None,
            requested_by: str = "",
            requested_at: str = "",
            kind: str = "",
        ) -> None:
            label = _usable_title(title, tmdb_id)
            if not label:
                return
            try:
                year_i = int(year or 0)
            except (TypeError, ValueError):
                year_i = 0
            key = (kind or source, media_type, int(tmdb_id or 0), int(tvdb_id or 0), label.lower(), year_i)
            row = unmatched_rows.setdefault(
                key,
                {
                    "source": source,
                    "media_type": media_type,
                    "title": label,
                    "year": year_i,
                    "plays": 0,
                    "reason": reason,
                    "tmdb_id": int(tmdb_id or 0),
                    "tvdb_id": int(tvdb_id or 0),
                    "seerr_media_id": seerr_media_id,
                    "requested_by": requested_by or "",
                    "requested_at": requested_at or "",
                    "kind": kind,
                },
            )
            row["plays"] += plays
            if requested_by and not row.get("requested_by"):
                row["requested_by"] = requested_by
            if requested_at and not row.get("requested_at"):
                row["requested_at"] = requested_at
            if seerr_media_id and not row.get("seerr_media_id"):
                row["seerr_media_id"] = seerr_media_id

        _progress("Loading user directories…", step="users")
        if client := tautulli():
            try:
                for user in client.users():
                    directory.ingest_tautulli(user)
            except Exception as exc:
                add_log(f"Tautulli users failed: {exc}", level="warn", category="sync", action="users")
        seerr_users_by_id: dict[int, dict[str, Any]] = {}
        if client := seerr():
            try:
                for user in client.users():
                    directory.ingest_seerr(user)
                    if user.get("id") is not None:
                        seerr_users_by_id[int(user["id"])] = user
            except Exception as exc:
                add_log(f"Seerr users failed: {exc}", level="warn", category="sync", action="users")

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
                "tautulli_rating_key": "",
                "availability": "downloaded",
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
                        "availability": movie_availability(movie),
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
                        "availability": series_availability(show),
                    },
                    _alt_titles(show),
                )
                if i == total or i % 40 == 0:
                    _progress(f"Loading Sonarr… {i}/{total}", step="sonarr", current=i, total=total)
            add_log(f"Loaded {total} series from Sonarr", category="sync", action="sonarr")

        _progress("Loading Seerr requests…", step="seerr")
        seerr_matched = 0
        seerr_total = 0
        if client := seerr():
            seerr_seen: dict[tuple, dict[str, Any]] = {}

            def seerr_bucket(media: dict[str, Any], req: dict[str, Any] | None = None) -> dict[str, Any]:
                media = media or {}
                req = req or {}
                media_type = _media_type(
                    req.get("type") or media.get("mediaType") or req.get("mediaType"),
                    "movie",
                )
                tmdb_id = int(media.get("tmdbId") or req.get("tmdbId") or 0)
                tvdb_id = int(media.get("tvdbId") or req.get("tvdbId") or 0)
                title = next(
                    (
                        value
                        for value in (
                            media.get("title"),
                            media.get("name"),
                            req.get("mediaTitle"),
                            req.get("title"),
                            (req.get("media") or {}).get("title") if isinstance(req.get("media"), dict) else "",
                        )
                        if value
                    ),
                    "",
                )
                key = (media_type, tmdb_id, tvdb_id) if (tmdb_id or tvdb_id) else (media_type, 0, 0, title.lower())
                row = seerr_seen.setdefault(
                    key,
                    {
                        "media_type": media_type,
                        "tmdb_id": tmdb_id,
                        "tvdb_id": tvdb_id,
                        "imdb_id": str(media.get("imdbId") or req.get("imdbId") or ""),
                        "title": "",
                        "year": media.get("year") or req.get("year"),
                        "seerr_media_id": media.get("id"),
                        "requests": [],
                        "claimed": False,
                        "in_flight": False,
                        "catalog_key": None,
                    },
                )
                if title and not row["title"]:
                    row["title"] = title
                if media.get("id"):
                    row["seerr_media_id"] = media.get("id")
                row["claimed"] = row["claimed"] or media_claimed(media)
                row["in_flight"] = row.get("in_flight") or media_in_flight(media)
                if req:
                    req_id = req.get("id")
                    seen_ids = {item.get("id") for item in row["requests"] if isinstance(item, dict)}
                    if req_id is None or req_id not in seen_ids:
                        row["requests"].append(req)
                nested = media.get("requests") or media.get("MediaRequests") or []
                for extra in nested:
                    if not isinstance(extra, dict):
                        continue
                    extra_id = extra.get("id")
                    seen_ids = {item.get("id") for item in row["requests"] if isinstance(item, dict)}
                    if extra_id is None or extra_id not in seen_ids:
                        row["requests"].append(extra)
                return row

            def attach_requester(req: dict[str, Any]) -> bool:
                media = req.get("media") or {}
                bucket = seerr_bucket(media, req)
                media_type = bucket["media_type"]
                titles = [
                    bucket.get("title") or "",
                    media.get("title") or "",
                    req.get("mediaTitle") or "",
                    req.get("title") or "",
                ]
                key = index.resolve(
                    media_type,
                    bucket["tmdb_id"],
                    bucket["tvdb_id"],
                    bucket["imdb_id"],
                    titles,
                    bucket.get("year"),
                )
                if not key:
                    return False
                bucket["catalog_key"] = key
                requested = req.get("requestedBy") or req.get("requested_by") or req.get("user") or {}
                if isinstance(requested, (int, float)) or (isinstance(requested, str) and requested.isdigit()):
                    requested = seerr_users_by_id.get(int(requested)) or {}
                identity = directory.resolve(requested)
                if identity["display"]:
                    catalog[key]["requested_by"] = identity["display"]
                catalog[key]["requested_at"] = req.get("createdAt") or req.get("modifiedAt") or catalog[key]["requested_at"]
                catalog[key]["seerr_media_id"] = bucket.get("seerr_media_id") or catalog[key]["seerr_media_id"]
                if request_is_open([req]) and (catalog[key].get("size_bytes") or 0) <= 0 and catalog[key].get("availability") != "partial":
                    catalog[key]["availability"] = "requested"
                return True

            requests = client.requests()
            seerr_total = len(requests)
            _progress(f"Matching Seerr requests… 0/{seerr_total}", step="seerr", current=0, total=seerr_total or 0)
            for i, req in enumerate(requests, 1):
                if attach_requester(req):
                    seerr_matched += 1
                if seerr_total and (i == seerr_total or i % 50 == 0):
                    _progress(f"Matching Seerr requests… {i}/{seerr_total}", step="seerr", current=i, total=seerr_total)
            for media in client.media():
                bucket = seerr_bucket(media)
                media_type = bucket["media_type"]
                key = index.resolve(
                    media_type,
                    bucket["tmdb_id"],
                    bucket["tvdb_id"],
                    bucket["imdb_id"],
                    [bucket.get("title") or "", media.get("title") or ""],
                    bucket.get("year") or media.get("year"),
                )
                if key:
                    bucket["catalog_key"] = key
                    catalog[key]["seerr_media_id"] = bucket.get("seerr_media_id") or catalog[key]["seerr_media_id"]
                for req in media.get("requests") or media.get("MediaRequests") or []:
                    if isinstance(req, dict):
                        nested = dict(req)
                        nested.setdefault("media", media)
                        if attach_requester(nested):
                            seerr_matched += 1

            for bucket in seerr_seen.values():
                key = bucket.get("catalog_key")
                if not key:
                    continue
                item = catalog[key]
                if (item.get("size_bytes") or 0) > 0 or item.get("availability") == "partial":
                    continue
                if bucket.get("in_flight") or request_is_open(bucket.get("requests")):
                    item["availability"] = "requested"

            stale = 0
            for bucket in seerr_seen.values():
                if bucket.get("catalog_key"):
                    continue
                claimed = bucket.get("claimed")
                requested = request_was_made(bucket.get("requests"))
                if bucket.get("in_flight") and not claimed:
                    continue
                if not claimed and not requested:
                    continue
                title = _usable_title(bucket.get("title"), bucket.get("tmdb_id") or 0)
                if not title:
                    continue
                requester = ""
                requested_at = ""
                for req in bucket.get("requests") or []:
                    created = str(req.get("createdAt") or req.get("created_at") or "")
                    if created and (not requested_at or created < requested_at):
                        requested_at = created
                    requested_by = req.get("requestedBy") or req.get("requested_by") or req.get("user") or {}
                    if isinstance(requested_by, (int, float)) or (isinstance(requested_by, str) and requested_by.isdigit()):
                        requested_by = seerr_users_by_id.get(int(requested_by)) or {}
                    if isinstance(requested_by, dict):
                        identity = directory.resolve(requested_by)
                        requester = identity["display"] or requester
                    if requester:
                        break
                reason = (
                    "Seerr still lists this as available, but it is not in Radarr/Sonarr"
                    if claimed
                    else "Requested in Seerr, but it is not in the library"
                )
                note_unmatched(
                    "seerr",
                    bucket["media_type"],
                    title,
                    bucket.get("year"),
                    reason=reason,
                    plays=max(1, len(bucket.get("requests") or [])),
                    tmdb_id=bucket.get("tmdb_id") or 0,
                    tvdb_id=bucket.get("tvdb_id") or 0,
                    seerr_media_id=bucket.get("seerr_media_id"),
                    requested_by=requester,
                    requested_at=requested_at,
                    kind="seerr_missing",
                )
                stale += 1

            missing_seerr = 0
            for item in catalog.values():
                if not (item.get("radarr_id") or item.get("sonarr_id")):
                    continue
                if item.get("seerr_media_id"):
                    continue
                title = _usable_title(item.get("title"), item.get("tmdb_id") or 0)
                if not title:
                    continue
                source = "radarr" if item.get("radarr_id") else "sonarr"
                note_unmatched(
                    source,
                    item["media_type"],
                    title,
                    item.get("year"),
                    reason="In the library, but not matched to Seerr",
                    tmdb_id=item.get("tmdb_id") or 0,
                    tvdb_id=item.get("tvdb_id") or 0,
                    kind="no_seerr",
                )
                missing_seerr += 1

            add_log(
                f"Attached Seerr requesters to {seerr_matched} library titles from {seerr_total} requests",
                category="sync",
                action="seerr",
                detail={
                    "matched": seerr_matched,
                    "requests": seerr_total,
                    "seerr_missing": stale,
                    "library_no_seerr": missing_seerr,
                },
            )

        plays: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)

        def attach_play(key: tuple[str, int, int] | None, event: dict[str, Any]) -> None:
            if key:
                plays[key].append(event)

        def log_history_match(source: str, matched: int, total: int) -> None:
            add_log(
                f"Matched {matched}/{total} {source} plays",
                category="sync",
                action=f"{source}_match",
                detail={"matched": matched, "total": total, "ignored": max(0, total - matched)},
            )

        _progress("Loading Tautulli history…", step="tautulli")
        if client := tautulli():
            try:
                _progress("Loading Tautulli library IDs…", step="tautulli")
                rating_map = client.rating_map()
            except Exception as exc:
                rating_map = {}
                add_log(f"Tautulli library map failed: {exc}", level="warn", category="sync", action="tautulli")
            for rating_key, meta in rating_map.items():
                mapped_type = _media_type(meta.get("media_type"), "movie")
                key = index.resolve(
                    mapped_type,
                    int(meta.get("tmdb_id") or 0),
                    int(meta.get("tvdb_id") or 0),
                    str(meta.get("imdb_id") or ""),
                    [meta.get("title") or ""],
                    meta.get("year"),
                )
                if key and not catalog[key].get("tautulli_rating_key"):
                    catalog[key]["tautulli_rating_key"] = rating_key

            def tautulli_progress(fetched: int) -> None:
                _progress(f"Fetching Tautulli history… {fetched}", step="tautulli", current=fetched, total=0)

            rows = client.history(on_progress=tautulli_progress)
            total = len(rows)
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
                identity = directory.resolve(
                    {
                        "user": row.get("user") or "",
                        "username": row.get("user") or "",
                        "friendly_name": row.get("friendly_name") or "",
                        "displayName": row.get("friendly_name") or "",
                    }
                )
                if key:
                    matched += 1
                attach_play(
                    key,
                    {
                        "user": identity["display"] or row.get("friendly_name") or row.get("user") or "Unknown",
                        "watched_at": _unix(row.get("date")),
                        "source": "tautulli",
                        "media_type": media_type,
                        "title": title or mapped.get("title") or row.get("full_title") or "Unknown",
                        "year": parse_year(year),
                    },
                )
                if i == total or i % 250 == 0:
                    _progress(f"Matching Tautulli history… {i}/{total}", step="tautulli", current=i, total=total)
            log_history_match("Tautulli", matched, total)

        _progress("Loading Tracearr history…", step="tracearr")
        if client := tracearr():
            def tracearr_progress(fetched: int) -> None:
                _progress(f"Fetching Tracearr history… {fetched}", step="tracearr", current=fetched, total=0)

            rows = client.history(on_progress=tracearr_progress)
            total = len(rows)
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
                identity = directory.resolve(row)
                if key:
                    matched += 1
                attach_play(
                    key,
                    {
                        "user": identity["display"] or _user_name(row) or "Unknown",
                        "watched_at": _unix(
                            row.get("viewedAt")
                            or row.get("watchedAt")
                            or row.get("startedAt")
                            or row.get("createdAt")
                            or row.get("date")
                        ),
                        "source": "tracearr",
                        "media_type": media_type,
                        "title": title or "Unknown",
                        "year": parse_year(year),
                    },
                )
                if i == total or i % 250 == 0:
                    _progress(f"Matching Tracearr history… {i}/{total}", step="tracearr", current=i, total=total)
            log_history_match("Tracearr", matched, total)

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
                    "tautulli_rating_key": item.get("tautulli_rating_key") or "",
                    "availability": item.get("availability") or "downloaded",
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
                    rating, rating_votes, rating_source, tautulli_rating_key, availability
                ) VALUES (
                    :media_type, :tmdb_id, :tvdb_id, :imdb_id, :title, :year, :poster_url, :size_bytes,
                    :radarr_id, :sonarr_id, :seerr_media_id, :requested_by, :requested_at,
                    :last_watched_at, :play_count, :watcher_count, :watchers_json, :sources_json, :path, :title_slug,
                    :rating, :rating_votes, :rating_source, :tautulli_rating_key, :availability
                )
                """,
                records,
            )
            people_stats: dict[str, dict[str, Any]] = {}
            for person in directory.snapshot():
                people_stats[person["canonical"]] = {
                    **person,
                    "request_count": 0,
                    "library_count": 0,
                    "library_size": 0,
                    "play_count": 0,
                    "last_watched_at": None,
                    "sources": set(filter(None, [])),
                }

            def person_row(ident: dict[str, str]) -> dict[str, Any] | None:
                key = ident.get("canonical") or ident.get("display") or ""
                if not key:
                    return None
                return people_stats.setdefault(
                    key,
                    {
                        "canonical": key,
                        "display_name": ident.get("display") or key,
                        "plex_username": ident.get("plex") or "",
                        "email": ident.get("email") or "",
                        "aliases": [],
                        "seerr_id": None,
                        "tautulli_id": None,
                        "request_count": 0,
                        "library_count": 0,
                        "library_size": 0,
                        "play_count": 0,
                        "last_watched_at": None,
                        "sources": set(),
                    },
                )

            for item in records:
                if item.get("requested_by"):
                    row = person_row(directory.resolve(item["requested_by"]))
                    if row:
                        row["request_count"] += 1
                        row["library_count"] += 1
                        row["library_size"] += item.get("size_bytes") or 0
                        row["sources"].add("seerr")
                for watcher in json.loads(item["watchers_json"] or "[]"):
                    row = person_row(directory.resolve(watcher.get("user")))
                    if not row:
                        continue
                    row["play_count"] += watcher.get("plays") or 0
                    ts = watcher.get("last_watched_at")
                    if ts and (row["last_watched_at"] or 0) < ts:
                        row["last_watched_at"] = ts
                    row["sources"].add("watch")
            conn.execute("DELETE FROM people")
            conn.executemany(
                """
                INSERT INTO people (
                    canonical, display_name, plex_username, email, aliases_json, tautulli_id, seerr_id,
                    request_count, library_count, library_size, play_count, last_watched_at, sources_json
                ) VALUES (
                    :canonical, :display_name, :plex_username, :email, :aliases_json, :tautulli_id, :seerr_id,
                    :request_count, :library_count, :library_size, :play_count, :last_watched_at, :sources_json
                )
                """,
                [
                    {
                        "canonical": row["canonical"],
                        "display_name": row.get("display_name") or row["canonical"],
                        "plex_username": row.get("plex_username") or "",
                        "email": row.get("email") or "",
                        "aliases_json": json.dumps(row.get("aliases") or []),
                        "tautulli_id": str(row.get("tautulli_id") or ""),
                        "seerr_id": str(row.get("seerr_id") or ""),
                        "request_count": row.get("request_count") or 0,
                        "library_count": row.get("library_count") or 0,
                        "library_size": row.get("library_size") or 0,
                        "play_count": row.get("play_count") or 0,
                        "last_watched_at": row.get("last_watched_at"),
                        "sources_json": json.dumps(sorted(row.get("sources") or [])),
                    }
                    for row in people_stats.values()
                ],
            )
            conn.execute("DELETE FROM unmatched")
            if unmatched_rows:
                conn.executemany(
                    """
                    INSERT INTO unmatched (
                        source, media_type, title, year, plays, reason,
                        tmdb_id, tvdb_id, seerr_media_id, requested_by, requested_at, kind
                    )
                    VALUES (
                        :source, :media_type, :title, :year, :plays, :reason,
                        :tmdb_id, :tvdb_id, :seerr_media_id, :requested_by, :requested_at, :kind
                    )
                    """,
                    list(unmatched_rows.values()),
                )
        watched = sum(1 for row in records if row["play_count"])
        message = f"Synced {len(records)} titles ({watched} with watch history, {len(unmatched_rows)} unmatched)"
        _set_job(status="idle", message=message, step="", current=0, total=0, finished_at=int(time.time()))
        add_log(
            message,
            category="sync",
            action="complete",
            detail={"titles": len(records), "watched": watched, "unmatched": len(unmatched_rows)},
        )
        warm_cache()
    except Exception as exc:
        _set_job(status="error", message=str(exc), step="", current=0, total=0, finished_at=int(time.time()))
        add_log(str(exc), level="error", category="sync", action="error")


def reset_job(message: str = "Idle") -> dict[str, Any]:
    _set_job(
        status="idle",
        message=message,
        step="",
        current=0,
        total=0,
        started_at=None,
        finished_at=None,
    )
    return job_status()


_scheduler_started = False


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop() -> None:
        while True:
            time.sleep(60)
            try:
                if get_setting("sync_schedule_enabled", "0") != "1":
                    continue
                if _job.get("status") == "running":
                    continue
                try:
                    hours = max(1, min(168, int(get_setting("sync_interval_hours", "24") or "24")))
                except ValueError:
                    hours = 24
                last = int(_job.get("finished_at") or 0)
                if last and (time.time() - last) < hours * 3600:
                    continue
                add_log(f"Scheduled sync every {hours}h", category="sync", action="schedule")
                start_sync()
            except Exception:
                continue

    threading.Thread(target=loop, daemon=True, name="cleanarr-scheduler").start()
