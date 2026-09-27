#!/usr/bin/env python3
"""
Michael Scott Quote feed — posts a random quote on each interval.
Cycles through all quotes before repeating any.
"""

import random
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from util import single_instance, is_network_error, load_config, post_json

CONFIG_PATH = Path(__file__).parent / "config.json"

QUOTES = [
    "That's what she said.",
    "I'm not superstitious, but I am a little stitious.",
    "Would I rather be feared or loved? Easy. Both. I want people to be afraid of how much they love me.",
    "I am running away from my responsibilities. And it feels good.",
    "I love inside jokes. I'd love to be a part of one someday.",
    "Do I need to be liked? Absolutely not. I like to be liked. I enjoy being liked. I have to be liked.",
    "Sometimes I'll start a sentence and I don't even know where it's going. I hope I find it along the way.",
    "I am Beyonce, always.",
    "The worst thing about prison was the dementors.",
    "I'm an early bird and I'm a night owl. So I'm wise and I have worms.",
    "You miss 100% of the shots you don't take. -Wayne Gretzky -Michael Scott",
    "Make friends first, make sales second, make love third. In no particular order.",
    "Why are you the way that you are?",
    "I have a lot of questions. Number one, how dare you.",
    "Webster's dictionary defines wedding as the fusing of two metals with a hot torch.",
    "I am the best boss. I would love to be managed by me.",
    "An office is a place to live life to the fullest, to the max, to... an office is a place where dreams are a reality.",
    "If I had a gun with two bullets and I was in a room with Hitler, Bin Laden, and Toby, I would shoot Toby twice.",
    "I worked in a paper company all my life. I don't want to die here. I have to do something.",
    "Presents are the best way to show someone how much you care. It is like this tangible thing that you can point to and say, hey man, I love you this many dollars worth.",
    "Guess what, I have flaws. What are they? I sing in the shower. Sometimes I spend too much time volunteering.",
    "There's no 'I' in team, but there's an 'I' in pie. And there's an 'I' in meat pie. Anagram of meat is team.",
    "I am a victim of a hate crime. Stanley is a racist.",
    "I'm not usually the butt of the joke. I'm usually the face of the joke.",
    "I want to be the Forrest Gump of our generation, but I'm not going to sit on a bench waiting for life.",
    "Fool me once, strike one. Fool me twice, strike three.",
    "I am running this place like a well-oiled machine. And I am the oil.",
    "I'm not a doctor but I play one in the office... on my feet... all day.",
    "I am about to do something very bold in this job that I've never done before: try.",
    "People say I am the best boss. I think there should be a competition.",
]




def post_quote(board_url, quote, ttl):
    payload = {
        "category": "quote",
        "text":     quote,
        "ttl_minutes": ttl,
    }
    return post_json(f"{board_url}/add", payload, timeout=10)


def main():
    single_instance("quotes")

    cfg  = load_config()
    qcfg = cfg.get("quotes", {})

    if not qcfg.get("enabled", True):
        print("quotes disabled — exiting")
        return

    used = []

    while True:
        cfg  = load_config()
        qcfg = cfg.get("quotes", {})

        if not qcfg.get("enabled", True):
            print("quotes disabled — exiting")
            return

        board_url = cfg.get("board_url", "http://matrixportal.local:8080")
        interval  = qcfg.get("interval_minutes", 60) * 60
        ttl       = interval / 60 + 5  # expire just after next post

        # Cycle through all quotes before repeating any
        remaining = [q for q in QUOTES if q not in used]
        if not remaining:
            used.clear()
            remaining = list(QUOTES)

        quote = random.choice(remaining)
        used.append(quote)

        try:
            result = post_quote(board_url, quote, ttl)
            print(f"Posted: {quote[:60]}{'...' if len(quote) > 60 else ''}")
        except Exception as e:
            msg = is_network_error(e)
            print(f"Board unreachable: {msg}" if msg else f"Error: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
