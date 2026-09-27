#!/usr/bin/env python3
"""
NASA Astronomy Picture of the Day — fetch, convert to 64x32 BMP, display on board.

Uses image_pipeline for image processing. Only re-uploads when the image
changes (once per day). Config in feeds/config.json.

Usage:
    python3 feeds/nasa.py          # run on schedule
    python3 feeds/nasa.py --once   # send once and exit
"""

import sys
import time
import json
import argparse
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config
from image_pipeline import process_image

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

CONFIG_PATH  = Path(__file__).parent / "config.json"
BOARD_PATH   = "/bitmaps/nasa.bmp"   # path on board filesystem
IMAGE_NAME   = "nasa_apod"


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080")

def get_interval():
    return load_config().get("nasa", {}).get("interval_minutes", 1440)

def get_api_key():
    return load_config().get("nasa", {}).get("api_key", "DEMO_KEY")

def is_enabled():
    return load_config().get("nasa", {}).get("enabled", True)

def get_ttl():
    return load_config().get("nasa", {}).get("ttl_minutes", 1440)


def fetch_apod(api_key):
    """Fetch today's APOD metadata. Returns (image_url, title, media_type)."""
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("url", ""), data.get("title", ""), data.get("media_type", "image")


def send_nasa():
    board_url = get_board_url()
    api_key   = get_api_key()
    ttl       = get_ttl()

    image_url, title, media_type = fetch_apod(api_key)

    if media_type != "image":
        log(f"APOD today is {media_type}, not an image — skipping")
        return

    if not image_url:
        log("No image URL in APOD response")
        return

    log(f"APOD: {title}")
    # Truncate caption to fit on display
    caption = title[:40] if title else ""
    result = process_image(
        url=image_url,
        name=IMAGE_NAME,
        board_base_url=board_url,
        board_path=BOARD_PATH,
        caption=caption,
        ttl_minutes=ttl,
    )
    log(f"Sent APOD: {result}")


def main():
    single_instance("nasa")
    parser = argparse.ArgumentParser(description="Send NASA APOD to LED display")
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            try:
                send_nasa()
            except Exception as e:
                friendly = is_network_error(e)
                if friendly:
                    log(f"Error: {friendly}")
                else:
                    log(f"Error: {e}")
                    log(traceback.format_exc().strip())
        else:
            log("NASA disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
