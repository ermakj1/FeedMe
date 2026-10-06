#!/usr/bin/env python3
"""
Director — LED Display feed manager.

Runs on the Raspberry Pi. Starts and supervises all feed subprocesses,
serves the management web UI on port 8099, and handles motion callbacks
from the Panel (MatrixPortal S3 LED board).
"""

import calendar
import json
import os
import queue as _queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from flask import Flask, Response, jsonify, request as freq

REPO_DIR      = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO_DIR / "feeds"))
from util import to_ascii  # noqa: E402  (panel font is ASCII-only)
CONFIG_PATH   = REPO_DIR / "feeds" / "config.json"
RESTART_DELAY = 5
WEB_PORT      = 8099

ALL_ANIM_TYPES = ["fireworks", "rainbow", "plasma", "fire", "life", "cube", "dvd", "dvd_text", "matrix"]

DEFAULT_CONFIG = {
    "board_url":       "http://matrixportal.local:8080",
    "callback_port":   8090,
    "callback_host":   "",
    "weather": {
        "interval_minutes": 30,
        "enabled": True,
        "cities": [
            {"name": "Kirkland, WA, United States", "lat": 47.6815, "lon": -122.2087, "timezone": "America/Los_Angeles"}
        ],
    },
    "stock": {
        "interval_minutes": 5,
        "enabled": True,
        "market_hours_only": True,
        "symbols": ["MSFT"],
    },
    "jokes": {
        "interval_minutes": 30,
        "enabled": True,
    },
    "bbc": {
        "interval_minutes": 120,
        "enabled": True,
        "active_hours_only": True,
        "count": 3,
        "ttl_minutes": 125,
    },
    "animations": {
        "interval_minutes": 20,
        "enabled": True,
        "types": list(ALL_ANIM_TYPES),
    },
    "nasa": {
        "interval_minutes": 1440,
        "enabled": True,
        "api_key": "DEMO_KEY",
        "ttl_minutes": 1440,
    },
    "wordofday": {
        "interval_minutes": 1440,
        "enabled": True,
        "ttl_minutes": 1440,
    },
    "history": {
        "interval_minutes": 60,
        "enabled": True,
        "count": 3,
        "ttl_minutes": 65,
    },
    "countdown": {
        "interval_minutes": 60,
        "enabled": True,
        "ttl_minutes": 65,
        "events": [],
    },
    "quotes": {
        "interval_minutes": 60,
        "enabled": True,
    },
    "hermes": {
        "enabled": True,
    },
    "jeopardy": {
        "interval_minutes": 30,
        "enabled": True,
        "count": 3,
        "min_value": 200,
        "max_value": 1000,
        "ttl_minutes": 35,
    },
}

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return json.loads(json.dumps(DEFAULT_CONFIG))

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Geocoding ─────────────────────────────────────────────────────────────────

def geocode(name):
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={urllib.parse.quote(name)}&count=8&language=en&format=json"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
        results = []
        for r in data.get("results", []):
            label = r.get("name", "")
            if r.get("admin1"):
                label += f", {r['admin1']}"
            if r.get("country"):
                label += f", {r['country']}"
            results.append({
                "name":     label,
                "lat":      round(r["latitude"],  4),
                "lon":      round(r["longitude"], 4),
                "timezone": r.get("timezone", "UTC"),
            })
        return results
    except Exception as e:
        _log(f"Geocoding error: {e}")
        return []

# ── Logging + SSE broadcast ───────────────────────────────────────────────────

_sse_queues       = []
_sse_lock         = threading.Lock()
_log_history      = []
_log_history_lock = threading.Lock()
LOG_HISTORY_MAX   = 500

_cfg_ref   = None
_proc_lock = threading.Lock()
_paused    = False

_panel_crash      = None   # None or {"detected_at": str, "text": str}
_panel_crash_lock = threading.Lock()

def _broadcast(line):
    with _log_history_lock:
        _log_history.append(line)
        if len(_log_history) > LOG_HISTORY_MAX:
            _log_history.pop(0)
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait(line)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)

def _log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _broadcast(line)

# ── Process management ────────────────────────────────────────────────────────

class ManagedProcess:
    def __init__(self, name, cmd, display_name=None, startup_delay=0):
        self.name          = name
        self.display_name  = display_name or name
        self.cmd           = cmd
        self.proc          = None
        self.restart_at    = time.time() + startup_delay

    def start(self):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        self.proc = subprocess.Popen(
            self.cmd, cwd=REPO_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", env=env,
        )
        threading.Thread(target=self._read_output, daemon=True).start()
        _log(f"Started {self.name} (pid {self.proc.pid})")

    def _read_output(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                _log(f"[{self.name}] {line}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if self.proc:
            try:
                self.proc.stdout.close()
            except Exception:
                pass
        self.proc = None

    def restart(self):
        self.stop()
        self.restart_at = 0
        self.start()

    def tick(self):
        if self.proc is None:
            if time.time() >= self.restart_at:
                self.start()
            return
        rc = self.proc.poll()
        if rc is not None:
            _log(f"{self.name} exited (code {rc}), restarting in {RESTART_DELAY}s")
            self.proc = None
            self.restart_at = time.time() + RESTART_DELAY

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None


_processes: list[ManagedProcess] = []

def _build_processes():
    # startup_delay staggers first-run posts so they don't all flood the board
    # queue simultaneously. Each feed waits N seconds before its first send.
    return [
        ManagedProcess("weather",    [sys.executable, "feeds/weather.py"],   startup_delay=0),
        ManagedProcess("stock",      [sys.executable, "feeds/stock.py"],     startup_delay=5),
        ManagedProcess("jokes",      [sys.executable, "feeds/jokes.py"],     startup_delay=10),
        ManagedProcess("bbc",        [sys.executable, "feeds/bbc_headlines.py"], startup_delay=12),
        ManagedProcess("animations", [sys.executable, "feeds/animations.py"],startup_delay=15),
        ManagedProcess("nasa",       [sys.executable, "feeds/nasa.py"],      startup_delay=20),
        ManagedProcess("wordofday",  [sys.executable, "feeds/wordofday.py"], startup_delay=25),
        ManagedProcess("history",    [sys.executable, "feeds/history.py"],   startup_delay=30),
        ManagedProcess("countdown",  [sys.executable, "feeds/countdown.py"], startup_delay=35),
        ManagedProcess("quotes",     [sys.executable, "feeds/quotes.py"],    startup_delay=40, display_name="Michael Scott"),
        ManagedProcess("jeopardy",   [sys.executable, "feeds/jeopardy.py"],  startup_delay=45),
    ]

def _panel_crash_poller():
    """Background thread: polls the Panel's /crash endpoint every 60s."""
    global _panel_crash
    while True:
        time.sleep(60)
        cfg = _cfg_ref or load_config()
        board_url = cfg.get("board_url", "")
        if not board_url:
            continue
        try:
            with urllib.request.urlopen(f"{board_url}/crash", timeout=5) as r:
                data = json.loads(r.read())
            crash_text = data.get("crash")
            if crash_text:
                with _panel_crash_lock:
                    if _panel_crash is None:
                        _panel_crash = {
                            "detected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "text": crash_text,
                        }
                        _log(f"⚠ PANEL CRASH DETECTED — check Director UI for details")
        except Exception:
            pass


def _manager_loop(stop_event):
    while not stop_event.is_set():
        with _proc_lock:
            if _paused:
                for p in _processes:
                    if p.running:
                        p.stop()
            else:
                for p in _processes:
                    p.tick()
        time.sleep(5)

def restart_feed(name):
    with _proc_lock:
        for p in _processes:
            if p.name == name:
                _log(f"Restarting {name}")
                p.restart()
                return

def restart_all():
    with _proc_lock:
        for p in _processes:
            p.restart()

# ── Home Assistant forwarding ─────────────────────────────────────────────────

_ha_last_error_msg  = None   # last error string logged
_ha_error_count     = 0      # consecutive failures since last success
_HA_LOG_EVERY       = 10     # only re-log the same error every N failures

def _forward_to_ha(event):
    global _ha_last_error_msg, _ha_error_count
    cfg = _cfg_ref
    if not cfg:
        return
    url = cfg.get("ha_webhook_motion")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
        _ha_last_error_msg = None
        _ha_error_count    = 0
        _log(f"HA notified ({event})")
    except Exception as e:
        err = str(e)
        _ha_error_count += 1
        # Log on first occurrence, then every _HA_LOG_EVERY failures
        if err != _ha_last_error_msg or _ha_error_count % _HA_LOG_EVERY == 0:
            _log(f"HA notify failed: {e}")
            _ha_last_error_msg = err

# ── Callback server (board → manage.py) ──────────────────────────────────────

def _local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            payload    = json.loads(body)
            event      = payload.get("event", "unknown")
            board_time = payload.get("board_time", "?")
            recv_time  = datetime.now().strftime("%H:%M:%S.%f")
            lag        = ""
            try:
                from datetime import datetime as _dt
                bt = _dt.strptime(board_time, "%H:%M:%S.%f")
                rt = _dt.strptime(recv_time,  "%H:%M:%S.%f")
                diff_ms = int((rt - bt).total_seconds() * 1000)
                if diff_ms >= 0:
                    lag = f"  lag={diff_ms}ms"
            except Exception:
                pass
            if event == "person_detected":
                _log(f"Motion: woke from sleep  (board={board_time} recv={recv_time}{lag})")
                _forward_to_ha(event)
            elif event == "motion":
                _log(f"Motion: active  (board={board_time} recv={recv_time}{lag})")
                _forward_to_ha(event)
            else:
                _log(f"Board event: {event}  (board={board_time} recv={recv_time})")
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_):
        pass

def _start_callback_server(cfg):
    port = int(cfg.get("callback_port", 8090))
    try:
        server = HTTPServer(("0.0.0.0", port), _CallbackHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host = cfg.get("callback_host") or _local_ip()
        _log(f"Callback server on port {port}")
        return f"http://{host}:{port}/"
    except OSError as e:
        _log(f"Could not start callback server: {e}")
        return None

REGISTER_EVERY_SECS = 60     # the board forgets its callback URL on every reboot
TIME_PUSH_EVERY_SECS = 3600  # keep the board clock on local time (DST) and drift-free

def _board_post(board_url, path, payload, timeout=5):
    req = urllib.request.Request(
        f"{board_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout):
        pass

def _local_epoch():
    """Seconds since 1970 in local wall-clock time (what the board's RTC holds)."""
    return int(calendar.timegm(datetime.now().timetuple()))

def _board_sync_loop(callback_url):
    """Keep the board's callback registration and clock current.

    Re-registers every minute so motion callbacks survive board reboots, and
    pushes local time hourly (and right after the board comes back) so the
    clock follows DST. Logs only on state changes to keep the log quiet.
    """
    reachable  = None   # last known state, for change-only logging
    time_ok    = None
    last_time  = 0
    while True:
        board_url = (_cfg_ref or load_config()).get("board_url", "")
        try:
            _board_post(board_url, "/register", {"url": callback_url})
            if reachable is not True:
                _log(f"Registered callback with board: {callback_url}")
                last_time = 0   # board may have just rebooted — resync its clock now
            reachable = True
        except Exception as e:
            if reachable is not False:
                _log(f"Board unreachable for callback registration: {e}")
            reachable = False

        if reachable and time.time() - last_time >= TIME_PUSH_EVERY_SECS:
            try:
                _board_post(board_url, "/time", {"local_epoch": _local_epoch()})
                last_time = time.time()
                if time_ok is not True:
                    _log("Board clock synced from Director")
                time_ok = True
            except Exception as e:
                # Boards older than v1.9 have no /time endpoint — retry next hour
                last_time = time.time()
                if time_ok is not False:
                    _log(f"Board clock sync failed: {e}")
                time_ok = False

        time.sleep(REGISTER_EVERY_SECS)

def _board_unregister(cfg):
    board_url = cfg.get("board_url", "")
    try:
        req = urllib.request.Request(
            f"{board_url}/unregister",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
        _log("Unregistered callback from board")
    except Exception:
        pass

# Feed name → board category/categories it posts under
_FEED_CATEGORIES = {
    "weather":   ["weather"],
    "stock":     ["stock"],
    "jokes":     ["joke"],
    "animations":["animation"],
    "nasa":      ["bitmap"],
    "bbc":       ["news"],
    "wordofday": ["word"],
    "history":   ["history"],
    "countdown": ["countdown"],
    "quotes":    ["quote"],
    "hermes":    ["news", "animation", "text"],
    "jeopardy":  ["jeopardy"],
}

def _clear_feed_from_board(cfg, feed_name):
    """Delete all board queue items belonging to a feed that was just disabled."""
    categories = _FEED_CATEGORIES.get(feed_name)
    if not categories:
        return
    board_url = cfg.get("board_url", "")
    try:
        with urllib.request.urlopen(f"{board_url}/", timeout=5) as r:
            queue = json.loads(r.read()).get("queue", [])
    except Exception as e:
        _log(f"Could not fetch board queue to clear {feed_name}: {e}")
        return
    removed = 0
    for item in queue:
        if item.get("category") in categories:
            try:
                req = urllib.request.Request(
                    f"{board_url}/delete",
                    data=json.dumps({"id": item["id"]}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
                removed += 1
            except Exception as e:
                _log(f"Failed to delete item {item.get('id')}: {e}")
    if removed:
        _log(f"Removed {removed} queued item(s) for disabled feed: {feed_name}")

def _do_clear_queue(cfg):
    board_url = cfg.get("board_url", "")
    try:
        req = urllib.request.Request(
            f"{board_url}/clear", data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        _log("Board queue cleared")
    except Exception as e:
        _log(f"Clear failed: {e}")

# ── Web server (Flask) ────────────────────────────────────────────────────────

flask_app = Flask(__name__)

@flask_app.route("/")
def web_index():
    html_path = REPO_DIR / "ui_manage.html"
    with open(html_path) as f:
        return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}

@flask_app.route("/api/logs")
def api_logs():
    client_q = _queue.Queue(maxsize=200)
    with _log_history_lock:
        history = list(_log_history)
    with _sse_lock:
        _sse_queues.append(client_q)

    def generate():
        try:
            for line in history:
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                try:
                    line = client_q.get(timeout=20)
                    yield f"data: {json.dumps(line)}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                try:
                    _sse_queues.remove(client_q)
                except ValueError:
                    pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@flask_app.route("/api/status")
def api_status():
    cfg = _cfg_ref or {}
    with _proc_lock:
        feeds = [
            {
                "name":         p.name,
                "display_name": p.display_name,
                "running":      p.running,
                "enabled":      cfg.get(p.name, {}).get("enabled", True),
            }
            for p in _processes
        ]
    return jsonify({"feeds": feeds, "paused": _paused})

@flask_app.route("/api/config")
def api_config():
    return jsonify(load_config())

@flask_app.route("/api/config/<section>", methods=["POST"])
def api_config_update(section):
    global _cfg_ref
    cfg  = load_config()
    data = freq.get_json(force=True)
    if section == "board":
        for key in ("board_url", "ha_webhook_motion", "callback_host"):
            if key in data:
                cfg[key] = data[key]
        save_config(cfg)
        _cfg_ref = cfg
        restart_all()
        _log("Board settings updated")
    elif section in ("weather", "stock", "jokes", "bbc", "animations",
                     "nasa", "wordofday", "history", "countdown", "quotes", "jeopardy", "hermes"):
        was_enabled = cfg.get(section, {}).get("enabled", True)
        now_enabled = data.get("enabled", True)
        cfg[section] = data
        save_config(cfg)
        _cfg_ref = cfg
        restart_feed(section)
        _log(f"{section} config updated")
        if was_enabled and not now_enabled:
            threading.Thread(
                target=_clear_feed_from_board, args=(cfg, section), daemon=True
            ).start()
    else:
        return jsonify({"ok": False, "reason": "unknown section"}), 400
    return jsonify({"ok": True})

@flask_app.route("/api/actions/pause", methods=["POST"])
def api_pause():
    global _paused
    _paused = True
    _log("Feeds paused — board offline mode")
    return jsonify({"ok": True, "paused": True})

@flask_app.route("/api/actions/resume", methods=["POST"])
def api_resume():
    global _paused
    _paused = False
    _log("Feeds resumed")
    return jsonify({"ok": True, "paused": False})

@flask_app.route("/api/actions/restart_all", methods=["POST"])
def api_restart_all():
    restart_all()
    _log("All feeds restarted")
    return jsonify({"ok": True})

@flask_app.route("/api/actions/restart/<name>", methods=["POST"])
def api_restart_feed_route(name):
    restart_feed(name)
    return jsonify({"ok": True})

@flask_app.route("/api/actions/clear_queue", methods=["POST"])
def api_clear_queue():
    cfg = _cfg_ref or load_config()
    _do_clear_queue(cfg)
    return jsonify({"ok": True})

@flask_app.route("/api/countdown/events", methods=["GET"])
def api_countdown_events():
    cfg    = load_config()
    events = cfg.get("countdown", {}).get("events", [])
    return jsonify({"events": events})

@flask_app.route("/api/countdown/events", methods=["POST"])
def api_countdown_events_save():
    data   = freq.get_json(force=True)
    events = data.get("events", [])
    cfg    = load_config()
    cfg.setdefault("countdown", {})["events"] = events
    save_config(cfg)
    restart_feed("countdown")
    _log(f"Countdown events saved ({len(events)} events)")
    return jsonify({"ok": True})

@flask_app.route("/api/panel/crash")
def api_panel_crash():
    with _panel_crash_lock:
        return jsonify({"crash": _panel_crash})

@flask_app.route("/api/panel/crash/clear", methods=["POST"])
def api_panel_crash_clear():
    global _panel_crash
    # Clear on Director
    with _panel_crash_lock:
        _panel_crash = None
    # Clear on Panel
    board_url = (_cfg_ref or load_config()).get("board_url", "")
    try:
        req = urllib.request.Request(
            f"{board_url}/crash/clear", data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        _log(f"Could not clear crash log on Panel: {e}")
    _log("Panel crash log cleared")
    return jsonify({"ok": True})

@flask_app.route("/api/geocode", methods=["POST"])
def api_geocode():
    data  = freq.get_json(force=True)
    query = data.get("query", "")
    if not query:
        return jsonify({"results": []})
    return jsonify({"results": geocode(query)})

@flask_app.route("/api/hermes/push", methods=["POST"])
def api_hermes_push():
    cfg = _cfg_ref or load_config()
    if not cfg.get("hermes", {}).get("enabled", True):
        return jsonify({"ok": False, "reason": "Hermes is disabled"}), 403

    data = freq.get_json(force=True)

    # Validate inputs
    text = data.get("text")
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            return jsonify({"ok": False, "reason": "text must be a non-empty string"}), 400
        if len(text) > 200:
            return jsonify({"ok": False, "reason": "text must be 200 characters or fewer"}), 400
        text = to_ascii(text.strip())
    for int_key in ("duration", "ttl"):
        if int_key in data and not isinstance(data[int_key], int):
            return jsonify({"ok": False, "reason": f"{int_key} must be an integer"}), 400

    category = str(data.get("category", "text"))
    if not text and category != "animation":
        return jsonify({"ok": False, "reason": "text is required"}), 400

    if data.get("interrupt"):
        # Show immediately (preempts the current message), then the queue resumes
        if not text:
            return jsonify({"ok": False, "reason": "interrupt needs text"}), 400
        path    = "/interrupt"
        payload = {"text": text, "duration": data.get("duration", 8)}
    else:
        path    = "/add"
        payload = {"category": category, "ttl_minutes": data.get("ttl", 60)}
        if text:
            payload["text"] = text
        if "type" in data:
            payload["type"] = str(data["type"])
        if "duration" in data:
            payload["duration"] = data["duration"]

    try:
        req = urllib.request.Request(
            _board_url() + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return jsonify({"ok": True, "board": json.loads(r.read())})
    except urllib.error.HTTPError as e:
        if e.code == 429:
            reason = "board queue is full"
        elif e.code == 404 and path == "/interrupt":
            reason = "panel firmware too old for Show now — deploy board v1.9"
        else:
            reason = f"board returned HTTP {e.code}"
        return jsonify({"ok": False, "reason": reason}), 502
    except Exception as e:
        return jsonify({"ok": False, "reason": f"board unreachable: {e}"}), 502

def _board_url():
    cfg = _cfg_ref or load_config()
    return cfg.get("board_url", "http://matrixportal.local:8080")

@flask_app.route("/api/board/queue")
def api_board_queue():
    try:
        with urllib.request.urlopen(_board_url() + "/", timeout=5) as r:
            return jsonify(json.loads(r.read()))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e), "count": 0, "queue": []}), 200

@flask_app.route("/api/board/delete", methods=["POST"])
def api_board_delete():
    data = freq.get_json(force=True)
    try:
        req = urllib.request.Request(
            _board_url() + "/delete",
            data=json.dumps({"id": data["id"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return jsonify(json.loads(r.read()))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)}), 500

@flask_app.route("/api/board/reorder", methods=["POST"])
def api_board_reorder():
    data = freq.get_json(force=True)
    try:
        req = urllib.request.Request(
            _board_url() + "/reorder",
            data=json.dumps({"ids": data["ids"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return jsonify(json.loads(r.read()))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)}), 500

# ── Entry point ───────────────────────────────────────────────────────────────

def _clear_lock_files():
    """Remove stale feed lock files on startup.

    manage.py is the authoritative process manager — if it's starting fresh,
    any existing lock files are guaranteed stale (leftover from a previous
    container run / crash).  Clearing them prevents single_instance() from
    mistaking a recycled PID in the new container for a still-running feed.
    """
    feeds_dir = REPO_DIR / "feeds"
    for lock in feeds_dir.glob(".*.lock"):
        try:
            lock.unlink()
        except OSError:
            pass


def main():
    global _processes, _cfg_ref

    _clear_lock_files()

    cfg = load_config()
    save_config(cfg)
    _cfg_ref = cfg

    _processes = _build_processes()

    stop_event = threading.Event()
    threading.Thread(target=_manager_loop, args=(stop_event,), daemon=True).start()
    threading.Thread(target=_panel_crash_poller, daemon=True).start()

    callback_url = _start_callback_server(cfg)
    if callback_url:
        threading.Thread(target=_board_sync_loop, args=(callback_url,), daemon=True).start()

    print("Starting feeds...", flush=True)
    time.sleep(1)

    _log(f"Web UI at http://localhost:{WEB_PORT}")

    try:
        flask_app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, use_reloader=False, debug=False)
    finally:
        stop_event.set()
        _board_unregister(cfg)
        print("\nStopping feeds...")
        for p in _processes:
            p.stop()
        print("Done.")

if __name__ == "__main__":
    main()
