from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PUBLIC = FRONTEND / "public"


@pytest.fixture(scope="module")
def generator():
    """Load frontend/scripts/generate-icons.py so the tests compare against its output."""
    spec = importlib.util.spec_from_file_location("generate_icons", FRONTEND / "scripts" / "generate-icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_favicon_is_a_real_multi_size_icon():
    """Heimdall and friends scrape /favicon.ico, so it has to be a bitmap, not an SVG."""
    data = (PUBLIC / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    assert count == 3

    sizes = []
    for index in range(count):
        entry = data[6 + 16 * index : 22 + 16 * index]
        width, height, _colors, _reserved, planes, bits, length, offset = struct.unpack("<BBBBHHII", entry)
        assert (width, planes, bits) == (height, 1, 32)
        sizes.append(width)

        # Entries are uncompressed BMPs rather than embedded PNGs, which older readers refuse.
        header = struct.unpack("<IiiHHIIiiII", data[offset : offset + 40])
        assert header[0] == 40
        assert (header[1], header[2]) == (width, height * 2)  # Height covers the XOR and AND masks.
        assert header[5] == 0  # BI_RGB.
        assert length == 40 + width * height * 4 + ((width + 31) // 32) * 4 * height
        assert offset + length <= len(data)

    assert sizes == [16, 32, 48]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("favicon-16x16.png", 16),
        ("favicon-32x32.png", 32),
        ("apple-touch-icon.png", 180),
        ("icon-192.png", 192),
        ("icon-512.png", 512),
    ],
)
def test_png_icons_are_square_and_the_advertised_size(name, expected):
    assert png_size((PUBLIC / name).read_bytes()) == (expected, expected)


def test_committed_icons_match_the_generator(generator):
    """Guards against a hand-edited icon drifting from the logo in Logo.tsx.

    Only the small tiles are redrawn: the 512px render is slow in pure Python and
    shares every code path with these.
    """
    tiles = {size: generator.render(size) for size in (16, 32, 48)}
    assert (PUBLIC / "favicon.ico").read_bytes() == generator.ico([(s, tiles[s]) for s in (16, 32, 48)])
    for size in (16, 32):
        assert (PUBLIC / f"favicon-{size}x{size}.png").read_bytes() == generator.png(tiles[size], size)


def test_manifest_points_at_files_that_exist():
    manifest = json.loads((PUBLIC / "site.webmanifest").read_text())
    assert manifest["icons"]
    for icon in manifest["icons"]:
        assert (PUBLIC / icon["src"].lstrip("/")).is_file(), icon["src"]


def test_index_html_links_the_bitmaps_not_a_data_uri():
    html = (FRONTEND / "index.html").read_text()
    for reference in ("/favicon.ico", "/apple-touch-icon.png", "/site.webmanifest"):
        assert reference in html, reference
    assert "data:image/svg+xml" not in html


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/favicon.ico", "image/vnd.microsoft.icon"),
        ("/favicon-32x32.png", "image/png"),
        ("/apple-touch-icon.png", "image/png"),
        ("/site.webmanifest", "application/manifest+json"),
    ],
)
def test_icons_are_served_from_the_bundle_root_without_a_session(client, bundle, path, content_type):
    """A dashboard scraping the icon is not logged in, so these must stay public."""
    client.cookies.clear()

    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.content == (PUBLIC / path.lstrip("/")).read_bytes()


def test_head_on_an_icon_is_answered(client, bundle):
    """Scrapers send HEAD first, and FastAPI does not add it to a GET route for you."""
    client.cookies.clear()

    response = client.head("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/vnd.microsoft.icon"
    assert response.headers["content-length"] == str((PUBLIC / "favicon.ico").stat().st_size)
