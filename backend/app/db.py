from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import settings

DB_PATH = Path(settings.data_dir) / "cleanarr.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_type TEXT NOT NULL,
                tmdb_id INTEGER NOT NULL DEFAULT 0,
                tvdb_id INTEGER NOT NULL DEFAULT 0,
                imdb_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                year INTEGER,
                poster_url TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                radarr_id INTEGER,
                sonarr_id INTEGER,
                seerr_media_id INTEGER,
                requested_by TEXT NOT NULL DEFAULT '',
                requested_at TEXT NOT NULL DEFAULT '',
                last_watched_at INTEGER,
                play_count INTEGER NOT NULL DEFAULT 0,
                watcher_count INTEGER NOT NULL DEFAULT 0,
                watchers_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                path TEXT NOT NULL DEFAULT '',
                title_slug TEXT NOT NULL DEFAULT '',
                UNIQUE(media_type, tmdb_id, tvdb_id)
            );

            CREATE TABLE IF NOT EXISTS whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_type TEXT NOT NULL DEFAULT 'title',
                media_type TEXT NOT NULL DEFAULT 'any',
                tmdb_id INTEGER NOT NULL DEFAULT 0,
                pattern TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'idle',
                message TEXT NOT NULL DEFAULT '',
                started_at INTEGER,
                finished_at INTEGER
            );

            INSERT OR IGNORE INTO sync_state (id, status) VALUES (1, 'idle');
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(media)").fetchall()}
        if "title_slug" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN title_slug TEXT NOT NULL DEFAULT ''")


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def all_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}
