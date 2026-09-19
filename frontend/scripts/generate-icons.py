#!/usr/bin/env python3
"""Rasterise the Cleanarr mark in src/Logo.tsx into the icons under public/.

Dashboards like Heimdall, Homarr and Organizr scrape a site for a bitmap and
ignore an inline SVG, so the mark has to exist as a real .ico and .png. Run this
after changing the logo:

    python3 frontend/scripts/generate-icons.py

Shapes are signed distance fields, which antialias exactly at any size, so the
16px tile stays legible instead of being a blurred downscale of a large render.
Deliberately dependency free: CI installs no Python imaging library.
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"

# Everything below is in the 64x64 viewBox of Logo.tsx.
VIEWBOX = 64.0
TILE_RADIUS = 16.0
TILE_GRADIENT = ((8, 4), (56, 60), (0x26, 0x1C, 0x11), (0x11, 0x10, 0x0C))
EDGE_COLOR = (0xD4, 0xA0, 0x54)
EDGE_ALPHA = 0.32
SWEEP_GRADIENT = ((16, 16), (46, 48), (0xF6, 0xDC, 0xAE), (0xC7, 0x8F, 0x3F))
SWEEP_CENTER = (31.27, 32.0)
SWEEP_RADIUS = 16.4
SWEEP_WIDTH = 6.0
# The arc runs from (37.9, 17) anticlockwise to (37.9, 47), leaving the gap on
# the right for the bars. Angles are clockwise from +x because y points down.
SWEEP_FROM = math.radians(66.16)
SWEEP_TO = math.radians(293.84)
BARS = (
    (35.0, 20.5, 14.0, 6.0, 3.0, (0xF6, 0xDC, 0xAE), 1.0),
    (35.0, 29.0, 10.5, 6.0, 3.0, (0xD4, 0xA0, 0x54), 1.0),
    (35.0, 37.5, 7.0, 6.0, 3.0, (0xD4, 0xA0, 0x54), 0.55),
)


def rounded_rect(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> float:
    """Signed distance to a rounded rectangle, negative inside."""
    hw, hh = w / 2, h / 2
    r = min(r, hw, hh)
    qx = abs(px - (x + hw)) - (hw - r)
    qy = abs(py - (y + hh)) - (hh - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - r


def arc(px: float, py: float) -> float:
    """Signed distance to the round-capped sweep, negative inside the stroke."""
    cx, cy = SWEEP_CENTER
    vx, vy = px - cx, py - cy
    angle = math.atan2(vy, vx) % (2 * math.pi)
    if SWEEP_FROM <= angle <= SWEEP_TO:
        edge = abs(math.hypot(vx, vy) - SWEEP_RADIUS)
    else:
        edge = min(
            math.hypot(px - (cx + SWEEP_RADIUS * math.cos(end)), py - (cy + SWEEP_RADIUS * math.sin(end)))
            for end in (SWEEP_FROM, SWEEP_TO)
        )
    return edge - SWEEP_WIDTH / 2


def gradient(px: float, py: float, spec) -> tuple[float, float, float]:
    (x1, y1), (x2, y2), start, end = spec
    dx, dy = x2 - x1, y2 - y1
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = min(max(t, 0.0), 1.0)
    return tuple(a + (b - a) * t for a, b in zip(start, end))


def over(dst: list[float], color, alpha: float) -> None:
    """Composite one antialiased layer onto an unpremultiplied RGBA pixel."""
    if alpha <= 0:
        return
    out_a = alpha + dst[3] * (1 - alpha)
    if out_a <= 0:
        dst[:] = [0.0, 0.0, 0.0, 0.0]
        return
    for i in range(3):
        dst[i] = (color[i] * alpha + dst[i] * dst[3] * (1 - alpha)) / out_a
    dst[3] = out_a


def render(size: int, square: bool = False) -> bytearray:
    """Draw the mark at `size` px. `square` skips the rounded corners for iOS,
    which applies its own mask and would otherwise round it twice."""
    scale = VIEWBOX / size
    # One pixel in viewBox units, the width the coverage ramp spans.
    unit = scale
    radius = 0.0 if square else TILE_RADIUS
    pixels = bytearray(size * size * 4)

    def coverage(distance: float) -> float:
        return min(max(0.5 - distance / unit, 0.0), 1.0)

    for row in range(size):
        py = (row + 0.5) * scale
        for col in range(size):
            px = (col + 0.5) * scale
            pixel = [0.0, 0.0, 0.0, 0.0]

            tile = rounded_rect(px, py, 0, 0, VIEWBOX, VIEWBOX, radius)
            over(pixel, gradient(px, py, TILE_GRADIENT), coverage(tile))

            if not square:
                edge = abs(rounded_rect(px, py, 1, 1, 62, 62, 15)) - 0.5
                over(pixel, EDGE_COLOR, coverage(edge) * EDGE_ALPHA)

            over(pixel, gradient(px, py, SWEEP_GRADIENT), coverage(arc(px, py)))

            for x, y, w, h, r, color, alpha in BARS:
                over(pixel, color, coverage(rounded_rect(px, py, x, y, w, h, r)) * alpha)

            at = (row * size + col) * 4
            pixels[at : at + 4] = bytes(
                round(min(max(channel, 0.0), 255.0))
                for channel in (pixel[0], pixel[1], pixel[2], pixel[3] * 255)
            )
    return pixels


def png(pixels: bytearray, size: int) -> bytes:
    stride = size * 4
    raw = bytearray()
    for row in range(size):
        raw.append(0)
        raw += pixels[row * stride : (row + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def ico(images: list[tuple[int, bytearray]]) -> bytes:
    """Pack tiles into a classic multi-resolution .ico.

    Entries are 32-bit BMPs rather than embedded PNGs: every scraper and every
    Windows shell reads BMP entries, while PNG entries are a later addition that
    older readers show as an empty box.
    """
    entries, bodies = [], []
    offset = 6 + 16 * len(images)
    for size, pixels in images:
        body = bytearray(struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0))
        for row in reversed(range(size)):  # BMP scanlines run bottom-up.
            for col in range(size):
                at = (row * size + col) * 4
                r, g, b, a = pixels[at : at + 4]
                body += bytes((b, g, r, a))
        body += bytes(((size + 31) // 32) * 4 * size)  # Empty AND mask.
        entries.append(
            struct.pack("<BBBBHHII", size & 0xFF, size & 0xFF, 0, 0, 1, 32, len(body), offset)
        )
        bodies.append(bytes(body))
        offset += len(body)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(bodies)


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    tiles = {size: render(size) for size in (16, 32, 48, 192, 512)}

    (PUBLIC / "favicon.ico").write_bytes(ico([(s, tiles[s]) for s in (16, 32, 48)]))
    for size in (16, 32):
        (PUBLIC / f"favicon-{size}x{size}.png").write_bytes(png(tiles[size], size))
    for size in (192, 512):
        (PUBLIC / f"icon-{size}.png").write_bytes(png(tiles[size], size))
    (PUBLIC / "apple-touch-icon.png").write_bytes(png(render(180, square=True), 180))

    manifest = {
        "name": "Cleanarr",
        "short_name": "Cleanarr",
        "description": "Reclaim disk space from Radarr and Sonarr using watch history and request data.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0c1018",
        "theme_color": "#0c1018",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/apple-touch-icon.png", "sizes": "180x180", "type": "image/png", "purpose": "maskable"},
        ],
    }
    (PUBLIC / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n")

    for path in sorted(PUBLIC.iterdir()):
        print(f"{path.name:<26} {path.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
