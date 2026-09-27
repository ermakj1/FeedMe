#!/usr/bin/env python3
"""
Countdown feed — shows days remaining until configured events.

Events are configured in feeds/config.json under "countdown.events":
  [{"name": "Vacation", "date": "2026-07-04"}, ...]

Past events are automatically skipped. Events within 1 day show hours.

Usage:
    python3 feeds/countdown.py         # run on schedule
    python3 feeds/countdown.py --once  # send once and exit
"""

import sys
import time
import json
import argparse
import traceback
from datetime import datetime, date
from pathlib import Path
import urllib.request
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

CONFIG_PATH = Path(__file__).parent / "config.json"


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_interval():
    return load_config().get("countdown", {}).get("interval_minutes", 60)

def get_events():
    return load_config().get("countdown", {}).get("events", [])

def is_enabled():
    return load_config().get("countdown", {}).get("enabled", True)

def get_ttl():
    return load_config().get("countdown", {}).get("ttl_minutes", 65)


def post_to_board(board_url, name, target_date, days, hours, ttl_minutes):
    payload = {
        "category":    "countdown",
        "name":        name,
        "target_date": target_date,
        "days":        days,
        "hours":       hours,
        "ttl_minutes": ttl_minutes,
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        board_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def send_all():
    board_url = get_board_url()
    events    = get_events()
    ttl       = get_ttl()
    today     = date.today()

    if not events:
        log("No countdown events configured")
        return

    for event in events:
        name     = event.get("name", "Event")
        date_str = event.get("date", "")
        if not date_str:
            continue
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            log(f"Invalid date for '{name}': {date_str}")
            continue

        delta = target - today
        if delta.days < 0:
            log(f"'{name}' already passed ({date_str}) — skipping")
            continue
        if delta.days == 0:
            log(f"'{name}' is TODAY!")

        # For events <= 1 day away also show hours
        now    = datetime.now()
        target_dt = datetime.fromisoformat(date_str)
        total_seconds = max(0, (target_dt - now).total_seconds())
        hours = int(total_seconds // 3600) if delta.days <= 1 else 0

        for attempt in range(3):
            try:
                result = post_to_board(board_url, name, date_str, delta.days, hours, ttl)
                log(f"Countdown '{name}': {delta.days}d {hours}h -> {result}")
                break
            except Exception as e:
                if getattr(e, "code", None) == 429:
                    if attempt < 2:
                        log(f"Queue full — waiting 60s before retry {attempt + 1}/2 for '{name}'...")
                        time.sleep(60)
                    else:
                        log(f"Queue still full after retries — skipping '{name}'")
                elif is_network_error(e):
                    log(f"Error: {is_network_error(e)}")
                    break
                else:
                    log(f"Error sending '{name}': {e}")
                    log(traceback.format_exc().strip())
                    break


def main():
    single_instance("countdown")
    parser = argparse.ArgumentParser(description="Send countdowns to LED display")
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
            log("Countdown disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
