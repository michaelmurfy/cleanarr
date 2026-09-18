from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .actions import is_protected, router as actions_router
from .art import serve_art, art_url
from .auth import (
    bootstrap_auth,
    current_user,
    get_setting,
    login_response,
    set_credentials,
    verify_password,
    COOKIE,
)
from .config import env_file_present, locked_setting_keys
from .db import all_settings, connect, init_db, set_setting
from .services.clients import KEYS, cfg, public_url, radarr, seerr, sonarr, tautulli, tracearr
from .logs import add_log, list_logs
from .sync import job_status, restore_job, start_sync

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Cleanarr")
app.include_router(actions_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def startup() -> None:
    init_db()
    bootstrap_auth()
    restore_job()


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
    values = {}
    for key in KEYS:
        configured = bool(cfg(key) or stored.get(key))
        values[f"{key}_set"] = configured
        values[f"{key}_locked"] = env_locked or key in locked
        if key.endswith("_api_key"):
            values[key] = ""
        elif values[f"{key}_locked"]:
            values[key] = cfg(key)
        else:
            values[key] = stored.get(key) or ""
    return {
        "values": values,
        "locked": sorted(locked),
        "env_file": env_locked,
        "username": get_setting("auth_username"),
        "username_locked": env_locked or "auth_username" in locked,
        "using_default_password": get_setting("using_default_password") == "1",
    }


@app.put("/api/settings")
def put_settings(payload: SettingsIn, request: Request):
    current_user(request)
    locked = locked_setting_keys()
    if env_file_present():
        return {"ok": True, "locked": True}
    for key, value in payload.values.items():
        if key not in KEYS or key in locked:
            continue
        if key.endswith("_api_key"):
            cleaned = value.strip()
            if not cleaned or set(cleaned) <= {"•", "*"}:
                continue
            set_setting(key, cleaned)
            continue
        set_setting(key, value.strip())
    if "auth_username" not in locked and (payload.username or payload.password):
        set_credentials(payload.username or "", payload.password)
    return {"ok": True}


@app.post("/api/settings/test")
def test_service(payload: TestIn, request: Request):
    user = current_user(request)
    testers = {
        "tautulli": tautulli,
        "tracearr": tracearr,
        "seerr": seerr,
        "radarr": radarr,
        "sonarr": sonarr,
    }
    factory = testers.get(payload.service)
    if not factory:
        raise HTTPException(400, "Unknown service")
    client = factory()
    if not client:
        raise HTTPException(400, f"{payload.service} is not configured")
    try:
        version = client.test()
        add_log(f"Tested {payload.service}: {version}", category="system", action="test", actor=user)
        return {"ok": True, "message": str(version)}
    except Exception as exc:
        add_log(f"Test {payload.service} failed: {exc}", level="error", category="system", action="test", actor=user)
        raise HTTPException(400, str(exc)) from exc


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
            "whitelist_reason": protected["pattern"] if protected else "",
            "links": _links(row),
            "art_url": art_url(row["id"], row.get("poster_url") or ""),
            "poster_url": "",
        }
        if media_type and item["media_type"] != media_type:
            continue
        if q:
            hay = f"{item['title']} {item['requested_by']} {' '.join(w['user'] for w in watchers)}".lower()
            if q.lower() not in hay:
                continue
        if max_rating is not None:
            rating = item.get("rating")
            if rating is None or float(rating) > max_rating:
                continue
        pool.append(item)

    never_watched = sum(1 for item in pool if not item["play_count"])
    stale_count = sum(
        1 for item in pool if not item["play_count"] or (item["last_watched_at"] or 0) <= cutoff
    )
    items = []
    for item in pool:
        if watched == "never" and item["play_count"]:
            continue
        if watched == "watched" and not item["play_count"]:
            continue
        if watched == "stale":
            if item["play_count"] and (item["last_watched_at"] or 0) > cutoff:
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
        "whitelisted": sum(1 for i in items if i["whitelisted"]),
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
    return links


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
