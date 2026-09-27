#!/usr/bin/env python3
"""
Fetch a random joke and send to LED display.

Two-part jokes (setup + punchline) are sent as two sequential messages.
Settings are configured in feeds/config.json.

Usage:
    python3 feeds/jokes.py                 # run on schedule
    python3 feeds/jokes.py --interval 60   # every hour
    python3 feeds/jokes.py --once          # send one joke and exit
"""

import sys
import time
import json
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


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_interval():
    return load_config().get("jokes", {}).get("interval_minutes", 30)

def is_enabled():
    return load_config().get("jokes", {}).get("enabled", True)

def fetch_joke():
    url = (
        "https://v2.jokeapi.dev/joke/Any?safe-mode"
        "&blacklistFlags=nsfw,religious,political,racist,sexist&maxLength=120"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())

def post_joke(board_url, setup=None, delivery=None, text=None, ttl_minutes=10):
    payload = {"category": "joke", "ttl_minutes": ttl_minutes, "max_plays": 1}
    if text:
        payload["text"] = text
    if setup:
        payload["setup"] = setup
    if delivery:
        payload["delivery"] = delivery
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        board_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())

def send_one():
    board_url = get_board_url()
    joke      = fetch_joke()
    if joke["type"] == "twopart":
        post_joke(board_url, setup=joke["setup"], delivery=joke["delivery"])
        log(f"Joke: {joke['setup']} / {joke['delivery']}")
    else:
        post_joke(board_url, text=joke["joke"])
        log(f"Joke: {joke['joke']}")

def main():
    single_instance("jokes")
    parser = argparse.ArgumentParser(description="Send jokes to LED display")
    parser.add_argument("--interval", type=float, default=None, help="Minutes between jokes")
    parser.add_argument("--once", action="store_true", help="Send one joke and exit")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            try:
                send_one()
            except Exception as e:
                friendly = is_network_error(e)
                if friendly:
                    log(f"Error: {friendly}")
                else:
                    log(f"Error: {e}")
                    log(traceback.format_exc().strip())
        else:
            log("Jokes disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)

if __name__ == "__main__":
    main()
