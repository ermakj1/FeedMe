#!/usr/bin/env python3
"""
Fetch stock prices and send to LED display.

Symbols are configured in feeds/config.json.

Usage:
    python3 feeds/stock.py                # run on schedule
    python3 feeds/stock.py --once         # send once and exit
    python3 feeds/stock.py --interval 10  # every 10 min
"""

import sys
import time
import json
import argparse
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config, post_json

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

CONFIG_PATH = Path(__file__).parent / "config.json"


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_symbols():
    return load_config().get("stock", {}).get("symbols", ["MSFT"])

def get_interval():
    return load_config().get("stock", {}).get("interval_minutes", 5)

def get_market_hours_only():
    return load_config().get("stock", {}).get("market_hours_only", True)

def is_enabled():
    return load_config().get("stock", {}).get("enabled", True)

def fetch_quote(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    meta       = data["chart"]["result"][0]["meta"]
    price      = meta["regularMarketPrice"]
    prev_close = meta["chartPreviousClose"]
    change_pct = round((price - prev_close) / prev_close * 100, 2)
    return price, change_pct

def post_to_board(board_url, symbol, price, change, ttl_minutes):
    payload = {
        "category":    "stock",
        "symbol":      symbol,
        "price":       round(price, 2),
        "change":      change,
        "ttl_minutes": ttl_minutes,
    }
    return post_json(board_url, payload)

def send_all(ttl_minutes):
    board_url        = get_board_url()
    symbols          = get_symbols()
    market_hours_only = get_market_hours_only()

    if not symbols:
        print("No symbols configured")
        return

    eastern = datetime.now(ZoneInfo("America/New_York"))
    # NYSE open Mon-Fri 9:30am-4:00pm Eastern
    if market_hours_only:
        if eastern.weekday() >= 5:
            log("Outside market hours (weekend) — skipping")
            return
        market_open  = eastern.replace(hour=9,  minute=30, second=0, microsecond=0)
        market_close = eastern.replace(hour=16, minute=0,  second=0, microsecond=0)
        if not (market_open <= eastern < market_close):
            log(f"Outside market hours ({eastern.strftime('%H:%M')} ET) — skipping")
            return

    for symbol in symbols:
        try:
            price, change = fetch_quote(symbol)
            result = post_to_board(board_url, symbol, price, change, ttl_minutes)
            log(f"{symbol}  ${price:.2f}  {change:+.2f}%  -> {result}")
        except Exception as e:
            friendly = is_network_error(e)
            if friendly:
                log(f"{symbol}: {friendly}")
            else:
                log(f"{symbol}: Error — {e}")
                log(traceback.format_exc().strip())

def main():
    single_instance("stock")
    parser = argparse.ArgumentParser(description="Send stock prices to LED display")
    parser.add_argument("--interval", type=float, default=None, help="Update interval in minutes")
    parser.add_argument("--once", action="store_true", help="Send once and exit")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            send_all(ttl_minutes=interval)
        else:
            log("Stock disabled — sleeping")
        if args.once:
            break
        time.sleep(interval * 60)

if __name__ == "__main__":
    main()
