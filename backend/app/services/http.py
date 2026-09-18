from __future__ import annotations

from typing import Any

import httpx


class ServiceError(RuntimeError):
    def __init__(self, service: str, message: str, status: int | None = None):
        super().__init__(message)
        self.service = service
        self.status = status


def _client() -> httpx.Client:
    return httpx.Client(timeout=45.0, follow_redirects=True)


def json_get(url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
    with _client() as client:
        response = client.get(url, headers=headers, params=params)
        if response.status_code >= 400:
            raise ServiceError("http", f"{url} returned {response.status_code}", response.status_code)
        if not response.content:
            return None
        return response.json()


def json_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
) -> Any:
    with _client() as client:
        response = client.request(method, url, headers=headers, params=params, json=json)
        if response.status_code >= 400:
            snippet = (response.text or "")[:240]
            raise ServiceError("http", f"{url} returned {response.status_code}: {snippet}", response.status_code)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except Exception:
            return response.text


def tidy_url(url: str) -> str:
    return (url or "").rstrip("/")
