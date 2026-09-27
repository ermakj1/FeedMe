"""
Image pipeline — reusable module for any feed that wants to display an image.

Flow:
  1. fetch_image(url)          → PIL Image
  2. resize_for_board(image)   → 64x32 PIL Image
  3. save_bmp(image, name)     → local cache path
  4. upload_to_board(board_url, local_path, board_path)
  5. queue_bitmap(board_url, board_path, caption, ttl_minutes)

Or use the all-in-one:
  process_image(url, name, board_url, caption, ttl_minutes)
"""

import io
import json
import os
import urllib.request
from datetime import date
from pathlib import Path

from util import post_json

BOARD_WIDTH  = 64
BOARD_HEIGHT = 32
CACHE_DIR    = Path(os.environ.get("FEEDME_DATA_DIR") or Path(__file__).parent) / ".image_cache"


def fetch_image(url, headers=None):
    """Download image from URL, return PIL Image."""
    from PIL import Image
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    return Image.open(io.BytesIO(data)).convert("RGB")


def resize_for_board(image, width=BOARD_WIDTH, height=BOARD_HEIGHT):
    """Resize/crop image to fit the board, preserving aspect ratio with center crop."""
    from PIL import Image
    img_ratio   = image.width / image.height
    board_ratio = width / height

    if img_ratio > board_ratio:
        # wider than board — scale to height, crop width
        new_h = height
        new_w = int(image.width * height / image.height)
    else:
        # taller than board — scale to width, crop height
        new_w = width
        new_h = int(image.height * width / image.width)

    image = image.resize((new_w, new_h), Image.LANCZOS)
    left  = (new_w - width)  // 2
    top   = (new_h - height) // 2
    return image.crop((left, top, left + width, top + height))


def save_bmp(image, name):
    """Save PIL Image as 8-bit indexed BMP to local cache. Returns path.

    CircuitPython's adafruit_imageload only handles palettized (indexed) BMPs.
    Saving as 24-bit true-color causes a hard C-level crash on the board.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{name}.bmp"
    # Quantize to 256 colours → 8-bit indexed BMP that adafruit_imageload handles
    image.quantize(colors=256).save(str(path), "BMP")
    return path


def upload_to_board(board_base_url, local_path, board_path):
    """Upload a local file to the board filesystem via /upload endpoint."""
    upload_url = board_base_url.rstrip("/") + "/upload"
    with open(local_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        upload_url, data=data,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Path":       board_path,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def queue_bitmap(board_url, board_path, caption="", ttl_minutes=60):
    """POST a bitmap message to the board queue."""
    payload = {
        "category":    "bitmap",
        "path":        board_path,
        "caption":     caption,
        "ttl_minutes": ttl_minutes,
    }
    return post_json(board_url.rstrip("/") + "/add", payload)


def is_cached_today(name):
    """Return True if we already processed this image today."""
    marker = CACHE_DIR / f"{name}.date"
    if marker.exists():
        return marker.read_text().strip() == str(date.today())
    return False


def mark_cached_today(name):
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{name}.date").write_text(str(date.today()))


def process_image(url, name, board_base_url, board_path, caption="",
                  ttl_minutes=60, force=False, headers=None):
    """
    Full pipeline: fetch → resize → save BMP → upload → queue.
    Skips upload if already done today (unless force=True).
    Returns the queue result dict.
    """
    if not force and is_cached_today(name):
        # Already uploaded today — just re-queue
        return queue_bitmap(board_base_url, board_path, caption, ttl_minutes)

    image    = fetch_image(url, headers=headers)
    resized  = resize_for_board(image)
    local    = save_bmp(resized, name)
    upload_to_board(board_base_url, local, board_path)
    mark_cached_today(name)
    return queue_bitmap(board_base_url, board_path, caption, ttl_minutes)
