from app import auth
from app.db import connect, get_setting, set_setting


def _clear_auth_state():
    with connect() as conn:
        for key in (
            "auth_username",
            "auth_salt",
            "auth_password_hash",
            "using_default_password",
            "setup_complete",
            "session_secret",
        ):
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def test_fresh_install_without_env_requires_setup(client, monkeypatch):
    monkeypatch.delenv("CLEANARR_USERNAME", raising=False)
    monkeypatch.delenv("CLEANARR_PASSWORD", raising=False)
    monkeypatch.delenv("CLEANARR_SECRET", raising=False)
    monkeypatch.setattr(auth, "env_file_present", lambda: False)
    monkeypatch.setattr(auth, "env_value", lambda _name: "")
    _clear_auth_state()
    auth.bootstrap_auth()

    assert client.get("/api/auth/status").json() == {"setup_required": True}
    assert client.post("/api/auth/login", json={"username": "admin", "password": "changeme"}).status_code == 403
    assert get_setting("session_secret")
    assert get_setting("session_secret") not in auth.PLACEHOLDER_SECRETS
    assert not get_setting("auth_password_hash")


def test_setup_creates_account_and_signs_in(client, monkeypatch):
    monkeypatch.delenv("CLEANARR_USERNAME", raising=False)
    monkeypatch.delenv("CLEANARR_PASSWORD", raising=False)
    monkeypatch.delenv("CLEANARR_SECRET", raising=False)
    monkeypatch.setattr(auth, "env_file_present", lambda: False)
    monkeypatch.setattr(auth, "env_value", lambda _name: "")
    _clear_auth_state()
    auth.bootstrap_auth()

    weak = client.post("/api/auth/setup", json={"username": "admin", "password": "short"})
    assert weak.status_code == 400

    created = client.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse"})
    assert created.status_code == 200
    assert created.json()["username"] == "admin"
    assert client.cookies.get("cleanarr_session")
    assert client.get("/api/auth/status").json() == {"setup_required": False}
    assert client.get("/api/auth/me").json()["username"] == "admin"

    again = client.post("/api/auth/setup", json={"username": "other", "password": "another-one"})
    assert again.status_code == 403


def test_setup_stays_closed_even_if_setup_flag_is_cleared(client, monkeypatch):
    """A password hash alone must block /api/auth/setup — the flag is not enough."""
    monkeypatch.delenv("CLEANARR_USERNAME", raising=False)
    monkeypatch.delenv("CLEANARR_PASSWORD", raising=False)
    monkeypatch.setattr(auth, "env_file_present", lambda: False)
    monkeypatch.setattr(auth, "env_value", lambda _name: "")
    _clear_auth_state()
    auth.bootstrap_auth()
    assert client.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse"}).status_code == 200

    set_setting("setup_complete", "0")
    assert auth.needs_setup() is False
    assert client.get("/api/auth/status").json() == {"setup_required": False}
    blocked = client.post("/api/auth/setup", json={"username": "hacker", "password": "totally-new"})
    assert blocked.status_code == 403
    assert get_setting("auth_username") == "admin"


def test_env_credentials_block_setup(client, monkeypatch):
    monkeypatch.setenv("CLEANARR_USERNAME", "env-admin")
    monkeypatch.setenv("CLEANARR_PASSWORD", "env-secret-password")
    monkeypatch.setattr(auth, "env_file_present", lambda: False)
    _clear_auth_state()
    auth.bootstrap_auth()

    assert client.get("/api/auth/status").json() == {"setup_required": False}
    assert client.post("/api/auth/setup", json={"username": "other", "password": "another-pass"}).status_code == 403


def test_placeholder_secret_is_replaced_with_random(monkeypatch):
    monkeypatch.delenv("CLEANARR_SECRET", raising=False)
    monkeypatch.setattr(auth, "env_value", lambda _name: "")
    _clear_auth_state()
    first = auth.ensure_session_secret()
    second = auth.ensure_session_secret()
    assert first == second
    assert len(first) == 64
    assert first not in auth.PLACEHOLDER_SECRETS
