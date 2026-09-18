from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
