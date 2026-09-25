from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from .config import settings

DB_PATH = Path(settings.data_dir) / "cleanarr.db"


class _Connection(sqlite3.Connection):
    """sqlite3's own context manager only commits; this one also closes, so
    `with connect() as conn:` does not leave a file handle open per request."""

    def __exit__(self, *exc: Any) -> Any:
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    # journal_mode=WAL is persistent in the file, so init_db sets it once.
    conn = sqlite3.connect(DB_PATH, timeout=10, factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Settings are read constantly (every link, every service client, every session
# check) but change rarely. One long-lived reader watches PRAGMA data_version,
# which moves whenever any other connection commits, and the table is reloaded
# only then; so a write from anywhere (sync thread, tests, raw SQL) is seen at once.
_settings_lock = threading.Lock()
_settings_conn: sqlite3.Connection | None = None
_settings_version: int | None = None
_settings_cache: dict[str, str] = {}


def _settings_snapshot() -> dict[str, str]:
    global _settings_conn, _settings_version, _settings_cache
    with _settings_lock:
        if _settings_conn is None:
            _settings_conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        version = _settings_conn.execute("PRAGMA data_version").fetchone()[0]
        if version != _settings_version:
            rows = _settings_conn.execute("SELECT key, value FROM settings").fetchall()
            _settings_cache = {key: value for key, value in rows}
            _settings_version = version
        return _settings_cache


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
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
                radarr_4k_id INTEGER,
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
                account_username TEXT NOT NULL DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS unmatched_ignored (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL DEFAULT '',
                tmdb_id INTEGER NOT NULL DEFAULT 0,
                tvdb_id INTEGER NOT NULL DEFAULT 0,
                title_key TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                UNIQUE(kind, media_type, tmdb_id, tvdb_id, title_key)
            );

            CREATE TABLE IF NOT EXISTS match_ignored (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_type TEXT NOT NULL DEFAULT '',
                seerr_tmdb_id INTEGER NOT NULL DEFAULT 0,
                seerr_tvdb_id INTEGER NOT NULL DEFAULT 0,
                library_tmdb_id INTEGER NOT NULL DEFAULT 0,
                library_tvdb_id INTEGER NOT NULL DEFAULT 0,
                seerr_title TEXT NOT NULL DEFAULT '',
                library_title TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT 'unlink',
                created_at INTEGER NOT NULL,
                UNIQUE(media_type, seerr_tmdb_id, seerr_tvdb_id, library_tmdb_id, library_tvdb_id)
            );

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
                kind TEXT NOT NULL DEFAULT '',
                seerr_state TEXT NOT NULL DEFAULT ''
            );
            """
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(media)").fetchall()}
        if "radarr_4k_id" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN radarr_4k_id INTEGER")
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
        if "seerr_match_via" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN seerr_match_via TEXT NOT NULL DEFAULT ''")
        if "seerr_tmdb_id" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN seerr_tmdb_id INTEGER NOT NULL DEFAULT 0")
        if "seerr_title" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN seerr_title TEXT NOT NULL DEFAULT ''")
        match_cols = {row["name"] for row in conn.execute("PRAGMA table_info(match_ignored)").fetchall()}
        if "action" not in match_cols:
            conn.execute("ALTER TABLE match_ignored ADD COLUMN action TEXT NOT NULL DEFAULT 'unlink'")
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
        if "account_username" not in people_cols:
            if "plex_username" in people_cols:
                conn.execute("ALTER TABLE people RENAME COLUMN plex_username TO account_username")
            else:
                conn.execute("ALTER TABLE people ADD COLUMN account_username TEXT NOT NULL DEFAULT ''")
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
        if "seerr_state" not in unmatched_cols:
            conn.execute("ALTER TABLE unmatched ADD COLUMN seerr_state TEXT NOT NULL DEFAULT ''")
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
                    kind TEXT NOT NULL DEFAULT '',
                    seerr_state TEXT NOT NULL DEFAULT ''
                )
                """
            )


def ignore_key(kind: str, media_type: str, tmdb_id: Any, tvdb_id: Any, title: str) -> tuple:
    """Identity an unmatched row keeps across syncs. Year is left out so a metadata fix does not resurrect it."""
    return (
        kind or "",
        media_type or "",
        int(tmdb_id or 0),
        int(tvdb_id or 0),
        (title or "").strip().lower(),
    )


def ignored_unmatched() -> set[tuple]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, media_type, tmdb_id, tvdb_id, title_key FROM unmatched_ignored"
        ).fetchall()
    return {ignore_key(r["kind"], r["media_type"], r["tmdb_id"], r["tvdb_id"], r["title_key"]) for r in rows}


def match_ignore_key(
    media_type: str,
    seerr_tmdb_id: Any,
    seerr_tvdb_id: Any,
    library_tmdb_id: Any,
    library_tvdb_id: Any,
) -> tuple:
    """Pair a Seerr title with the *arr row it must not attach to again."""
    return (
        media_type or "",
        int(seerr_tmdb_id or 0),
        int(seerr_tvdb_id or 0),
        int(library_tmdb_id or 0),
        int(library_tvdb_id or 0),
    )


def ignored_matches(action: str = "unlink") -> set[tuple]:
    """Seerr↔library pairs the user decided on: 'unlink' blocks the pair, 'keep' confirms it."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT media_type, seerr_tmdb_id, seerr_tvdb_id, library_tmdb_id, library_tvdb_id
            FROM match_ignored WHERE action = ?
            """,
            (action,),
        ).fetchall()
    return {
        match_ignore_key(
            r["media_type"],
            r["seerr_tmdb_id"],
            r["seerr_tvdb_id"],
            r["library_tmdb_id"],
            r["library_tvdb_id"],
        )
        for r in rows
    }


def get_setting(key: str, default: str = "") -> str:
    return _settings_snapshot().get(key, default)


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def all_settings() -> dict[str, str]:
    return dict(_settings_snapshot())


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
