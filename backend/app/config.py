from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_KEY_MAP = {
    "tautulli_url": "TAUTULLI_URL",
    "tautulli_api_key": "TAUTULLI_API_KEY",
    "tracearr_url": "TRACEARR_URL",
    "tracearr_api_key": "TRACEARR_API_KEY",
    "jellystat_url": "JELLYSTAT_URL",
    "jellystat_api_key": "JELLYSTAT_API_KEY",
    "seerr_url": "SEERR_URL",
    "seerr_api_key": "SEERR_API_KEY",
    "sonarr_url": "SONARR_URL",
    "sonarr_api_key": "SONARR_API_KEY",
    "radarr_url": "RADARR_URL",
    "radarr_api_key": "RADARR_API_KEY",
    "radarr_4k_url": "RADARR_4K_URL",
    "radarr_4k_api_key": "RADARR_4K_API_KEY",
    "sonarr_external_url": "SONARR_EXTERNAL_URL",
    "radarr_external_url": "RADARR_EXTERNAL_URL",
    "radarr_4k_external_url": "RADARR_4K_EXTERNAL_URL",
    "seerr_external_url": "SEERR_EXTERNAL_URL",
    "tautulli_external_url": "TAUTULLI_EXTERNAL_URL",
    "tracearr_external_url": "TRACEARR_EXTERNAL_URL",
    "jellystat_external_url": "JELLYSTAT_EXTERNAL_URL",
    "auth_username": "CLEANARR_USERNAME",
}


def env_value(name: str) -> str:
    return (os.environ.get(name) or dotenv_values().get(name) or "").strip()


def env_flag(name: str) -> bool:
    return env_value(name).lower() in {"1", "true", "yes", "on"}


def hide_env_settings() -> bool:
    return env_flag("CLEANARR_HIDE_SETTINGS")


def env_file_paths() -> list[Path]:
    paths = [
        Path("/app/.env"),
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        paths.append(Path(data_dir) / ".env")
    return paths


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def dotenv_values() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in env_file_paths():
        if path and path.is_file():
            merged.update(_parse_env_file(path))
    return merged


def env_file_present() -> bool:
    return any(path and path.is_file() for path in env_file_paths())


def locked_setting_keys() -> set[str]:
    if hide_env_settings():
        return set(ENV_KEY_MAP.keys())
    locked: set[str] = set()
    for field, env_name in ENV_KEY_MAP.items():
        if env_value(env_name):
            locked.add(field)
    return locked


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cleanarr_username: str = "admin"
    cleanarr_password: str = "changeme"
    cleanarr_secret: str = "change-me"
    data_dir: str = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parents[2] / "data"))
    port: int = 7585

    tautulli_url: str = ""
    tautulli_api_key: str = ""
    tracearr_url: str = ""
    tracearr_api_key: str = ""
    jellystat_url: str = ""
    jellystat_api_key: str = ""
    seerr_url: str = ""
    seerr_api_key: str = ""
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_4k_url: str = ""
    radarr_4k_api_key: str = ""


APP_SETTING_KEYS = (
    "sync_schedule_enabled",
    "sync_interval_hours",
    "auto_delete_enabled",
    "auto_delete_max_per_run",
    "auto_delete_stale_days",
)

settings = Settings()
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
(Path(settings.data_dir) / "art").mkdir(parents=True, exist_ok=True)
