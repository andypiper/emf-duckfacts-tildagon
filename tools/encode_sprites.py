# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "Pillow",
#     "numpy",
# ]
# ///
"""
Slice and encode duck spritesheet frames into gzip JSON for the Tildagon badge.

Handles three duck families:
  duck  — ducky_3_spritesheet.png  (mallard, 192×128 spritesheet)
  duck2 — ducky_2_spritesheet.png  (yellow rubber duck, same layout)
  duck3 — ducky-idle.png + ducky-walk.png  (alternate yellow duck, separate PNGs)

Usage:
    uv run tools/encode_sprites.py
"""

import gzip
import json
from pathlib import Path

import numpy as np
from PIL import Image

OUT_DIR = Path("assets")
ALPHA_THRESHOLD = 128

# ---------------------------------------------------------------------------
# Duck 1 & 2 — shared 192×128 spritesheet layout
# ---------------------------------------------------------------------------

# Frame pixel bounds within the spritesheet (col_start, col_end inclusive)
SHEET_COL_BOUNDS = [(5, 29), (37, 60), (70, 91), (101, 125), (134, 155), (166, 187)]
SHEET_ROW_BOUNDS = [(7, 31), (37, 63), (68, 95), (101, 127)]

# Populated frames per row (row_index: [col_indices])
SHEET_POPULATED = {
    0: [0, 1],
    1: [0, 1, 2, 3, 4, 5],
    2: [0, 1, 2, 3],
    3: [0, 1, 2, 3, 4, 5],
}

# Duck 1 (mallard) palette — 18 colours
PALETTE_DUCK1 = {
    "wh": (255, 255, 255),  # white highlight
    "dg": (30, 60, 9),  # dark green (body shadow)
    "mg": (72, 126, 35),  # mid green (body main)
    "lg": (96, 159, 54),  # light green (body highlight)
    "ag": (50, 93, 20),  # accent green
    "bk": (0, 0, 0),  # black (outline/eye)
    "dk": (57, 43, 28),  # dark brown (outline)
    "br": (93, 73, 52),  # brown (belly shadow)
    "mt": (132, 107, 82),  # mid tan (belly)
    "ht": (147, 124, 101),  # highlight tan
    "lt": (183, 160, 137),  # light tan
    "bl": (222, 203, 183),  # buff/linen (belly light)
    "pk": (255, 232, 185),  # peach highlight
    "gd": (174, 128, 33),  # gold (bill base)
    "yo": (219, 166, 55),  # yellow-orange (bill mid)
    "ya": (252, 197, 82),  # yellow highlight (bill)
    "or": (210, 95, 39),  # orange (bill/feet)
    "ro": (171, 72, 24),  # red-orange (bill shadow)
}

# Duck 2 (yellow rubber duck) palette — same art style, all-yellow body
PALETTE_DUCK2 = {
    "wh": (255, 255, 255),  # white highlight
    "bk": (0, 0, 0),  # black (outline/eye)
    "pk": (255, 232, 185),  # peach/cream highlight
    "lo": (255, 132, 73),  # light orange
    "ya": (252, 197, 82),  # bright yellow (body highlight)
    "yo": (219, 166, 55),  # yellow-orange (body mid)
    "gd": (174, 128, 33),  # gold (body shadow)
    "or": (210, 95, 39),  # orange (bill/feet)
    "ro": (171, 72, 24),  # red-orange (bill shadow)
}

# ---------------------------------------------------------------------------
# Duck 3 — separate PNGs, 48px-wide frame slots
# ---------------------------------------------------------------------------

# Each PNG divides into 48×48px frame slots; content sits within each slot
DUCK3_SLOT_W = 48
DUCK3_SLOT_H = 48

# Tight content bounds within each slot (determined by analysis)
# idle: 2 frames — content at slot x=15-32, y=23-47  → 18×25
# walk: 4 frames — content at slot x=15-33, y=22-47  → 19×26
DUCK3_IDLE_CROP = (15, 23, 33, 48)  # (x0, y0, x1, y1) exclusive
DUCK3_WALK_CROP = (15, 22, 34, 48)  # slightly wider/taller for walk

PALETTE_DUCK3 = {
    "wh": (255, 255, 255),  # white highlight
    "bk": (64, 49, 12),  # dark brown (outline — this duck uses brown not black)
    "db": (108, 51, 13),  # darker brown (bill shadow)
    "mb": (112, 88, 27),  # mid brown
    "ya": (255, 204, 75),  # bright yellow (body)
    "yo": (211, 166, 53),  # yellow-gold (body shadow)
    "hi": (255, 244, 216),  # near-white highlight
    "or": (223, 113, 38),  # orange (bill/feet)
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_lookup(palette):
    return {rgb: code for code, rgb in palette.items()}


def closest_colour(rgb, palette, lookup):
    if rgb in lookup:
        return lookup[rgb]
    r, g, b = rgb
    best = min(
        palette.items(),
        key=lambda kv: sum((a - c) ** 2 for a, c in zip(kv[1], (r, g, b))),
    )
    return best[0]


def encode_frame(crop: np.ndarray, palette: dict, lookup: dict) -> list:
    """Encode one frame crop as RLE segments: [colour_code, x, y, run_length]."""
    h, w = crop.shape[:2]
    segments = []
    for y in range(h):
        x = 0
        while x < w:
            r, g, b, a = (int(v) for v in crop[y, x])
            if a < ALPHA_THRESHOLD:
                x += 1
                continue
            colour = (r, g, b)
            code = closest_colour(colour, palette, lookup)
            run_start = x
            x += 1
            while x < w:
                nr, ng, nb, na = (int(v) for v in crop[y, x])
                if na < ALPHA_THRESHOLD or (nr, ng, nb) != colour:
                    break
                x += 1
            segments.append([code, run_start, y, x - run_start])
    return segments


def write_animation(name: str, fw: int, fh: int, frames: list, palette: dict):
    data = {
        "w": fw,
        "h": fh,
        "palette": {code: list(rgb) for code, rgb in palette.items()},
        "frames": frames,
    }
    out_path = OUT_DIR / f"{name}.json.gz"
    payload = json.dumps(data, separators=(",", ":")).encode()
    out_path.write_bytes(gzip.compress(payload))
    total_segs = sum(len(fr) for fr in frames)
    print(
        f"  {name}: {len(frames)} frames, {total_segs} segments, "
        f"{out_path.stat().st_size} bytes gzipped"
    )


# ---------------------------------------------------------------------------
# Duck 1 & 2 spritesheet encoder
# ---------------------------------------------------------------------------


def encode_sheet_animation(
    sheet_path: Path, row_indices: list, name: str, palette: dict
):
    arr = np.array(Image.open(sheet_path))
    lookup = _make_lookup(palette)
    cx, cx2 = SHEET_COL_BOUNDS[0]
    ry, ry2 = SHEET_ROW_BOUNDS[0]
    fw, fh = cx2 - cx + 1, ry2 - ry + 1

    frames = []
    for row_i in row_indices:
        for col_i in SHEET_POPULATED.get(row_i, []):
            ry, ry2 = SHEET_ROW_BOUNDS[row_i]
            cx, cx2 = SHEET_COL_BOUNDS[col_i]
            crop = arr[ry : ry2 + 1, cx : cx2 + 1]
            frames.append(encode_frame(crop, palette, lookup))

    write_animation(name, fw, fh, frames, palette)


# ---------------------------------------------------------------------------
# Duck 3 separate-PNG encoder
# ---------------------------------------------------------------------------


def encode_duck3_png(img_path: Path, crop_box: tuple, name: str):
    """Encode a strip of 48px-wide frame slots from a single PNG."""
    arr = np.array(Image.open(img_path))
    h, w = arr.shape[:2]
    num_frames = w // DUCK3_SLOT_W
    x0, y0, x1, y1 = crop_box
    fw, fh = x1 - x0, y1 - y0

    palette = PALETTE_DUCK3
    lookup = _make_lookup(palette)

    frames = []
    for i in range(num_frames):
        slot_x = i * DUCK3_SLOT_W
        crop = arr[y0:y1, slot_x + x0 : slot_x + x1]
        frames.append(encode_frame(crop, palette, lookup))

    write_animation(name, fw, fh, frames, palette)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    OUT_DIR.mkdir(exist_ok=True)

    print("Duck 1 — mallard (ducky_3_spritesheet.png)")
    sheet = Path("duckies/ducky_3_spritesheet.png")
    encode_sheet_animation(sheet, [0], "duck_idle_normal", PALETTE_DUCK1)
    encode_sheet_animation(sheet, [1], "duck_walk_normal", PALETTE_DUCK1)
    encode_sheet_animation(sheet, [2], "duck_idle_bounce", PALETTE_DUCK1)
    encode_sheet_animation(sheet, [3], "duck_walk_bounce", PALETTE_DUCK1)

    print("\nDuck 2 — yellow rubber duck (ducky_2_spritesheet.png)")
    sheet2 = Path("duckies/ducky_2_spritesheet.png")
    encode_sheet_animation(sheet2, [0], "duck2_idle_normal", PALETTE_DUCK2)
    encode_sheet_animation(sheet2, [1], "duck2_walk_normal", PALETTE_DUCK2)
    encode_sheet_animation(sheet2, [2], "duck2_idle_bounce", PALETTE_DUCK2)
    encode_sheet_animation(sheet2, [3], "duck2_walk_bounce", PALETTE_DUCK2)

    print("\nDuck 3 — alternate yellow duck (ducky-idle.png / ducky-walk.png)")
    encode_duck3_png(
        Path("duckies/ducky-idle.png"), DUCK3_IDLE_CROP, "duck3_idle_normal"
    )
    encode_duck3_png(
        Path("duckies/ducky-walk.png"), DUCK3_WALK_CROP, "duck3_walk_normal"
    )

    print(f"\nAll assets written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
