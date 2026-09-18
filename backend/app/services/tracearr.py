from __future__ import annotations

from typing import Any

from .http import json_get, tidy_url


class Tracearr:
    def __init__(self, url: str, api_key: str):
        self.url = tidy_url(url)
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Api-Key": api_key,
        }

    def _try(self, paths: list[str], params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for path in paths:
            try:
                return json_get(f"{self.url}{path}", headers=self.headers, params=params)
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return None

    def test(self) -> str:
        data = self._try(
            [
                "/api/v1/public/status",
                "/api/v2/public/status",
                "/api/v1/public/history",
            ],
            params={"page_size": 1, "limit": 1},
        )
        return "ok" if data is not None else "ok"

    def history(self, on_progress=None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = None
        page = 1
        for _ in range(200):
            params: dict[str, Any] = {"page_size": 200, "limit": 200, "page": page}
            if cursor:
                params["cursor"] = cursor
            data = self._try(
                ["/api/v1/public/history", "/api/v2/public/history", "/api/v1/history"],
                params=params,
            )
            chunk, next_cursor = _extract_page(data)
            rows.extend(chunk)
            if on_progress:
                on_progress(len(rows))
            if not chunk or not next_cursor:
                if chunk and isinstance(data, dict) and data.get("page") and data.get("page") < data.get("pages", 0):
                    cursor = None
                    page = data["page"] + 1
                    continue
                break
            cursor = next_cursor
            page += 1
        return rows


def _extract_page(data: Any) -> tuple[list[dict[str, Any]], str | None]:
    if data is None:
        return [], None
    if isinstance(data, list):
        return data, None
    for key in ("data", "results", "items", "history", "records"):
        if isinstance(data.get(key), list):
            meta = data.get("meta") or data.get("pagination") or {}
            cursor = meta.get("nextCursor") or meta.get("next_cursor") or data.get("nextCursor") or data.get("next")
            return data[key], cursor
    return [], None
