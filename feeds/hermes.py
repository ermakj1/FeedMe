#!/usr/bin/env python3
"""
Simple bridge to send messages from Hermes Cron jobs to the LED display.

Usage:
    python3 feeds/hermes.py "Your message here"
    python3 feeds/hermes.py --category animation --type fireworks
"""

import sys
import argparse
from pathlib import Path

# Add feeds dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from util import is_network_error, load_config, post_json


def get_board_url():
    return load_config().get("board_url", "http://matrixportal.local:8080") + "/add"

def send_to_board(payload):
    try:
        # No queue-full retry: callers (Director API, Hermes cron) expect a quick answer
        return post_json(get_board_url(), payload, retries=0)
    except Exception as e:
        friendly = is_network_error(e)
        if friendly:
            print(f"Error: {friendly}")
        else:
            print(f"Error: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Send messages to the FeedMe LED display")
    parser.add_argument("text", nargs="?", help="Text message to display")
    parser.add_argument("--category", default="text", help="Category (text, news, animation, etc.)")
    parser.add_argument("--ttl", type=int, default=60, help="TTL in minutes")
    
    # Allow passing extra fields for specific categories (e.g. --type for animations)
    parser.add_argument("--type", help="Animation type")
    parser.add_argument("--duration", type=int, help="Animation duration")
    
    args = parser.parse_args()

    if not args.text and args.category != "animation":
        parser.print_help()
        sys.exit(1)

    payload = {
        "category": args.category,
        "ttl_minutes": args.ttl
    }

    if args.text:
        payload["text"] = args.text
    
    if args.type:
        payload["type"] = args.type
    
    if args.duration:
        payload["duration"] = args.duration

    result = send_to_board(payload)
    print(f"Success: {result}")

if __name__ == "__main__":
    main()
