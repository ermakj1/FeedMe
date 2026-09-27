#!/usr/bin/env python3
"""
Countdown feed — shows days remaining until configured events.

Events are configured in feeds/config.json under "countdown.events":
  [{"name": "Vacation", "date": "2026-07-04"},
   {"name": "Mila's Birthday!", "date": "2026-06-13", "yearly": true}, ...]

Past events are skipped, except "yearly" ones, which roll forward to their
next anniversary. Events more than "max_days_ahead" (default 100) away are
not shown yet. Events within 1 day show hours.

Usage:
    python3 feeds/countdown.py         # run on schedule
    python3 feeds/countdown.py --once  # send once and exit
"""

import sys
import time
import argparse
import traceback
from datetime import datetime, date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config, post_json

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

def get_max_days():
    return load_config().get("countdown", {}).get("max_days_ahead", 100)

def get_ttl():
    return load_config().get("countdown", {}).get("ttl_minutes", 65)


def next_occurrence(target, today, yearly):
    """Return the date to count down to, or None if a one-off event has passed."""
    if target >= today:
        return target
    if not yearly:
        return None
    for year in (today.year, today.year + 1):
        try:
            candidate = target.replace(year=year)
        except ValueError:               # Feb 29 in a non-leap year
            candidate = date(year, 2, 28)
        if candidate >= today:
            return candidate
    return None


def post_to_board(board_url, name, target_date, days, hours, ttl_minutes):
    payload = {
        "category":    "countdown",
        "name":        name,
        "target_date": target_date,
        "days":        days,
        "hours":       hours,
        "ttl_minutes": ttl_minutes,
    }
    return post_json(board_url, payload)


def send_all():
    board_url = get_board_url()
    events    = get_events()
    ttl       = get_ttl()
    max_days  = get_max_days()
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

        target = next_occurrence(target, today, event.get("yearly", False))
        if target is None:
            log(f"'{name}' already passed ({date_str}) — skipping")
            continue
        date_str = target.isoformat()
        delta = target - today
        if max_days and delta.days > max_days:
            log(f"'{name}' is {delta.days} days away — waiting until {max_days} days out")
            continue
        if delta.days == 0:
            log(f"'{name}' is TODAY!")

        # For events <= 1 day away also show hours
        now    = datetime.now()
        target_dt = datetime.fromisoformat(date_str)
        total_seconds = max(0, (target_dt - now).total_seconds())
        hours = int(total_seconds // 3600) if delta.days <= 1 else 0

        # post_json waits and retries if the board queue is full
        try:
            result = post_to_board(board_url, name, date_str, delta.days, hours, ttl)
            log(f"Countdown '{name}': {delta.days}d {hours}h -> {result}")
        except Exception as e:
            friendly = is_network_error(e)
            if friendly:
                log(f"Error: {friendly}")
            else:
                log(f"Error sending '{name}': {e}")
                log(traceback.format_exc().strip())


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
