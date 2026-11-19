from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from functools import wraps
from urllib.parse import quote, unquote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import oidc
from .config import env_value, settings
from .db import get_setting, set_setting

COOKIE = "cleanarr_session"
# Existing installs keep their 120k hashes until the next successful login.
LEGACY_PBKDF_ITERS = 120_000
PBKDF_ITERS = 600_000
PLACEHOLDER_SECRETS = {"", "change-me", "change-this-to-a-long-random-string"}
PLACEHOLDER_PASSWORDS = {"", "changeme", "change-me", "change-this-to-a-long-random-string"}
SESSION_MAX_AGE = 60 * 60 * 24 * 14
MIN_PASSWORD_LENGTH = 8


def _env_username() -> str:
    return (os.environ.get("CLEANARR_USERNAME") or env_value("CLEANARR_USERNAME") or "").strip()


def _env_password() -> str:
    """Real env password only. Placeholders and empty values do not count."""
    raw = (os.environ.get("CLEANARR_PASSWORD") or env_value("CLEANARR_PASSWORD") or "").strip()
    return "" if raw.lower() in PLACEHOLDER_PASSWORDS else raw


def _hash_password(password: str, salt: str, iterations: int = PBKDF_ITERS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return digest.hex()


def _stored_iterations() -> int:
    raw = get_setting("auth_pbkdf_iters")
    if raw.isdigit():
        return int(raw)
    # Hashes written before the iteration count was stored used the legacy floor.
    return LEGACY_PBKDF_ITERS if get_setting("auth_password_hash") else PBKDF_ITERS


def ensure_session_secret() -> str:
    """Persist a session signing secret. Prefer CLEANARR_SECRET; otherwise generate one."""
    existing = get_setting("session_secret")
    if existing:
        return existing
    env_secret = (os.environ.get("CLEANARR_SECRET") or env_value("CLEANARR_SECRET") or "").strip()
    secret = env_secret if env_secret not in PLACEHOLDER_SECRETS else secrets.token_hex(32)
    set_setting("session_secret", secret)
    return secret


def credentials_configured() -> bool:
    """True once an admin password exists, SSO is set up, or a real CLEANARR_PASSWORD is supplied."""
    if get_setting("auth_password_hash") or oidc.enabled():
        return True
    return bool(_env_password())


def needs_setup() -> bool:
    # Never offer setup if credentials are already in place (DB, env, or SSO).
    return not credentials_configured()


def _apply_password(password: str, *, default: bool = False) -> None:
    salt = secrets.token_hex(16)
    set_setting("auth_salt", salt)
    set_setting("auth_password_hash", _hash_password(password, salt, PBKDF_ITERS))
    set_setting("auth_pbkdf_iters", str(PBKDF_ITERS))
    set_setting("using_default_password", "1" if default else "0")


def bootstrap_auth() -> None:
    ensure_session_secret()

    env_user = _env_username()
    env_pass = _env_password()

    # Only a real CLEANARR_PASSWORD seeds or refreshes the hash. A bare .env file,
    # username-only env, or the example "changeme" value must not overwrite a
    # password the user already set in the UI.
    if env_pass:
        if env_user:
            set_setting("auth_username", env_user)
        elif not get_setting("auth_username"):
            set_setting("auth_username", settings.cleanarr_username or "admin")
        if not verify_password(env_pass):
            _apply_password(env_pass, default=False)
        set_setting("setup_complete", "1")
        return

    if env_user and not get_setting("auth_username"):
        set_setting("auth_username", env_user)

    # Existing install (upgrade): keep credentials, mark setup done.
    if get_setting("auth_password_hash") or oidc.enabled():
        set_setting("setup_complete", "1")
        return

    # Fresh install with no real env password and no SSO: force account creation.
    set_setting("setup_complete", "0")
    set_setting("using_default_password", "0")
    if not get_setting("auth_username"):
        set_setting("auth_username", "")


def verify_password(password: str) -> bool:
    salt = get_setting("auth_salt")
    stored = get_setting("auth_password_hash")
    if not salt or not stored:
        return False
    candidate = _hash_password(password, salt, _stored_iterations())
    return hmac.compare_digest(candidate, stored)


def rehash_password_if_needed(password: str) -> None:
    """Upgrade a legacy iteration count after a successful login."""
    if _stored_iterations() >= PBKDF_ITERS:
        return
    if not verify_password(password):
        return
    _apply_password(password, default=get_setting("using_default_password") == "1")


def set_credentials(username: str, password: str | None) -> None:
    if username:
        set_setting("auth_username", username.strip())
    if password:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
            )
        _apply_password(password, default=False)


def complete_setup(username: str, password: str) -> None:
    if credentials_configured() or not needs_setup():
        raise HTTPException(status_code=403, detail="Admin account already configured")
    name = username.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Username is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    # Re-check immediately before write in case another request finished setup.
    if get_setting("auth_password_hash"):
        raise HTTPException(status_code=403, detail="Admin account already configured")
    set_credentials(name, password)
    set_setting("setup_complete", "1")


def session_secret() -> str:
    return ensure_session_secret()


def bump_session_epoch() -> None:
    """Invalidate every outstanding session cookie (used on logout)."""
    current = get_setting("session_epoch") or "0"
    try:
        next_epoch = str(int(current) + 1)
    except ValueError:
        next_epoch = "1"
    set_setting("session_epoch", next_epoch)


def _session_key(method: str = "password") -> bytes:
    """Signing key bound to whatever let the session in.

    Password sessions fold in the stored credentials and a logout epoch, so
    changing the password/username or signing out invalidates every cookie.
    SSO sessions fold in the provider, client, and allow lists instead, plus
    the same logout epoch.
    """
    epoch = get_setting("session_epoch") or "0"
    if method == "oidc":
        material = f"{session_secret()}|oidc|{oidc.session_material()}|{epoch}"
    else:
        material = (
            f"{session_secret()}|{get_setting('auth_username')}|"
            f"{get_setting('auth_password_hash')}|{epoch}"
        )
    return hashlib.sha256(material.encode()).digest()


def sign_session(username: str, method: str = "password") -> str:
    nonce = secrets.token_hex(8)
    # Cookies only carry printable ASCII, so anything else in a name is percent-encoded.
    # Plain names encode to themselves, which keeps older cookies valid.
    payload = f"{quote(username, safe='')}:{int(time.time())}:{nonce}"
    sig = hmac.new(_session_key(method), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def session_info(token: str | None) -> tuple[str, str] | None:
    """(username, method) for a valid session cookie, else None."""
    if not token:
        return None
    # Split from the right so a username containing ":" still parses.
    parts = token.rsplit(":", 3)
    if len(parts) != 4:
        return None
    encoded, issued, nonce, sig = parts
    if not issued.isdigit():
        return None
    age = time.time() - int(issued)
    # A little slack for clock skew, then a hard expiry.
    if age < -300 or age > SESSION_MAX_AGE:
        return None
    payload = f"{encoded}:{issued}:{nonce}".encode()
    username = unquote(encoded)
    password_sig = hmac.new(_session_key("password"), payload, hashlib.sha256).hexdigest()
    if hmac.compare_digest(password_sig, sig):
        if username != get_setting("auth_username") or not oidc.password_login_enabled():
            return None
        return username, "password"
    if oidc.enabled():
        oidc_sig = hmac.new(_session_key("oidc"), payload, hashlib.sha256).hexdigest()
        if hmac.compare_digest(oidc_sig, sig):
            return username, "oidc"
    return None


def read_session(token: str | None) -> str | None:
    info = session_info(token)
    return info[0] if info else None


def current_user(request: Request) -> str:
    user = read_session(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def _secure_cookie(request: Request | None) -> bool:
    """Mark the cookie Secure when asked to, or whenever the request arrived over TLS."""
    setting = os.environ.get("CLEANARR_SECURE_COOKIE", "").strip().lower()
    if setting in {"1", "true", "yes", "on"}:
        return True
    if setting in {"0", "false", "no", "off"}:
        return False
    if request is None:
        return False
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


def set_session_cookie(response: Response, username: str, request: Request | None, method: str = "password") -> None:
    response.set_cookie(
        COOKIE,
        sign_session(username, method),
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
        max_age=SESSION_MAX_AGE,
        path="/",
    )


def clear_session_cookie(response: Response, request: Request | None = None) -> None:
    """Delete the session cookie with the same flags it was set with."""
    response.delete_cookie(
        COOKIE,
        path="/",
        samesite="lax",
        secure=_secure_cookie(request),
    )


def login_response(username: str, request: Request | None = None) -> JSONResponse:
    response = JSONResponse(
        {
            "ok": True,
            "username": username,
            "using_default_password": get_setting("using_default_password") == "1",
        }
    )
    set_session_cookie(response, username, request)
    return response


def require_auth(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        request: Request = kwargs.get("request")
        if request is None:
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
        if request is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        current_user(request)
        return await fn(*args, **kwargs)

    return wrapper
