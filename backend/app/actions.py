from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .art import remove_art
from .auth import current_user
from .db import connect
from .logs import add_log
from .services.clients import radarr, seerr, sonarr
from .services.http import ServiceError

router = APIRouter()


class WhitelistIn(BaseModel):
    match_type: str = "title"
    media_type: str = "any"
    tmdb_id: int = 0
    pattern: str
    note: str = ""


class CleanupIn(BaseModel):
    items: list[dict] = Field(default_factory=list)
    delete_files: bool = True
    blacklist: bool = False


class ClearSeerrIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    all_stale: bool = False


def _whitelist_rows() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM whitelist ORDER BY pattern COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]


def is_protected(title: str, media_type: str, tmdb_id: int, rows: list[dict] | None = None) -> dict | None:
    needle = (title or "").lower()
    for row in rows if rows is not None else _whitelist_rows():
        if row["media_type"] not in {"any", media_type}:
            continue
        if row["match_type"] == "id":
            if tmdb_id and int(row["tmdb_id"] or 0) == int(tmdb_id):
                return row
            continue
        pattern = (row["pattern"] or "").lower().strip()
        if pattern and pattern in needle:
            return row
    return None


def protect_reason(row: dict) -> str:
    pattern = (row.get("pattern") or "").strip()
    if row.get("match_type") == "id":
        label = f"TMDB {row.get('tmdb_id') or pattern}"
        if pattern and not str(pattern).isdigit():
            label = f"{label} · {pattern}"
    else:
        label = f"contains “{pattern}”" if pattern else "title rule"
    note = (row.get("note") or "").strip()
    if note and note.casefold() != pattern.casefold():
        label = f"{label} · {note}"
    return label


@router.get("/whitelist")
def list_whitelist(request: Request):
    current_user(request)
    return {"items": _whitelist_rows()}


@router.post("/whitelist")
def add_whitelist(payload: WhitelistIn, request: Request):
    user = current_user(request)
    pattern = payload.pattern.strip()
    if not pattern:
        raise HTTPException(400, "Pattern is required")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.match_type if payload.match_type in {"title", "id"} else "title",
                payload.media_type if payload.media_type in {"any", "movie", "tv"} else "any",
                payload.tmdb_id,
                pattern,
                payload.note.strip(),
                int(time.time()),
            ),
        )
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    add_log(
        f"Whitelisted {pattern}",
        category="audit",
        action="whitelist_add",
        actor=user,
        detail={"pattern": pattern, "match_type": payload.match_type, "media_type": payload.media_type},
    )
    return {"ok": True, "id": item_id}


@router.delete("/whitelist/{item_id}")
def remove_whitelist(item_id: int, request: Request):
    user = current_user(request)
    with connect() as conn:
        row = conn.execute("SELECT pattern FROM whitelist WHERE id = ?", (item_id,)).fetchone()
        conn.execute("DELETE FROM whitelist WHERE id = ?", (item_id,))
    add_log(
        f"Removed whitelist {row['pattern'] if row else item_id}",
        category="audit",
        action="whitelist_remove",
        actor=user,
    )
    return {"ok": True}


def _load_media(media_type: str, tmdb_id: int, tvdb_id: int = 0) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM media
            WHERE media_type = ? AND tmdb_id = ? AND (tvdb_id = ? OR ? = 0)
            """,
            (media_type, tmdb_id, tvdb_id, tvdb_id),
        ).fetchone()
    return dict(row) if row else None


@router.post("/cleanup")
def cleanup(payload: CleanupIn, request: Request):
    user = current_user(request)
    results = []
    radarr_client = radarr()
    sonarr_client = sonarr()
    seerr_client = seerr()
    for raw in payload.items:
        media_type = raw.get("media_type")
        tmdb_id = int(raw.get("tmdb_id") or 0)
        tvdb_id = int(raw.get("tvdb_id") or 0)
        item = _load_media(media_type, tmdb_id, tvdb_id)
        if not item:
            results.append({"title": raw.get("title"), "ok": False, "error": "Not found"})
            continue
        blocked = is_protected(item["title"], item["media_type"], item["tmdb_id"])
        if blocked:
            results.append(
                {
                    "title": item["title"],
                    "ok": False,
                    "error": f"Whitelisted ({blocked['pattern']})",
                }
            )
            continue
        try:
            if item["media_type"] == "movie":
                if not item.get("radarr_id") or not radarr_client:
                    raise RuntimeError("No Radarr id for this movie")
                radarr_client.delete(int(item["radarr_id"]), payload.delete_files, payload.blacklist)
            else:
                if not item.get("sonarr_id") or not sonarr_client:
                    raise RuntimeError("No Sonarr id for this series")
                sonarr_client.delete(int(item["sonarr_id"]), payload.delete_files, payload.blacklist)
            if seerr_client:
                if payload.blacklist and item.get("tmdb_id"):
                    try:
                        seerr_client.blacklist(int(item["tmdb_id"]), item["media_type"], item["title"])
                    except Exception:
                        pass
                if item.get("seerr_media_id"):
                    try:
                        seerr_client.delete_media(int(item["seerr_media_id"]))
                    except Exception:
                        pass
            with connect() as conn:
                conn.execute("DELETE FROM media WHERE id = ?", (item["id"],))
            remove_art(item["media_type"], item.get("tmdb_id") or 0, item.get("tvdb_id") or 0)
            results.append({"title": item["title"], "ok": True})
            add_log(
                f"{'Banned and deleted' if payload.blacklist else 'Deleted'} {item['title']}",
                category="audit",
                action="ban" if payload.blacklist else "delete",
                actor=user,
                detail={"media_type": item["media_type"], "tmdb_id": item.get("tmdb_id")},
            )
        except Exception as exc:
            results.append({"title": item["title"], "ok": False, "error": str(exc)})
            add_log(
                f"Failed to delete {item['title']}: {exc}",
                level="error",
                category="audit",
                action="delete_error",
                actor=user,
            )
    return {"results": results}


@router.post("/unmatched/clear-seerr")
def clear_stale_seerr(payload: ClearSeerrIn, request: Request):
    user = current_user(request)
    client = seerr()
    if not client:
        raise HTTPException(400, "Seerr is not configured")
    if not payload.all_stale and not payload.ids:
        raise HTTPException(400, "Nothing selected")
    with connect() as conn:
        if payload.all_stale:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM unmatched WHERE kind = 'seerr_missing'"
            ).fetchall()]
        else:
            placeholders = ",".join("?" for _ in payload.ids)
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM unmatched WHERE kind = 'seerr_missing' AND id IN ({placeholders})",
                payload.ids,
            ).fetchall()]
    results = []
    for row in rows:
        title = row.get("title") or "Untitled"
        try:
            media_id = int(row.get("seerr_media_id") or 0)
            if not media_id:
                raise RuntimeError("No Seerr media id to delete")
            try:
                client.delete_media(media_id)
            except ServiceError as exc:
                if exc.status not in {404, 410}:
                    raise
            with connect() as conn:
                conn.execute("DELETE FROM unmatched WHERE id = ?", (row["id"],))
            results.append({"title": title, "ok": True})
            add_log(
                f"Cleared stale Seerr record {title}",
                category="audit",
                action="clear-seerr",
                actor=user,
                detail={"tmdb_id": row.get("tmdb_id"), "seerr_media_id": row.get("seerr_media_id")},
            )
        except Exception as exc:
            results.append({"title": title, "ok": False, "error": str(exc)})
            add_log(
                f"Failed to clear Seerr record {title}: {exc}",
                level="error",
                category="audit",
                action="clear-seerr",
                actor=user,
            )
    remaining = 0
    with connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) AS n FROM unmatched").fetchone()["n"]
    return {"results": results, "remaining": remaining}
