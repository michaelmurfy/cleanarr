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
