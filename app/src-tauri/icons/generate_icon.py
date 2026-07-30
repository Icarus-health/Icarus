#!/usr/bin/env python3
"""Erzeugt das App-Icon.

Bewusst generiert statt als Binärblob eingecheckt: so ist im Repo nachlesbar,
woraus das Icon besteht, und es lässt sich ohne Grafikprogramm ändern.

    python3 generate_icon.py

Schreibt icon.png (512x512, RGBA). Für die Auslieferung erzeugt
`tauri icon icons/icon.png` daraus die plattformspezifischen Formate.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 512

# Dunkler Grund, heller Aufstieg — Icarus.
BG = (23, 22, 20, 255)
MARK = (122, 167, 217, 255)
ACCENT = (214, 158, 92, 255)


def _blend(base: tuple[int, ...], over: tuple[int, ...], alpha: float) -> tuple[int, int, int, int]:
    return (
        round(base[0] + (over[0] - base[0]) * alpha),
        round(base[1] + (over[1] - base[1]) * alpha),
        round(base[2] + (over[2] - base[2]) * alpha),
        255,
    )


def _coverage(cx: float, cy: float, x: int, y: int, radius: float) -> float:
    """Antialiasing über 2x2-Supersampling."""
    hits = 0.0
    for dx in (0.25, 0.75):
        for dy in (0.25, 0.75):
            px, py = x + dx - cx, y + dy - cy
            if px * px + py * py <= radius * radius:
                hits += 0.25
    return hits


def build_pixels() -> list[list[tuple[int, int, int, int]]]:
    cx = cy = SIZE / 2
    outer = SIZE * 0.46
    rows: list[list[tuple[int, int, int, int]]] = []

    for y in range(SIZE):
        row: list[tuple[int, int, int, int]] = []
        for x in range(SIZE):
            disc = _coverage(cx, cy, x, y, outer)
            pixel = _blend((0, 0, 0, 0), BG, disc) if disc else (0, 0, 0, 0)

            # Aufsteigende Schwinge: drei Balken zunehmender Länge.
            nx = (x - cx) / outer
            ny = (y - cy) / outer
            for index, (offset, length) in enumerate(((0.34, 0.30), (0.02, 0.52), (-0.30, 0.74))):
                if abs(ny - offset) < 0.075 and -0.62 < nx < -0.62 + length:
                    colour = ACCENT if index == 2 else MARK
                    pixel = _blend(pixel, colour, disc)

            # Diagonale, die die Balken verbindet.
            if disc and abs((ny - nx * 0.62) - 0.02) < 0.055 and -0.10 < nx < 0.56:
                pixel = _blend(pixel, MARK, disc)

            row.append(pixel)
        rows.append(row)
    return rows


def write_png(path: Path, rows: list[list[tuple[int, int, int, int]]]) -> None:
    raw = bytearray()
    for row in rows:
        raw.append(0)  # Filter-Typ "None"
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    target = Path(__file__).with_name("icon.png")
    write_png(target, build_pixels())
    print(f"{target} geschrieben ({target.stat().st_size} Bytes)")
