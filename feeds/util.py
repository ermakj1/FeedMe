import os
import sys
import json
import time
import unicodedata
import socket
import urllib.error
import urllib.request
from pathlib import Path

# Persistent storage for caches (Jeopardy dataset, images). In Docker this is a
# mounted volume so it survives rebuilds; locally it defaults to feeds/.
DATA_DIR = Path(os.environ.get("FEEDME_DATA_DIR") or Path(__file__).parent)

def single_instance(name):
    """Exit if another instance of this feed is already running."""
    lock_path = Path(__file__).parent / f".{name}.lock"

    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            os.kill(pid, 0)  # raises if process is gone
            print(f"{name} already running (pid {pid}) — exiting")
            sys.exit(0)
        except (ProcessLookupError, OSError, ValueError, SystemError):
            pass  # stale lock, overwrite it

    lock_path.write_text(str(os.getpid()))

    import atexit
    atexit.register(lambda: lock_path.unlink(missing_ok=True))


def is_network_error(e):
    """Return a friendly message if e looks like a board-unreachable error, else None."""
    if getattr(e, "code", None) == 429:
        return "Board queue still full after retries — skipped"
    msg = str(e).lower()
    # Unwrap URLError to check the underlying reason
    reason = getattr(e, "reason", e)
    if isinstance(reason, socket.gaierror) or "no address associated with hostname" in msg or "name or service not known" in msg:
        return "Board unreachable — possibly on wrong network (can't resolve hostname)"
    if isinstance(reason, (ConnectionRefusedError, TimeoutError)) or isinstance(e, (ConnectionRefusedError, TimeoutError)):
        return "Board unreachable — connection refused or timed out (board may be rebooting)"
    if "timed out" in msg or "connection refused" in msg:
        return "Board unreachable — connection refused or timed out (board may be rebooting)"
    return None


CONFIG_PATH = Path(__file__).parent / "config.json"
_last_good_config = {}

def load_config():
    """Read feeds/config.json, tolerating a read that races with the Director's write.

    The Director rewrites config.json in place, so a feed can occasionally read a
    truncated file. Retry briefly, then fall back to the last config that parsed.
    """
    global _last_good_config
    if not CONFIG_PATH.exists():
        return _last_good_config
    for attempt in range(3):
        try:
            with open(CONFIG_PATH) as f:
                _last_good_config = json.load(f)
            return _last_good_config
        except (json.JSONDecodeError, OSError):
            time.sleep(0.2)
    return _last_good_config


# The panel font is ASCII-only; anything else renders as a blank box.
_ASCII_EXTRAS = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...",
    "\u00a3": "GBP", "\u20ac": "EUR", "\u00b0": " deg",
    "\u00e6": "ae", "\u00c6": "AE", "\u00f8": "o", "\u00d8": "O",
    "\u00df": "ss", "\u0153": "oe", "\u0152": "OE", "\u0142": "l", "\u0141": "L",
}

def to_ascii(text):
    """Fold text to ASCII for the panel: e-acute -> e, curly quotes -> straight, GBP sign -> GBP."""
    if text.isascii():
        return text
    for old, new in _ASCII_EXTRAS.items():
        text = text.replace(old, new)
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")

def _ascii_payload(value):
    if isinstance(value, str):
        return to_ascii(value)
    if isinstance(value, dict):
        return {k: _ascii_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ascii_payload(v) for v in value]
    return value


QUEUE_FULL_RETRIES   = 2
QUEUE_FULL_WAIT_SECS = 60

def post_json(url, payload, timeout=5, retries=QUEUE_FULL_RETRIES, wait=QUEUE_FULL_WAIT_SECS):
    """POST JSON to the board and return the parsed reply.

    All strings are folded to ASCII (the panel font can't draw anything else).
    If the board answers 429 (queue full), wait and retry up to `retries` times
    before re-raising. Other errors are raised immediately.
    """
    data = json.dumps(_ascii_payload(payload)).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == retries:
                raise
            print(f"Board queue full — retrying in {wait}s ({attempt + 1}/{retries})", flush=True)
            time.sleep(wait)
