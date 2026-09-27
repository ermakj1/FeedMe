#!/usr/bin/env python3
"""
Word of the Day — fetches word + definition and sends to LED display.

Uses Merriam-Webster Word of the Day RSS for the word, then Free Dictionary
API for definition. No API key required.

Usage:
    python3 feeds/wordofday.py         # run on schedule
    python3 feeds/wordofday.py --once  # send once and exit
"""

import sys
import time
import json
import argparse
import traceback
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
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
    return load_config().get("wordofday", {}).get("interval_minutes", 1440)

def is_enabled():
    return load_config().get("wordofday", {}).get("enabled", True)

def get_ttl():
    return load_config().get("wordofday", {}).get("ttl_minutes", 1440)


def fetch_word_of_day():
    """Fetch word of the day from Merriam-Webster RSS. Returns (word, part_of_speech)."""
    url = "https://www.merriam-webster.com/wotd/feed/rss2"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        xml_data = resp.read()
    root = ET.fromstring(xml_data)
    # First item in the RSS feed is today's word
    ns = {"mw": "http://www.merriam-webster.com/rss/"}
    item = root.find(".//item")
    if item is None:
        raise ValueError("No items in MW RSS feed")
    title = item.findtext("title", "").strip()
    # Title format is usually "Word of the Day: <word>"
    word = title.replace("Word of the Day:", "").strip()
    if ":" in word:
        word = word.split(":")[-1].strip()
    return word


def fetch_definition(word):
    """Fetch short definition from Free Dictionary API. Returns (part_of_speech, definition)."""
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        meanings = data[0].get("meanings", [])
        if not meanings:
            return "", ""
        meaning = meanings[0]
        pos    = meaning.get("partOfSpeech", "")
        defs   = meaning.get("definitions", [])
        defn   = defs[0].get("definition", "") if defs else ""
        # Truncate long definitions
        if len(defn) > 120:
            defn = defn[:117] + "..."
        return pos, defn
    except Exception:
        return "", ""


def post_to_board(board_url, word, pos, definition, ttl_minutes):
    payload = {
        "category":    "word",
        "word":        word,
        "pos":         pos,
        "definition":  definition,
        "ttl_minutes": ttl_minutes,
    }
    return post_json(board_url, payload)


def send_word():
    board_url = get_board_url()
    ttl       = get_ttl()
    word      = fetch_word_of_day()
    pos, defn = fetch_definition(word)
    log(f"Word: {word} ({pos}) — {defn[:60]}...")
    result = post_to_board(board_url, word, pos, defn, ttl)
    log(f"Sent: {result}")


def main():
    single_instance("wordofday")
    parser = argparse.ArgumentParser(description="Send word of the day to LED display")
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            try:
                send_word()
            except Exception as e:
                friendly = is_network_error(e)
                if friendly:
                    log(f"Error: {friendly}")
                else:
                    log(f"Error: {e}")
                    log(traceback.format_exc().strip())
        else:
            log("Word of day disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
