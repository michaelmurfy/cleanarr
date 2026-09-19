from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

import httpx

from .config import env_flag, env_value

__version__ = "1.1.0"

REPO_SLUG = "michaelmurfy/cleanarr"
REPO_URL = f"https://github.com/{REPO_SLUG}"
RELEASES_URL = f"{REPO_URL}/releases"
ISSUES_URL = f"{REPO_URL}/issues"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"

CACHE_SECONDS = 6 * 3600
_lock = threading.Lock()
_cache: dict[str, Any] = {}


def current_version() -> str:
    """The running version. A build can stamp CLEANARR_VERSION to override it."""
    return (os.environ.get("CLEANARR_VERSION") or env_value("CLEANARR_VERSION") or __version__).strip() or __version__


def update_check_disabled() -> bool:
    return env_flag("CLEANARR_DISABLE_UPDATE_CHECK")


def parse_version(value: str) -> tuple[int, ...]:
    """Loose semver parse. Anything unparseable sorts as (0,) so it never wins."""
    numbers = re.findall(r"\d+", (value or "").strip().lstrip("vV"))
    if not numbers:
        return (0,)
    return tuple(int(part) for part in numbers[:4])


def is_newer(latest: str, current: str) -> bool:
    if not latest or not current:
        return False
    left, right = parse_version(latest), parse_version(current)
    if left == (0,) or right == (0,):
        return False
    size = max(len(left), len(right))
    return left + (0,) * (size - len(left)) > right + (0,) * (size - len(right))


def _fetch_latest() -> dict[str, Any]:
    with httpx.Client(timeout=8.0, follow_redirects=True) as client:
        response = client.get(
            LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Cleanarr"},
        )
    if response.status_code == 404:
        # A repo with no published release is normal, not a failure.
        return {"latest": "", "release_url": RELEASES_URL, "published_at": "", "error": ""}
    if response.status_code == 403:
        return {"latest": "", "release_url": RELEASES_URL, "published_at": "", "error": "GitHub rate limit reached"}
    response.raise_for_status()
    payload = response.json() or {}
    return {
        "latest": str(payload.get("tag_name") or payload.get("name") or "").strip(),
        "release_url": str(payload.get("html_url") or RELEASES_URL),
        "published_at": str(payload.get("published_at") or ""),
        "error": "",
    }


def version_info(force: bool = False) -> dict[str, Any]:
    """Current version plus the newest published release, cached to spare GitHub.

    Never raises: a failed lookup reports the current version with an `error`
    string so the UI can stay useful offline.
    """
    current = current_version()
    base = {
        "current": current,
        "latest": "",
        "update_available": False,
        "checked_at": 0,
        "release_url": RELEASES_URL,
        "published_at": "",
        "repo_url": REPO_URL,
        "releases_url": RELEASES_URL,
        "issues_url": ISSUES_URL,
        "check_enabled": not update_check_disabled(),
        "error": "",
    }
    if update_check_disabled():
        return base

    now = time.time()
    with _lock:
        cached = dict(_cache) if _cache else {}
    fresh = cached and not force and (now - float(cached.get("checked_at") or 0)) < CACHE_SECONDS
    if fresh:
        return {**base, **cached}

    try:
        result = _fetch_latest()
    except Exception as exc:
        result = {"latest": "", "release_url": RELEASES_URL, "published_at": "", "error": f"Update check failed: {exc}"}

    result["checked_at"] = int(now)
    result["update_available"] = is_newer(result.get("latest") or "", current)
    if not result.get("error"):
        with _lock:
            _cache.clear()
            _cache.update(result)
    return {**base, **result}


def reset_cache() -> None:
    with _lock:
        _cache.clear()
