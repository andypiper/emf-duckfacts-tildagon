# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "Pillow",
#     "numpy",
# ]
# ///
"""
Convert SVG icons to gzip JSON sprites for the Tildagon badge.

Uses rsvg-convert (librsvg) to rasterise each SVG at ICON_SIZE×ICON_SIZE px,
then encodes non-transparent pixels as white RLE segments in the same format
used by encode_sprites.py.  A single-colour palette {"wh": [255,255,255]}
means the renderer can tint icons any colour at draw time if needed.

Usage:
    uv run tools/encode_icons.py

System requirement:
    rsvg-convert — dnf install librsvg2-tools
"""

import gzip
import io
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

OUT_DIR = Path("assets")
ICON_DIR = Path("icon-svgs")
ICON_SIZE = 20
ALPHA_THRESHOLD = 128
PALETTE = {"wh": [255, 255, 255]}

ICONS = {
    "icon_bolt": "bolt.svg",
    "icon_confetti": "confetti.svg",
    "icon_picture": "picture.svg",
    "icon_heart": "heart.svg",
    "icon_refresh": "refresh.svg",
}


def render_svg(svg_path: Path, size: int) -> np.ndarray:
    """Rasterise an SVG to an RGBA numpy array using rsvg-convert."""
    result = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path)],
        capture_output=True,
        check=True,
    )
    img = Image.open(io.BytesIO(result.stdout)).convert("RGBA")
    return np.array(img)


def encode_icon(arr: np.ndarray) -> list:
    """Encode non-transparent pixels as RLE segments [colour, x, y, run_length]."""
    h, w = arr.shape[:2]
    frame = []
    for y in range(h):
        x = 0
        while x < w:
            if int(arr[y, x, 3]) < ALPHA_THRESHOLD:
                x += 1
                continue
            run_start = x
            x += 1
            while x < w and int(arr[y, x, 3]) >= ALPHA_THRESHOLD:
                x += 1
            frame.append(["wh", run_start, y, x - run_start])
    return frame


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print(f"Encoding icons at {ICON_SIZE}×{ICON_SIZE}px\n")
    for name, svg_name in ICONS.items():
        svg_path = ICON_DIR / svg_name
        arr = render_svg(svg_path, ICON_SIZE)
        frame = encode_icon(arr)
        data = {
            "w": ICON_SIZE,
            "h": ICON_SIZE,
            "palette": PALETTE,
            "frames": [frame],
        }
        out = OUT_DIR / f"{name}.json.gz"
        out.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))
        print(f"  {name}: {len(frame)} segments, {out.stat().st_size} bytes gzipped")
    print(f"\nDone. Icons in {OUT_DIR}/")


if __name__ == "__main__":
    main()
