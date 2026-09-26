from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from typing import Any

from .art import warm_cache
from .db import connect, get_setting, ignore_key, ignored_matches, ignored_unmatched, match_ignore_key
from .identity import UserDirectory, _clean
from .actions import Whitelist, delete_item, is_protected, is_stale_unwatched
from .logs import add_log
from .match import CatalogIndex, MatchHit, parse_year
from .services.arr import movie_availability, pick_rating, poster_from, series_availability
from .services.clients import jellystat, radarr, radarr_4k, seerr, sonarr, tautulli, tracearr
from .services.jellystat import play_title
from .services.seerr import (
    media_available,
    media_blocked,
    media_claimed,
    media_deleted,
    media_in_flight,
    request_is_open,
    request_was_made,
)
from .services.tautulli import as_int, parse_ids, pick_live_rating_key

SEERR_TITLE_LOOKUPS = 200  # cap the per-sync TMDB lookups used to name Seerr-only rows

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
    else:
        # Only while a sync is running — idle/error leave percent unset so the UI can hide the bar.
        data["percent"] = None
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


def start_sync(scheduled: bool = False) -> dict[str, Any]:
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
    add_log(
        f"Sync started ({'scheduled' if scheduled else 'manual'})",
        category="sync",
        action="start",
        actor="scheduler" if scheduled else "",
    )
    thread = threading.Thread(target=_run_sync, kwargs={"auto_delete": scheduled}, daemon=True)
    thread.start()
    return job_status()


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


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
    user = row.get("user") or row.get("friendly_name") or row.get("username") or row.get("UserName") or row.get("Name") or ""
    return _clean(user)


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


def _run_sync(auto_delete: bool = False) -> None:
    try:
        # Auto-delete reads "no plays" as "delete me", so it must not run on a sync
        # where a history source was configured but did not report properly.
        history_health: dict[str, Any] = {"configured": [], "degraded": [], "rows": 0}
        catalog: dict[tuple[str, int, int], dict[str, Any]] = {}
        index = CatalogIndex()
        directory = UserDirectory()
        unmatched_rows: dict[tuple, dict[str, Any]] = {}
        ignored = ignored_unmatched()
        blocked_matches = ignored_matches()

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
            seerr_state: str = "",
        ) -> None:
            label = _usable_title(title, tmdb_id)
            if not label:
                return
            try:
                year_i = int(year or 0)
            except (TypeError, ValueError):
                year_i = 0
            if ignore_key(kind or source, media_type, tmdb_id, tvdb_id, label) in ignored:
                return
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
                    "seerr_state": seerr_state,
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
                add_log(f"Tautulli: could not load users: {exc}", level="warn", category="sync", action="users")
        if client := jellystat():
            try:
                for user in client.users():
                    directory.ingest_jellystat(user)
            except Exception as exc:
                add_log(f"Jellystat: could not load users: {exc}", level="warn", category="sync", action="users")
        seerr_users_by_id: dict[int, dict[str, Any]] = {}
        if client := seerr():
            try:
                for user in client.users():
                    directory.ingest_seerr(user)
                    if user.get("id") is not None:
                        seerr_users_by_id[int(user["id"])] = user
            except Exception as exc:
                add_log(f"Seerr: could not load users: {exc}", level="warn", category="sync", action="users")

        _AVAIL_RANK = {"requested": 0, "partial": 1, "downloaded": 2}

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
                "radarr_4k_id": None,
                "sonarr_id": None,
                "seerr_media_id": None,
                "seerr_tmdb_id": 0,
                "seerr_match_via": "",
                "seerr_title": "",
                "requested_by": "",
                "requested_at": "",
                "path": "",
                "title_slug": "",
                "rating": None,
                "rating_votes": 0,
                "rating_source": "",
                "tautulli_rating_key": "",
                "jellystat_item_id": "",
                "availability": "downloaded",
                "added_at": None,
            }
            # Same TMDB title can live in both Radarr and Radarr 4K, so keep both ids and sum disk.
            summing_size = bool(
                (item.get("radarr_4k_id") and current.get("radarr_id") and not current.get("radarr_4k_id"))
                or (item.get("radarr_id") and current.get("radarr_4k_id") and not current.get("radarr_id"))
            )
            for field in current:
                if field == "size_bytes" and summing_size:
                    current["size_bytes"] = int(current.get("size_bytes") or 0) + int(item.get("size_bytes") or 0)
                    continue
                if field == "availability":
                    new_av = item.get("availability")
                    if new_av and _AVAIL_RANK.get(str(new_av), 0) >= _AVAIL_RANK.get(
                        str(current.get("availability") or ""), 0
                    ):
                        current["availability"] = new_av
                    continue
                if field == "added_at":
                    new_added = item.get("added_at")
                    if new_added:
                        old = current.get("added_at")
                        current["added_at"] = min(int(old), int(new_added)) if old else new_added
                    continue
                if item.get(field) not in (None, "", 0, []):
                    current[field] = item[field]
            catalog[key] = current
            index.add(key, current, extra_titles or [])
            return key

        def load_radarr_movies(client, *, id_field: str, label: str, step: str) -> None:
            movies = client.movies()
            total = len(movies)
            _progress(f"Loading {label}… 0/{total}", step=step, current=0, total=total)
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
                        id_field: movie.get("id"),
                        "path": movie.get("path") or "",
                        "title_slug": movie.get("titleSlug") or "",
                        "rating": rating,
                        "rating_votes": votes,
                        "rating_source": source,
                        "availability": movie_availability(movie),
                        "added_at": _unix(movie.get("added")),
                    },
                    _alt_titles(movie),
                )
                if i == total or i % 75 == 0:
                    _progress(f"Loading {label}… {i}/{total}", step=step, current=i, total=total)
            add_log(f"{label}: loaded {total:,} movies", category="sync", action=step, detail={"movies": total})

        _progress("Loading Radarr…", step="radarr")
        if client := radarr():
            load_radarr_movies(client, id_field="radarr_id", label="Radarr", step="radarr")

        _progress("Loading Radarr 4K…", step="radarr_4k")
        if client := radarr_4k():
            load_radarr_movies(client, id_field="radarr_4k_id", label="Radarr 4K", step="radarr_4k")

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
                        "added_at": _unix(show.get("added")),
                    },
                    _alt_titles(show),
                )
                if i == total or i % 40 == 0:
                    _progress(f"Loading Sonarr… {i}/{total}", step="sonarr", current=i, total=total)
            add_log(f"Sonarr: loaded {total:,} series", category="sync", action="sonarr", detail={"series": total})

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
                        "blocked": False,
                        "deleted": False,
                        "available": False,
                        "service_link": False,
                        "catalog_key": None,
                    },
                )
                if title and not row["title"]:
                    row["title"] = title
                if media.get("id"):
                    row["seerr_media_id"] = media.get("id")
                row["claimed"] = row["claimed"] or media_claimed(media)
                row["in_flight"] = row.get("in_flight") or media_in_flight(media)
                row["blocked"] = row.get("blocked") or media_blocked(media)
                row["deleted"] = row.get("deleted") or media_deleted(media)
                row["available"] = row.get("available") or media_available(media)
                row["service_link"] = row.get("service_link") or bool(
                    media.get("externalServiceId") or media.get("externalServiceId4k")
                )
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

            def seerr_attach(hit: MatchHit | None, bucket: dict[str, Any]) -> tuple[str, int, int] | None:
                """Honor ignored Seerr↔*arr pairs so a rejected title match stays rejected."""
                if not hit:
                    return None
                media_type, lib_tmdb, lib_tvdb = hit.key
                pair = match_ignore_key(
                    media_type,
                    bucket.get("tmdb_id") or 0,
                    bucket.get("tvdb_id") or 0,
                    lib_tmdb,
                    lib_tvdb,
                )
                if pair in blocked_matches:
                    return None
                return hit.key

            def note_seerr_link(key: tuple[str, int, int], hit: MatchHit | None, bucket: dict[str, Any]) -> None:
                """Record how Seerr reached this row, and the Seerr-side name so a guess can be reviewed."""
                if not hit:
                    return
                row = catalog[key]
                if row.get("seerr_match_via") and hit.via != "tmdb":
                    return
                row["seerr_match_via"] = hit.via
                label = _usable_title(bucket.get("title"), 0) or ""
                year = parse_year(bucket.get("year"))
                row["seerr_title"] = f"{label} ({year})" if label and year else label

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
                hit = index.resolve_hit(
                    media_type,
                    bucket["tmdb_id"],
                    bucket["tvdb_id"],
                    bucket["imdb_id"],
                    titles,
                    bucket.get("year"),
                )
                key = seerr_attach(hit, bucket)
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
                catalog[key]["seerr_tmdb_id"] = int(bucket.get("tmdb_id") or catalog[key].get("seerr_tmdb_id") or 0)
                note_seerr_link(key, hit, bucket)
                if request_is_open([req]) and (catalog[key].get("size_bytes") or 0) <= 0 and catalog[key].get("availability") != "partial":
                    catalog[key]["availability"] = "requested"
                return True

            requests = client.requests()
            seerr_total = len(requests)
            blocked_keys: set[tuple[str, int]] = set()
            try:
                for item in client.blocklist():
                    media = item.get("media") if isinstance(item.get("media"), dict) else {}
                    tmdb_id = int(item.get("tmdbId") or item.get("tmdb_id") or media.get("tmdbId") or 0)
                    if not tmdb_id:
                        continue
                    media_type = _media_type(
                        item.get("mediaType") or item.get("media_type") or media.get("mediaType"),
                        "movie",
                    )
                    blocked_keys.add((media_type, tmdb_id))
            except Exception as exc:
                add_log(f"Seerr: could not load the blocklist: {exc}", level="warn", category="sync", action="seerr")
            _progress(f"Matching Seerr requests… 0/{seerr_total}", step="seerr", current=0, total=seerr_total or 0)
            for i, req in enumerate(requests, 1):
                if attach_requester(req):
                    seerr_matched += 1
                if seerr_total and (i == seerr_total or i % 50 == 0):
                    _progress(f"Matching Seerr requests… {i}/{seerr_total}", step="seerr", current=i, total=seerr_total)
            for media in client.media():
                bucket = seerr_bucket(media)
                media_type = bucket["media_type"]
                hit = index.resolve_hit(
                    media_type,
                    bucket["tmdb_id"],
                    bucket["tvdb_id"],
                    bucket["imdb_id"],
                    [bucket.get("title") or "", media.get("title") or ""],
                    bucket.get("year") or media.get("year"),
                )
                key = seerr_attach(hit, bucket)
                if key:
                    bucket["catalog_key"] = key
                    catalog[key]["seerr_media_id"] = bucket.get("seerr_media_id") or catalog[key]["seerr_media_id"]
                    catalog[key]["seerr_tmdb_id"] = int(bucket.get("tmdb_id") or catalog[key].get("seerr_tmdb_id") or 0)
                    note_seerr_link(key, hit, bucket)
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
            blocked = 0
            deleted = 0
            lookups = 0

            def seerr_label(bucket: dict[str, Any]) -> str | None:
                """Name a Seerr-only row, asking Seerr's TMDB proxy when the row itself is unnamed."""
                nonlocal lookups
                if not bucket.get("title") and bucket.get("tmdb_id") and lookups < SEERR_TITLE_LOOKUPS:
                    lookups += 1
                    try:
                        info = client.detail(bucket["media_type"], int(bucket["tmdb_id"]))
                    except Exception:
                        info = {}
                    bucket["title"] = info.get("title") or info.get("name") or ""
                    if not bucket.get("year"):
                        bucket["year"] = parse_year(str(info.get("releaseDate") or info.get("firstAirDate") or "")[:4])
                return _usable_title(bucket.get("title"), bucket.get("tmdb_id") or 0)

            def seerr_requester(bucket: dict[str, Any]) -> tuple[str, str]:
                requester = ""
                first_requested = ""
                for req in bucket.get("requests") or []:
                    created = str(req.get("createdAt") or req.get("created_at") or "")
                    if created and (not first_requested or created < first_requested):
                        first_requested = created
                    requested_by = req.get("requestedBy") or req.get("requested_by") or req.get("user") or {}
                    if isinstance(requested_by, (int, float)) or (isinstance(requested_by, str) and requested_by.isdigit()):
                        requested_by = seerr_users_by_id.get(int(requested_by)) or {}
                    if isinstance(requested_by, dict):
                        identity = directory.resolve(requested_by)
                        requester = identity["display"] or requester
                    if requester:
                        break
                return requester, first_requested

            for bucket in seerr_seen.values():
                if bucket.get("catalog_key"):
                    continue
                key = (bucket.get("media_type") or "movie", int(bucket.get("tmdb_id") or 0))
                if bucket.get("blocked") or (key[1] and key in blocked_keys):
                    blocked += 1
                    continue
                requested = request_was_made(bucket.get("requests"))
                # A deleted media row is the current truth even when another payload in this sync
                # still carried an available status or a dangling *arr id. Nothing needs clearing:
                # Seerr has let the title go, so anyone can request it again. Report it as history
                # rather than as a stale record.
                if bucket.get("deleted"):
                    deleted += 1
                    if not requested:
                        continue
                    title = seerr_label(bucket)
                    if not title:
                        continue
                    requester, requested_at = seerr_requester(bucket)
                    note_unmatched(
                        "seerr",
                        bucket["media_type"],
                        title,
                        bucket.get("year"),
                        reason="Deleted in Seerr, so it can be requested again",
                        plays=max(1, len(bucket.get("requests") or [])),
                        tmdb_id=bucket.get("tmdb_id") or 0,
                        tvdb_id=bucket.get("tvdb_id") or 0,
                        seerr_media_id=bucket.get("seerr_media_id"),
                        requested_by=requester,
                        requested_at=requested_at,
                        kind="seerr_deleted",
                        seerr_state="deleted",
                    )
                    continue
                claimed = bucket.get("claimed")
                if bucket.get("in_flight") and not claimed:
                    continue
                if not claimed and not requested:
                    continue
                title = seerr_label(bucket)
                if not title:
                    continue
                requester, requested_at = seerr_requester(bucket)
                orphan_service = bool(bucket.get("service_link")) and not bucket.get("available")
                if claimed and orphan_service:
                    state = "orphan"
                    reason = "Seerr still points at a Radarr/Sonarr entry that no longer exists"
                elif claimed:
                    state = "available"
                    reason = "Seerr still lists this as available, but it is not in Radarr/Sonarr"
                else:
                    state = "requested"
                    reason = "Requested in Seerr, but it never arrived in the library"
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
                    seerr_state=state,
                )
                stale += 1

            missing_seerr = 0
            for item in catalog.values():
                if not (item.get("radarr_id") or item.get("radarr_4k_id") or item.get("sonarr_id")):
                    continue
                if item.get("seerr_media_id"):
                    continue
                title = _usable_title(item.get("title"), item.get("tmdb_id") or 0)
                if not title:
                    continue
                if item.get("radarr_id"):
                    source = "radarr"
                elif item.get("radarr_4k_id"):
                    source = "radarr_4k"
                else:
                    source = "sonarr"
                note_unmatched(
                    source,
                    item["media_type"],
                    title,
                    item.get("year"),
                    reason=(
                        "In the library, but not matched to Seerr"
                        if item.get("tmdb_id")
                        else "In the library, but Seerr cannot track it: no TMDB id"
                    ),
                    tmdb_id=item.get("tmdb_id") or 0,
                    tvdb_id=item.get("tvdb_id") or 0,
                    kind="no_seerr",
                    seerr_state="absent",
                )
                missing_seerr += 1

            add_log(
                f"Seerr: matched {seerr_matched:,} of {seerr_total:,} requests to library titles",
                category="sync",
                action="seerr",
                detail={
                    "matched": seerr_matched,
                    "requests": seerr_total,
                    "seerr_missing": stale,
                    "seerr_blocked": blocked,
                    "seerr_deleted": deleted,
                    "library_no_seerr": missing_seerr,
                },
            )

        plays: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)

        def attach_play(key: tuple[str, int, int] | None, event: dict[str, Any]) -> None:
            if key:
                plays[key].append(event)

        def log_history_match(source: str, matched: int, total: int) -> None:
            add_log(
                f"{source.title()}: matched {matched:,} of {total:,} plays",
                category="sync",
                action=f"{source}_match",
                detail={"matched": matched, "total": total, "ignored": max(0, total - matched)},
            )

        _progress("Loading Tautulli history…", step="tautulli")
        if client := tautulli():
            history_health["configured"].append("tautulli")
            rating_map = {}
            tautulli_hits: dict[tuple[str, int, int], dict[str, dict[str, Any]]] = defaultdict(dict)

            def remember_tautulli(key: tuple[str, int, int] | None, rating_key: str, meta: dict[str, Any]) -> None:
                if not key or not rating_key:
                    return
                rating_key = str(rating_key)
                slot = tautulli_hits[key].setdefault(rating_key, {"rating_key": rating_key})
                for field in ("title", "year", "media_type", "thumb"):
                    if meta.get(field) and not slot.get(field):
                        slot[field] = meta[field]
                for field in ("play_count", "last_played", "added_at", "file_size"):
                    slot[field] = max(as_int(slot.get(field)), as_int(meta.get(field)))
                if meta.get("in_library"):
                    slot["in_library"] = True

            try:
                _progress("Loading Tautulli library IDs…", step="tautulli")
                rating_map = client.rating_map()
            except Exception as exc:
                rating_map = {}
                history_health["degraded"].append("tautulli")
                add_log(f"Tautulli: could not map the library: {exc}", level="warn", category="sync", action="tautulli")
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
                remember_tautulli(key, rating_key, meta)

            def tautulli_progress(fetched: int) -> None:
                _progress(f"Fetching Tautulli history… {fetched}", step="tautulli", current=fetched, total=0)

            rows = client.history(on_progress=tautulli_progress)
            history_health["rows"] += len(rows)
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
                    mapped_meta = rating_map.get(rating_key) or {}
                    remember_tautulli(
                        key,
                        rating_key,
                        {
                            **mapped_meta,
                            "rating_key": rating_key,
                            "last_played": _unix(row.get("date")) or mapped_meta.get("last_played"),
                            "play_count": max(1, as_int(mapped_meta.get("play_count"))),
                            "in_library": rating_key in rating_map,
                        },
                    )
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
            exists_cache: dict[str, bool] = {}

            def tautulli_exists(rating_key: str) -> bool:
                if rating_key not in exists_cache:
                    exists_cache[rating_key] = bool(client.metadata(rating_key))
                return exists_cache[rating_key]

            for key, options in tautulli_hits.items():
                catalog[key]["tautulli_rating_key"] = pick_live_rating_key(options, tautulli_exists)

        _progress("Loading Tracearr history…", step="tracearr")
        if client := tracearr():
            history_health["configured"].append("tracearr")

            def tracearr_progress(fetched: int) -> None:
                _progress(f"Fetching Tracearr history… {fetched}", step="tracearr", current=fetched, total=0)

            rows = client.history(on_progress=tracearr_progress)
            history_health["rows"] += len(rows)
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

        _progress("Loading Jellystat history…", step="jellystat")
        if client := jellystat():
            history_health["configured"].append("jellystat")
            library_map: dict[str, dict[str, Any]] = {}
            try:
                _progress("Loading Jellystat library…", step="jellystat")
                library_map = client.library_map()
            except Exception as exc:
                history_health["degraded"].append("jellystat")
                add_log(f"Jellystat: could not map the library: {exc}", level="warn", category="sync", action="jellystat")
            for item_id, meta in library_map.items():
                mapped_type = _media_type(meta.get("media_type"), "movie")
                key = index.resolve(
                    mapped_type,
                    0,
                    0,
                    "",
                    [meta.get("title") or ""],
                    meta.get("year"),
                )
                if key and not catalog[key].get("jellystat_item_id"):
                    catalog[key]["jellystat_item_id"] = item_id

            def jellystat_progress(fetched: int) -> None:
                _progress(f"Fetching Jellystat history… {fetched}", step="jellystat", current=fetched, total=0)

            rows = client.history(on_progress=jellystat_progress)
            history_health["rows"] += len(rows)
            total = len(rows)
            matched = 0
            _progress(f"Matching Jellystat history… 0/{total}", step="jellystat", current=0, total=total)
            for i, row in enumerate(rows, 1):
                item_id = str(row.get("NowPlayingItemId") or row.get("nowPlayingItemId") or "")
                mapped = library_map.get(item_id) or {}
                media_type, title = play_title(row)
                if mapped.get("media_type"):
                    media_type = _media_type(mapped.get("media_type"), media_type)
                if mapped.get("title") and media_type != "tv":
                    title = mapped["title"] or title
                elif mapped.get("title") and not title:
                    title = mapped["title"]
                year = mapped.get("year") or row.get("ProductionYear") or row.get("productionYear")
                directory.ingest_jellystat(row)
                key = index.resolve(
                    media_type,
                    0,
                    0,
                    "",
                    [
                        title,
                        row.get("SeriesName") or "",
                        row.get("NowPlayingItemName") or "",
                        mapped.get("title") or "",
                    ],
                    year,
                )
                identity = directory.resolve(row)
                if key:
                    matched += 1
                    if not catalog[key].get("jellystat_item_id") and media_type == "movie" and item_id:
                        catalog[key]["jellystat_item_id"] = item_id
                attach_play(
                    key,
                    {
                        "user": identity["display"] or _user_name(row) or "Unknown",
                        "watched_at": _unix(
                            row.get("ActivityDateInserted")
                            or row.get("activityDateInserted")
                            or row.get("ActivityDate")
                            or row.get("date")
                        ),
                        "source": "jellystat",
                        "media_type": media_type,
                        "title": title or "Unknown",
                        "year": parse_year(year),
                    },
                )
                if i == total or i % 250 == 0:
                    _progress(f"Matching Jellystat history… {i}/{total}", step="jellystat", current=i, total=total)
            log_history_match("Jellystat", matched, total)

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
                    "radarr_id": item.get("radarr_id"),
                    "radarr_4k_id": item.get("radarr_4k_id"),
                    "sonarr_id": item.get("sonarr_id"),
                    "tautulli_rating_key": item.get("tautulli_rating_key") or "",
                    "jellystat_item_id": item.get("jellystat_item_id") or "",
                    "availability": item.get("availability") or "downloaded",
                    "added_at": item.get("added_at"),
                    "seerr_tmdb_id": int(item.get("seerr_tmdb_id") or 0),
                    "seerr_match_via": item.get("seerr_match_via") or "",
                    "seerr_title": item.get("seerr_title") or "",
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
                    radarr_id, radarr_4k_id, sonarr_id, seerr_media_id, requested_by, requested_at,
                    last_watched_at, play_count, watcher_count, watchers_json, sources_json, path, title_slug,
                    rating, rating_votes, rating_source, tautulli_rating_key, jellystat_item_id, availability,
                    added_at, seerr_tmdb_id, seerr_match_via, seerr_title
                ) VALUES (
                    :media_type, :tmdb_id, :tvdb_id, :imdb_id, :title, :year, :poster_url, :size_bytes,
                    :radarr_id, :radarr_4k_id, :sonarr_id, :seerr_media_id, :requested_by, :requested_at,
                    :last_watched_at, :play_count, :watcher_count, :watchers_json, :sources_json, :path, :title_slug,
                    :rating, :rating_votes, :rating_source, :tautulli_rating_key, :jellystat_item_id, :availability,
                    :added_at, :seerr_tmdb_id, :seerr_match_via, :seerr_title
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
                        "account_username": ident.get("account") or "",
                        "email": ident.get("email") or "",
                        "aliases": [],
                        "seerr_id": None,
                        "tautulli_id": None,
                        "jellystat_id": None,
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
                    canonical, display_name, account_username, email, aliases_json, tautulli_id, jellystat_id, seerr_id,
                    request_count, library_count, library_size, play_count, last_watched_at, sources_json
                ) VALUES (
                    :canonical, :display_name, :account_username, :email, :aliases_json, :tautulli_id, :jellystat_id, :seerr_id,
                    :request_count, :library_count, :library_size, :play_count, :last_watched_at, :sources_json
                )
                """,
                [
                    {
                        "canonical": row["canonical"],
                        "display_name": row.get("display_name") or row["canonical"],
                        "account_username": row.get("account_username") or "",
                        "email": row.get("email") or "",
                        "aliases_json": json.dumps(row.get("aliases") or []),
                        "tautulli_id": str(row.get("tautulli_id") or ""),
                        "jellystat_id": str(row.get("jellystat_id") or ""),
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
                        tmdb_id, tvdb_id, seerr_media_id, requested_by, requested_at, kind, seerr_state
                    )
                    VALUES (
                        :source, :media_type, :title, :year, :plays, :reason,
                        :tmdb_id, :tvdb_id, :seerr_media_id, :requested_by, :requested_at, :kind, :seerr_state
                    )
                    """,
                    list(unmatched_rows.values()),
                )
        watched = sum(1 for row in records if row["play_count"])
        unmatched = len(unmatched_rows)
        message = f"{len(records):,} titles"
        if unmatched:
            message += f" · {unmatched:,} unmatched"
        finished_at = int(time.time())
        elapsed = _duration(finished_at - int(_job.get("started_at") or finished_at))
        _set_job(
            status="idle",
            message=message,
            step="",
            current=0,
            total=0,
            finished_at=finished_at,
        )
        summary = f"{len(records):,} titles, {watched:,} watched"
        if unmatched:
            summary += f", {unmatched:,} unmatched"
        add_log(
            f"Sync finished in {elapsed}: {summary}",
            category="sync",
            action="complete",
            detail={
                "titles": len(records),
                "watched": watched,
                "unmatched": unmatched,
                "seconds": finished_at - int(_job.get("started_at") or finished_at),
            },
        )
        if auto_delete:
            run_auto_delete(history_health)
        warm_cache()
    except Exception as exc:
        _set_job(status="error", message=str(exc), step="", current=0, total=0, finished_at=int(time.time()))
        add_log(f"Sync failed: {exc}", level="error", category="sync", action="error")


AUTO_DELETE_DEFAULTS = {"enabled": "0", "max_per_run": "10", "stale_days": "365"}


def auto_delete_settings() -> dict[str, Any]:
    try:
        cap = int(get_setting("auto_delete_max_per_run", AUTO_DELETE_DEFAULTS["max_per_run"]) or 10)
    except ValueError:
        cap = 10
    try:
        days = int(get_setting("auto_delete_stale_days", AUTO_DELETE_DEFAULTS["stale_days"]) or 365)
    except ValueError:
        days = 365
    return {
        "enabled": get_setting("auto_delete_enabled", AUTO_DELETE_DEFAULTS["enabled"]) == "1",
        "max_per_run": max(1, min(500, cap)),
        "stale_days": max(1, min(3650, days)),
    }


def auto_delete_candidates(stale_days: int, limit: int) -> list[dict[str, Any]]:
    """On-disk titles added over stale_days ago with no plays since.

    added_at is NULL until a sync has run against a new-enough schema, and a NULL
    never matches, so an un-backfilled library deletes nothing.
    """
    cutoff = int(time.time()) - stale_days * 24 * 3600
    with connect() as conn:
        rows = conn.execute("SELECT * FROM media ORDER BY added_at ASC").fetchall()
        whitelist = Whitelist([dict(row) for row in conn.execute("SELECT * FROM whitelist").fetchall()])
    candidates = []
    for row in rows:
        item = dict(row)
        if not is_stale_unwatched(item, cutoff):
            continue
        if is_protected(item["title"], item["media_type"], item["tmdb_id"], whitelist):
            continue
        candidates.append(item)
        if len(candidates) >= limit:
            break
    return candidates


def history_block_reason(history: dict[str, Any] | None) -> str:
    """Why this sync's watch history cannot be trusted to mark a title unwatched, if so."""
    if not history:
        return "no watch history was collected"
    configured = history.get("configured") or []
    degraded = history.get("degraded") or []
    if not configured:
        return "no watch history source is configured"
    if degraded:
        return f"{', '.join(sorted(set(degraded)))} did not respond properly"
    if not history.get("rows"):
        return f"{', '.join(configured)} reported no plays at all"
    return ""


def run_auto_delete(history: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delete never-watched stale titles after a scheduled sync. Off unless enabled."""
    config = auto_delete_settings()
    if not config["enabled"]:
        return {"enabled": False, "deleted": 0, "failed": 0, "capped": False, "skipped": ""}

    blocked = history_block_reason(history)
    if blocked:
        add_log(
            f"Automatic delete skipped: {blocked}",
            level="warn",
            category="audit",
            action="auto_delete_skipped",
            actor="automatic",
        )
        return {"enabled": True, "deleted": 0, "failed": 0, "capped": False, "skipped": blocked}

    candidates = auto_delete_candidates(config["stale_days"], config["max_per_run"])
    if not candidates:
        add_log(
            f"Automatic delete found nothing unwatched for {config['stale_days']} days",
            category="audit",
            action="auto_delete",
            actor="automatic",
        )
        return {"enabled": True, "deleted": 0, "failed": 0, "capped": False, "skipped": ""}

    radarr_client = radarr()
    radarr_4k_client = radarr_4k()
    sonarr_client = sonarr()
    seerr_client = seerr()
    deleted = 0
    failed = 0
    freed = 0
    for item in candidates:
        try:
            delete_item(
                item,
                delete_files=True,
                blacklist=False,
                actor="automatic",
                radarr_client=radarr_client,
                radarr_4k_client=radarr_4k_client,
                sonarr_client=sonarr_client,
                seerr_client=seerr_client,
            )
            deleted += 1
            freed += item.get("size_bytes") or 0
        except Exception as exc:
            failed += 1
            add_log(
                f"Automatic delete failed for {item['title']}: {exc}",
                level="error",
                category="audit",
                action="auto_delete_error",
                actor="automatic",
            )
    capped = deleted + failed >= config["max_per_run"]
    add_log(
        f"Automatic delete removed {deleted} titles ({freed / 1_000_000_000:.1f} GB)"
        + (f", {failed} failed" if failed else "")
        + (f", stopped at the {config['max_per_run']} per run cap" if capped else ""),
        level="warn" if failed else "info",
        category="audit",
        action="auto_delete",
        actor="automatic",
        detail={"deleted": deleted, "failed": failed, "freed_bytes": freed, "capped": capped},
    )
    return {"enabled": True, "deleted": deleted, "failed": failed, "capped": capped, "skipped": ""}


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
                # start_sync already records the run; a second line here just doubles it.
                start_sync(scheduled=True)
            except Exception:
                continue

    threading.Thread(target=loop, daemon=True, name="cleanarr-scheduler").start()
