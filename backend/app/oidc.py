"""OpenID Connect sign-in (Pocket ID, or any provider with discovery).

Authorization code flow with PKCE, a signed state cookie carrying state, nonce
and the verifier, and an ID token verified against the provider's JWKS.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote_plus, urlencode

import httpx
import jwt
from fastapi import Request

from .config import env_flag, env_value
from .db import get_setting

STATE_COOKIE = "cleanarr_oidc"
STATE_COOKIE_PATH = "/api/auth/oidc"
STATE_MAX_AGE = 600
CALLBACK_PATH = "/api/auth/oidc/callback"
DEFAULT_SCOPES = "openid profile email groups"
CACHE_SECONDS = 3600
# Asymmetric only: an HS* token would be "signed" with the client secret, and
# "none" is not signed at all.
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]
DEFAULT_LABEL = "Pocket ID"


class OIDCError(Exception):
    """A sign-in that must not succeed. `code` is what the login page is told."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _cfg(key: str) -> str:
    from .services.clients import cfg

    return cfg(key)


def issuer() -> str:
    return _cfg("oidc_issuer").rstrip("/")


def client_id() -> str:
    return _cfg("oidc_client_id")


def enabled() -> bool:
    return bool(issuer() and client_id())


def button_label() -> str:
    return _cfg("oidc_button_label") or DEFAULT_LABEL


def password_login_enabled() -> bool:
    """Password sign-in can only be switched off from the environment, and only
    while SSO is configured, so a bad Settings change cannot lock everyone out."""
    return not (enabled() and env_flag("CLEANARR_DISABLE_PASSWORD_LOGIN"))


def scopes() -> str:
    """Providers that check scopes against the client (Authelia) reject any the
    client was not given, so the list can be trimmed with OIDC_SCOPES."""
    requested = env_value("OIDC_SCOPES").replace(",", " ").split() or DEFAULT_SCOPES.split()
    if "openid" not in requested:
        requested.insert(0, "openid")
    return " ".join(requested)


def _split(value: str) -> list[str]:
    return [part.strip().lower() for part in (value or "").replace("\n", ",").split(",") if part.strip()]


def allowed_groups() -> list[str]:
    return _split(_cfg("oidc_allowed_groups"))


def allowed_users() -> list[str]:
    return _split(_cfg("oidc_allowed_users"))


def session_material() -> str:
    """Folded into the SSO session key so changing the provider, client, or who is
    allowed in signs out everyone who came in under the old rules."""
    return "|".join([issuer(), client_id(), ",".join(allowed_groups()), ",".join(allowed_users())])


def redirect_uri(request: Request) -> str:
    configured = env_value("OIDC_REDIRECT_URI")
    if configured:
        return configured
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}{CALLBACK_PATH}"


# --- provider metadata -------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        response = client.get(url, headers=headers)
    if response.status_code >= 400:
        raise OIDCError("provider", f"{url} returned {response.status_code}")
    return response.json()


def _post_form(url: str, data: dict[str, str], auth: tuple[str, str] | None) -> Any:
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        response = client.post(url, data=data, auth=auth, headers={"Accept": "application/json"})
    if response.status_code >= 400:
        raise OIDCError("exchange", f"Token endpoint returned {response.status_code}")
    return response.json()


def _cached(key: str, loader, refresh: bool = False) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not refresh and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def discovery() -> dict[str, Any]:
    base = issuer()

    def load() -> dict[str, Any]:
        doc = _get_json(f"{base}/.well-known/openid-configuration")
        if not isinstance(doc, dict) or (doc.get("issuer") or "").rstrip("/") != base:
            raise OIDCError("provider", "Discovery document does not match the configured issuer")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not doc.get(field):
                raise OIDCError("provider", f"Discovery document has no {field}")
        return doc

    return _cached(f"discovery:{base}", load)


def _signing_key(token: str, jwks_uri: str):
    header = jwt.get_unverified_header(token)
    if header.get("alg") not in ALGORITHMS:
        raise OIDCError("token", f"Unsupported ID token algorithm {header.get('alg')!r}")
    kid = header.get("kid")
    for refresh in (False, True):
        keys = jwt.PyJWKSet.from_dict(_cached(f"jwks:{jwks_uri}", lambda: _get_json(jwks_uri), refresh=refresh))
        for key in keys.keys:
            if kid is None or key.key_id == kid:
                return key, header["alg"]
    raise OIDCError("token", "ID token was signed with an unknown key")


# --- state cookie -------------------------------------------------------------

def _state_key() -> bytes:
    from .auth import session_secret

    return hashlib.sha256(f"{session_secret()}|oidc-state".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pack_state(values: dict[str, Any]) -> str:
    body = _b64(json.dumps(values, separators=(",", ":")).encode())
    sig = hmac.new(_state_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def unpack_state(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_state_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        values = json.loads(_unb64(body))
    except Exception:
        return None
    if not isinstance(values, dict) or time.time() - float(values.get("ts") or 0) > STATE_MAX_AGE:
        return None
    return values


# --- flow --------------------------------------------------------------------

def begin(request: Request) -> tuple[str, str]:
    """Return (authorization URL, state cookie value)."""
    doc = discovery()
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    callback = redirect_uri(request)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id(),
            "redirect_uri": callback,
            "scope": scopes(),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    endpoint = doc["authorization_endpoint"]
    joiner = "&" if "?" in endpoint else "?"
    cookie = pack_state({"state": state, "nonce": nonce, "verifier": verifier, "redirect_uri": callback, "ts": int(time.time())})
    return f"{endpoint}{joiner}{query}", cookie


def _exchange(doc: dict[str, Any], code: str, verifier: str, callback: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback,
        "code_verifier": verifier,
        "client_id": client_id(),
    }
    secret = _cfg("oidc_client_secret")
    auth = None
    if secret:
        # Basic is the spec default and what Authelia, Keycloak and Kanidm register
        # clients with; some providers refuse a method the client was not set up for.
        methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        if "client_secret_basic" in methods or "client_secret_post" not in methods:
            # RFC 6749 2.3.1: both halves are form-encoded before going into the header.
            auth = (quote_plus(client_id()), quote_plus(secret))
        else:
            data["client_secret"] = secret
    tokens = _post_form(doc["token_endpoint"], data, auth)
    if not isinstance(tokens, dict) or not tokens.get("id_token"):
        raise OIDCError("exchange", "Token endpoint returned no ID token")
    return tokens


def verify_id_token(token: str, nonce: str) -> dict[str, Any]:
    doc = discovery()
    key, alg = _signing_key(token, doc["jwks_uri"])
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=client_id(),
            issuer=doc["issuer"],
            leeway=60,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError("token", f"ID token rejected: {exc}") from exc
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id():
        raise OIDCError("token", "ID token was issued to a different client")
    if not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise OIDCError("token", "ID token nonce does not match")
    return claims


def _userinfo(doc: dict[str, Any], access_token: str, subject: str) -> dict[str, Any]:
    endpoint = doc.get("userinfo_endpoint")
    if not endpoint or not access_token:
        return {}
    try:
        info = _get_json(endpoint, headers={"Authorization": f"Bearer {access_token}"})
    except Exception:
        return {}
    if not isinstance(info, dict) or info.get("sub") != subject:
        return {}
    return info


def identity(claims: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Display name, the names it can be allowed by, and its groups (lowercased)."""
    username = str(claims.get("preferred_username") or claims.get("email") or claims.get("name") or claims["sub"])
    names = {str(claims[key]).strip().lower() for key in ("preferred_username", "email", "sub") if claims.get(key)}
    raw_groups = claims.get("groups") or []
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    groups = [str(group).strip().lower() for group in raw_groups if str(group).strip()]
    return username, sorted(names), groups


def authorize(claims: dict[str, Any]) -> str:
    username, names, groups = identity(claims)
    want_groups, want_users = allowed_groups(), allowed_users()
    if not want_groups and not want_users:
        return username
    if any(group in want_groups for group in groups) or any(name in want_users for name in names):
        return username
    raise OIDCError("denied", f"'{username}' is not in the allowed users or groups")


def complete(request: Request, params: dict[str, str], cookie: str | None) -> str:
    """Finish the callback and return the username to sign in, or raise OIDCError."""
    if params.get("error"):
        raise OIDCError("cancelled", f"Provider returned {params.get('error')}")
    saved = unpack_state(cookie)
    if not saved:
        raise OIDCError("state", "Sign-in expired or was started in another browser")
    if not hmac.compare_digest(str(params.get("state") or ""), str(saved.get("state") or "")):
        raise OIDCError("state", "State does not match")
    code = params.get("code") or ""
    if not code:
        raise OIDCError("state", "No authorization code returned")
    doc = discovery()
    tokens = _exchange(doc, code, saved["verifier"], saved["redirect_uri"])
    claims = verify_id_token(tokens["id_token"], saved["nonce"])
    if not claims.get("groups") or not claims.get("preferred_username"):
        extra = _userinfo(doc, tokens.get("access_token") or "", claims["sub"])
        claims = {**extra, **{key: value for key, value in claims.items() if value}}
    return authorize(claims)
