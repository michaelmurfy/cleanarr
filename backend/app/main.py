from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .actions import is_protected, is_stale_unwatched, protect_reason, router as actions_router
from .art import art_url, cache_stats, clear_cache, serve_art
from .auth import (
    bootstrap_auth,
    current_user,
    get_setting,
    login_response,
    set_credentials,
    verify_password,
    COOKIE,
)
from .config import APP_SETTING_KEYS, env_file_present, hide_env_settings, locked_setting_keys
from .db import all_settings, clear_library, connect, init_db, set_setting
from .services.clients import KEYS, cfg, public_url, jellystat, radarr, seerr, sonarr, tautulli, tracearr
from .logs import add_log, list_logs
from .sync import job_status, reset_job, restore_job, start_scheduler, start_sync

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_auth()
    restore_job()
    start_scheduler()
    yield


app = FastAPI(title="Cleanarr", lifespan=lifespan)
app.include_router(actions_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True}


SERVICES = {
    "tautulli": tautulli,
    "tracearr": tracearr,
    "jellystat": jellystat,
    "seerr": seerr,
    "radarr": radarr,
    "sonarr": sonarr,
}


class LoginIn(BaseModel):
    username: str
    password: str


class SettingsIn(BaseModel):
    values: dict[str, str]
    username: str | None = None
    password: str | None = None


class TestIn(BaseModel):
    service: str


@app.post("/api/auth/login")
def login(payload: LoginIn):
    username = get_setting("auth_username")
    if payload.username != username or not verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return login_response(username)


@app.post("/api/auth/logout")
def logout():
    from fastapi.responses import JSONResponse

    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    return {
        "username": user,
        "using_default_password": get_setting("using_default_password") == "1",
    }


@app.get("/api/settings")
def get_settings(request: Request):
    current_user(request)
    stored = all_settings()
    locked = locked_setting_keys()
    env_locked = env_file_present()
    hide_settings = hide_env_settings()
    values = {}
    for key in KEYS:
        configured = bool(cfg(key) or stored.get(key))
        values[f"{key}_set"] = configured
        values[f"{key}_locked"] = hide_settings or key in locked
        values[f"{key}_hidden"] = hide_settings
        if key.endswith("_api_key"):
            values[key] = ""
        elif values[f"{key}_locked"]:
            values[key] = cfg(key)
        else:
            values[key] = stored.get(key) or ""
    values["sync_schedule_enabled"] = stored.get("sync_schedule_enabled") or "0"
    values["sync_interval_hours"] = stored.get("sync_interval_hours") or "24"
    values["auto_delete_enabled"] = stored.get("auto_delete_enabled") or "0"
    values["auto_delete_max_per_run"] = stored.get("auto_delete_max_per_run") or "10"
    values["auto_delete_stale_days"] = stored.get("auto_delete_stale_days") or "365"
    cache = cache_stats()
    with connect() as conn:
        library_count = conn.execute("SELECT COUNT(*) AS n FROM media").fetchone()["n"]
        people_count = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        unmatched_count = conn.execute("SELECT COUNT(*) AS n FROM unmatched").fetchone()["n"]
    return {
        "values": values,
        "locked": sorted(locked),
        "env_file": env_locked,
        "hide_settings": hide_settings,
        "username": get_setting("auth_username"),
        "username_locked": hide_settings or "auth_username" in locked,
        "using_default_password": get_setting("using_default_password") == "1",
        "sync": job_status(),
        "maintenance": {
            "cache_files": cache["files"],
            "cache_bytes": cache["bytes"],
            "library_count": library_count,
            "people_count": people_count,
            "unmatched_count": unmatched_count,
        },
    }


BOOL_SETTINGS = {"sync_schedule_enabled", "auto_delete_enabled"}

# key -> (default, min, max)
NUMERIC_SETTINGS = {
    "sync_interval_hours": ("24", 1, 168),
    "auto_delete_max_per_run": ("10", 1, 500),
    "auto_delete_stale_days": ("365", 1, 3650),
}


def _normalize_app_setting(key: str, value: str) -> str:
    cleaned = (value or "").strip()
    if key in BOOL_SETTINGS:
        return "1" if cleaned.lower() in {"1", "true", "on", "yes"} else "0"
    if key in NUMERIC_SETTINGS:
        default, low, high = NUMERIC_SETTINGS[key]
        try:
            return str(max(low, min(high, int(cleaned or default))))
        except ValueError:
            return default
    return cleaned


@app.put("/api/settings")
def put_settings(payload: SettingsIn, request: Request):
    current_user(request)
    locked = locked_setting_keys()
    hide_settings = hide_env_settings()
    for key, value in payload.values.items():
        if key in APP_SETTING_KEYS:
            set_setting(key, _normalize_app_setting(key, value))
            continue
        if hide_settings or key not in KEYS or key in locked:
            continue
        if key.endswith("_api_key"):
            cleaned = value.strip()
            if not cleaned or set(cleaned) <= {"•", "*"}:
                continue
            set_setting(key, cleaned)
            continue
        set_setting(key, value.strip())
    if not hide_settings and "auth_username" not in locked and (payload.username or payload.password):
        set_credentials(payload.username or "", payload.password)
    return {"ok": True, "hide_settings": hide_settings}


def _probe_service(name: str) -> dict:
    factory = SERVICES.get(name)
    if not factory:
        return {"service": name, "ok": False, "configured": False, "message": "Unknown service"}
    client = factory()
    if not client:
        return {"service": name, "ok": False, "configured": False, "message": "Not configured"}
    try:
        version = client.test()
        return {"service": name, "ok": True, "configured": True, "message": str(version)}
    except Exception as exc:
        return {"service": name, "ok": False, "configured": True, "message": str(exc)}


@app.post("/api/settings/test")
def test_service(payload: TestIn, request: Request):
    user = current_user(request)
    if payload.service not in SERVICES:
        raise HTTPException(400, "Unknown service")
    result = _probe_service(payload.service)
    add_log(
        f"Tested {payload.service}: {result['message']}",
        level="info" if result["ok"] else "error",
        category="system",
        action="test",
        actor=user,
    )
    return result


@app.post("/api/settings/test-all")
def test_all_services(request: Request):
    user = current_user(request)
    results = [_probe_service(name) for name in SERVICES]
    ok_count = sum(1 for row in results if row["ok"])
    add_log(
        f"Tested {ok_count}/{len(results)} services",
        category="system",
        action="test",
        actor=user,
        detail={"results": results},
    )
    return {"results": results, "ok": all(row["ok"] for row in results if row["configured"])}


@app.post("/api/settings/clear-cache")
def clear_poster_cache(request: Request):
    user = current_user(request)
    removed = clear_cache()
    cache = cache_stats()
    add_log(f"Cleared {removed} cached posters", category="system", action="clear-cache", actor=user)
    return {"ok": True, "removed": removed, "cache_files": cache["files"], "cache_bytes": cache["bytes"]}


@app.post("/api/settings/clear-library")
def clear_synced_library(request: Request):
    user = current_user(request)
    counts = clear_library()
    posters = clear_cache()
    reset_job("Library cleared")
    add_log(
        f"Cleared synced library ({counts['media']} titles, {counts['people']} users, {posters} posters)",
        category="system",
        action="clear-library",
        actor=user,
        detail={**counts, "posters": posters},
    )
    return {"ok": True, **counts, "posters": posters}


@app.get("/api/sync")
def sync_status(request: Request):
    current_user(request)
    return job_status()


@app.post("/api/sync")
def sync_now(request: Request):
    current_user(request)
    return start_sync()


@app.get("/api/logs")
def logs(
    request: Request,
    q: str = "",
    category: str = "",
    level: str = "",
    page: int = 1,
    page_size: int = 100,
):
    current_user(request)
    return list_logs(q=q, category=category, level=level, page=page, page_size=page_size)


@app.get("/api/users")
def list_users(request: Request, q: str = "", sort: str = "requests"):
    current_user(request)
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM people").fetchall()]
    items = []
    tautulli_base = public_url("tautulli", "tautulli_url")
    seerr_base = public_url("seerr", "seerr_url")
    jellystat_base = public_url("jellystat", "jellystat_url")
    for row in rows:
        aliases = json.loads(row.get("aliases_json") or "[]")
        sources = json.loads(row.get("sources_json") or "[]")
        if q:
            hay = f"{row.get('display_name')} {row.get('plex_username')} {row.get('email')} {' '.join(aliases)}".lower()
            if q.lower() not in hay:
                continue
        links = {}
        if tautulli_base and row.get("tautulli_id"):
            links["tautulli"] = f"{tautulli_base}/user?user_id={row['tautulli_id']}"
        if jellystat_base and row.get("jellystat_id"):
            links["jellystat"] = f"{jellystat_base}/users/{row['jellystat_id']}"
        if seerr_base and row.get("seerr_id"):
            links["seerr"] = f"{seerr_base}/users/{row['seerr_id']}"
        items.append(
            {
                **row,
                "aliases": aliases,
                "sources": sources,
                "links": links,
                "matched": bool(row.get("plex_username")),
            }
        )

    def sort_value(item):
        if sort == "library":
            return item.get("library_count") or 0
        if sort == "plays":
            return item.get("play_count") or 0
        if sort == "size":
            return item.get("library_size") or 0
        if sort == "name":
            return (item.get("display_name") or "").lower()
        return item.get("request_count") or 0

    items.sort(key=sort_value, reverse=sort != "name")
    return {
        "items": items,
        "stats": {
            "users": len(items),
            "requests": sum(item.get("request_count") or 0 for item in items),
            "library": sum(item.get("library_count") or 0 for item in items),
            "plays": sum(item.get("play_count") or 0 for item in items),
            "unmatched": sum(1 for item in items if not item.get("matched")),
        },
        "sync": job_status(),
    }


@app.get("/api/unmatched")
def unmatched(
    request: Request,
    q: str = "",
    source: str = "",
    media_type: str = "",
    kind: str = "",
    page: int = 1,
    page_size: int = 50,
):
    current_user(request)
    page = max(1, page)
    page_size = min(max(page_size, 10), 200)
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM unmatched ORDER BY kind DESC, title ASC").fetchall()]
    by_source: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for row in rows:
        by_source[row.get("source") or "unknown"] = by_source.get(row.get("source") or "unknown", 0) + 1
        row_kind = row.get("kind") or ""
        if row_kind:
            by_kind[row_kind] = by_kind.get(row_kind, 0) + 1
    items = []
    for row in rows:
        if source and row.get("source") != source:
            continue
        if kind and row.get("kind") != kind:
            continue
        if media_type and row.get("media_type") != media_type:
            continue
        if q and q.lower() not in f"{row.get('title') or ''} {row.get('source') or ''} {row.get('requested_by') or ''}".lower():
            continue
        items.append({**row, "links": _unmatched_links(row)})
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "stats": {
            "count": len(rows),
            **{f"{key}_count": value for key, value in by_source.items()},
            "seerr_missing": by_kind.get("seerr_missing") or 0,
            "no_seerr": by_kind.get("no_seerr") or 0,
        },
        "sync": job_status(),
    }


@app.get("/api/art/{item_id}")
def artwork(item_id: int, request: Request):
    current_user(request)
    return serve_art(item_id)


@app.get("/api/library")
def library(
    request: Request,
    q: str = "",
    media_type: str = "",
    watched: str = "",
    sort: str = "oldest",
    page: int = 1,
    page_size: int = 50,
    stale_days: int = 365,
    max_rating: float | None = None,
):
    current_user(request)
    page = max(1, page)
    page_size = min(max(page_size, 10), 200)
    stale_days = max(1, stale_days)
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM media").fetchall()]
        whitelist = [dict(row) for row in conn.execute("SELECT * FROM whitelist").fetchall()]
        unmatched_count = conn.execute("SELECT COUNT(*) AS n FROM unmatched").fetchone()["n"]

    cutoff = int(time.time()) - stale_days * 24 * 3600
    pool = []
    for row in rows:
        watchers = json.loads(row["watchers_json"] or "[]")
        sources = json.loads(row["sources_json"] or "[]")
        protected = is_protected(row["title"], row["media_type"], row["tmdb_id"], whitelist)
        item = {
            **row,
            "watchers": watchers,
            "sources": sources,
            "whitelisted": bool(protected),
            "whitelist_reason": protect_reason(protected) if protected else "",
            "links": _links(row),
            "art_url": art_url(row["id"], row.get("poster_url") or ""),
            "poster_url": "",
        }
        if media_type and item["media_type"] != media_type:
            continue
        if q:
            status = "requested not downloaded queued" if (item.get("availability") or "") == "requested" else (item.get("availability") or "")
            hay = f"{item['title']} {item['requested_by']} {' '.join(w['user'] for w in watchers)} {status}".lower()
            if q.lower() not in hay:
                continue
        if max_rating is not None:
            rating = item.get("rating")
            if rating is None or float(rating) > max_rating:
                continue
        pool.append(item)

    def on_disk(item):
        return (item.get("availability") or "downloaded") != "requested"

    never_watched = sum(1 for item in pool if on_disk(item) and not item["play_count"])
    stale_count = sum(1 for item in pool if is_stale_unwatched(item, cutoff))
    protected_count = sum(1 for item in pool if item["whitelisted"])
    requested_count = sum(1 for item in pool if (item.get("availability") or "downloaded") == "requested")
    items = []
    for item in pool:
        if watched == "protected":
            if not item["whitelisted"]:
                continue
        elif watched == "requested":
            if (item.get("availability") or "downloaded") != "requested":
                continue
        elif watched == "never":
            if item["play_count"] or not on_disk(item):
                continue
        elif watched == "watched" and not item["play_count"]:
            continue
        elif watched == "stale":
            if not is_stale_unwatched(item, cutoff):
                continue
        items.append(item)

    def sort_value(item):
        if sort == "title":
            return item["title"].lower()
        if sort == "size":
            return item["size_bytes"] or 0
        if sort == "plays":
            return item["play_count"] or 0
        if sort == "requested":
            return (item["requested_by"] or "").lower()
        if sort == "rating":
            rating = item.get("rating")
            return float(rating) if rating is not None else 99.0
        return item["last_watched_at"] or 0

    reverse = sort in {"size", "plays", "last_watched"}
    items.sort(key=sort_value, reverse=reverse)

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    stats = {
        "count": total,
        "never_watched": never_watched,
        "stale": stale_count,
        "whitelisted": protected_count,
        "requested": requested_count,
        "unmatched": unmatched_count,
        "size_bytes": sum(i["size_bytes"] or 0 for i in items),
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }
    return {
        "items": page_items,
        "stats": stats,
        "total": total,
        "page": page,
        "page_size": page_size,
        "whitelist_count": len(whitelist),
        "sync": job_status(),
    }


def _unmatched_links(row: dict) -> dict:
    links = {}
    media_type = row.get("media_type") or "movie"
    tmdb_id = row.get("tmdb_id")
    seerr_base = public_url("seerr", "seerr_url")
    if seerr_base and tmdb_id:
        kind = "movie" if media_type == "movie" else "tv"
        links["seerr"] = f"{seerr_base}/{kind}/{tmdb_id}"
    elif seerr_base and row.get("title"):
        links["seerr"] = f"{seerr_base}/search?query={quote(str(row['title']))}"
    if media_type == "movie" and tmdb_id:
        base = public_url("radarr", "radarr_url")
        if base:
            links["radarr"] = f"{base}/movie/{tmdb_id}"
    if media_type == "tv":
        base = public_url("sonarr", "sonarr_url")
        if base:
            links["sonarr"] = f"{base}/add/new?term={quote(str(row.get('title') or ''))}" if row.get("title") else base
    return links


def _links(row: dict) -> dict:
    links = {}
    if row["media_type"] == "movie" and row.get("radarr_id"):
        base = public_url("radarr", "radarr_url")
        if base:
            links["radarr"] = f"{base}/movie/{row['tmdb_id']}"
    if row["media_type"] == "tv" and row.get("sonarr_id"):
        base = public_url("sonarr", "sonarr_url")
        if base:
            slug = row.get("title_slug") or str(row.get("sonarr_id") or "")
            links["sonarr"] = f"{base}/series/{slug}"
    seerr_base = public_url("seerr", "seerr_url")
    if seerr_base and row.get("tmdb_id"):
        kind = "movie" if row["media_type"] == "movie" else "tv"
        links["seerr"] = f"{seerr_base}/{kind}/{row['tmdb_id']}"
    tautulli_base = public_url("tautulli", "tautulli_url")
    if tautulli_base:
        if row.get("tautulli_rating_key"):
            links["tautulli"] = f"{tautulli_base}/info?rating_key={row['tautulli_rating_key']}"
        elif row.get("title"):
            links["tautulli"] = f"{tautulli_base}/search?query={quote(str(row['title']))}"
    jellystat_base = public_url("jellystat", "jellystat_url")
    if jellystat_base:
        if row.get("jellystat_item_id"):
            links["jellystat"] = f"{jellystat_base}/libraries/item/{row['jellystat_item_id']}"
        elif row.get("title"):
            links["jellystat"] = f"{jellystat_base}/libraries"
    tracearr_base = public_url("tracearr", "tracearr_url")
    if tracearr_base:
        title = quote(str(row.get("title") or ""))
        links["tracearr"] = f"{tracearr_base}/history?q={title}" if title else f"{tracearr_base}/history"
    return links


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
