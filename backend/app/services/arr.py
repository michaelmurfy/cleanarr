from __future__ import annotations

from typing import Any

from .http import json_get, json_request, tidy_url


def poster_from(images: list[dict[str, Any]] | None) -> str:
    for image in images or []:
        if (image.get("coverType") or "").lower() == "poster":
            return image.get("remoteUrl") or image.get("url") or ""
    return ""


def movie_availability(movie: dict[str, Any] | None) -> str:
    movie = movie or {}
    if movie.get("hasFile") or movie.get("movieFile"):
        return "downloaded"
    if int(movie.get("sizeOnDisk") or 0) > 0:
        return "downloaded"
    return "requested"


def series_availability(show: dict[str, Any] | None) -> str:
    show = show or {}
    stats = show.get("statistics") or {}
    files = int(stats.get("episodeFileCount") or 0)
    size = int(stats.get("sizeOnDisk") or show.get("sizeOnDisk") or 0)
    aired = int(stats.get("episodeCount") or 0)
    if files <= 0 and size <= 0:
        return "requested"
    if aired and files < aired:
        return "partial"
    try:
        pct = float(stats.get("percentOfEpisodes") or 100)
    except (TypeError, ValueError):
        pct = 100.0
    if files > 0 and pct < 100:
        return "partial"
    return "downloaded"


def pick_rating(ratings: dict[str, Any] | None) -> tuple[float | None, int, str]:
    if not ratings:
        return None, 0, ""
    if isinstance(ratings.get("value"), (int, float)) and ratings.get("value"):
        return float(ratings["value"]), int(ratings.get("votes") or 0), "arr"
    for source in ("imdb", "tmdb", "trakt", "rottenTomatoes", "metacritic"):
        block = ratings.get(source) or {}
        value = block.get("value")
        if not isinstance(value, (int, float)) or not value:
            continue
        votes = int(block.get("votes") or 0)
        score = float(value)
        if source in {"rottenTomatoes", "metacritic"} and score > 10:
            score = score / 10.0
        return round(score, 1), votes, source
    return None, 0, ""


class Radarr:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.headers = {"X-Api-Key": api_key}

    def test(self) -> str:
        data = json_get(f"{self.url}/api/v3/system/status", headers=self.headers)
        return str((data or {}).get("version") or "ok")

    def movies(self) -> list[dict[str, Any]]:
        data = json_get(f"{self.url}/api/v3/movie", headers=self.headers) or []
        return data if isinstance(data, list) else []

    def delete(self, movie_id: int, delete_files: bool = True, exclude: bool = False) -> None:
        json_request(
            "DELETE",
            f"{self.url}/api/v3/movie/{movie_id}",
            headers=self.headers,
            params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": str(exclude).lower()},
        )


class Sonarr:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.headers = {"X-Api-Key": api_key}

    def test(self) -> str:
        data = json_get(f"{self.url}/api/v3/system/status", headers=self.headers)
        return str((data or {}).get("version") or "ok")

    def series(self) -> list[dict[str, Any]]:
        data = json_get(f"{self.url}/api/v3/series", headers=self.headers) or []
        return data if isinstance(data, list) else []

    def delete(self, series_id: int, delete_files: bool = True, exclude: bool = False) -> None:
        json_request(
            "DELETE",
            f"{self.url}/api/v3/series/{series_id}",
            headers=self.headers,
            params={
                "deleteFiles": str(delete_files).lower(),
                "addImportListExclusion": str(exclude).lower(),
            },
        )
