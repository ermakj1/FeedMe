#!/usr/bin/env python3
"""
This Day in History — fetches historical events for today's date and
sends them to the LED display, rotating through multiple events.

Uses Wikipedia's free On This Day API. No API key required.

Usage:
    python3 feeds/history.py         # run on schedule
    python3 feeds/history.py --once  # send once and exit
"""

import sys
import time
import json
import random
import argparse
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config, post_json

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

CONFIG_PATH = Path(__file__).parent / "config.json"


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_interval():
    return load_config().get("history", {}).get("interval_minutes", 60)

def get_count():
    return load_config().get("history", {}).get("count", 3)

def is_enabled():
    return load_config().get("history", {}).get("enabled", True)

def get_ttl():
    return load_config().get("history", {}).get("ttl_minutes", 65)


def fetch_events():
    """Fetch historical events for today from Wikipedia. Returns list of (year, text)."""
    now   = datetime.now()
    month = now.month
    day   = now.day
    url   = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}"
    req   = urllib.request.Request(url, headers={"User-Agent": "LED-Board/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    events = data.get("events", [])
    return [(e["year"], e["text"]) for e in events if e.get("text")]


def post_to_board(board_url, year, text, ttl_minutes):
    # Truncate to keep display manageable
    short = text if len(text) <= 100 else text[:97] + "..."
    payload = {
        "category":    "history",
        "year":        year,
        "text":        short,
        "ttl_minutes": ttl_minutes,
    }
    return post_json(board_url, payload)


def send_all():
    board_url = get_board_url()
    count     = get_count()
    ttl       = get_ttl()

    events = fetch_events()
    if not events:
        log("No events found for today")
        return

    # Pick a random sample spread across different eras
    selected = random.sample(events, min(count, len(events)))

    for year, text in selected:
        try:
            result = post_to_board(board_url, year, text, ttl)
            log(f"{year}: {text[:50]}... -> {result}")
        except Exception as e:
            friendly = is_network_error(e)
            if friendly:
                log(f"Error: {friendly}")
            else:
                log(f"Error sending event: {e}")
                log(traceback.format_exc().strip())


def main():
    single_instance("history")
    parser = argparse.ArgumentParser(description="Send This Day in History to LED display")
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            try:
                send_all()
            except Exception as e:
                friendly = is_network_error(e)
                if friendly:
                    log(f"Error: {friendly}")
                else:
                    log(f"Error: {e}")
                    log(traceback.format_exc().strip())
        else:
            log("History disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
