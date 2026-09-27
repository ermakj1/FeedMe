#!/usr/bin/env python3
"""
Jeopardy feed — sends real Jeopardy clues to the LED display.

Questions come from the jwolle1 dataset on GitHub (530k+ clues, all seasons).
On first run it downloads the TSV and builds a local sample cache (~5000 clues).
Subsequent runs pick from the cache instantly.

Config in feeds/config.json under "jeopardy":
  {
    "enabled": true,
    "interval_minutes": 30,
    "count": 3,
    "min_value": 200,
    "max_value": 1000,
    "ttl_minutes": 35
  }

Usage:
    python3 feeds/jeopardy.py         # run on schedule
    python3 feeds/jeopardy.py --once  # send once and exit
    python3 feeds/jeopardy.py --once --refresh  # force rebuild cache
"""

import sys
import time
import json
import csv
import html
import io
import random
import argparse
import traceback
import urllib.request
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, DATA_DIR, load_config

def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)

CONFIG_PATH  = Path(__file__).parent / "config.json"
CACHE_PATH   = DATA_DIR / ".jeopardy_cache.json"
DATASET_REPO = "jwolle1/jeopardy_clue_dataset"
# Fallback if the GitHub API lookup fails. The file is renamed each season
# (combined_season1-41.tsv -> 1-42 ...), so we normally discover the name.
DATASET_URL  = (
    f"https://raw.githubusercontent.com/{DATASET_REPO}/main/combined_season1-42.tsv"
)
CACHE_SIZE   = 5000   # questions kept in local cache
MIN_CLUE_LEN = 15     # skip very short/empty clues
MAX_CLUE_LEN = 200    # skip clues too long to be useful



def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def get_interval():
    return load_config().get("jeopardy", {}).get("interval_minutes", 30)

def get_count():
    return load_config().get("jeopardy", {}).get("count", 3)

def get_min_value():
    return load_config().get("jeopardy", {}).get("min_value", 200)

def get_max_value():
    return load_config().get("jeopardy", {}).get("max_value", 1000)

def get_ttl():
    return load_config().get("jeopardy", {}).get("ttl_minutes", 35)

def is_enabled():
    return load_config().get("jeopardy", {}).get("enabled", True)

def is_celebrity_only():
    return load_config().get("jeopardy", {}).get("celebrity_only", False)


# ── Cache management ──────────────────────────────────────────────────────────

CELEB_CACHE_PATH = DATA_DIR / ".jeopardy_celebrity_cache.json"

def _clean(text):
    """Unescape HTML and normalize quotes/dashes for the LED display."""
    if not text:
        return ""
    # 1. HTML unescape (&quot; -> ")
    text = html.unescape(text)
    # 2. Remove backslashes escaping quotes in the source dataset
    text = text.replace('\\"', '"').replace("\\'", "'")
    # 3. Normalize smart quotes and dashes
    replacements = {
        '\u201c': '"', '\u201d': '"',
        '\u2018': "'", '\u2019': "'",
        '\u2014': "-", '\u2013': "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.strip()

def _clue_ok(row, min_val=None, max_val=None, celebrity_only=False):
    """Return True if this TSV row is a usable clue."""
    # Celebrity filter: notes field must mention "Celebrity Jeopardy"
    if celebrity_only:
        notes = (row.get("notes") or "").lower()
        if "celebrity" not in notes:
            return False

    if min_val is not None or max_val is not None:
        try:
            val = int(row.get("clue_value", 0) or 0)
        except (ValueError, TypeError):
            return False
        if min_val is not None and val < min_val:
            return False
        if max_val is not None and val > max_val:
            return False

    clue   = _clean(row.get("answer") or "")    # "answer" column = clue text
    answer = _clean(row.get("question") or "")  # "question" column = response
    if len(clue) < MIN_CLUE_LEN or len(clue) > MAX_CLUE_LEN:
        return False
    if not answer or len(answer) > 60:
        return False
    # Skip clues with leftover HTML tags or other weirdness
    if "<" in clue or "(" in answer:
        return False
    return True


def _row_to_clue(row):
    return {
        "clue":     _clean(row["answer"]),
        "answer":   _clean(row["question"]),
        "category": _clean(row["category"]).upper(),
        "value":    int(row["clue_value"] or 0),
    }


def _dataset_url():
    """Find the newest combined_season1-N.tsv in the dataset repo."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{DATASET_REPO}/contents",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            files = json.loads(resp.read())
        combined = [
            f for f in files
            if f.get("name", "").startswith("combined_season1-") and f["name"].endswith(".tsv")
        ]
        if combined:
            newest = max(combined, key=lambda f: int(f["name"][len("combined_season1-"):-4]))
            return newest["download_url"]
    except Exception as e:
        log(f"Dataset lookup failed ({e}) — using fallback URL")
    return DATASET_URL


def _download_raw():
    url = _dataset_url()
    log(f"Downloading Jeopardy dataset {url.rsplit('/', 1)[-1]} (~60 MB, one-time)...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    log(f"Download complete ({len(raw) // 1024 // 1024} MB).")
    return raw


def build_cache(min_val=200, max_val=1000):
    """Download the full TSV and build BOTH caches (regular + celebrity) at once."""
    raw    = _download_raw()
    reader = list(csv.DictReader(io.StringIO(raw), delimiter="\t"))
    log(f"Filtering clues from {len(reader)} rows...")

    # ── Regular cache ──────────────────────────────────────────────────────────
    eligible = [r for r in reader if _clue_ok(r, min_val, max_val, celebrity_only=False)]
    sample   = random.sample(eligible, min(CACHE_SIZE, len(eligible)))
    cache    = [_row_to_clue(r) for r in sample]
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)
    log(f"Regular cache: {len(cache)} clues (from {len(eligible)} eligible)")

    # ── Celebrity cache — keep ALL celebrity clues (only ~4800 total) ──────────
    celeb = [r for r in reader if _clue_ok(r, celebrity_only=True)]
    celeb_cache = [_row_to_clue(r) for r in celeb]
    with open(CELEB_CACHE_PATH, "w") as f:
        json.dump(celeb_cache, f)
    log(f"Celebrity cache: {len(celeb_cache)} clues saved")

    return cache


def load_cache(celebrity_only=False):
    """Load the appropriate cached clues from disk, or None if missing/corrupt."""
    path = CELEB_CACHE_PATH if celebrity_only else CACHE_PATH
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) > 0:
            return data
    except Exception:
        pass
    return None


def get_clues(count, force_refresh=False, celebrity_only=False):
    """Return `count` random clues, building/refreshing cache as needed."""
    cache = None if force_refresh else load_cache(celebrity_only=celebrity_only)
    if cache is None:
        # Build both caches in one download pass
        build_cache(get_min_value(), get_max_value())
        cache = load_cache(celebrity_only=celebrity_only) or []
    return random.sample(cache, min(count, len(cache)))


# ── Board posting ─────────────────────────────────────────────────────────────

def post_clue(board_url, clue, answer, category, value, ttl_minutes):
    payload = {
        "category":          "jeopardy",
        "clue":              clue,
        "answer":            answer,
        "jeopardy_category": category,
        "value":             value,
        "ttl_minutes":       ttl_minutes,
    }
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        board_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _is_queue_full(e):
    """Return True if the board rejected with 429 (queue full)."""
    code = getattr(e, "code", None)
    return code == 429


def send_clues(force_refresh=False):
    board_url   = get_board_url()
    count       = get_count()
    ttl         = get_ttl()
    celeb_only  = is_celebrity_only()

    clues = get_clues(count, force_refresh=force_refresh, celebrity_only=celeb_only)
    for q in clues:
        # Retry up to 3 times with 2-minute waits if the queue is full
        for attempt in range(3):
            try:
                result = post_clue(
                    board_url, q["clue"], q["answer"],
                    q["category"], q["value"], ttl,
                )
                log(f"Jeopardy [{q['category']} ${q['value']}]: {q['clue'][:40]}... → {result}")
                break
            except Exception as e:
                if _is_queue_full(e):
                    if attempt < 2:
                        log(f"Queue full — waiting 2 min before retry {attempt + 1}/2...")
                        time.sleep(120)
                    else:
                        log("Queue still full after retries — skipping clue")
                elif is_network_error(e):
                    log(f"Error: {is_network_error(e)}")
                    break
                else:
                    log(f"Error posting clue: {e}")
                    break


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    single_instance("jeopardy")
    parser = argparse.ArgumentParser(description="Send Jeopardy clues to LED display")
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--once",    action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Force rebuild clue cache")
    args = parser.parse_args()

    while True:
        interval = args.interval or get_interval()
        if is_enabled():
            try:
                send_clues(force_refresh=args.refresh)
                args.refresh = False  # only force on first loop
            except Exception as e:
                friendly = is_network_error(e)
                if friendly:
                    log(f"Error: {friendly}")
                else:
                    log(f"Error: {e}")
                    log(traceback.format_exc().strip())
        else:
            log("Jeopardy disabled — sleeping")

        if args.once:
            break
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
