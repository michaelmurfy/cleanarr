from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .art import remove_art
from .auth import current_user
from .db import connect, ignore_key
from .logs import add_log
from .services.clients import radarr, radarr_4k, seerr, sonarr
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


class AddSeerrIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    all_missing: bool = False


class IgnoreIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


def _whitelist_rows() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM whitelist ORDER BY pattern COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]


def _whitelist_matches(rule: dict, media: list[dict]) -> list[dict]:
    """Library titles this one rule currently protects."""
    matches = []
    for item in media:
        if not is_protected(item["title"], item["media_type"], int(item.get("tmdb_id") or 0), [rule]):
            continue
        matches.append(
            {
                "id": item["id"],
                "title": item["title"],
                "year": item.get("year"),
                "media_type": item["media_type"],
                "tmdb_id": int(item.get("tmdb_id") or 0),
            }
        )
    matches.sort(key=lambda row: (row["title"] or "").casefold())
    return matches


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


def is_stale_unwatched(item: dict, cutoff: int) -> bool:
    """On disk, added before the cutoff, and not played since it.

    Shared by the library's Stale / unwatched filter and automatic delete so the
    list you review is exactly the set a scheduled run would remove. A title played
    at an unknown time never qualifies, and neither does one with no added date,
    so missing data always fails closed.
    """
    if (item.get("availability") or "downloaded") == "requested":
        return False
    added = item.get("added_at")
    if not added or int(added) > cutoff:
        return False
    if item.get("play_count"):
        last_watched = item.get("last_watched_at")
        if not last_watched or int(last_watched) > cutoff:
            return False
    return True


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
    with connect() as conn:
        rules = [dict(row) for row in conn.execute("SELECT * FROM whitelist ORDER BY pattern COLLATE NOCASE").fetchall()]
        media = [
            dict(row)
            for row in conn.execute(
                "SELECT id, title, year, media_type, tmdb_id FROM media ORDER BY title COLLATE NOCASE"
            ).fetchall()
        ]
    items = []
    for rule in rules:
        matches = _whitelist_matches(rule, media)
        items.append({**rule, "matches": matches, "match_count": len(matches)})
    return {"items": items}


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


def delete_item(
    item: dict,
    *,
    delete_files: bool,
    blacklist: bool,
    actor: str,
    radarr_client=None,
    radarr_4k_client=None,
    sonarr_client=None,
    seerr_client=None,
) -> dict:
    """Delete one title in Radarr/Sonarr, Seerr and the local library. Raises on *arr failure."""
    if item["media_type"] == "movie":
        deleted = False
        if item.get("radarr_id"):
            if not radarr_client:
                raise RuntimeError("Radarr is not configured")
            radarr_client.delete(int(item["radarr_id"]), delete_files, blacklist)
            deleted = True
        if item.get("radarr_4k_id"):
            if not radarr_4k_client:
                raise RuntimeError("Radarr 4K is not configured")
            radarr_4k_client.delete(int(item["radarr_4k_id"]), delete_files, blacklist)
            deleted = True
        if not deleted:
            raise RuntimeError("No Radarr id for this movie")
    else:
        if not item.get("sonarr_id") or not sonarr_client:
            raise RuntimeError("No Sonarr id for this series")
        sonarr_client.delete(int(item["sonarr_id"]), delete_files, blacklist)
    if seerr_client:
        if blacklist and item.get("tmdb_id"):
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
    add_log(
        f"{'Banned and deleted' if blacklist else 'Deleted'} {item['title']}",
        category="audit",
        action="ban" if blacklist else "delete",
        actor=actor,
        detail={"media_type": item["media_type"], "tmdb_id": item.get("tmdb_id")},
    )
    return {"title": item["title"], "ok": True}


@router.post("/cleanup")
def cleanup(payload: CleanupIn, request: Request):
    user = current_user(request)
    results = []
    radarr_client = radarr()
    radarr_4k_client = radarr_4k()
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
            results.append(
                delete_item(
                    item,
                    delete_files=payload.delete_files,
                    blacklist=payload.blacklist,
                    actor=user,
                    radarr_client=radarr_client,
                    radarr_4k_client=radarr_4k_client,
                    sonarr_client=sonarr_client,
                    seerr_client=seerr_client,
                )
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
        remaining = _remaining(conn)
    return {"results": results, "remaining": remaining}


@router.post("/unmatched/add-seerr")
def add_missing_seerr(payload: AddSeerrIn, request: Request):
    user = current_user(request)
    client = seerr()
    if not client:
        raise HTTPException(400, "Seerr is not configured")
    if not payload.all_missing and not payload.ids:
        raise HTTPException(400, "Nothing selected")
    with connect() as conn:
        if payload.all_missing:
            # Seerr is keyed on TMDB, so a bulk add skips rows it could never take.
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM unmatched WHERE kind = 'no_seerr' AND tmdb_id > 0"
            ).fetchall()]
        else:
            placeholders = ",".join("?" for _ in payload.ids)
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM unmatched WHERE kind = 'no_seerr' AND id IN ({placeholders})",
                payload.ids,
            ).fetchall()]
    results = []
    for row in rows:
        title = row.get("title") or "Untitled"
        try:
            tmdb_id = int(row.get("tmdb_id") or 0)
            if not tmdb_id:
                raise RuntimeError("No TMDB id to request")
            try:
                client.request_media(tmdb_id, row.get("media_type") or "movie")
            except ServiceError as exc:
                # 409 duplicate request, 403 blocklisted: Seerr already knows about the title.
                if exc.status not in {409, 403}:
                    raise
            with connect() as conn:
                conn.execute("DELETE FROM unmatched WHERE id = ?", (row["id"],))
            results.append({"title": title, "ok": True})
            add_log(
                f"Added {title} to Seerr",
                category="audit",
                action="add-seerr",
                actor=user,
                detail={"media_type": row.get("media_type"), "tmdb_id": row.get("tmdb_id")},
            )
        except Exception as exc:
            results.append({"title": title, "ok": False, "error": str(exc)})
            add_log(
                f"Failed to add {title} to Seerr: {exc}",
                level="error",
                category="audit",
                action="add-seerr",
                actor=user,
            )
    with connect() as conn:
        remaining = _remaining(conn)
    return {"results": results, "remaining": remaining}


@router.get("/unmatched/ignored")
def list_ignored(request: Request):
    current_user(request)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM unmatched_ignored ORDER BY title COLLATE NOCASE"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("/unmatched/ignore")
def ignore_unmatched(payload: IgnoreIn, request: Request):
    user = current_user(request)
    if not payload.ids:
        raise HTTPException(400, "Nothing selected")
    placeholders = ",".join("?" for _ in payload.ids)
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM unmatched WHERE id IN ({placeholders})", payload.ids
        ).fetchall()]
        for row in rows:
            key = ignore_key(
                row.get("kind") or row.get("source") or "",
                row.get("media_type") or "",
                row.get("tmdb_id"),
                row.get("tvdb_id"),
                row.get("title") or "",
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO unmatched_ignored
                    (kind, media_type, tmdb_id, tvdb_id, title_key, title, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*key, row.get("title") or "", row.get("reason") or "", int(time.time())),
            )
            conn.execute("DELETE FROM unmatched WHERE id = ?", (row["id"],))
    for row in rows:
        add_log(
            f"Ignoring unmatched {row.get('title')}",
            category="audit",
            action="ignore-unmatched",
            actor=user,
            detail={"kind": row.get("kind"), "tmdb_id": row.get("tmdb_id"), "tvdb_id": row.get("tvdb_id")},
        )
    with connect() as conn:
        remaining = _remaining(conn)
    return {"ignored": len(rows), "remaining": remaining}


@router.delete("/unmatched/ignored/{item_id}")
def unignore_unmatched(item_id: int, request: Request):
    user = current_user(request)
    with connect() as conn:
        row = conn.execute("SELECT * FROM unmatched_ignored WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        conn.execute("DELETE FROM unmatched_ignored WHERE id = ?", (item_id,))
    add_log(
        f"Stopped ignoring unmatched {row['title']}",
        category="audit",
        action="unignore-unmatched",
        actor=user,
    )
    return {"ok": True}


SHAKY_MATCHES = ("title", "title_alt")


def _pair(item: dict) -> tuple:
    seerr_tmdb = int(item.get("seerr_tmdb_id") or 0)
    if not seerr_tmdb and (item.get("seerr_match_via") or "") == "tmdb":
        seerr_tmdb = int(item.get("tmdb_id") or 0)
    return (
        item.get("media_type") or "",
        seerr_tmdb,
        0,
        int(item.get("tmdb_id") or 0),
        int(item.get("tvdb_id") or 0),
    )


def matches_to_review(conn) -> list[dict]:
    """Library titles whose Seerr request was attached by a title guess and nobody has confirmed yet."""
    rows = [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM media WHERE seerr_match_via IN ({','.join('?' for _ in SHAKY_MATCHES)})"
            " ORDER BY title COLLATE NOCASE",
            SHAKY_MATCHES,
        ).fetchall()
    ]
    kept = {
        (r["media_type"], r["seerr_tmdb_id"], r["seerr_tvdb_id"], r["library_tmdb_id"], r["library_tvdb_id"])
        for r in conn.execute("SELECT * FROM match_ignored WHERE action = 'keep'").fetchall()
    }
    return [row for row in rows if _pair(row) not in kept]


def _remaining(conn) -> int:
    unmatched = conn.execute("SELECT COUNT(*) AS n FROM unmatched").fetchone()["n"]
    return unmatched + len(matches_to_review(conn))


class MatchDecisionIn(BaseModel):
    reason: str = ""


def _decide(item_id: int, action: str, reason: str, user: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        item = dict(row)
        if not item.get("seerr_match_via") and not item.get("seerr_media_id"):
            raise HTTPException(400, "This title is not linked to Seerr")
        pair = _pair(item)
        conn.execute(
            """
            INSERT INTO match_ignored
                (media_type, seerr_tmdb_id, seerr_tvdb_id, library_tmdb_id, library_tvdb_id,
                 seerr_title, library_title, reason, action, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_type, seerr_tmdb_id, seerr_tvdb_id, library_tmdb_id, library_tvdb_id)
            DO UPDATE SET action = excluded.action, reason = excluded.reason,
                seerr_title = excluded.seerr_title, library_title = excluded.library_title,
                created_at = excluded.created_at
            """,
            (
                *pair,
                item.get("seerr_title") or "",
                item.get("title") or "",
                reason,
                action,
                int(time.time()),
            ),
        )
        if action == "unlink":
            conn.execute(
                """
                UPDATE media
                SET seerr_media_id = NULL, requested_by = '', requested_at = '',
                    seerr_match_via = '', seerr_tmdb_id = 0, seerr_title = ''
                WHERE id = ?
                """,
                (item_id,),
            )
        remaining = _remaining(conn)
    add_log(
        f"{'Unlinked' if action == 'unlink' else 'Confirmed'} Seerr match for {item.get('title')}",
        category="audit",
        action=f"match-{action}",
        actor=user,
        detail={
            "media_id": item_id,
            "seerr_title": item.get("seerr_title") or "",
            "seerr_tmdb_id": pair[1],
            "library_tmdb_id": pair[3],
            "via": item.get("seerr_match_via") or "",
        },
    )
    return {"ok": True, "remaining": remaining}


@router.get("/matches/review")
def review_matches(request: Request):
    current_user(request)
    with connect() as conn:
        rows = matches_to_review(conn)
    return {
        "items": [
            {
                "id": row["id"],
                "media_type": row["media_type"],
                "title": row["title"],
                "year": row.get("year"),
                "tmdb_id": row.get("tmdb_id") or 0,
                "seerr_title": row.get("seerr_title") or "",
                "seerr_tmdb_id": row.get("seerr_tmdb_id") or 0,
                "via": row.get("seerr_match_via") or "",
                "requested_by": row.get("requested_by") or "",
                "requested_at": row.get("requested_at") or "",
            }
            for row in rows
        ]
    }


@router.post("/matches/{item_id}/unlink")
def unlink_match(item_id: int, payload: MatchDecisionIn, request: Request):
    """Detach a wrong Seerr request from a library title and keep it detached on later syncs."""
    user = current_user(request)
    return _decide(item_id, "unlink", (payload.reason or "").strip() or "Wrong title", user)


@router.post("/matches/{item_id}/keep")
def keep_match(item_id: int, payload: MatchDecisionIn, request: Request):
    user = current_user(request)
    return _decide(item_id, "keep", (payload.reason or "").strip() or "Confirmed", user)


@router.get("/matches/decisions")
def match_decisions(request: Request):
    current_user(request)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM match_ignored ORDER BY library_title COLLATE NOCASE"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.delete("/matches/decisions/{item_id}")
def undo_match_decision(item_id: int, request: Request):
    user = current_user(request)
    with connect() as conn:
        row = conn.execute("SELECT * FROM match_ignored WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        conn.execute("DELETE FROM match_ignored WHERE id = ?", (item_id,))
    add_log(
        f"Undid Seerr match decision for {row['library_title']}",
        category="audit",
        action="match-undo",
        actor=user,
    )
    return {"ok": True}
