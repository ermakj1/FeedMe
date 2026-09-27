#!/usr/bin/env python3
"""
Send decorative animations to the LED display on a schedule.

Animation types and interval are configured in feeds/config.json.

Usage:
    python3 feeds/animations.py                    # run on schedule
    python3 feeds/animations.py --interval 15      # every 15 min
    python3 feeds/animations.py --type fireworks   # always fireworks
    python3 feeds/animations.py --once             # send one and exit
"""

import sys
import time
import json
import random
import argparse
import traceback
import urllib.request
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

CONFIG_PATH = Path(__file__).parent / "config.json"

ALL_TYPES = ["fireworks", "rainbow", "plasma", "fire", "life", "cube", "dvd", "dvd_text", "matrix"]


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_interval():
    return load_config().get("animations", {}).get("interval_minutes", 20)

def get_types():
    return load_config().get("animations", {}).get("types", ALL_TYPES) or ALL_TYPES

def is_enabled():
    return load_config().get("animations", {}).get("enabled", True)

def send_animation(board_url, anim_type, duration=10):
    payload = {
        "category":    "animation",
        "type":        anim_type,
        "duration":    duration,
        "ttl_minutes": 1,
        "max_plays":   1,
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        board_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())

def main():
    single_instance("animations")
    parser = argparse.ArgumentParser(description="Send animations to LED display")
    parser.add_argument("--interval", type=float, default=None, help="Minutes between animations")
    parser.add_argument("--type", choices=ALL_TYPES, default=None, help="Animation type (default: random)")
    parser.add_argument("--duration", type=float, default=10, help="Seconds each animation runs (default: 10)")
    parser.add_argument("--once", action="store_true", help="Send one animation and exit")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            try:
                board_url = get_board_url()
                chosen    = args.type or random.choice(get_types())
                result    = send_animation(board_url, chosen, args.duration)
                log(f"Sent {chosen} ({args.duration}s): {result}")
            except Exception as e:
                friendly = is_network_error(e)
                if friendly:
                    log(f"Error: {friendly}")
                else:
                    log(f"Error: {e}")
                    log(traceback.format_exc().strip())
        else:
            log("Animations disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)

if __name__ == "__main__":
    main()
