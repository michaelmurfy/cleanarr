from __future__ import annotations

import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .actions import Whitelist, is_stale_unwatched, matches_to_review, protect_reason, router as actions_router
from .art import art_url, cache_stats, clear_cache, serve_art
from .auth import (
    bootstrap_auth,
    complete_setup,
    current_user,
    get_setting,
    login_response,
    needs_setup,
    set_credentials,
    verify_password,
    COOKIE,
)
from .backup import MAX_RESTORE_BYTES, apply_backup, build_backup
from .config import APP_SETTING_KEYS, env_file_present, hide_env_settings, locked_setting_keys
from .db import all_settings, clear_library, connect, init_db, set_setting
from .security import SecurityMiddleware, client_key, login_throttle, redact
from .services.clients import KEYS, cfg, public_url, jellystat, radarr, radarr_4k, seerr, sonarr, tautulli, tracearr
from .logs import add_log, list_logs
from .sync import job_status, reset_job, restore_job, start_scheduler, start_sync
from .version import current_version

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_auth()
    restore_job()
    start_scheduler()
    yield


class _GZip(GZipMiddleware):
    """Compress JSON and the bundle; posters are already compressed images."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/art/"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app = FastAPI(title="Cleanarr", version=current_version(), lifespan=lifespan)
app.add_middleware(SecurityMiddleware)
app.add_middleware(_GZip, minimum_size=1024)
app.include_router(actions_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True, "version": current_version()}


SERVICES = {
    "tautulli": tautulli,
    "tracearr": tracearr,
    "jellystat": jellystat,
    "seerr": seerr,
    "radarr": radarr,
    "radarr_4k": radarr_4k,
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


@app.get("/api/auth/status")
def auth_status():
    return {"setup_required": needs_setup()}


@app.post("/api/auth/setup")
def setup(payload: LoginIn, request: Request):
    complete_setup(payload.username, payload.password)
    return login_response(get_setting("auth_username"), request)


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request):
    if needs_setup():
        raise HTTPException(status_code=403, detail="Create an admin account first")
    throttle_key = client_key(request, payload.username)
    wait = login_throttle.retry_after(throttle_key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed sign-ins. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    username = get_setting("auth_username")
    # Both checks always run so a wrong username is not faster than a wrong password.
    name_ok = hmac.compare_digest(payload.username or "", username or "")
    password_ok = verify_password(payload.password)
    if not (name_ok and password_ok):
        locked = login_throttle.record_failure(throttle_key)
        add_log(
            f"Failed sign-in for '{payload.username}'" + (", temporarily locked out" if locked else ""),
            level="warn",
            category="audit",
            action="login_failed",
            detail={"client": request.client.host if request.client else "unknown"},
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")
    login_throttle.record_success(throttle_key)
    return login_response(username, request)


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


def _friendly_probe_detail(raw: object) -> str:
    text = str(raw or "").strip()
    if not text or text.lower() in {"ok", "none", "null", "true"}:
        return ""
    if text[:1].isdigit() and all(part.isdigit() for part in text.replace("-", ".").split(".")):
        return f"v{text.lstrip('vV')}"
    return text


def _probe_service(name: str) -> dict:
    factory = SERVICES.get(name)
    if not factory:
        return {"service": name, "ok": False, "configured": False, "message": "Unknown service", "detail": ""}
    client = factory()
    if not client:
        return {"service": name, "ok": False, "configured": False, "message": "Not configured", "detail": ""}
    try:
        detail = _friendly_probe_detail(client.test())
        return {
            "service": name,
            "ok": True,
            "configured": True,
            "message": "Passed",
            # Success detail comes from the upstream app too, so it gets the same scrub.
            "detail": redact(detail) if detail else "",
        }
    except Exception as exc:
        return {
            "service": name,
            "ok": False,
            "configured": True,
            "message": "Failed",
            "detail": redact(exc),
        }


@app.post("/api/settings/test")
def test_service(payload: TestIn, request: Request):
    user = current_user(request)
    if payload.service not in SERVICES:
        raise HTTPException(400, "Unknown service")
    result = _probe_service(payload.service)
    summary = result["message"] if not result.get("detail") else f"{result['message']}: {result['detail']}"
    add_log(
        f"Tested {payload.service}: {summary}",
        level="info" if result["ok"] else "error",
        category="system",
        action="test",
        actor=user,
    )
    return result


@app.post("/api/settings/test-all")
def test_all_services(request: Request):
    user = current_user(request)
    results = []
    for name in SERVICES:
        row = _probe_service(name)
        if row["configured"]:
            results.append(row)
    ok_count = sum(1 for row in results if row["ok"])
    add_log(
        f"Tested {ok_count}/{len(results)} services",
        category="system",
        action="test",
        actor=user,
        detail={"results": results},
    )
    return {"results": results, "ok": bool(results) and all(row["ok"] for row in results)}


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


@app.get("/api/settings/backup")
def backup_settings(request: Request):
    user = current_user(request)
    payload = build_backup()
    stamp = time.strftime("%Y%m%d", time.gmtime(payload["exported_at"]))
    filename = f"cleanarr-config-{stamp}.json"
    add_log(
        "Downloaded configuration backup",
        category="audit",
        action="backup",
        actor=user,
        detail={
            "settings": len(payload.get("settings") or {}),
            "skipped_locked": len(payload.get("skipped_locked") or []),
            "whitelist": len(payload.get("whitelist") or []),
            "unmatched_ignored": len(payload.get("unmatched_ignored") or []),
            "match_decisions": len(payload.get("match_decisions") or []),
        },
    )
    body = json.dumps(payload, indent=2, sort_keys=True)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/settings/restore")
async def restore_settings(request: Request):
    user = current_user(request)
    raw = await request.body()
    if len(raw) > MAX_RESTORE_BYTES:
        raise HTTPException(400, "Backup file is too large")
    if not raw.strip():
        raise HTTPException(400, "Backup file is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Backup file is not valid JSON") from exc
    result = apply_backup(payload)
    add_log(
        "Restored configuration backup",
        category="audit",
        action="restore",
        actor=user,
        detail={
            "settings_applied": result["settings_applied"],
            "settings_skipped": result["settings_skipped"],
            "whitelist": result["whitelist"],
            "unmatched_ignored": result["unmatched_ignored"],
            "match_decisions": result["match_decisions"],
        },
    )
    return result


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
            hay = f"{row.get('display_name')} {row.get('account_username')} {row.get('email')} {' '.join(aliases)}".lower()
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
                "matched": bool(row.get("account_username")),
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
        ignored_count = conn.execute("SELECT COUNT(*) AS n FROM unmatched_ignored").fetchone()["n"]
        review_count = len(matches_to_review(conn))
        decided_count = conn.execute("SELECT COUNT(*) AS n FROM match_ignored").fetchone()["n"]
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
        items.append(row)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": [{**row, "links": _unmatched_links(row)} for row in items[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "stats": {
            "count": len(rows),
            # seerr_deleted rows are history, not something to fix, so they stay out of the
            # count the nav badge and Library card use.
            # Title-guessed Seerr links waiting for a yes/no count as work too.
            "actionable": len(rows) - (by_kind.get("seerr_deleted") or 0) + review_count,
            "review": review_count,
            "decided": decided_count,
            **{f"{key}_count": value for key, value in by_source.items()},
            "seerr_missing": by_kind.get("seerr_missing") or 0,
            "seerr_deleted": by_kind.get("seerr_deleted") or 0,
            "no_seerr": by_kind.get("no_seerr") or 0,
            "ignored": ignored_count,
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
    requester: str = "",
    hide_unprocessed: bool = True,
):
    current_user(request)
    page = max(1, page)
    page_size = min(max(page_size, 10), 200)
    stale_days = max(1, stale_days)
    requester_key = requester.strip().lower()
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM media").fetchall()]
        whitelist = [dict(row) for row in conn.execute("SELECT * FROM whitelist").fetchall()]
        unmatched_count = conn.execute(
            "SELECT COUNT(*) AS n FROM unmatched WHERE kind != 'seerr_deleted'"
        ).fetchone()["n"] + len(matches_to_review(conn))

    cutoff = int(time.time()) - stale_days * 24 * 3600
    rules = Whitelist(whitelist)
    pool = []
    for row in rows:
        # Cheap column filters first; JSON is only decoded for rows that survive them.
        if media_type and row["media_type"] != media_type:
            continue
        if requester_key and (row.get("requested_by") or "").strip().lower() != requester_key:
            continue
        protected = rules.match(row["title"], row["media_type"], row["tmdb_id"])
        item = {
            **row,
            "whitelisted": bool(protected),
            "whitelist_reason": protect_reason(protected) if protected else "",
        }
        if q:
            watchers = json.loads(row["watchers_json"] or "[]")
            item["watchers"] = watchers
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
        else:
            # A protected title is never a deletion candidate, so mixing it into
            # every other view (including a plain search) is noise the user has
            # already resolved. The Protected filter above is the one place it
            # still shows up.
            if item["whitelisted"]:
                continue
            if watched == "requested":
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
        # Pending Seerr requests stay out of the main lists unless you ask for them.
        if hide_unprocessed and watched != "requested" and not on_disk(item):
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
        if sort == "requests":
            never = 0 if on_disk(item) and not item["play_count"] else 1
            requested_at = (item.get("requested_at") or "").strip() or "9999-99-99"
            return (never, requested_at, item["title"].lower())
        return item["last_watched_at"] or 0

    reverse = sort in {"size", "plays", "last_watched"}
    items.sort(key=sort_value, reverse=reverse)

    total = len(items)
    start = (page - 1) * page_size
    # Links and poster URLs are only built for the rows actually returned.
    bases = _link_bases()
    for item in items[start : start + page_size]:
        if "watchers" not in item:
            item["watchers"] = json.loads(item.get("watchers_json") or "[]")
        item["sources"] = json.loads(item.get("sources_json") or "[]")
    page_items = [
        {
            **item,
            "links": _links(item, bases),
            "art_url": art_url(item["id"], item.get("poster_url") or ""),
            "poster_url": "",
        }
        for item in items[start : start + page_size]
    ]
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
        base_4k = public_url("radarr_4k", "radarr_4k_url")
        if base_4k:
            links["radarr_4k"] = f"{base_4k}/movie/{tmdb_id}"
    if media_type == "tv":
        base = public_url("sonarr", "sonarr_url")
        if base:
            links["sonarr"] = f"{base}/add/new?term={quote(str(row.get('title') or ''))}" if row.get("title") else base
    return links


def _link_bases() -> dict[str, str]:
    return {
        "radarr": public_url("radarr", "radarr_url"),
        "radarr_4k": public_url("radarr_4k", "radarr_4k_url"),
        "sonarr": public_url("sonarr", "sonarr_url"),
        "seerr": public_url("seerr", "seerr_url"),
        "tautulli": public_url("tautulli", "tautulli_url"),
        "jellystat": public_url("jellystat", "jellystat_url"),
        "tracearr": public_url("tracearr", "tracearr_url"),
    }


def _links(row: dict, bases: dict[str, str] | None = None) -> dict:
    bases = bases if bases is not None else _link_bases()
    links = {}
    if row["media_type"] == "movie":
        if row.get("radarr_id") and bases["radarr"]:
            links["radarr"] = f"{bases['radarr']}/movie/{row['tmdb_id']}"
        if row.get("radarr_4k_id") and bases["radarr_4k"]:
            links["radarr_4k"] = f"{bases['radarr_4k']}/movie/{row['tmdb_id']}"
    if row["media_type"] == "tv" and row.get("sonarr_id") and bases["sonarr"]:
        slug = row.get("title_slug") or str(row.get("sonarr_id") or "")
        links["sonarr"] = f"{bases['sonarr']}/series/{slug}"
    # Only surface Seerr / history apps when we actually have a linked id. A
    # search fallback made every title look matched via Tautulli or Seerr.
    seerr_tmdb = int(row.get("seerr_tmdb_id") or 0) or (int(row.get("tmdb_id") or 0) if row.get("seerr_media_id") else 0)
    if bases["seerr"] and row.get("seerr_media_id") and seerr_tmdb:
        kind = "movie" if row["media_type"] == "movie" else "tv"
        links["seerr"] = f"{bases['seerr']}/{kind}/{seerr_tmdb}"
    if bases["tautulli"] and row.get("tautulli_rating_key"):
        links["tautulli"] = f"{bases['tautulli']}/info?rating_key={row['tautulli_rating_key']}"
    if bases["jellystat"] and row.get("jellystat_item_id"):
        links["jellystat"] = f"{bases['jellystat']}/libraries/item/{row['jellystat_item_id']}"
    sources = row.get("sources")
    if sources is None and row.get("sources_json"):
        try:
            sources = json.loads(row["sources_json"] or "[]")
        except Exception:
            sources = []
    if bases["tracearr"] and "tracearr" in (sources or []):
        title = quote(str(row.get("title") or ""))
        links["tracearr"] = f"{bases['tracearr']}/history?q={title}" if title else f"{bases['tracearr']}/history"
    return links


def _static_file(full_path: str) -> Path | None:
    """Resolve a request path inside STATIC_DIR, or None if it escapes or is hidden.

    The route is a catch-all, so `full_path` is attacker-controlled and can carry
    encoded `..` segments or an absolute path. Resolving and then confirming the
    result is still under STATIC_DIR is what keeps this from serving /data or .env.
    """
    if not full_path:
        return None
    root = STATIC_DIR.resolve()
    candidate = (root / full_path).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    if any(part.startswith(".") for part in candidate.relative_to(root).parts):
        return None
    return candidate if candidate.is_file() else None


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


# Registered whether or not a bundle was built, so the tests can exercise it; without
# one the handler 404s. HEAD is spelled out because FastAPI does not derive it from
# GET, and an icon scraper sends HEAD /favicon.ico before it downloads anything.
@app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def spa(full_path: str):
    # Unknown API routes must not fall through to the HTML shell.
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    candidate = _static_file(full_path)
    if candidate:
        return FileResponse(candidate, headers={"Cache-Control": "public, max-age=86400"})
    # The shell names the hashed bundles, so caching it strands clients on an old build.
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
