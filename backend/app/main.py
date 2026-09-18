from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .actions import is_protected, router as actions_router
from .auth import (
    bootstrap_auth,
    current_user,
    get_setting,
    login_response,
    set_credentials,
    verify_password,
    COOKIE,
)
from .config import settings
from .db import all_settings, init_db, set_setting
from .services.clients import KEYS, cfg, public_url, radarr, seerr, sonarr, tautulli, tracearr
from .sync import job_status, start_sync
from .db import connect

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
    for key in KEYS:
        if not get_setting(key) and cfg(key):
            set_setting(key, cfg(key))


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
    values = {}
    for key in KEYS:
        value = stored.get(key) or cfg(key)
        if key.endswith("_api_key") and value:
            values[key] = "••••••••"
            values[f"{key}_set"] = True
        else:
            values[key] = value
            values[f"{key}_set"] = bool(value)
    return {
        "values": values,
        "username": get_setting("auth_username"),
        "using_default_password": get_setting("using_default_password") == "1",
    }


@app.put("/api/settings")
def put_settings(payload: SettingsIn, request: Request):
    current_user(request)
    for key, value in payload.values.items():
        if key not in KEYS:
            continue
        if key.endswith("_api_key") and value.strip("•") == "":
            continue
        set_setting(key, value.strip())
    if payload.username or payload.password:
        set_credentials(payload.username or "", payload.password)
    return {"ok": True}


@app.post("/api/settings/test")
def test_service(payload: TestIn, request: Request):
    current_user(request)
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
        return {"ok": True, "message": str(version)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/sync")
def sync_status(request: Request):
    current_user(request)
    return job_status()


@app.post("/api/sync")
def sync_now(request: Request):
    current_user(request)
    return start_sync()


@app.get("/api/library")
def library(request: Request, q: str = "", media_type: str = "", watched: str = "", sort: str = "last_watched"):
    current_user(request)
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM media").fetchall()]
        whitelist = [dict(row) for row in conn.execute("SELECT * FROM whitelist").fetchall()]

    items = []
    for row in rows:
        watchers = json.loads(row["watchers_json"] or "[]")
        sources = json.loads(row["sources_json"] or "[]")
        protected = is_protected(row["title"], row["media_type"], row["tmdb_id"])
        item = {
            **row,
            "watchers": watchers,
            "sources": sources,
            "whitelisted": bool(protected),
            "whitelist_reason": protected["pattern"] if protected else "",
            "links": _links(row),
        }
        if media_type and item["media_type"] != media_type:
            continue
        if q:
            hay = f"{item['title']} {item['requested_by']} {' '.join(w['user'] for w in watchers)}".lower()
            if q.lower() not in hay:
                continue
        if watched == "never" and item["play_count"]:
            continue
        if watched == "watched" and not item["play_count"]:
            continue
        if watched == "stale":
            import time

            cutoff = int(time.time()) - 365 * 24 * 3600
            if item["play_count"] and (item["last_watched_at"] or 0) > cutoff:
                continue
            if not item["play_count"]:
                pass
        items.append(item)

    reverse = True
    key = sort
    if sort.startswith("-"):
        reverse = False
        key = sort[1:]

    def sort_value(item):
        if key == "title":
            return item["title"].lower()
        if key == "size":
            return item["size_bytes"] or 0
        if key == "plays":
            return item["play_count"] or 0
        if key == "requested":
            return (item["requested_by"] or "").lower()
        return item["last_watched_at"] or 0

    items.sort(key=sort_value, reverse=False if key in {"title", "requested"} else reverse)

    stats = {
        "count": len(items),
        "never_watched": sum(1 for i in items if not i["play_count"]),
        "whitelisted": sum(1 for i in items if i["whitelisted"]),
        "size_bytes": sum(i["size_bytes"] or 0 for i in items),
    }
    return {"items": items, "stats": stats, "whitelist_count": len(whitelist), "sync": job_status()}


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
