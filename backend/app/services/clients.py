from __future__ import annotations

from ..config import settings
from ..db import get_setting
from .arr import Radarr, Sonarr
from .seerr import Seerr
from .tautulli import Tautulli
from .tracearr import Tracearr

KEYS = [
    "tautulli_url",
    "tautulli_api_key",
    "tracearr_url",
    "tracearr_api_key",
    "seerr_url",
    "seerr_api_key",
    "sonarr_url",
    "sonarr_api_key",
    "radarr_url",
    "radarr_api_key",
    "sonarr_external_url",
    "radarr_external_url",
    "seerr_external_url",
]


def cfg(key: str) -> str:
    stored = get_setting(key)
    if stored:
        return stored.strip()
    return str(getattr(settings, key, "") or "").strip()


def tautulli() -> Tautulli | None:
    url, key = cfg("tautulli_url"), cfg("tautulli_api_key")
    return Tautulli(url, key) if url and key else None


def tracearr() -> Tracearr | None:
    url, key = cfg("tracearr_url"), cfg("tracearr_api_key")
    return Tracearr(url, key) if url and key else None


def seerr() -> Seerr | None:
    url, key = cfg("seerr_url"), cfg("seerr_api_key")
    return Seerr(url, key) if url and key else None


def radarr() -> Radarr | None:
    url, key = cfg("radarr_url"), cfg("radarr_api_key")
    return Radarr(url, key) if url and key else None


def sonarr() -> Sonarr | None:
    url, key = cfg("sonarr_url"), cfg("sonarr_api_key")
    return Sonarr(url, key) if url and key else None


def public_url(kind: str, fallback_key: str) -> str:
    return cfg(f"{kind}_external_url") or cfg(fallback_key)
