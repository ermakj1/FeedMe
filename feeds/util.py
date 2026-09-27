import os
import sys
import json
import time
import socket
import urllib.error
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
