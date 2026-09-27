#!/usr/bin/env python3
"""
Stand-alone script to fetch BBC headlines and send to FeedMe board.
Saves tokens by moving logic from AI prompt to local Python.
"""
import urllib.request
import xml.etree.ElementTree as ET
import time
from datetime import datetime
from pathlib import Path
from util import single_instance, load_config, post_json

REPO_DIR = Path(__file__).parent.parent.resolve()
CONFIG_PATH = REPO_DIR / "feeds" / "config.json"


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def is_active_hours(cfg):
    if not cfg.get("active_hours_only", True):
        return True
    hour = datetime.now().hour
    return 8 <= hour < 20

def fetch_headlines():
    cfg = load_config().get("bbc", {})
    rss_url = cfg.get("rss_url", "https://feeds.bbci.co.uk/news/rss.xml")
    try:
        with urllib.request.urlopen(rss_url, timeout=10) as response:
            content = response.read()
        root = ET.fromstring(content)
        # The titles are usually inside <item><title>
        items = root.findall('.//item')
        headlines = []
        for item in items[:cfg.get("count", 3)]:
            title_node = item.find('title')
            if title_node is not None and title_node.text:
                title = title_node.text
                if title.startswith('<![CDATA['):
                    title = title[9:-3]
                headlines.append(title.strip())
        return headlines
    except Exception as e:
        print(f"Error fetching headlines: {e}")
        return []

def send_to_board(headline, cfg):
    payload = {
        "text": headline,
        "category": "news",
        "ttl_minutes": cfg.get("ttl_minutes", 125)
    }
    try:
        post_json(get_board_url(), payload)
        print(f"Sent: {headline}")
    except Exception as e:
        print(f"Failed to send headline: {e}")

def main():
    print("BBC Headline script started", flush=True)
    single_instance("bbc")
    
    while True:
        try:
            print("Loop tick", flush=True)
            bbc_cfg = load_config().get("bbc", {})
            if not bbc_cfg.get("enabled", True):
                print("BBC feed disabled in config. Sleeping...", flush=True)
            elif not is_active_hours(bbc_cfg):
                print("Outside active hours. Sleeping...", flush=True)
            else:
                print("Fetching headlines...", flush=True)
                headlines = fetch_headlines()
                if headlines:
                    print(f"Found {len(headlines)} headlines. Sending...", flush=True)
                    for h in headlines:
                        send_to_board(h, bbc_cfg)
                        time.sleep(2)
                else:
                    print("No headlines found.", flush=True)
            
            interval = bbc_cfg.get("interval_minutes", 120)
            print(f"Sleeping for {interval} minutes...", flush=True)
            time.sleep(interval * 60)
        except Exception as e:
            print(f"Error in main loop: {e}", flush=True)
            time.sleep(60)

if __name__ == "__main__":
    main()
