from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from functools import wraps

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .config import env_file_present, settings
from .db import get_setting, set_setting

COOKIE = "cleanarr_session"
PBKDF_ITERS = 120_000


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ITERS)
    return digest.hex()


def bootstrap_auth() -> None:
    env_user = os.environ.get("CLEANARR_USERNAME")
    env_pass = os.environ.get("CLEANARR_PASSWORD")
    if env_file_present() or env_user:
        set_setting("auth_username", env_user or settings.cleanarr_username)
    elif not get_setting("auth_username"):
        set_setting("auth_username", settings.cleanarr_username)
    if env_file_present() or env_pass:
        password = env_pass or settings.cleanarr_password
        salt = secrets.token_hex(16)
        set_setting("auth_salt", salt)
        set_setting("auth_password_hash", _hash_password(password, salt))
        set_setting("using_default_password", "1" if password == "changeme" else "0")
    elif not get_setting("auth_salt"):
        salt = secrets.token_hex(16)
        password = settings.cleanarr_password
        set_setting("auth_salt", salt)
        set_setting("auth_password_hash", _hash_password(password, salt))
        set_setting("using_default_password", "1" if password == "changeme" else "0")


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


def session_secret() -> str:
    secret = get_setting("session_secret")
    if not secret:
        secret = settings.cleanarr_secret if settings.cleanarr_secret != "change-me" else secrets.token_hex(32)
        set_setting("session_secret", secret)
    return secret


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
