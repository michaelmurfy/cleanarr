from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from functools import wraps

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .config import env_file_present, env_value, settings
from .db import get_setting, set_setting

COOKIE = "cleanarr_session"
PBKDF_ITERS = 120_000
PLACEHOLDER_SECRETS = {"", "change-me", "change-this-to-a-long-random-string"}


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ITERS)
    return digest.hex()


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
    """True once an admin password exists or env/.env supplies credentials."""
    if get_setting("auth_password_hash"):
        return True
    env_user = (os.environ.get("CLEANARR_USERNAME") or env_value("CLEANARR_USERNAME") or "").strip()
    env_pass = (os.environ.get("CLEANARR_PASSWORD") or env_value("CLEANARR_PASSWORD") or "").strip()
    return bool(env_file_present() or env_user or env_pass)


def needs_setup() -> bool:
    # Never offer setup if credentials are already in place (DB or env).
    return not credentials_configured()


def bootstrap_auth() -> None:
    ensure_session_secret()

    env_user = (os.environ.get("CLEANARR_USERNAME") or env_value("CLEANARR_USERNAME") or "").strip()
    env_pass = (os.environ.get("CLEANARR_PASSWORD") or env_value("CLEANARR_PASSWORD") or "").strip()
    from_env = bool(env_file_present() or env_user or env_pass)

    if from_env:
        set_setting("auth_username", env_user or settings.cleanarr_username)
        password = env_pass or settings.cleanarr_password
        salt = secrets.token_hex(16)
        set_setting("auth_salt", salt)
        set_setting("auth_password_hash", _hash_password(password, salt))
        set_setting("using_default_password", "1" if password == "changeme" else "0")
        set_setting("setup_complete", "1")
        return

    # Existing install (upgrade): keep credentials, mark setup done.
    if get_setting("auth_password_hash"):
        set_setting("setup_complete", "1")
        return

    # Fresh install with no .env: force account creation before the UI is usable.
    set_setting("setup_complete", "0")
    set_setting("using_default_password", "0")
    if not get_setting("auth_username"):
        set_setting("auth_username", "")


def verify_password(password: str) -> bool:
    salt = get_setting("auth_salt")
    stored = get_setting("auth_password_hash")
    if not salt or not stored:
        return False
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, stored)


def set_credentials(username: str, password: str | None) -> None:
    if username:
        set_setting("auth_username", username.strip())
    if password:
        salt = secrets.token_hex(16)
        set_setting("auth_salt", salt)
        set_setting("auth_password_hash", _hash_password(password, salt))
        set_setting("using_default_password", "0")


def complete_setup(username: str, password: str) -> None:
    if credentials_configured() or not needs_setup():
        raise HTTPException(status_code=403, detail="Admin account already configured")
    name = username.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Username is required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    # Re-check immediately before write in case another request finished setup.
    if get_setting("auth_password_hash"):
        raise HTTPException(status_code=403, detail="Admin account already configured")
    set_credentials(name, password)
    set_setting("setup_complete", "1")


def session_secret() -> str:
    return ensure_session_secret()


def sign_session(username: str) -> str:
    nonce = secrets.token_hex(8)
    payload = f"{username}:{nonce}"
    sig = hmac.new(session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def read_session(token: str | None) -> str | None:
    if not token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    username, nonce, sig = parts
    payload = f"{username}:{nonce}"
    expected = hmac.new(session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    if username != get_setting("auth_username"):
        return None
    return username


def current_user(request: Request) -> str:
    user = read_session(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def login_response(username: str) -> JSONResponse:
    response = JSONResponse(
        {
            "ok": True,
            "username": username,
            "using_default_password": get_setting("using_default_password") == "1",
        }
    )
    secure = os.environ.get("CLEANARR_SECURE_COOKIE", "").lower() in {"1", "true", "yes"}
    response.set_cookie(
        COOKIE,
        sign_session(username),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 24 * 14,
        path="/",
    )
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
