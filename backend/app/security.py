from __future__ import annotations

import re
import threading
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

MASK = "***"

# Query parameters and header-ish tokens that carry a credential in plain text.
_SECRET_PARAM_RE = re.compile(
    r"((?:api[_-]?key|apikey|token|x-api-key|x-api-token|x-mediabrowser-token|password|secret)"
    r"\s*[=:]\s*)(['\"]?)([^\s'\"&;,)]+)",
    re.I,
)

# Methods that change state and therefore need cross-site protection.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()",
}

# Inline styles are used for the sync progress bar width, and the fonts come from
# Google Fonts, so those two sources have to stay allowed.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "img-src 'self' data:",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "connect-src 'self'",
    ]
)


def _configured_secrets() -> list[str]:
    """Every API key Cleanarr currently holds, longest first so overlaps mask fully."""
    try:
        from .services.clients import KEYS, cfg
    except Exception:
        return []
    values = set()
    for key in KEYS:
        if not key.endswith("_api_key"):
            continue
        try:
            value = cfg(key)
        except Exception:
            continue
        # Very short values would mask unrelated text, so ignore them.
        if value and len(value) >= 8:
            values.add(value)
    return sorted(values, key=len, reverse=True)


def redact(value: Any) -> str:
    """Strip credentials out of text headed for a log, an API response, or the UI."""
    text = str(value if value is not None else "")
    if not text:
        return text
    for secret in _configured_secrets():
        text = text.replace(secret, MASK)
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)


class LoginThrottle:
    """Fixed-window lockout for failed sign-ins, keyed on client IP and username.

    Cleanarr is a single-admin app with one password, so an unthrottled login form
    is the whole attack surface. State is in memory: a restart clears it, which is
    acceptable for a single-process self-hosted service.
    """

    def __init__(self, max_attempts: int = 8, window_seconds: int = 300, lockout_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait, or 0 when it may try again now."""
        now = time.time()
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until <= now:
                self._locked_until.pop(key, None)
                return 0
            return max(1, int(until - now))

    def record_failure(self, key: str) -> int:
        now = time.time()
        with self._lock:
            recent = self._prune(key, now)
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout_seconds
                self._failures.pop(key, None)
                return self.lockout_seconds
        return 0

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


login_throttle = LoginThrottle()


def client_key(request: Request, username: str = "") -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}|{(username or '').strip().lower()}"


class SecurityMiddleware(BaseHTTPMiddleware):
    """Adds hardening headers and rejects obvious cross-site writes.

    `Sec-Fetch-Site` is sent by every current browser and is set by the browser
    itself, so it cannot be forged from page script. Requests without it (curl,
    scripts, old browsers) are left alone: the session cookie is SameSite=Lax,
    which already blocks the cross-site form post those clients would need.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in UNSAFE_METHODS and request.headers.get("sec-fetch-site") == "cross-site":
            response: Response = JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        else:
            response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if request.url.path.startswith("/api/"):
            # Artwork sets its own long cache; everything else is per-session data.
            response.headers.setdefault("Cache-Control", "no-store")
        return response
