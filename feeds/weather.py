#!/usr/bin/env python3
"""
Fetch weather for configured cities and send to LED display.

Uses Open-Meteo (free, no API key).
Cities are configured in feeds/config.json.

Usage:
    python3 feeds/weather.py                # run on schedule
    python3 feeds/weather.py --once         # send once and exit
    python3 feeds/weather.py --interval 60  # every hour
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

DEFAULT_CITIES = [
    {"name": "Kirkland, WA", "lat": 47.6815, "lon": -122.2087, "timezone": "America/Los_Angeles"}
]


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_cities():
    return load_config().get("weather", {}).get("cities", DEFAULT_CITIES)

def get_interval():
    return load_config().get("weather", {}).get("interval_minutes", 30)

def is_enabled():
    return load_config().get("weather", {}).get("enabled", True)

def wmo_to_condition(code):
    if code in (0, 1, 2):              return "sunny"
    if code == 3:                      return "cloudy"
    if code in (45, 48):               return "cloudy"
    if code in (51, 53, 55, 56, 57):  return "rain"
    if code in (61, 63, 65, 66, 67):  return "rain"
    if code in (71, 73, 75, 77):      return "snow"
    if code in (80, 81, 82):           return "rain"
    if code in (85, 86):               return "snow"
    if code in (95, 96, 99):           return "storm"
    return "cloudy"

def fetch_weather(city):
    lat, lon, tz = city["lat"], city["lon"], city.get("timezone", "UTC")
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&temperature_unit=fahrenheit"
        f"&timezone={tz.replace('/', '%2F')}"
        "&forecast_days=1"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())

    condition = wmo_to_condition(data["current"]["weather_code"])
    high      = round(data["daily"]["temperature_2m_max"][0])
    low       = round(data["daily"]["temperature_2m_min"][0])
    precip    = data["daily"]["precipitation_probability_max"][0]
    return condition, high, low, precip

def post_to_board(board_url, condition, high, low, precip, ttl_minutes, city=None):
    payload = {
        "category":    "weather",
        "condition":   condition,
        "high":        high,
        "low":         low,
        "precip":      precip,
        "ttl_minutes": ttl_minutes,
    }
    if city:
        payload["city"] = city
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        board_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())

def send_all(ttl_minutes):
    board_url   = get_board_url()
    cities      = get_cities()
    multi_city  = len(cities) > 1
    if not cities:
        log("No cities configured")
        return
    for city in cities:
        try:
            condition, high, low, precip = fetch_weather(city)
            short_name = city["name"].split(",")[0].strip() if multi_city else None
            result = post_to_board(board_url, condition, high, low, precip, ttl_minutes, city=short_name)
            log(f"{city['name']}: {condition}  H:{high}  L:{low}  precip:{precip}%  -> {result}")
        except Exception as e:
            friendly = is_network_error(e)
            if friendly:
                log(f"{city['name']}: {friendly}")
            else:
                log(f"{city['name']}: Error — {e}")
                log(traceback.format_exc().strip())

def main():
    single_instance("weather")
    parser = argparse.ArgumentParser(description="Send weather to LED display")
    parser.add_argument("--interval", type=float, default=None, help="Update interval in minutes")
    parser.add_argument("--once", action="store_true", help="Send once and exit")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            send_all(interval + 5)
        else:
            log("Weather disabled — sleeping")
        if args.once:
            break
        time.sleep(interval * 60)

if __name__ == "__main__":
    main()
