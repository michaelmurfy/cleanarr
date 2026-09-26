from pathlib import Path

from app import config


def test_env_flag_truthy_values(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CLEANARR_TEST_FLAG", value)
        assert config.env_flag("CLEANARR_TEST_FLAG") is True
    for value in ("0", "false", "", "maybe"):
        monkeypatch.setenv("CLEANARR_TEST_FLAG", value)
        assert config.env_flag("CLEANARR_TEST_FLAG") is False


def test_parse_env_file_strips_quotes_and_comments(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('# comment\nFOO=bar\nBAZ="quoted"\nQUX=\'single\'\nnot-a-pair\n', encoding="utf-8")
    assert config._parse_env_file(env) == {"FOO": "bar", "BAZ": "quoted", "QUX": "single"}


def test_locked_keys_cover_everything_when_settings_are_hidden(monkeypatch):
    monkeypatch.setenv("CLEANARR_HIDE_SETTINGS", "1")
    assert config.locked_setting_keys() == set(config.ENV_KEY_MAP)


def test_locked_keys_track_individual_env_vars(monkeypatch):
    monkeypatch.setenv("CLEANARR_HIDE_SETTINGS", "0")
    monkeypatch.setattr(config, "dotenv_values", dict)
    monkeypatch.setenv("RADARR_URL", "http://radarr:7878")
    monkeypatch.delenv("SONARR_URL", raising=False)
    locked = config.locked_setting_keys()
    assert "radarr_url" in locked
    assert "sonarr_url" not in locked


def test_cfg_prefers_env_and_dotenv_over_db(monkeypatch, tmp_path):
    from app.db import init_db, set_setting
    from app.services import clients

    init_db()
    monkeypatch.setattr(config, "dotenv_values", lambda: {"RADARR_URL": "http://from-dotenv:7878"})
    monkeypatch.delenv("RADARR_URL", raising=False)
    set_setting("radarr_url", "http://from-db:7878")
    assert clients.cfg("radarr_url") == "http://from-dotenv:7878"

    monkeypatch.setenv("RADARR_URL", "http://from-process:7878")
    assert clients.cfg("radarr_url") == "http://from-process:7878"


def test_prune_removes_db_duplicates_of_env_keys(monkeypatch):
    from app.db import get_setting, init_db, set_setting
    from app.services import clients

    init_db()
    monkeypatch.setenv("RADARR_URL", "http://env-radarr")
    monkeypatch.setattr(config, "dotenv_values", dict)
    set_setting("radarr_url", "http://db-radarr")
    set_setting("sonarr_url", "http://sonarr-db")
    removed = clients.prune_env_overridden_settings()
    assert removed >= 1
    assert get_setting("radarr_url") == ""
    assert get_setting("sonarr_url") == "http://sonarr-db"
    assert clients.cfg("radarr_url") == "http://env-radarr"
