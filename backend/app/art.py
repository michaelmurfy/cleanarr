from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from .config import settings
from .db import connect

ART_DIR = Path(settings.data_dir) / "art"
ALLOWED_SCHEMES = {"http", "https"}
CACHE_HEADERS = {"Cache-Control": "private, max-age=2592000, immutable"}
MAX_ART_BYTES = 12 * 1024 * 1024
ALLOWED_ART_TYPES = ("image/",)
_warming = False


def _path_for(media_type: str, tmdb_id: int, tvdb_id: int) -> Path:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    kind = "tv" if media_type == "tv" else "movie"
    return ART_DIR / f"{kind}-{int(tmdb_id or 0)}-{int(tvdb_id or 0)}.img"


def art_url(item_id: int, poster_url: str) -> str:
    if not poster_url:
        return ""
    return f"/api/art/{int(item_id)}"


def serve_art(item_id: int) -> Response:
    row = _row(item_id)
    cached = _path_for(row["media_type"], row["tmdb_id"], row["tvdb_id"])
    if cached.exists() and cached.stat().st_size > 0:
        return FileResponse(cached, media_type=_guess_type(cached), headers=CACHE_HEADERS)
    data, media_type = _download(row["poster_url"], cached)
    return Response(content=data, media_type=media_type, headers=CACHE_HEADERS)


def remove_art(media_type: str, tmdb_id: int, tvdb_id: int) -> None:
    path = _path_for(media_type, tmdb_id, tvdb_id)
    if path.exists():
        path.unlink()


def warm_cache() -> None:
    global _warming
    if _warming:
        return

    def run() -> None:
        global _warming
        _warming = True
        try:
            with connect() as conn:
                rows = conn.execute(
                    "SELECT media_type, tmdb_id, tvdb_id, poster_url FROM media WHERE poster_url != ''"
                ).fetchall()
            for row in rows:
                cached = _path_for(row["media_type"], row["tmdb_id"], row["tvdb_id"])
                if cached.exists() and cached.stat().st_size > 0:
                    continue
                try:
                    _download(row["poster_url"], cached)
                except Exception:
                    continue
        finally:
            _warming = False

    threading.Thread(target=run, daemon=True).start()


def _row(item_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT media_type, tmdb_id, tvdb_id, poster_url FROM media WHERE id = ?",
            (item_id,),
        ).fetchone()
    if not row or not row["poster_url"]:
        raise HTTPException(status_code=404, detail="No artwork")
    return row


def _download(url: str, cached: Path) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid artwork URL")
    try:
        # Poster URLs come from *arr metadata, so cap the redirect chain and the
        # response size rather than trusting whatever they point at.
        with httpx.Client(timeout=20.0, follow_redirects=True, max_redirects=3) as client:
            response = client.get(url, headers={"User-Agent": "Cleanarr"})
            response.raise_for_status()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Artwork fetch failed: {exc}") from exc
    declared = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if declared and not declared.startswith(ALLOWED_ART_TYPES):
        raise HTTPException(status_code=502, detail="Artwork URL did not return an image")
    data = response.content
    if not data:
        raise HTTPException(status_code=502, detail="Empty artwork")
    if len(data) > MAX_ART_BYTES:
        raise HTTPException(status_code=502, detail="Artwork is too large")
    ART_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(cached)
    return data, declared or "image/jpeg"


def cache_stats() -> dict[str, int]:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    files = 0
    size = 0
    for path in ART_DIR.iterdir():
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return {"files": files, "bytes": size}


def clear_cache() -> int:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    removed = 0
    for path in ART_DIR.iterdir():
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def _guess_type(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"GIF"):
        return "image/gif"
    if head.startswith(b"RIFF") and b"WEBP" in head:
        return "image/webp"
    return "image/jpeg"
