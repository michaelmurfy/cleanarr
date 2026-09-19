import pytest


def test_version_comparison_handles_tags_and_junk():
    from app.version import is_newer, parse_version

    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("") == (0,)
    assert parse_version("nightly") == (0,)
    assert is_newer("v1.2.0", "1.1.9")
    assert is_newer("1.1.0", "1.0.12")
    assert not is_newer("1.1.0", "1.1.0")
    assert not is_newer("1.0.0", "1.1.0")
    # An unparseable tag on either side must never claim an update is waiting.
    assert not is_newer("nightly", "1.1.0")
    assert not is_newer("1.2.0", "")


def test_version_endpoint_requires_a_session(client):
    client.cookies.clear()
    assert client.get("/api/version").status_code == 401


def test_version_endpoint_reports_the_running_build(auth_client, monkeypatch):
    from app import version as version_module

    version_module.reset_cache()
    monkeypatch.setattr(
        version_module,
        "_fetch_latest",
        lambda: {"latest": "v9.9.9", "release_url": "https://example.test/r", "published_at": "", "error": ""},
    )
    body = auth_client.get("/api/version").json()
    assert body["current"] == version_module.current_version()
    assert body["latest"] == "v9.9.9"
    assert body["update_available"] is True
    assert body["repo_url"].startswith("https://github.com/")
    version_module.reset_cache()


def test_a_failed_github_lookup_is_reported_not_raised(auth_client, monkeypatch):
    from app import version as version_module

    version_module.reset_cache()

    def boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(version_module, "_fetch_latest", boom)
    body = auth_client.get("/api/version").json()
    assert body["update_available"] is False
    assert "no network" in body["error"]
    # A failure must not be cached, otherwise a blip hides updates for six hours.
    assert not version_module._cache
    version_module.reset_cache()


def test_update_check_can_be_turned_off(auth_client, monkeypatch):
    from app import version as version_module

    version_module.reset_cache()
    monkeypatch.setenv("CLEANARR_DISABLE_UPDATE_CHECK", "1")
    monkeypatch.setattr(version_module, "_fetch_latest", lambda: pytest.fail("should not call GitHub"))
    body = auth_client.get("/api/version").json()
    assert body["check_enabled"] is False
    assert body["latest"] == ""
