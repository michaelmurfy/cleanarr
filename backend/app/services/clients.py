from __future__ import annotations

from .arr import Radarr, Sonarr
from .jellystat import Jellystat
from .seerr import Seerr
from .tautulli import Tautulli
from .tracearr import Tracearr
from ..config import ENV_KEY_MAP, env_value, settings
from ..db import connect, get_setting

KEYS = [
    "tautulli_url",
    "tautulli_api_key",
    "tracearr_url",
    "tracearr_api_key",
    "jellystat_url",
    "jellystat_api_key",
    "seerr_url",
    "seerr_api_key",
    "sonarr_url",
    "sonarr_api_key",
    "radarr_url",
    "radarr_api_key",
    "radarr_4k_url",
    "radarr_4k_api_key",
    "sonarr_external_url",
    "radarr_external_url",
    "radarr_4k_external_url",
    "seerr_external_url",
    "tautulli_external_url",
    "tracearr_external_url",
    "jellystat_external_url",
]


def cfg(key: str) -> str:
    """Resolve a service setting. Process env and .env always win over the DB."""
    env_name = ENV_KEY_MAP.get(key, key.upper())
    env_val = env_value(env_name)
    if env_val:
        return env_val
    stored = get_setting(key)
    if stored:
        return stored.strip()
    return str(getattr(settings, key, "") or "").strip()


def prune_env_overridden_settings() -> int:
    """Remove DB copies of keys already defined in the environment or .env."""
    removed = 0
    with connect() as conn:
        for key in KEYS:
            env_name = ENV_KEY_MAP.get(key, key.upper())
            if not env_value(env_name):
                continue
            cur = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            removed += int(cur.rowcount or 0)
    return removed


def tautulli() -> Tautulli | None:
    url, key = cfg("tautulli_url"), cfg("tautulli_api_key")
    return Tautulli(url, key) if url and key else None


def tracearr() -> Tracearr | None:
    url, key = cfg("tracearr_url"), cfg("tracearr_api_key")
    return Tracearr(url, key) if url and key else None


def jellystat() -> Jellystat | None:
    url, key = cfg("jellystat_url"), cfg("jellystat_api_key")
    return Jellystat(url, key) if url and key else None


def seerr() -> Seerr | None:
    url, key = cfg("seerr_url"), cfg("seerr_api_key")
    return Seerr(url, key) if url and key else None


def radarr() -> Radarr | None:
    url, key = cfg("radarr_url"), cfg("radarr_api_key")
    return Radarr(url, key) if url and key else None


def radarr_4k() -> Radarr | None:
    url, key = cfg("radarr_4k_url"), cfg("radarr_4k_api_key")
    return Radarr(url, key) if url and key else None


def sonarr() -> Sonarr | None:
    url, key = cfg("sonarr_url"), cfg("sonarr_api_key")
    return Sonarr(url, key) if url and key else None


def public_url(kind: str, fallback_key: str) -> str:
    return cfg(f"{kind}_external_url") or cfg(fallback_key)
