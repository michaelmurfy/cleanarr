import httpx
import pytest
from fastapi import HTTPException

from app import art


def use_transport(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(art.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_oversized_artwork_is_cut_off(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "MAX_ART_BYTES", 1000)
    use_transport(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 5000))
    with pytest.raises(HTTPException) as caught:
        art._download("https://img.example/poster.jpg", tmp_path / "p.jpg")
    assert "too large" in caught.value.detail
    assert not (tmp_path / "p.jpg").exists()


def test_artwork_errors_are_redacted(monkeypatch, tmp_path):
    def boom(request):
        raise httpx.ConnectError(f"failed {request.url}")

    use_transport(monkeypatch, boom)
    with pytest.raises(HTTPException) as caught:
        art._download("https://img.example/poster.jpg?apikey=abcdef123456", tmp_path / "p.jpg")
    assert "abcdef123456" not in caught.value.detail


def test_small_image_is_cached(monkeypatch, tmp_path):
    use_transport(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "image/png"}, content=b"png-bytes"))
    data, kind = art._download("https://img.example/poster.png", tmp_path / "p.png")
    assert data == b"png-bytes" and kind == "image/png"
    assert (tmp_path / "p.png").read_bytes() == b"png-bytes"
