import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import oidc

ISSUER = "https://id.example.test"
CLIENT = "cleanarr-client"
SECRET = "pocket-id-client-secret-value"


class FakeProvider:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.claims = {"sub": "user-1", "preferred_username": "ada", "email": "ada@example.test", "groups": ["media"]}
        self.overrides = {}
        self.nonce = ""
        self.token_requests = []
        self.userinfo = None

    def id_token(self):
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT, "iat": now, "exp": now + 300, "nonce": self.nonce, **self.claims, **self.overrides}
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": "k1"})

    def get_json(self, url, headers=None):
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/api/oidc/token",
                "userinfo_endpoint": f"{ISSUER}/api/oidc/userinfo",
                "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            }
        if url.endswith("jwks.json"):
            return self.jwks
        if url.endswith("userinfo"):
            return self.userinfo or {}
        raise AssertionError(url)

    def post_form(self, url, data, auth):
        self.token_requests.append(data)
        return {"access_token": "at", "id_token": self.id_token(), "token_type": "Bearer"}


@pytest.fixture
def provider(client, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(oidc, "_get_json", fake.get_json)
    monkeypatch.setattr(oidc, "_post_form", fake.post_form)
    oidc.clear_cache()
    client.cookies.clear()
    yield fake
    oidc.clear_cache()
    client.cookies.clear()


def start(client, provider):
    response = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    provider.nonce = query["nonce"][0]
    return query


def callback(client, **params):
    return client.get("/api/auth/oidc/callback", params=params, follow_redirects=False)


def test_status_advertises_sso(client, provider):
    status = client.get("/api/auth/status").json()
    assert status["sso"] == {"enabled": True, "label": "Pocket ID"}
    assert status["password_login"] is True


def test_login_redirect_uses_pkce_state_and_nonce(client, provider):
    query = start(client, provider)
    assert query["client_id"] == [CLIENT]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://testserver/api/auth/oidc/callback"]
    assert "openid" in query["scope"][0].split()
    assert query["state"][0] and query["nonce"][0]
    assert oidc.STATE_COOKIE in client.cookies


def test_full_sign_in_creates_an_sso_session(client, provider):
    query = start(client, provider)
    response = callback(client, code="abc", state=query["state"][0])
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    me = client.get("/api/auth/me").json()
    assert me == {"username": "ada", "method": "oidc", "using_default_password": me["using_default_password"]}
    sent = provider.token_requests[0]
    assert sent["code_verifier"] and sent["client_secret"] == SECRET
    assert client.get("/api/library").status_code == 200


def test_wrong_state_is_rejected(client, provider):
    start(client, provider)
    response = callback(client, code="abc", state="forged")
    assert response.headers["location"] == "/?sso_error=state"
    assert client.get("/api/auth/me").status_code == 401


def test_callback_without_the_state_cookie_is_rejected(client, provider):
    query = start(client, provider)
    client.cookies.clear()
    response = callback(client, code="abc", state=query["state"][0])
    assert response.headers["location"] == "/?sso_error=state"


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example.test"},
        {"nonce": "replayed"},
        {"exp": int(time.time()) - 3600},
    ],
)
def test_bad_id_tokens_are_rejected(client, provider, override):
    query = start(client, provider)
    provider.overrides = override
    response = callback(client, code="abc", state=query["state"][0])
    assert response.headers["location"] == "/?sso_error=token"
    assert client.get("/api/auth/me").status_code == 401


def test_unsigned_and_symmetric_tokens_are_rejected(client, provider):
    for token in (
        jwt.encode({"sub": "x", "aud": CLIENT, "iss": ISSUER}, SECRET, algorithm="HS256"),
        jwt.encode({"sub": "x", "aud": CLIENT, "iss": ISSUER}, None, algorithm="none"),
    ):
        with pytest.raises(oidc.OIDCError):
            oidc.verify_id_token(token, "n")


def test_token_signed_by_another_key_is_rejected(client, provider):
    query = start(client, provider)
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    response = callback(client, code="abc", state=query["state"][0])
    assert response.headers["location"] == "/?sso_error=token"


def test_allowed_groups_and_users(client, provider, monkeypatch):
    monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "admins")
    query = start(client, provider)
    assert callback(client, code="abc", state=query["state"][0]).headers["location"] == "/?sso_error=denied"

    provider.claims["groups"] = ["Admins"]
    query = start(client, provider)
    assert callback(client, code="abc", state=query["state"][0]).headers["location"] == "/"

    client.cookies.clear()
    provider.claims["groups"] = []
    monkeypatch.setenv("OIDC_ALLOWED_USERS", "ADA@example.test")
    query = start(client, provider)
    assert callback(client, code="abc", state=query["state"][0]).headers["location"] == "/"


def test_groups_can_come_from_userinfo(client, provider, monkeypatch):
    monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "media")
    provider.claims.pop("groups")
    provider.userinfo = {"sub": "user-1", "groups": ["media"]}
    query = start(client, provider)
    assert callback(client, code="abc", state=query["state"][0]).headers["location"] == "/"

    client.cookies.clear()
    provider.userinfo = {"sub": "someone-else", "groups": ["media"]}
    query = start(client, provider)
    assert callback(client, code="abc", state=query["state"][0]).headers["location"] == "/?sso_error=denied"


def test_changing_allow_list_signs_sso_sessions_out(client, provider, monkeypatch):
    query = start(client, provider)
    callback(client, code="abc", state=query["state"][0])
    assert client.get("/api/auth/me").status_code == 200
    monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "admins")
    assert client.get("/api/auth/me").status_code == 401


def test_sso_session_dies_when_sso_is_turned_off(client, provider, monkeypatch):
    query = start(client, provider)
    callback(client, code="abc", state=query["state"][0])
    monkeypatch.delenv("OIDC_ISSUER")
    assert client.get("/api/auth/me").status_code == 401


def test_provider_error_is_reported_as_cancelled(client, provider):
    start(client, provider)
    response = callback(client, error="access_denied")
    assert response.headers["location"] == "/?sso_error=cancelled"


def test_disable_password_login(client, provider, monkeypatch):
    from app.auth import bootstrap_auth

    bootstrap_auth()
    assert client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"}).status_code == 200
    monkeypatch.setenv("CLEANARR_DISABLE_PASSWORD_LOGIN", "1")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"}).status_code == 403
    assert client.get("/api/auth/status").json()["password_login"] is False


def test_disable_password_login_is_ignored_without_sso(client, monkeypatch):
    monkeypatch.setenv("CLEANARR_DISABLE_PASSWORD_LOGIN", "1")
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    from app.auth import bootstrap_auth

    bootstrap_auth()
    assert client.post("/api/auth/login", json={"username": "tester", "password": "hunter2"}).status_code == 200
    client.post("/api/auth/logout")


def test_settings_never_return_the_client_secret(auth_client, monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    saved = auth_client.put("/api/settings", json={"values": {"oidc_issuer": ISSUER, "oidc_client_secret": SECRET}})
    assert saved.status_code == 200
    body = auth_client.get("/api/settings")
    assert SECRET not in body.text
    values = body.json()["values"]
    assert values["oidc_client_secret"] == "" and values["oidc_client_secret_set"] is True
    assert values["oidc_issuer"] == ISSUER
    assert body.json()["sso"]["callback_url"].endswith("/api/auth/oidc/callback")

    from app.security import redact

    assert SECRET not in redact(f"token endpoint said {SECRET}")
    auth_client.put("/api/settings", json={"values": {"oidc_issuer": ""}})


def test_sso_settings_stay_out_of_backups(auth_client, monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    auth_client.put("/api/settings", json={"values": {"oidc_client_secret": SECRET, "oidc_client_id": CLIENT}})
    backup = auth_client.get("/api/settings/backup")
    assert backup.status_code == 200
    assert SECRET not in backup.text and CLIENT not in backup.text
    auth_client.put("/api/settings", json={"values": {"oidc_client_id": ""}})


def test_hidden_settings_hide_sso(auth_client, monkeypatch):
    monkeypatch.setenv("CLEANARR_HIDE_SETTINGS", "1")
    values = auth_client.get("/api/settings").json()["values"]
    assert values["oidc_issuer_hidden"] is True and values["oidc_issuer_locked"] is True
