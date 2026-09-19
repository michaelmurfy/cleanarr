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
                rating REAL,
                rating_votes INTEGER NOT NULL DEFAULT 0,
                rating_source TEXT NOT NULL DEFAULT '',
                tautulli_rating_key TEXT NOT NULL DEFAULT '',
                jellystat_item_id TEXT NOT NULL DEFAULT '',
                availability TEXT NOT NULL DEFAULT 'downloaded',
                added_at INTEGER,
                UNIQUE(media_type, tmdb_id, tvdb_id)
            );

            CREATE TABLE IF NOT EXISTS people (
                canonical TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                plex_username TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                aliases_json TEXT NOT NULL DEFAULT '[]',
                tautulli_id TEXT NOT NULL DEFAULT '',
                jellystat_id TEXT NOT NULL DEFAULT '',
                seerr_id TEXT NOT NULL DEFAULT '',
                request_count INTEGER NOT NULL DEFAULT 0,
                library_count INTEGER NOT NULL DEFAULT 0,
                library_size INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                last_watched_at INTEGER,
                sources_json TEXT NOT NULL DEFAULT '[]'
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

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                category TEXT NOT NULL DEFAULT 'system',
                action TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_logs_category ON logs(category, id DESC);

            CREATE TABLE IF NOT EXISTS unmatched (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                year INTEGER NOT NULL DEFAULT 0,
                plays INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                tmdb_id INTEGER NOT NULL DEFAULT 0,
                tvdb_id INTEGER NOT NULL DEFAULT 0,
                seerr_media_id INTEGER,
                requested_by TEXT NOT NULL DEFAULT '',
                requested_at TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT ''
            );
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(media)").fetchall()}
        if "title_slug" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN title_slug TEXT NOT NULL DEFAULT ''")
        if "rating" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN rating REAL")
        if "rating_votes" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN rating_votes INTEGER NOT NULL DEFAULT 0")
        if "rating_source" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN rating_source TEXT NOT NULL DEFAULT ''")
        if "tautulli_rating_key" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN tautulli_rating_key TEXT NOT NULL DEFAULT ''")
        if "jellystat_item_id" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN jellystat_item_id TEXT NOT NULL DEFAULT ''")
        if "availability" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN availability TEXT NOT NULL DEFAULT 'downloaded'")
        if "added_at" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN added_at INTEGER")
        sync_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sync_state)").fetchall()}
        if "step" not in sync_cols:
            conn.execute("ALTER TABLE sync_state ADD COLUMN step TEXT NOT NULL DEFAULT ''")
        if "progress_current" not in sync_cols:
            conn.execute("ALTER TABLE sync_state ADD COLUMN progress_current INTEGER NOT NULL DEFAULT 0")
        if "progress_total" not in sync_cols:
            conn.execute("ALTER TABLE sync_state ADD COLUMN progress_total INTEGER NOT NULL DEFAULT 0")
        people_cols = {row["name"] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
        if "jellystat_id" not in people_cols:
            conn.execute("ALTER TABLE people ADD COLUMN jellystat_id TEXT NOT NULL DEFAULT ''")
        unmatched_cols = {row["name"] for row in conn.execute("PRAGMA table_info(unmatched)").fetchall()}
        if "tmdb_id" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN tmdb_id INTEGER NOT NULL DEFAULT 0")
        if "tvdb_id" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN tvdb_id INTEGER NOT NULL DEFAULT 0")
        if "seerr_media_id" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN seerr_media_id INTEGER")
        if "requested_by" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN requested_by TEXT NOT NULL DEFAULT ''")
        if "requested_at" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN requested_at TEXT NOT NULL DEFAULT ''")
        if "kind" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN kind TEXT NOT NULL DEFAULT ''")
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='unmatched'"
        ).fetchone()
        if schema and "UNIQUE" in (schema["sql"] or ""):
            conn.execute("DROP TABLE unmatched")
            conn.execute(
                """
                CREATE TABLE unmatched (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    media_type TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    year INTEGER NOT NULL DEFAULT 0,
                    plays INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    tmdb_id INTEGER NOT NULL DEFAULT 0,
                    tvdb_id INTEGER NOT NULL DEFAULT 0,
                    seerr_media_id INTEGER,
                    requested_by TEXT NOT NULL DEFAULT '',
                    requested_at TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT ''
                )
                """
            )


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


def clear_library() -> dict[str, int]:
    with connect() as conn:
        media = conn.execute("SELECT COUNT(*) AS n FROM media").fetchone()["n"]
        people = conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        unmatched = conn.execute("SELECT COUNT(*) AS n FROM unmatched").fetchone()["n"]
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM people")
        conn.execute("DELETE FROM unmatched")
        conn.execute(
            """
            UPDATE sync_state
            SET status = 'idle', message = 'Library cleared', step = '',
                progress_current = 0, progress_total = 0, started_at = NULL, finished_at = NULL
            WHERE id = 1
            """
        )
    return {"media": media, "people": people, "unmatched": unmatched}
