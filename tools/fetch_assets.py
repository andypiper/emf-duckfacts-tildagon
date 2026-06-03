# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "requests",
#     "Pillow",
#     "qrcode",
#     "numpy",
# ]
# ///
"""
Fetch and preprocess online assets for the Duck Facts Tildagon app.

Downloads and builds:
  - Duck facts text file (bjorn-knudsen) → assets/facts.txt.gz
  - @emfducks Mastodon avatar             → assets/emfducks_avatar.jpg
  - @emfducks profile QR code             → assets/emfducks_qr.json.gz

Usage:
    uv run tools/fetch_assets.py
"""

import gzip
import json
import re
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image

OUT_DIR = Path("assets")

FACTS_URL = (
    "https://raw.githubusercontent.com/"
    "bjorn-knudsen/duck-facts-bot/main/duck_facts.txt"
)
MASTODON_LOOKUP = "https://mastodon.social/api/v1/accounts/lookup?acct=emfducks"
EMFDUCKS_URL = "https://mastodon.social/@emfducks"

AVATAR_SIZE = (100, 100)  # matches the 100×100 rendered size (r=50 circle)
AVATAR_QUALITY = 75


def fetch_facts():
    print("Fetching duck facts...", end=" ", flush=True)
    r = requests.get(FACTS_URL, timeout=10)
    r.raise_for_status()
    facts = []
    for line in r.text.splitlines():
        line = re.sub(r"^\d+\.\s*", "", line.strip())
        if line:
            facts.append(line)
    compressed = gzip.compress("\n".join(facts).encode())
    (OUT_DIR / "facts.txt.gz").write_bytes(compressed)
    print(f"{len(facts)} facts, {len(compressed)} bytes gzipped")


def fetch_avatar():
    print("Fetching @emfducks avatar...", end=" ", flush=True)
    account = requests.get(MASTODON_LOOKUP, timeout=10).json()
    avatar_url = account["avatar_static"]
    img_bytes = requests.get(avatar_url, timeout=10).content
    img = (
        Image.open(BytesIO(img_bytes)).convert("RGB").resize(AVATAR_SIZE, Image.LANCZOS)
    )
    out = OUT_DIR / "emfducks_avatar.jpg"
    img.save(out, "JPEG", quality=AVATAR_QUALITY)
    print(f"{out.stat().st_size} bytes → {out}")


def build_qr():
    print("Building @emfducks QR code...", end=" ", flush=True)
    try:
        import qrcode
    except ImportError:
        print("skipped (pip install qrcode)")
        return

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(EMFDUCKS_URL)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    arr = np.array(img)
    h, w = arr.shape[:2]

    # Encode dark modules only; white background drawn at runtime
    palette = {"bk": [0, 0, 0]}
    frame = []
    for y in range(h):
        x = 0
        while x < w:
            if arr[y, x, 0] < 128:  # dark module
                start = x
                x += 1
                while x < w and arr[y, x, 0] < 128:
                    x += 1
                frame.append(["bk", start, y, x - start])
            else:
                x += 1

    data = {"w": w, "h": h, "palette": palette, "frames": [frame]}
    compressed = gzip.compress(json.dumps(data, separators=(",", ":")).encode())
    out = OUT_DIR / "emfducks_qr.json.gz"
    out.write_bytes(compressed)
    print(f"{w}×{h}px, {len(frame)} segments, {len(compressed)} bytes → {out}")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    fetch_facts()
    fetch_avatar()
    build_qr()
    print(f"\nDone. Assets in {OUT_DIR}/")


if __name__ == "__main__":
    main()
