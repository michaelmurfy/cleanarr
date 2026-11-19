from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

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
# Browsers send these for first-party navigation and same-origin fetches.
SAFE_FETCH_SITES = frozenset({"same-origin", "none"})

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
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
        from .config import OIDC_KEYS, is_secret_key
        from .services.clients import KEYS, cfg
    except Exception:
        return []
    values = set()
    for key in (*KEYS, *OIDC_KEYS):
        if not is_secret_key(key):
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
    """Fixed-window lockout for failed sign-ins, keyed on client IP.

    Cleanarr is a single-admin app with one password, so an unthrottled login form
    is the whole attack surface. Keying on IP alone stops a caller from dodging
    the lockout by changing the username on every attempt. State is in memory: a
    restart clears it, which is acceptable for a single-process self-hosted service.
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
    # Username is ignored: varying it used to dodge the lockout. Kept as a kwarg
    # so existing call sites keep working.
    _ = username
    host = request.client.host if request.client else "unknown"
    return host


def _request_host(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    raw = forwarded or request.headers.get("host") or request.url.netloc or ""
    return raw.lower()


def _origin_host(origin: str) -> str:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.netloc.lower()


def cross_site_write_blocked(request: Request) -> bool:
    """True when an unsafe method looks like a cross-site write.

    Prefer Sec-Fetch-Site (set by the browser, not forgeable from page script).
    Fall back to Origin vs Host when the fetch metadata header is absent (older
    browsers, plain HTTP in some cases). Requests with neither header (curl,
    scripts) are left alone: SameSite=Lax already blocks the cross-site form
    post those clients would need, and requiring Origin would break API clients.
    """
    if request.method not in UNSAFE_METHODS:
        return False
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site:
        return site not in SAFE_FETCH_SITES
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return False
    origin_host = _origin_host(origin)
    if not origin_host:
        return True
    return origin_host != _request_host(request)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Adds hardening headers and rejects obvious cross-site writes."""

    async def dispatch(self, request: Request, call_next):
        if cross_site_write_blocked(request):
            response: Response = JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        else:
            response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if request.url.path.startswith("/api/"):
            # Artwork sets its own long cache; everything else is per-session data.
            response.headers.setdefault("Cache-Control", "no-store")
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if forwarded == "https" or request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
