from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_KEY_MAP = {
    "tautulli_url": "TAUTULLI_URL",
    "tautulli_api_key": "TAUTULLI_API_KEY",
    "tracearr_url": "TRACEARR_URL",
    "tracearr_api_key": "TRACEARR_API_KEY",
    "seerr_url": "SEERR_URL",
    "seerr_api_key": "SEERR_API_KEY",
    "sonarr_url": "SONARR_URL",
    "sonarr_api_key": "SONARR_API_KEY",
    "radarr_url": "RADARR_URL",
    "radarr_api_key": "RADARR_API_KEY",
    "sonarr_external_url": "SONARR_EXTERNAL_URL",
    "radarr_external_url": "RADARR_EXTERNAL_URL",
    "seerr_external_url": "SEERR_EXTERNAL_URL",
    "tautulli_external_url": "TAUTULLI_EXTERNAL_URL",
    "tracearr_external_url": "TRACEARR_EXTERNAL_URL",
    "auth_username": "CLEANARR_USERNAME",
}


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
    locked: set[str] = set()
    file_vals = dotenv_values()
    for field, env_name in ENV_KEY_MAP.items():
        if env_name in os.environ or env_name in file_vals:
            locked.add(field)
    if env_file_present():
        locked.update(ENV_KEY_MAP.keys())
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
    seerr_url: str = ""
    seerr_api_key: str = ""
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    radarr_url: str = ""
    radarr_api_key: str = ""


settings = Settings()
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
(Path(settings.data_dir) / "art").mkdir(parents=True, exist_ok=True)
