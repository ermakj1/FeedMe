VERSION = "1.9"

# MatrixPortal S3 - scrolling message queue with HTTP API
#
# POST /add       category-specific JSON payload (see send_message.py)
# POST /clear     clears the queue
# POST /interrupt {"text": "...", "duration": 5} — preempt display, then resume queue
# POST /register  {"url": "..."} — register callback URL for board events
# POST /time      {"local_epoch": N} — set the clock (Director pushes hourly)
# GET  /          returns current queue as JSON
#
# UP button: skip current message
# DOWN button: clear queue
#
# PIR sensor on A3: display sleeps after SLEEP_TIMEOUT_SECONDS of no motion
# and wakes on the next detection.

import os
import io
import sys
import time
import rtc
import json
import board
import wifi
import mdns
import supervisor
import socketpool
import ssl
import digitalio
import displayio
import framebufferio
import rgbmatrix
import adafruit_ntp
import adafruit_requests
import adafruit_connection_manager
from adafruit_httpserver import Server, Request, Response
import renderers

PANEL_WIDTH  = 64
PANEL_HEIGHT = 32
MAX_QUEUE    = 50
DEFAULT_TTL_MINUTES   = 60
SLEEP_TIMEOUT_SECONDS      = 300   # 5 minutes of no motion → sleep
PIR_ENABLED                = True  # set False if PIR sensor is not connected
HEARTBEAT_SECONDS          = 60    # log a heartbeat this often while sleeping
PRESENCE_HEARTBEAT_MINUTES = 5     # send "motion" callback this often while room is occupied
LOG_MAX_LINES         = 100
WATCHDOG_SECONDS      = 120    # hardware reset if the main loop/renderers stop polling this long (0 = off)
WIFI_CHECK_SECONDS    = 15     # how often the main loop checks the WiFi link
LOOP_ERRORS_BEFORE_RELOAD = 5  # main-loop exceptions within 60s before soft-reloading
# Buttons: UP wakes from sleep / skips message. DOWN sleeps immediately.

# --- Log buffer ---

_log_lines = []

def log(msg):
    print(msg)
    t = time.localtime()
    entry = f"{t.tm_hour:02}:{t.tm_min:02}:{t.tm_sec:02}  {msg}"
    _log_lines.append(entry)
    if len(_log_lines) > LOG_MAX_LINES:
        _log_lines.pop(0)

# --- Display ---

displayio.release_displays()

matrix = rgbmatrix.RGBMatrix(
    width=PANEL_WIDTH,
    height=PANEL_HEIGHT,
    bit_depth=3,
    rgb_pins=[
        board.MTX_R1, board.MTX_G1, board.MTX_B1,
        board.MTX_R2, board.MTX_G2, board.MTX_B2,
    ],
    addr_pins=[board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC, board.MTX_ADDRD],
    clock_pin=board.MTX_CLK,
    latch_pin=board.MTX_LAT,
    output_enable_pin=board.MTX_OE,
)

display = framebufferio.FramebufferDisplay(matrix, auto_refresh=True)

# --- Buttons ---

btn_up = digitalio.DigitalInOut(board.BUTTON_UP)
btn_up.switch_to_input(pull=digitalio.Pull.UP)

btn_down = digitalio.DigitalInOut(board.BUTTON_DOWN)
btn_down.switch_to_input(pull=digitalio.Pull.UP)

# --- PIR ---

pir = digitalio.DigitalInOut(board.A3)
pir.switch_to_input()

last_motion_ref      = [time.monotonic()]
asleep               = False
sleep_start          = None
last_heartbeat       = None
_heartbeat_interval  = HEARTBEAT_SECONDS  # doubles each log, resets on wake

_msgs_since_clock        = 0    # show clock break after this many messages
CLOCK_BREAK_EVERY        = 4    # messages between clock breaks
CLOCK_BREAK_SECS         = 30   # how long to show the clock each break
_last_presence_heartbeat = 0    # last time we sent the presence callback
SLEEP_MUTE_SECONDS       = 5    # ignore motion for this long after manual sleep
_sleep_mute_until        = 0    # monotonic time after which motion re-enables

def pir_active():
    return PIR_ENABLED and pir.value

# --- WiFi + NTP ---

def wifi_connect():
    wifi.radio.connect(
        os.getenv("CIRCUITPY_WIFI_SSID"),
        os.getenv("CIRCUITPY_WIFI_PASSWORD"),
    )

log("Connecting to WiFi...")
_wifi_delay = 2
while True:
    try:
        wifi_connect()
        break
    except Exception as e:
        log(f"WiFi connect failed: {e} — retrying in {_wifi_delay}s")
        time.sleep(_wifi_delay)
        _wifi_delay = min(_wifi_delay * 2, 30)
log(f"Connected: {wifi.radio.ipv4_address}")

pool = socketpool.SocketPool(wifi.radio)

tz_offset = int(os.getenv("TIMEZONE_OFFSET", 0))
ntp = adafruit_ntp.NTP(pool, tz_offset=tz_offset, socket_timeout=10)
try:
    rtc.RTC().datetime = ntp.datetime
    t = time.localtime()
    log(f"Time synced: {t.tm_hour:02}:{t.tm_min:02}")
except Exception as e:
    log(f"NTP sync failed: {e}")

mdns_server = mdns.Server(wifi.radio)
mdns_server.hostname = "matrixportal"
mdns_server.advertise_service(service_type="_http", protocol="_tcp", port=80)

server = Server(pool)

# --- Outbound (event callbacks) ---

_rm = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
_requests = adafruit_requests.Session(_rm, adafruit_connection_manager.get_radio_ssl_context(wifi.radio))
callback_url = None

def notify_callback(event, board_time=None):
    if not callback_url:
        return
    try:
        if board_time is None:
            mono = time.monotonic()
            t = time.localtime()
            ms = int((mono % 1) * 1000)
            board_time = f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"
        _requests.post(callback_url, json={"event": event, "board_time": board_time}, timeout=5)
    except Exception as e:
        log(f"Callback failed: {e}")

# --- Message queue ---

message_queue   = []
_deleted_ids    = set()
_next_id        = 0
current_msg     = None
_pending_reload = False
_pending_wake   = False

# Interrupt state — set by POST /interrupt, consumed by main loop
_interrupt_ref = [False]   # mutable so renderers._poll() can read it
_interrupt_msg = None      # {"text": ..., "duration": ...}

def _new_id():
    global _next_id
    _next_id += 1
    return _next_id

def _msg_summary(m):
    cat = m.get("category", "")
    if cat == "stock":
        symbol = m.get("symbol", "?")
        price  = m.get("price", None)
        change = m.get("change", None)
        if price is not None and change is not None:
            prev = price / (1 + change / 100)
            dollar = price - prev
            sign = "+" if change >= 0 else "-"
            return f"{symbol} ${price:.2f} {sign}${abs(dollar):.2f} ({sign}{abs(change):.2f}%)"
        return f"{symbol} {change}%"
    if cat == "weather": return f"{m.get('condition','?')} H:{m.get('high','?')} L:{m.get('low','?')}"
    if cat == "joke":
        setup = m.get("setup", m.get("text", ""))
        delivery = m.get("delivery", "")
        return (setup[:30] + " / " + delivery[:20]) if delivery else setup[:50]
    if cat == "animation":
        return f"{m.get('type','fireworks')} {m.get('duration',10)}s"
    return str(m.get("text", m.get("condition", "")))[:50]

def purge_expired():
    now = time.monotonic()
    expired = [m for m in message_queue if now >= m["expires_at"]]
    for m in expired:
        message_queue.remove(m)
        log(f"Expired [{m.get('category','')}]: {_msg_summary(m)}")

@server.route("/add", "POST")
def add_message(request: Request):
    try:
        data = json.loads(request.body)
        category = str(data.get("category", "")).lower()
        ttl = float(data.get("ttl_minutes", DEFAULT_TTL_MINUTES))
        purge_expired()
        if len(message_queue) >= MAX_QUEUE:
            return Response(request, '{"ok":false,"reason":"queue full"}', content_type="application/json", status=(429, "Too Many Requests"))
        msg = dict(data)
        msg["category"] = category
        msg["expires_at"] = time.monotonic() + ttl * 60
        msg["id"] = _new_id()
        message_queue.append(msg)
        log(f"Queued [{category}]: {_msg_summary(msg)}  (ttl={ttl}m, queue={len(message_queue)})")
        body = json.dumps({"ok": True, "queued": len(message_queue), "ttl_minutes": ttl})
        return Response(request, body, content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(400, "Bad Request"))

@server.route("/register", "POST")
def register(request: Request):
    global callback_url
    try:
        data = json.loads(request.body)
        url = str(data.get("url", "")).strip()
        if not url:
            return Response(request, '{"ok":false,"reason":"missing url"}', content_type="application/json", status=(400, "Bad Request"))
        if url != callback_url:   # Director re-registers every minute; only log changes
            log(f"Registered callback: {url}")
        callback_url = url
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(400, "Bad Request"))

@server.route("/unregister", "POST")
def unregister(request: Request):
    global callback_url
    callback_url = None
    log("Callback unregistered")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/ui", "GET")
def serve_ui(request: Request):
    try:
        with open("/ui.html", "r") as f:
            html = f.read()
        return Response(request, html, content_type="text/html")
    except Exception as e:
        return Response(request, f"<h1>Error loading UI: {e}</h1>", content_type="text/html")

@server.route("/delete", "POST")
def delete_message(request: Request):
    try:
        data = json.loads(request.body)
        msg_id = int(data.get("id"))
        for m in message_queue:
            if m.get("id") == msg_id:
                message_queue.remove(m)
                log(f"Deleted [{m.get('category','')}]: {_msg_summary(m)}")
                break
        else:
            if current_msg is not None and current_msg.get("id") == msg_id:
                # Can't pull it mid-render; the main loop drops it when the render ends
                _deleted_ids.add(msg_id)
                log(f"Deleted id={msg_id} (was playing)")
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(400, "Bad Request"))

@server.route("/reorder", "POST")
def reorder_queue(request: Request):
    try:
        ids     = json.loads(request.body).get("ids", [])
        id_map  = {m["id"]: m for m in message_queue}
        reordered = [id_map[i] for i in ids if i in id_map]
        leftover  = [m for m in message_queue if m["id"] not in ids]
        message_queue[:] = reordered + leftover
        log("Queue reordered")
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(400, "Bad Request"))

@server.route("/clear", "POST")
def clear_queue(request: Request):
    message_queue.clear()
    log("Queue cleared via HTTP")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/interrupt", "POST")
def interrupt_display(request: Request):
    global _interrupt_msg
    try:
        data = json.loads(request.body)
        text = str(data.get("text", "")).strip()
        if not text:
            return Response(request, '{"ok":false,"reason":"missing text"}', content_type="application/json", status=(400, "Bad Request"))
        duration = float(data.get("duration", 5))
        _interrupt_msg   = {"text": text, "duration": duration}
        _interrupt_ref[0] = True
        log(f"Interrupt requested: \"{text}\" for {duration}s")
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(400, "Bad Request"))

@server.route("/time", "POST")
def set_time(request: Request):
    # {"local_epoch": <seconds since 1970 in local wall-clock time>} — pushed
    # hourly by the Director so the clock follows DST and doesn't drift.
    try:
        epoch = int(json.loads(request.body)["local_epoch"])
        before = time.localtime()
        rtc.RTC().datetime = time.localtime(epoch)
        after = time.localtime()
        if (before.tm_hour, before.tm_min) != (after.tm_hour, after.tm_min):
            log(f"Time set: {after.tm_hour:02}:{after.tm_min:02}")
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(400, "Bad Request"))

@server.route("/upload", "POST")
def upload_file(request: Request):
    path = request.headers.get("X-Path", "").strip().lstrip("/")
    if not path or ".." in path:
        return Response(request, '{"ok":false,"reason":"invalid path"}', content_type="application/json", status=(400, "Bad Request"))
    try:
        full_path = "/" + path
        parent = full_path.rsplit("/", 1)[0]
        if parent and parent != "/":
            try:
                os.mkdir(parent)
            except OSError:
                pass
        with open(full_path, "wb") as f:
            f.write(request.body)
        log(f"Uploaded {path} ({len(request.body)}b)")
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(500, "Internal Server Error"))

@server.route("/reload", "POST")
def reload_board(request: Request):
    global _pending_reload
    _pending_reload = True
    log("Reload requested")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/usb", "GET")
def usb_status(request: Request):
    try:
        open("/usb_enabled", "r").close()
        enabled = True
    except OSError:
        enabled = False
    return Response(request, json.dumps({"usb_enabled": enabled}), content_type="application/json")

@server.route("/usb/enable", "POST")
def usb_enable(request: Request):
    global _pending_reload
    try:
        with open("/usb_enabled", "w") as f:
            f.write("1")
        _pending_reload = True
        log("USB enabled — rebooting")
        return Response(request, '{"ok":true}', content_type="application/json")
    except Exception as e:
        return Response(request, json.dumps({"ok": False, "reason": str(e)}), content_type="application/json", status=(500, "Internal Server Error"))

@server.route("/wake", "POST")
def wake_display(request: Request):
    global _pending_wake
    _pending_wake = True
    log("Wake requested via web")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/pir", "GET")
def pir_status(request: Request):
    return Response(request, json.dumps({"pir_enabled": PIR_ENABLED}), content_type="application/json")

@server.route("/pir/enable", "POST")
def pir_enable(request: Request):
    global PIR_ENABLED
    PIR_ENABLED = True
    log("PIR enabled")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/pir/disable", "POST")
def pir_disable(request: Request):
    global PIR_ENABLED
    PIR_ENABLED = False
    log("PIR disabled")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/usb/disable", "POST")
def usb_disable(request: Request):
    global _pending_reload
    try:
        os.remove("/usb_enabled")
    except OSError:
        pass
    _pending_reload = True
    log("USB disabled — rebooting")
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/schema", "GET")
def serve_schema(request: Request):
    schema = {
        "post_url": "/add",
        "common_fields": {
            "ttl_minutes": "number (optional) — expire after this many minutes",
            "max_plays": "number (optional) — remove after being shown this many times"
        },
        "categories": {
            "news": {
                "description": "Scrolling news headline",
                "fields": {
                    "text": "string (required) — the headline",
                    "ttl_minutes": "number (optional, default 60)"
                }
            },
            "weather": {
                "description": "Current weather conditions",
                "fields": {
                    "condition": "string (required) — e.g. sunny, rain, snow",
                    "high": "number (required) — high temp in F",
                    "low": "number (required) — low temp in F",
                    "precip": "number (required) — precipitation chance 0-100",
                    "ttl_minutes": "number (optional, default 120)"
                }
            },
            "stock": {
                "description": "Stock ticker update",
                "fields": {
                    "symbol": "string (required) — ticker symbol e.g. AAPL",
                    "change": "number (required) — percent change e.g. 2.3 or -1.5",
                    "ttl_minutes": "number (optional, default 30)"
                }
            },
            "calendar": {
                "description": "Upcoming calendar event reminder",
                "fields": {
                    "time": "string (required) — e.g. 2pm or in 15min",
                    "text": "string (required) — event title",
                    "ttl_minutes": "number (optional, default 120)"
                }
            },
            "text": {
                "description": "Generic plain text message",
                "fields": {
                    "text": "string (required) — message to display",
                    "ttl_minutes": "number (optional, default 60)"
                }
            },
            "joke": {
                "description": "Joke with optional punchline — setup scrolls, then animated pause, then delivery",
                "fields": {
                    "setup": "string — joke setup (or use text for single-line jokes)",
                    "delivery": "string (optional) — punchline",
                    "text": "string — single-line joke (alternative to setup/delivery)",
                    "ttl_minutes": "number (optional, default 60)"
                }
            },
            "animation": {
                "description": "Full-panel decorative animation",
                "fields": {
                    "type": "string — fireworks | rainbow | dvd | dvd_text | matrix | plasma | fire | life | cube (default: fireworks)",
                    "duration": "number (optional) — seconds to run (default 10)"
                }
            }
        }
    }
    return Response(request, json.dumps(schema), content_type="application/json")

CRASH_LOG      = "/crash.log"
RENDER_CRUMB   = "/rendering.txt"  # written before each render, deleted after

def _crumb_write(label):
    try:
        with open(RENDER_CRUMB, "w") as f:
            f.write(label)
    except Exception:
        pass

def _crumb_clear():
    try:
        import os as _os
        _os.remove(RENDER_CRUMB)
    except OSError:
        pass

def _write_crash(category, exc):
    """Capture traceback to crash.log and to the in-memory log."""
    buf = io.StringIO()
    sys.print_exception(exc, buf)
    tb = buf.getvalue()
    log(f"RENDER CRASH [{category}]: {exc}")
    log(tb[:200])   # first 200 chars to in-memory log
    try:
        with open(CRASH_LOG, "w") as f:
            f.write(f"category: {category}\n{tb}")
    except Exception:
        pass

@server.route("/crash", "GET")
def serve_crash(request: Request):
    try:
        with open(CRASH_LOG, "r") as f:
            body = f.read()
    except OSError:
        body = ""
    if not body:
        return Response(request, '{"crash":null}', content_type="application/json")
    return Response(request, json.dumps({"crash": body}), content_type="application/json")

@server.route("/crash/clear", "POST")
def clear_crash(request: Request):
    try:
        os.remove(CRASH_LOG)
    except OSError:
        pass
    return Response(request, '{"ok":true}', content_type="application/json")

@server.route("/log", "GET")
def serve_log(request: Request):
    lines = "\n".join(_log_lines[-100:])
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='5'>"
        "<title>MatrixPortal Log</title>"
        "<style>body{background:#111;color:#0f0;font-family:monospace;font-size:13px;padding:12px;}"
        "pre{white-space:pre-wrap;word-break:break-all;}</style></head>"
        "<body><pre>" + lines + "</pre>"
        "<script>window.scrollTo(0,document.body.scrollHeight);</script>"
        "</body></html>"
    )
    return Response(request, html, content_type="text/html")

@server.route("/", "GET")
def status(request: Request):
    now = time.monotonic()
    all_msgs = ([current_msg] if current_msg else []) + message_queue
    queue_out = [
        {k: v for k, v in m.items() if k != "expires_at"} |
        {"expires_in_minutes": round((m["expires_at"] - now) / 60, 1),
         "playing": (m is current_msg)}
        for m in all_msgs
    ]
    body = json.dumps({"count": len(queue_out), "queue": queue_out})
    return Response(request, body, content_type="application/json")

server.start(str(wifi.radio.ipv4_address), port=8080)
log(f"Listening at http://matrixportal.local:8080  ({wifi.radio.ipv4_address}:8080)")
if not PIR_ENABLED:
    log("PIR disabled — display will not sleep (set PIR_ENABLED=True when sensor is connected)")

# --- Renderers init ---

# --- Hardware watchdog ---
# Resets the board if nothing feeds it for WATCHDOG_SECONDS (hung render, stuck
# socket, etc.). Fed by the main loop and by renderers._poll().

wdt = None
if WATCHDOG_SECONDS:
    try:
        import microcontroller
        from watchdog import WatchDogMode
        wdt = microcontroller.watchdog
        wdt.timeout = WATCHDOG_SECONDS
        wdt.mode = WatchDogMode.RESET
        wdt.feed()
        log(f"Watchdog armed ({WATCHDOG_SECONDS}s)")
    except Exception as e:
        wdt = None
        log(f"Watchdog unavailable: {e}")

renderers.init(display, server, pir, btn_up, btn_down, last_motion_ref, SLEEP_TIMEOUT_SECONDS, _interrupt_ref, wdt)

# --- Helpers ---

def clear_display():
    display.root_group = displayio.Group()

def _run_interrupt():
    """Display the pending interrupt message, then clear interrupt state."""
    global _interrupt_msg
    msg = _interrupt_msg
    if msg is None:
        _interrupt_ref[0] = False
        return
    text     = msg["text"]
    duration = float(msg.get("duration", 5))
    # Clear flag before rendering so _poll() won't immediately re-trigger
    _interrupt_ref[0] = False
    _interrupt_msg    = None
    log(f"Interrupt display: \"{text}\" ({duration}s)")
    _crumb_write("interrupt")
    try:
        renderers.render_interrupt(text, duration)
    except Exception as e:
        _write_crash("interrupt", e)
    _crumb_clear()
    clear_display()

_last_wifi_check = time.monotonic()

def check_wifi():
    """Reconnect WiFi (and rebind the HTTP server) if the link dropped."""
    global _last_wifi_check
    now = time.monotonic()
    if now - _last_wifi_check < WIFI_CHECK_SECONDS:
        return
    _last_wifi_check = now
    connected = getattr(wifi.radio, "connected", None)
    if connected is None:
        connected = wifi.radio.ipv4_address is not None
    if connected:
        return
    log("WiFi lost — reconnecting")
    try:
        wifi_connect()
    except Exception as e:
        log(f"WiFi reconnect failed: {e}")
        return
    log(f"WiFi reconnected: {wifi.radio.ipv4_address}")
    try:
        server.stop()
    except Exception:
        pass
    try:
        server.start(str(wifi.radio.ipv4_address), port=8080)
    except Exception as e:
        # Can't rebind cleanly — a soft reload gets a fresh socket pool
        log(f"Server restart failed: {e} — reloading")
        time.sleep(0.2)
        supervisor.reload()

# --- Main loop ---

# If a render breadcrumb exists from a previous boot, a hard crash occurred there
try:
    with open(RENDER_CRUMB) as _f:
        _prior = _f.read().strip()
    log(f"Hard crash detected — was rendering: {_prior}")
    _crumb_clear()
except OSError:
    pass

clear_display()
log(f"Ready  v{VERSION}")

_loop_errors     = 0
_last_loop_error = 0

while True:
    try:
        if wdt is not None:
            wdt.feed()
        check_wifi()

        server.poll()

        if _pending_reload:
            log("Reloading...")
            time.sleep(0.2)
            supervisor.reload()

        if _pending_wake and asleep:
            _pending_wake = False
            log("Woken via web")
            asleep = False
            sleep_start = None
            last_motion_ref[0] = time.monotonic()
        elif _pending_wake:
            _pending_wake = False

        if pir_active() and time.monotonic() >= _sleep_mute_until:
            if asleep:
                slept = int(time.monotonic() - sleep_start) if sleep_start else 0
                log(f"Motion detected — waking up (slept {slept}s)")
                asleep      = False
                sleep_start = None
                notify_callback("person_detected")
            last_motion_ref[0] = time.monotonic()

        # Presence heartbeat — fire "motion" callback periodically while room is occupied
        if not asleep and PIR_ENABLED:
            now = time.monotonic()
            if (now - last_motion_ref[0] < SLEEP_TIMEOUT_SECONDS and
                    now - _last_presence_heartbeat >= PRESENCE_HEARTBEAT_MINUTES * 60):
                _last_presence_heartbeat = now
                log("Presence heartbeat")
                notify_callback("motion")

        if PIR_ENABLED and not asleep and time.monotonic() - last_motion_ref[0] > SLEEP_TIMEOUT_SECONDS:
            log(f"No motion for {SLEEP_TIMEOUT_SECONDS}s — sleeping")
            clear_display()
            asleep              = True
            sleep_start         = time.monotonic()
            last_heartbeat      = time.monotonic()
            _heartbeat_interval = HEARTBEAT_SECONDS

        if asleep:
            now = time.monotonic()
            if now - last_heartbeat >= _heartbeat_interval:
                log(f"Still sleeping... ({int(now - sleep_start)}s)")
                last_heartbeat      = now
                _heartbeat_interval = min(_heartbeat_interval * 2, 3600)
            if not btn_up.value:
                slept = int(time.monotonic() - sleep_start) if sleep_start else 0
                log(f"Woken by UP button (slept {slept}s)")
                asleep = False
                sleep_start = None
                last_motion_ref[0] = time.monotonic()
                notify_callback("person_detected")
                time.sleep(0.3)
            server.poll()
            time.sleep(0.1)
            continue

        if not btn_down.value:
            log("DOWN button — sleeping")
            clear_display()
            asleep              = True
            sleep_start         = time.monotonic()
            last_heartbeat      = time.monotonic()
            _heartbeat_interval = HEARTBEAT_SECONDS
            _sleep_mute_until   = time.monotonic() + SLEEP_MUTE_SECONDS
            time.sleep(0.3)
            continue

        if not btn_up.value:
            last_motion_ref[0] = time.monotonic()  # reset inactivity timer
            time.sleep(0.3)
            continue

        # Interrupt — preempts whatever is currently displayed
        if _interrupt_ref[0] and _interrupt_msg is not None:
            _run_interrupt()

        purge_expired()

        # Periodically show the clock even when messages are queued
        if message_queue and _msgs_since_clock >= CLOCK_BREAK_EVERY:
            _msgs_since_clock = 0
            log("Clock break")
            break_end = time.monotonic() + CLOCK_BREAK_SECS
            _crumb_write("clock")
            while time.monotonic() < break_end:
                try:
                    action = renderers.render_clock()
                except Exception as e:
                    _write_crash("clock", e)
                    action = "done"
                if action == "interrupt":
                    break
                elif action == "sleep":
                    asleep      = True
                    sleep_start = time.monotonic()
                    break
                elif action == "clear":
                    message_queue.clear()
                    break
            _crumb_clear()
            if _interrupt_ref[0] and _interrupt_msg is not None:
                _run_interrupt()

        if message_queue:
            msg = message_queue.pop(0)
            if time.monotonic() >= msg["expires_at"]:
                log(f"Skipping expired [{msg.get('category','')}]: {_msg_summary(msg)}")
                continue
            current_msg = msg
            log(f"Displaying [{msg.get('category','')}]: {_msg_summary(msg)}")
            _crumb_write(msg.get("category", "?"))
            try:
                result = renderers.render(msg)
            except Exception as e:
                _write_crash(msg.get("category", "?"), e)
                result = "done"
            _crumb_clear()
            current_msg = None
            _msgs_since_clock += 1
            was_deleted = msg.get("id") in _deleted_ids
            _deleted_ids.discard(msg.get("id"))   # only ever holds the playing id
            clear_display()
            if result == "interrupt":
                # Put the preempted message back at the front of the queue, then show interrupt
                if not was_deleted:
                    message_queue.insert(0, msg)
                _run_interrupt()
                continue
            elif result == "sleep":
                log("No motion mid-display — sleeping")
                asleep         = True
                sleep_start    = time.monotonic()
                last_heartbeat = time.monotonic()
            elif result == "clear":
                message_queue.clear()
                log("Queue cleared by button")
                time.sleep(0.3)
            elif result == "done":
                msg["plays"] = msg.get("plays", 0) + 1
                max_plays = msg.get("max_plays", None)
                still_valid = (
                    time.monotonic() < msg["expires_at"]
                    and not was_deleted
                    and (max_plays is None or msg["plays"] < max_plays)
                )
                if still_valid:
                    message_queue.append(msg)
                elif max_plays is not None and msg["plays"] >= max_plays:
                    log(f"Max plays reached [{msg.get('category','')}]: {_msg_summary(msg)}")
        else:
            result = renderers.render_clock()
            if result == "interrupt":
                _run_interrupt()
            elif result == "clear":
                message_queue.clear()
            elif result == "sleep":
                clear_display()
                asleep         = True
                sleep_start    = time.monotonic()
                last_heartbeat = time.monotonic()

    except KeyboardInterrupt:
        if wdt is not None:
            wdt.deinit()   # don't reset the board while someone is at the REPL
        raise
    except Exception as e:
        # Previously any uncaught error here ended code.py and froze the panel.
        now = time.monotonic()
        _loop_errors = _loop_errors + 1 if now - _last_loop_error < 60 else 1
        _last_loop_error = now
        _write_crash("main loop", e)
        _crumb_clear()
        current_msg = None
        _deleted_ids.clear()
        if _loop_errors >= LOOP_ERRORS_BEFORE_RELOAD:
            log(f"{_loop_errors} main-loop errors in a minute — reloading")
            time.sleep(0.5)
            supervisor.reload()
        time.sleep(1)
