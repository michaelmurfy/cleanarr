from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import httpx2

_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


class ServiceError(RuntimeError):
    def __init__(self, service: str, message: str, status: int | None = None):
        super().__init__(message)
        self.service = service
        self.status = status


def _same_host(left: str, right: str) -> bool:
    a, b = urlparse(left), urlparse(right)
    return bool(a.netloc and a.netloc.lower() == b.netloc.lower())


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
) -> httpx2.Response:
    """HTTP call that follows redirects only while the host stays the same.

    Cross-host redirects would re-send X-Api-Key / x-api-token. Same-host hops
    (http→https, trailing slash) still work so existing service URLs keep connecting.
    """
    current_url = url
    current_method = method
    current_json = json
    with httpx2.Client(timeout=45.0, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            response = client.request(
                current_method,
                current_url,
                headers=headers,
                params=params,
                json=current_json,
            )
            if response.status_code not in _REDIRECTS:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            next_url = urljoin(str(response.url), location)
            if not _same_host(current_url, next_url):
                return response
            current_url = next_url
            params = None
            if response.status_code == 303:
                current_method = "GET"
                current_json = None
        return response


def json_get(url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
    response = _request("GET", url, headers=headers, params=params)
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
    response = _request(method, url, headers=headers, params=params, json=json)
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
