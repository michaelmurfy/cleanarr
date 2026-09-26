from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException

from .config import APP_SETTING_KEYS, hide_env_settings, locked_setting_keys
from .db import connect, get_setting, ignore_key, set_setting
from .services.clients import KEYS

BACKUP_FORMAT = "cleanarr-config"
BACKUP_VERSION = 1
MAX_RESTORE_BYTES = 1_000_000

# Never leave the box via backup, and never accept them on restore.
AUTH_KEYS = frozenset(
    {
        "auth_username",
        "auth_password_hash",
        "auth_salt",
        "session_secret",
        "setup_complete",
        "using_default_password",
        "cleanarr_password",
        "cleanarr_secret",
        "cleanarr_username",
    }
)

ALLOWED_SETTING_KEYS = frozenset(KEYS) | frozenset(APP_SETTING_KEYS)

BOOL_SETTINGS = {"sync_schedule_enabled", "auto_delete_enabled"}
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


def _exportable_settings() -> tuple[dict[str, str], list[str]]:
    """DB-stored settings only. Env-locked and hide-settings keys are skipped."""
    locked = locked_setting_keys()
    hide = hide_env_settings()
    values: dict[str, str] = {}
    skipped: list[str] = []
    for key in KEYS:
        if hide or key in locked:
            skipped.append(key)
            continue
        stored = get_setting(key)
        if stored:
            values[key] = stored
    for key in APP_SETTING_KEYS:
        values[key] = get_setting(key) or ""
    return values, skipped


def _whitelist_rows() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT match_type, media_type, tmdb_id, pattern, note, created_at FROM whitelist ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def _ignored_rows() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT kind, media_type, tmdb_id, tvdb_id, title_key, title, reason, created_at
            FROM unmatched_ignored ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _match_decision_rows() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT media_type, seerr_tmdb_id, seerr_tvdb_id, library_tmdb_id, library_tvdb_id,
                   seerr_title, library_title, reason, action, created_at
            FROM match_ignored ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def build_backup() -> dict[str, Any]:
    settings_values, skipped = _exportable_settings()
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": int(time.time()),
        "settings": settings_values,
        "skipped_locked": skipped,
        "whitelist": _whitelist_rows(),
        "unmatched_ignored": _ignored_rows(),
        "match_decisions": _match_decision_rows(),
    }


def _as_str(value: Any, *, max_len: int = 2000) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) > max_len:
        raise HTTPException(400, "Backup field is too long")
    return text


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _apply_settings(raw: Any) -> dict[str, int]:
    if raw is None:
        return {"applied": 0, "skipped": 0}
    if not isinstance(raw, dict):
        raise HTTPException(400, "settings must be an object")
    locked = locked_setting_keys()
    hide = hide_env_settings()
    applied = 0
    skipped = 0
    for key, value in raw.items():
        name = str(key)
        if name in AUTH_KEYS or name not in ALLOWED_SETTING_KEYS:
            skipped += 1
            continue
        if name in KEYS and (hide or name in locked):
            skipped += 1
            continue
        text = _as_str(value, max_len=4000)
        if name in APP_SETTING_KEYS:
            set_setting(name, _normalize_app_setting(name, text))
            applied += 1
            continue
        if name.endswith("_api_key"):
            if not text or set(text) <= {"•", "*"}:
                skipped += 1
                continue
            set_setting(name, text)
            applied += 1
            continue
        set_setting(name, text)
        applied += 1
    return {"applied": applied, "skipped": skipped}


def _replace_whitelist(rows: Any) -> int:
    if rows is None:
        return 0
    if not isinstance(rows, list):
        raise HTTPException(400, "whitelist must be a list")
    if len(rows) > 5000:
        raise HTTPException(400, "whitelist is too large")
    now = int(time.time())
    prepared: list[tuple] = []
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(400, "Invalid whitelist row")
        pattern = _as_str(row.get("pattern"), max_len=500)
        if not pattern:
            continue
        match_type = _as_str(row.get("match_type") or "title", max_len=20)
        media_type = _as_str(row.get("media_type") or "any", max_len=20)
        if match_type not in {"title", "id"}:
            match_type = "title"
        if media_type not in {"any", "movie", "tv"}:
            media_type = "any"
        prepared.append(
            (
                match_type,
                media_type,
                _as_int(row.get("tmdb_id")),
                pattern,
                _as_str(row.get("note"), max_len=500),
                _as_int(row.get("created_at"), now) or now,
            )
        )
    with connect() as conn:
        conn.execute("DELETE FROM whitelist")
        conn.executemany(
            """
            INSERT INTO whitelist (match_type, media_type, tmdb_id, pattern, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            prepared,
        )
    return len(prepared)


def _replace_unmatched_ignored(rows: Any) -> int:
    if rows is None:
        return 0
    if not isinstance(rows, list):
        raise HTTPException(400, "unmatched_ignored must be a list")
    if len(rows) > 20000:
        raise HTTPException(400, "unmatched_ignored is too large")
    now = int(time.time())
    prepared: list[tuple] = []
    seen: set[tuple] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(400, "Invalid unmatched_ignored row")
        key = ignore_key(
            _as_str(row.get("kind"), max_len=80),
            _as_str(row.get("media_type"), max_len=20),
            row.get("tmdb_id"),
            row.get("tvdb_id"),
            _as_str(row.get("title_key") or row.get("title"), max_len=500),
        )
        if key in seen:
            continue
        seen.add(key)
        prepared.append(
            (
                *key,
                _as_str(row.get("title"), max_len=500),
                _as_str(row.get("reason"), max_len=500),
                _as_int(row.get("created_at"), now) or now,
            )
        )
    with connect() as conn:
        conn.execute("DELETE FROM unmatched_ignored")
        conn.executemany(
            """
            INSERT OR IGNORE INTO unmatched_ignored
                (kind, media_type, tmdb_id, tvdb_id, title_key, title, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prepared,
        )
    return len(prepared)


def _replace_match_decisions(rows: Any) -> int:
    if rows is None:
        return 0
    if not isinstance(rows, list):
        raise HTTPException(400, "match_decisions must be a list")
    if len(rows) > 20000:
        raise HTTPException(400, "match_decisions is too large")
    now = int(time.time())
    prepared: list[tuple] = []
    seen: set[tuple] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(400, "Invalid match_decisions row")
        media_type = _as_str(row.get("media_type") or "movie", max_len=20)
        if media_type not in {"movie", "tv"}:
            media_type = "movie"
        action = _as_str(row.get("action") or "unlink", max_len=20)
        if action not in {"unlink", "keep"}:
            action = "unlink"
        pair = (
            media_type,
            _as_int(row.get("seerr_tmdb_id")),
            _as_int(row.get("seerr_tvdb_id")),
            _as_int(row.get("library_tmdb_id")),
            _as_int(row.get("library_tvdb_id")),
        )
        if pair in seen:
            continue
        seen.add(pair)
        prepared.append(
            (
                *pair,
                _as_str(row.get("seerr_title"), max_len=500),
                _as_str(row.get("library_title"), max_len=500),
                _as_str(row.get("reason"), max_len=500),
                action,
                _as_int(row.get("created_at"), now) or now,
            )
        )
    with connect() as conn:
        conn.execute("DELETE FROM match_ignored")
        conn.executemany(
            """
            INSERT OR IGNORE INTO match_ignored
                (media_type, seerr_tmdb_id, seerr_tvdb_id, library_tmdb_id, library_tvdb_id,
                 seerr_title, library_title, reason, action, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prepared,
        )
    return len(prepared)


def apply_backup(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(400, "Backup must be a JSON object")
    if payload.get("format") != BACKUP_FORMAT:
        raise HTTPException(400, "Not a Cleanarr configuration backup")
    try:
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Invalid backup version") from exc
    if version != BACKUP_VERSION:
        raise HTTPException(400, f"Unsupported backup version {version}")

    settings_stats = _apply_settings(payload.get("settings"))
    whitelist_count = _replace_whitelist(payload.get("whitelist"))
    ignored_count = _replace_unmatched_ignored(payload.get("unmatched_ignored"))
    decisions_count = _replace_match_decisions(payload.get("match_decisions"))
    return {
        "ok": True,
        "settings_applied": settings_stats["applied"],
        "settings_skipped": settings_stats["skipped"],
        "whitelist": whitelist_count,
        "unmatched_ignored": ignored_count,
        "match_decisions": decisions_count,
    }
