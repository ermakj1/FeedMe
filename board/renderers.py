# renderers.py - one render function per message category
#
# Call init() once after display/hardware setup, then render(msg) for each message.
# Each renderer returns: 'done' | 'skip' | 'clear' | 'sleep'

import time
import random
import math
import displayio
import terminalio
from adafruit_display_text import label

PANEL_WIDTH  = 64
PANEL_HEIGHT = 32
SCROLL_DELAY = 0.0125

ICON_W = 16
ICON_H = 16
ICON_X = 0
ICON_Y = (PANEL_HEIGHT - ICON_H) // 2  # = 8


# Injected via init()
_display         = None
_server          = None
_pir             = None
_btn_up          = None
_btn_down        = None
_last_motion_ref = None  # mutable list [float] shared with code.py
_sleep_timeout   = None
_interrupt_ref   = None  # mutable list [bool] shared with code.py — set True to preempt render
_watchdog        = None  # microcontroller.watchdog, fed on every _poll()


def init(display, server, pir, btn_up, btn_down, last_motion_ref, sleep_timeout, interrupt_ref=None, watchdog=None):
    global _display, _server, _pir, _btn_up, _btn_down, _last_motion_ref, _sleep_timeout, _interrupt_ref, _watchdog
    _display         = display
    _server          = server
    _pir             = pir
    _btn_up          = btn_up
    _btn_down        = btn_down
    _last_motion_ref = last_motion_ref
    _sleep_timeout   = sleep_timeout
    _interrupt_ref   = interrupt_ref
    _watchdog        = watchdog


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _poll():
    """Poll server, update PIR timestamp, check buttons. Returns action or None."""
    if _watchdog is not None:
        _watchdog.feed()
    _server.poll()
    if _pir.value:
        _last_motion_ref[0] = time.monotonic()
    if _interrupt_ref is not None and _interrupt_ref[0]:
        return "interrupt"
    if not _btn_up.value:
        return "skip"
    if not _btn_down.value:
        return "clear"
    if time.monotonic() - _last_motion_ref[0] > _sleep_timeout:
        return "sleep"
    return None


def _scroll_label(lbl, start_x=None):
    """Scroll lbl from right edge to off-screen left. Returns action."""
    text_width = lbl.bounding_box[2]
    if start_x is None:
        start_x = PANEL_WIDTH
    for x in range(start_x, -text_width - 1, -1):
        action = _poll()
        if action:
            return action
        lbl.x = x
        time.sleep(SCROLL_DELAY)
    return "done"


def _wrap(text, max_chars):
    """Word-wrap text into lines of at most max_chars."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _show_text(text, color, bg_color, hold_secs=3):
    """Show text statically — scale=2 if short, wrapped lines if medium, scroll if long."""
    chars_per_line = PANEL_WIDTH // 6  # 10 at scale=1

    if len(text) <= PANEL_WIDTH // 12:
        # Short enough for scale=2, centered
        lbl = label.Label(terminalio.FONT, text=text, color=color)
        lbl.x = 0
        lbl.y = 0
        grp = displayio.Group(scale=2)
        grp.x = (PANEL_WIDTH - len(text) * 12) // 2
        grp.y = 8
        grp.append(lbl)
        outer = displayio.Group()
        outer.append(_bg(bg_color))
        outer.append(grp)
        _display.root_group = outer
        return _hold(hold_secs)

    lines = _wrap(text, chars_per_line)
    # Y centers for 1-3 lines, leaving room for descenders on last line
    y_map = {
        1: [14],
        2: [9, 21],
        3: [6, 15, 24],
    }
    # Static only if every line fits — a single word longer than chars_per_line
    # (e.g. "Christianity") would otherwise be clipped on both sides.
    if len(lines) <= 3 and all(len(line) <= chars_per_line for line in lines):
        grp = displayio.Group()
        grp.append(_bg(bg_color))
        for line, y in zip(lines, y_map[len(lines)]):
            lbl = label.Label(terminalio.FONT, text=line, color=color)
            lbl.x = (PANEL_WIDTH - len(line) * 6) // 2
            lbl.y = y
            grp.append(lbl)
        _display.root_group = grp
        return _hold(hold_secs)

    # Too long — scroll
    lbl = label.Label(terminalio.FONT, text=text, color=color)
    lbl.y = PANEL_HEIGHT // 2 - 3
    grp = displayio.Group()
    grp.append(_bg(bg_color))
    grp.append(lbl)
    _display.root_group = grp
    return _scroll_label(lbl)


def _category_header(text, color, bg_color, hold=0.9):
    """Flash a bold category label for ~1 second so the viewer knows what's coming."""
    # Use scale=2 only if the text fits on the 64px panel; fall back to scale=1
    scale = 2 if len(text) * 6 * 2 <= PANEL_WIDTH else 1
    lbl = label.Label(terminalio.FONT, text=text, color=color)
    lbl.x = 0
    lbl.y = 0
    grp = displayio.Group(scale=scale)
    grp.x = (PANEL_WIDTH - len(text) * 6 * scale) // 2
    grp.y = (PANEL_HEIGHT - 8 * scale) // 2
    grp.append(lbl)
    outer = displayio.Group()
    outer.append(_bg(bg_color))
    outer.append(grp)
    _display.root_group = outer
    return _hold(hold)


def _hold(seconds):
    """Hold the current display for N seconds. Returns action or 'done'."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        action = _poll()
        if action:
            return action
        time.sleep(0.05)
    return "done"


def _bg(color):
    """Return a full-panel background TileGrid in the given color."""
    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, 1)
    pal = displayio.Palette(1)
    pal[0] = color
    return displayio.TileGrid(bm, pixel_shader=pal, x=0, y=0)


def _flash(color, duration=0.15):
    """Briefly flood the display with color (e.g. green/red on stock move)."""
    grp = displayio.Group()
    grp.append(_bg(color))
    _display.root_group = grp
    time.sleep(duration)


def _clock_color():
    """Return a time-of-day color for the clock."""
    h = time.localtime().tm_hour
    if 5  <= h < 8:  return 0xFF6600  # dawn — orange
    if 8  <= h < 17: return 0xFFFFFF  # day  — white
    if 17 <= h < 20: return 0xFF8800  # dusk — amber
    return 0x4466FF                    # night — blue


# ---------------------------------------------------------------------------
# Icon drawing (16×16 bitmap, transparent background)
# ---------------------------------------------------------------------------

# Palette indices
_BG      = 0
_WHITE   = 1
_YELLOW  = 2
_ORANGE  = 3
_BLUE    = 4
_LTBLUE  = 5
_GRAY    = 6
_LTGRAY  = 7


def _make_icon_palette():
    p = displayio.Palette(8)
    p.make_transparent(_BG)
    p[_WHITE]  = 0xFFFFFF
    p[_YELLOW] = 0xFFDD00
    p[_ORANGE] = 0xFF8800
    p[_BLUE]   = 0x3388FF
    p[_LTBLUE] = 0x88CCFF
    p[_GRAY]   = 0x888888
    p[_LTGRAY] = 0xBBBBBB
    return p


def _clear_icon(bm):
    for y in range(ICON_H):
        for x in range(ICON_W):
            bm[x, y] = _BG


# Sun: circle core + 8 rays alternating bright/dim each frame
_SUN_CORE = [(dx, dy) for dy in range(-3, 4) for dx in range(-3, 4) if dx*dx + dy*dy <= 10]
_SUN_RAYS = [
    ((7, 2),  (7, 1)),    # N
    ((11, 3), (12, 2)),   # NE
    ((12, 7), (13, 7)),   # E
    ((11, 11),(12, 12)),  # SE
    ((7, 12), (7, 13)),   # S
    ((3, 11), (2, 12)),   # SW
    ((2, 7),  (1, 7)),    # W
    ((3, 3),  (2, 2)),    # NW
]

def _draw_sun(bm, frame):
    _clear_icon(bm)
    for dx, dy in _SUN_CORE:
        bm[7 + dx, 7 + dy] = _YELLOW
    for i, (inner, outer) in enumerate(_SUN_RAYS):
        color = _YELLOW if (i + frame) % 2 == 0 else _ORANGE
        for px, py in (inner, outer):
            if 0 <= px < ICON_W and 0 <= py < ICON_H:
                bm[px, py] = color


# Rain: falling blue drops with short tail
def _init_drops(n=7):
    return [(random.randint(0, ICON_W - 1), random.randint(0, ICON_H - 1)) for _ in range(n)]

def _draw_rain(bm, drops):
    _clear_icon(bm)
    new_drops = []
    for x, y in drops:
        if 0 <= y < ICON_H:
            bm[x, y] = _BLUE
        if 0 <= y - 1 < ICON_H:
            bm[x, y - 1] = _LTBLUE
        ny = y + 2
        nx = x
        if ny >= ICON_H:
            ny = random.randint(-3, 0)
            nx = random.randint(0, ICON_W - 1)
        new_drops.append((nx, ny))
    return new_drops


# Snow: slow drifting white flakes
def _init_flakes(n=6):
    return [(random.randint(0, ICON_W - 1), random.randint(0, ICON_H - 1)) for _ in range(n)]

def _draw_snow(bm, flakes, frame):
    _clear_icon(bm)
    new_flakes = []
    for x, y in flakes:
        if 0 <= x < ICON_W and 0 <= y < ICON_H:
            bm[x, y] = _WHITE
        ny = y + 1
        nx = x + (1 if frame % 8 < 4 else -1)
        if ny >= ICON_H:
            ny = 0
            nx = random.randint(0, ICON_W - 1)
        new_flakes.append((nx % ICON_W, ny))
    return new_flakes


# Cloud: gray blob with slow pulse
_CLOUD_PX = [
    (6,4),(7,4),(8,4),(9,4),
    (5,5),(6,5),(7,5),(8,5),(9,5),(10,5),
    (3,6),(4,6),(5,6),(6,6),(7,6),(8,6),(9,6),(10,6),(11,6),(12,6),
    (3,7),(4,7),(5,7),(6,7),(7,7),(8,7),(9,7),(10,7),(11,7),(12,7),
    (3,8),(4,8),(5,8),(6,8),(7,8),(8,8),(9,8),(10,8),(11,8),(12,8),
    (4,9),(5,9),(6,9),(7,9),(8,9),(9,9),(10,9),(11,9),
]

def _draw_cloud(bm, frame):
    _clear_icon(bm)
    color = _LTGRAY if (frame // 4) % 2 == 0 else _GRAY
    for px, py in _CLOUD_PX:
        if 0 <= px < ICON_W and 0 <= py < ICON_H:
            bm[px, py] = color


# Storm: cloud + rain + occasional lightning bolt
_LIGHTNING = [(7,9),(7,10),(8,11),(8,12),(7,13)]

def _draw_storm(bm, drops, frame):
    drops = _draw_rain(bm, drops)
    for px, py in _CLOUD_PX:
        if 0 <= px < ICON_W and 0 <= py < ICON_H:
            bm[px, py] = _GRAY
    if frame % 24 < 3:
        for px, py in _LIGHTNING:
            if 0 <= px < ICON_W and 0 <= py < ICON_H:
                bm[px, py] = _WHITE
    return drops


def _condition_to_icon(condition):
    c = condition.lower()
    if any(w in c for w in ("sun", "clear", "fair")):      return "sun"
    if any(w in c for w in ("snow", "blizzard", "flurr")): return "snow"
    if any(w in c for w in ("storm", "thunder")):           return "storm"
    if any(w in c for w in ("rain", "shower", "drizzle")):  return "rain"
    return "cloud"


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------

def render(msg):
    """Dispatch to the right renderer based on category."""
    category = msg.get("category", "")
    if category == "weather":   return render_weather(msg)
    if category == "stock":     return render_stock(msg)
    if category == "news":      return render_news(msg)
    if category == "calendar":  return render_calendar(msg)
    if category == "joke":      return render_joke(msg)
    if category == "animation": return render_animation(msg)
    if category == "bitmap":    return render_bitmap(msg)
    if category == "word":      return render_word(msg)
    if category == "history":   return render_history(msg)
    if category == "countdown": return render_countdown(msg)
    if category == "quote":     return render_quote(msg)
    if category == "jeopardy":  return render_jeopardy(msg)
    return render_generic(msg)


_WDAYS  = ("Mon","Tue","Wed","Thu","Fri","Sat","Sun")
_MONTHS = ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")

def render_clock():
    """Show time + date for ~1 second, polling server. Returns action or 'done'."""
    t        = time.localtime()
    color    = _clock_color()
    hour     = t.tm_hour % 12 or 12
    time_str = f"{hour}:{t.tm_min:02}"
    date_str = f"{_WDAYS[t.tm_wday]} {_MONTHS[t.tm_mon-1]} {t.tm_mday}"

    # Split hours/mins into separate scale=2 labels with a custom colon bitmap
    # so the colon dots are properly centered (font colon sits too low)
    hour_str = f"{hour}"
    min_str  = f"{t.tm_min:02}"
    hour_w   = len(hour_str) * 6 * 2   # 12 or 24px
    colon_w  = 4
    total_w  = hour_w + colon_w + 24   # mins always 2 digits = 24px
    x0       = (PANEL_WIDTH - total_w) // 2
    GY       = 10   # group y — text center in display space

    hour_lbl = label.Label(terminalio.FONT, text=hour_str, color=color)
    hour_lbl.x = 0;  hour_lbl.y = 0
    hour_grp = displayio.Group(scale=2)
    hour_grp.x = x0;  hour_grp.y = GY
    hour_grp.append(hour_lbl)

    min_lbl = label.Label(terminalio.FONT, text=min_str, color=color)
    min_lbl.x = 0;  min_lbl.y = 0
    min_grp = displayio.Group(scale=2)
    min_grp.x = x0 + hour_w + colon_w;  min_grp.y = GY
    min_grp.append(min_lbl)

    # 4×16 colon bitmap with 2×2 dots at 1/3 and 2/3 height, aligned to GY
    col_bm  = displayio.Bitmap(4, 16, 2)
    col_pal = displayio.Palette(2)
    col_pal[0] = 0x000000
    col_pal[1] = color
    for cx in (1, 2):
        col_bm[cx, 4] = 1;  col_bm[cx, 5] = 1    # top dot
        col_bm[cx, 10] = 1; col_bm[cx, 11] = 1   # bottom dot
    col_tg = displayio.TileGrid(col_bm, pixel_shader=col_pal,
                                x=x0 + hour_w, y=GY - 8)

    date_lbl = label.Label(terminalio.FONT, text=date_str, color=0x444444)
    date_lbl.x = (PANEL_WIDTH - len(date_str) * 6) // 2
    date_lbl.y = 25

    group = displayio.Group()
    group.append(hour_grp)
    group.append(col_tg)
    group.append(min_grp)
    group.append(date_lbl)

    # Seconds progress bar along the bottom — dimmed version of clock color
    bar_w = max(1, int(t.tm_sec * PANEL_WIDTH / 60))
    bar_bm  = displayio.Bitmap(bar_w, 1, 1)
    bar_pal = displayio.Palette(1)
    r = ((color >> 16) & 0xFF) >> 2
    g = ((color >> 8)  & 0xFF) >> 2
    b = (color         & 0xFF) >> 2
    bar_pal[0] = (r << 16) | (g << 8) | b
    group.append(displayio.TileGrid(bar_bm, pixel_shader=bar_pal, x=0, y=31))

    # Motion indicator — top-right pixel, red for 2s after last detection
    dot_bm  = displayio.Bitmap(1, 1, 1)
    dot_bm[0, 0] = 0
    dot_pal = displayio.Palette(1)
    dot_pal[0] = 0x000000
    group.append(displayio.TileGrid(dot_bm, pixel_shader=dot_pal,
                                    x=PANEL_WIDTH - 1, y=0))

    _display.root_group = group

    end = time.monotonic() + 1
    while time.monotonic() < end:
        action = _poll()
        if action:
            return action
        dot_pal[0] = 0xFF0000 if time.monotonic() - _last_motion_ref[0] < 2.0 else 0x000000
        time.sleep(0.05)
    return "done"




def render_interrupt(text, duration=5):
    """Show an interrupt message for `duration` seconds, then return 'done'."""
    return _show_text(text, 0xFF4400, 0x0A0000, hold_secs=duration)


def render_generic(msg):
    return _show_text(msg.get("text", ""), 0xFFFFFF, 0x0A0A0A)


def render_news(msg):
    return _show_text(msg.get("text", ""), 0xDD88FF, 0x0C0018)


def render_calendar(msg):
    time_str = msg.get("time", "")
    text     = msg.get("text", "")
    full     = f"{time_str}: {text}" if time_str else text
    return _show_text(full, 0xFFEE44, 0x0C0A00)


def render_stock(msg):
    symbol     = msg.get("symbol", "???").upper()
    change_pct = float(msg.get("change", 0))
    price      = float(msg.get("price", 0))
    is_up      = change_pct >= 0
    chg_color  = 0x00FF44 if is_up else 0xFF3300
    bg_color   = 0x001500 if is_up else 0x150000
    sign       = "+" if is_up else "-"

    prev_close    = price / (1 + change_pct / 100) if price else 0
    dollar_change = abs(price - prev_close)

    # Flash the panel on significant moves (>= 2%)
    if abs(change_pct) >= 2:
        _flash(0x003300 if is_up else 0x330000)

    sym_lbl = label.Label(terminalio.FONT, text=symbol, color=0xFFFFFF)
    sym_lbl.x = 2
    sym_lbl.y = 6

    price_lbl = label.Label(terminalio.FONT, text=f"${price:.2f}", color=0xAAAAAA)
    price_lbl.x = 2
    price_lbl.y = 16

    chg_lbl = label.Label(terminalio.FONT, text="", color=chg_color)
    chg_lbl.x = 2
    chg_lbl.y = 26

    group = displayio.Group()
    group.append(_bg(bg_color))
    group.append(sym_lbl)
    group.append(price_lbl)
    group.append(chg_lbl)
    _display.root_group = group

    # Toggle between $ and % change every 3 seconds
    for show_pct in [False, True, False, True]:
        chg_lbl.text = f"{sign}{abs(change_pct):.2f}%" if show_pct else f"{sign}${dollar_change:.2f}"
        result = _hold(3)
        if result and result != "done":
            return result
    return "done"


def render_joke(msg):
    setup    = msg.get("setup", msg.get("text", ""))
    delivery = msg.get("delivery", "")
    joke_bg  = 0x080A00

    if setup:
        result = _show_text(setup, 0xFFCC44, joke_bg, hold_secs=3)
        if result and result != "done":
            return result

    if not delivery:
        return "done"

    # Animated "..." — dots appear one by one
    for dots in (".", "..", "..."):
        dot_lbl = label.Label(terminalio.FONT, text=dots, color=0x668833)
        dot_lbl.x = (PANEL_WIDTH - len(dots) * 6) // 2
        dot_lbl.y = PANEL_HEIGHT // 2 - 3
        grp = displayio.Group()
        grp.append(_bg(joke_bg))
        grp.append(dot_lbl)
        _display.root_group = grp
        result = _hold(0.5)
        if result and result != "done":
            return result

    return _show_text(delivery, 0xFFFF88, joke_bg, hold_secs=4)


def render_weather(msg):
    condition = msg.get("condition", "")
    high      = msg.get("high",   None)
    low       = msg.get("low",    None)
    precip    = msg.get("precip", None)
    icon_type = _condition_to_icon(condition)

    # Randomly place icon on left or right half
    icon_on_left = random.choice([True, False])
    icon_x = 0  if icon_on_left else 32
    text_x = 34 if icon_on_left else 2

    # Icon: 16x16 bitmap scaled x2 to fill a 32x32 half
    icon_bm   = displayio.Bitmap(ICON_W, ICON_H, 8)
    icon_pal  = _make_icon_palette()
    icon_tile = displayio.TileGrid(icon_bm, pixel_shader=icon_pal)
    icon_grp  = displayio.Group(scale=2)
    icon_grp.x = icon_x
    icon_grp.y = 0
    icon_grp.append(icon_tile)

    # Static text: high, low, precip on the other half
    group = displayio.Group()
    group.append(_bg(0x000A18))
    group.append(icon_grp)

    def add_label(text, color, y):
        lbl = label.Label(terminalio.FONT, text=text, color=color)
        lbl.x = text_x
        lbl.y = y
        group.append(lbl)

    city = msg.get("city", "")
    if city:
        add_label(city[:8],           0xCCCCCC, 4)
        if high   is not None: add_label(f"H:{int(high)}", 0xFF8844, 14)
        if low    is not None: add_label(f"L:{int(low)}",  0x4499FF, 24)
    else:
        if high   is not None: add_label(f"H:{int(high)}", 0xFF8844, 4)
        if low    is not None: add_label(f"L:{int(low)}",  0x4499FF, 13)
        if precip is not None: add_label(f"{int(precip)}%",0x44CCFF, 22)

    _display.root_group = group

    state = _init_drops() if icon_type in ("rain", "storm") else \
            _init_flakes() if icon_type == "snow" else None

    frame = 0
    end   = time.monotonic() + 6
    while time.monotonic() < end:
        action = _poll()
        if action:
            return action
        if frame % 3 == 0:
            icon_frame = frame // 3
            if   icon_type == "sun":   _draw_sun(icon_bm, icon_frame)
            elif icon_type == "rain":  state = _draw_rain(icon_bm, state)
            elif icon_type == "snow":  state = _draw_snow(icon_bm, state, icon_frame)
            elif icon_type == "storm": state = _draw_storm(icon_bm, state, icon_frame)
            elif icon_type == "cloud": _draw_cloud(icon_bm, icon_frame)
        frame += 1
        time.sleep(0.05)
    return "done"


# ---------------------------------------------------------------------------
# Animation renderers
# ---------------------------------------------------------------------------

_ANIM_COLORS = [
    0xFF2200, 0xFF8800, 0xFFEE00, 0x00FF44,
    0x00AAFF, 0x8800FF, 0xFF00CC, 0xFFFFFF,
]

def _hsv_to_rgb(h, s, v):
    """h/s/v each 0.0–1.0 → packed 24-bit RGB int."""
    if s == 0:
        c = int(v * 255)
        return (c << 16) | (c << 8) | c
    h6 = h * 6.0
    i  = int(h6) % 6
    f  = h6 - int(h6)
    p  = v * (1 - s)
    q  = v * (1 - s * f)
    t  = v * (1 - s * (1 - f))
    if   i == 0: r, g, b = v, t, p
    elif i == 1: r, g, b = q, v, p
    elif i == 2: r, g, b = p, v, t
    elif i == 3: r, g, b = p, q, v
    elif i == 4: r, g, b = t, p, v
    else:        r, g, b = v, p, q
    return (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)


def _anim_fireworks(duration):
    """Colored particle bursts from random launch points."""
    N = len(_ANIM_COLORS)
    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, N + 1)
    pal = displayio.Palette(N + 1)
    pal.make_transparent(0)
    for i, c in enumerate(_ANIM_COLORS):
        pal[i + 1] = c

    bg_bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, 1)
    bg_pal = displayio.Palette(1)
    bg_pal[0] = 0x000000
    grp = displayio.Group()
    grp.append(displayio.TileGrid(bg_bm, pixel_shader=bg_pal))
    grp.append(displayio.TileGrid(bm,    pixel_shader=pal))
    _display.root_group = grp

    # Each particle: [x, y, dx, dy, life, color_idx]
    particles = []

    end        = time.monotonic() + duration
    frame      = 0
    next_burst = 0

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        bm.fill(0)

        if frame >= next_burst:
            cx = random.randint(10, PANEL_WIDTH  - 10)
            cy = random.randint(8,  PANEL_HEIGHT - 8)
            ci = random.randint(1, N)
            for _ in range(14):
                angle = random.uniform(0, 6.283)
                speed = random.uniform(0.4, 1.3)
                dx    = speed * math.cos(angle)
                dy    = speed * math.sin(angle) * 0.55  # squash vertically
                particles.append([float(cx), float(cy), dx, dy,
                                   random.randint(7, 14), ci])
            next_burst = frame + 22 + random.randint(0, 12)

        i = 0
        while i < len(particles):
            p = particles[i]
            p[0] += p[2]
            p[1] += p[3]
            p[4] -= 1
            ix, iy = int(p[0]), int(p[1])
            if p[4] <= 0 or not (0 <= ix < PANEL_WIDTH and 0 <= iy < PANEL_HEIGHT):
                particles.pop(i)
            else:
                bm[ix, iy] = p[5]
                i += 1

        frame += 1
        time.sleep(0.05)

    return "done"


def _anim_rainbow_pulse(duration):
    """Full-panel rainbow that shifts hue over time with a brightness wave."""
    N = 32  # palette slots, 2 columns each
    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, N)
    pal = displayio.Palette(N)
    tile = displayio.TileGrid(bm, pixel_shader=pal)
    grp  = displayio.Group()
    grp.append(tile)
    _display.root_group = grp

    # Map each column to a palette index (static)
    for y in range(PANEL_HEIGHT):
        for x in range(PANEL_WIDTH):
            bm[x, y] = (x * N) // PANEL_WIDTH

    end   = time.monotonic() + duration
    frame = 0

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        hue_offset = (frame * 0.018) % 1.0
        for i in range(N):
            hue        = (hue_offset + i / N) % 1.0
            brightness = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(frame * 0.12 + i * 0.4))
            pal[i]     = _hsv_to_rgb(hue, 1.0, brightness)

        frame += 1
        time.sleep(0.05)

    return "done"


def _bounce_step(x, y, dx, dy, w, h):
    """Advance one bounce frame. Returns (x, y, dx, dy, hit)."""
    x += dx
    y += dy
    hit = False
    if x < 0:
        x = 0.0; dx = abs(dx); hit = True
    elif x + w > PANEL_WIDTH:
        x = float(PANEL_WIDTH - w); dx = -abs(dx); hit = True
    if y < 0:
        y = 0.0; dy = abs(dy); hit = True
    elif y + h > PANEL_HEIGHT:
        y = float(PANEL_HEIGHT - h); dy = -abs(dy); hit = True
    return x, y, dx, dy, hit


def _anim_dvd_bounce(duration):
    """Classic bouncing rectangle; changes color on each wall hit."""
    RW, RH = 14, 7
    x, y   = float(PANEL_WIDTH // 3), float(PANEL_HEIGHT // 3)
    dx, dy = 1.1, 0.75
    ci     = 0

    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = _ANIM_COLORS[ci]
    grp = displayio.Group()
    grp.append(displayio.TileGrid(bm, pixel_shader=pal))
    _display.root_group = grp

    end = time.monotonic() + duration

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        bm.fill(0)
        ix, iy = int(x), int(y)
        for ry in range(RH):
            for rx in range(RW):
                px, py = ix + rx, iy + ry
                if 0 <= px < PANEL_WIDTH and 0 <= py < PANEL_HEIGHT:
                    bm[px, py] = 1

        x, y, dx, dy, hit = _bounce_step(x, y, dx, dy, RW, RH)
        if hit:
            ci     = (ci + 1) % len(_ANIM_COLORS)
            pal[1] = _ANIM_COLORS[ci]

        time.sleep(0.05)

    return "done"


def _anim_dvd_text(duration):
    """'DVD' text bouncing around the panel, changing color on wall hits."""
    ci  = 0
    lbl = label.Label(terminalio.FONT, text="DVD", color=_ANIM_COLORS[ci])
    lbl.x = 0
    lbl.y = 0

    bg_bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, 1)
    bg_pal = displayio.Palette(1)
    bg_pal[0] = 0x000000
    grp = displayio.Group()
    grp.append(displayio.TileGrid(bg_bm, pixel_shader=bg_pal))
    grp.append(lbl)
    _display.root_group = grp

    # Measure text after it's been placed on display
    bb = lbl.bounding_box   # (x_off, y_off, width, height)
    TW = bb[2]
    TH = bb[3]

    x, y   = float(PANEL_WIDTH // 3), float(PANEL_HEIGHT // 3)
    dx, dy = 1.1, 0.75

    end = time.monotonic() + duration

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        lbl.x = int(x)
        lbl.y = int(y) + TH // 2  # label y is vertical center anchor

        x, y, dx, dy, hit = _bounce_step(x, y, dx, dy, TW, TH)
        if hit:
            ci        = (ci + 1) % len(_ANIM_COLORS)
            lbl.color = _ANIM_COLORS[ci]

        time.sleep(0.05)

    return "done"


def _anim_matrix_rain(duration):
    """Green falling pixel streams — classic Matrix screensaver."""
    # 8-entry palette: black + 6 green shades + near-white head
    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, 8)
    pal = displayio.Palette(8)
    pal[0] = 0x000000  # black
    pal[1] = 0x001400  # barely visible
    pal[2] = 0x002C00  # very dim
    pal[3] = 0x005200  # dim
    pal[4] = 0x009900  # medium
    pal[5] = 0x00DD00  # bright
    pal[6] = 0x00FF44  # full green
    pal[7] = 0xCCFFCC  # near-white head

    grp = displayio.Group()
    grp.append(displayio.TileGrid(bm, pixel_shader=pal))
    _display.root_group = grp
    bm.fill(0)

    # 16 columns, each 3px wide with 1px gap
    COL_W  = 4
    N_COLS = PANEL_WIDTH // COL_W  # 16

    # streams: [x, y_head (float), speed, trail_len]
    streams = [
        [i * COL_W,
         float(-random.randint(2, PANEL_HEIGHT + 6)),
         random.uniform(0.35, 0.95),
         random.randint(6, 20)]
        for i in range(N_COLS)
    ]

    # Trail index → palette index
    _TRAIL = [7, 6, 6, 5, 5, 5, 4, 4, 4, 3, 3, 3, 2, 2, 1, 1, 1, 1, 1, 1]

    end = time.monotonic() + duration

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        bm.fill(0)

        for s in streams:
            x, y, speed, trail = s
            hy = int(y)
            for t in range(trail):
                ty = hy - t
                if 0 <= ty < PANEL_HEIGHT:
                    ci = _TRAIL[t] if t < len(_TRAIL) else 1
                    for dx in range(COL_W - 1):  # 1px gap between columns
                        if x + dx < PANEL_WIDTH:
                            bm[x + dx, ty] = ci
            s[1] += speed
            if int(s[1]) - trail > PANEL_HEIGHT:
                s[1] = float(-random.randint(2, 10))
                s[2] = random.uniform(0.35, 0.95)
                s[3] = random.randint(6, 20)

        time.sleep(0.05)

    return "done"


def _anim_plasma(duration):
    """Flowing sine-wave color fields via palette cycling — pre-computed once."""
    N  = 32
    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, N)
    pal = displayio.Palette(N)

    # Build static index map — only done once at start
    for y in range(PANEL_HEIGHT):
        sy = math.sin(y * 0.4)
        for x in range(PANEL_WIDTH):
            v = math.sin(x * 0.3) + sy + math.sin((x + y) * 0.15)
            bm[x, y] = int((v + 3.0) / 6.0 * N) % N

    grp = displayio.Group()
    grp.append(displayio.TileGrid(bm, pixel_shader=pal))
    _display.root_group = grp

    end   = time.monotonic() + duration
    frame = 0

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action
        t = frame * 0.04
        for i in range(N):
            hue = (i / N + t * 0.25) % 1.0
            pal[i] = _hsv_to_rgb(hue, 1.0, 0.9)
        frame += 1
        time.sleep(0.04)

    return "done"


def _anim_fire(duration):
    """Classic bottom-up fire simulation — 32×16 grid scaled ×2."""
    FW, FH = PANEL_WIDTH // 2, PANEL_HEIGHT // 2   # 32×16
    N      = 32  # heat levels

    pal = displayio.Palette(N)
    for i in range(N):
        t = i / (N - 1)
        if t < 0.4:
            r = int(t / 0.4 * 180);  g = 0; b = 0
        elif t < 0.7:
            r = 180 + int((t - 0.4) / 0.3 * 75)
            g = int((t - 0.4) / 0.3 * 140); b = 0
        else:
            r = 255
            g = 140 + int((t - 0.7) / 0.3 * 115)
            b = int((t - 0.7) / 0.3 * 200)
        pal[i] = (r << 16) | (g << 8) | b

    bm   = displayio.Bitmap(FW, FH, N)
    grp  = displayio.Group(scale=2)
    grp.append(displayio.TileGrid(bm, pixel_shader=pal))
    _display.root_group = grp

    # Pre-compute wrap-around column indices to avoid modulo in hot loop
    xm = [(x - 1) % FW for x in range(FW)]
    xp = [(x + 1) % FW for x in range(FW)]

    heat = [0] * (FW * FH)
    end  = time.monotonic() + duration

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        # Seed bottom two rows
        bot1 = (FH - 1) * FW
        bot2 = (FH - 2) * FW
        for x in range(FW):
            heat[bot1 + x] = (N // 2) + (random.getrandbits(4) % (N // 2))
            heat[bot2 + x] = (N // 2) + (random.getrandbits(4) % (N // 2))

        # Diffuse upward — runs at hardware speed, no sleep
        for y in range(FH - 3, -1, -1):
            row  = y * FW
            row1 = row + FW
            row2 = row1 + FW
            for x in range(FW):
                avg = (heat[row1 + x] + heat[row2 + x] +
                       heat[row1 + xm[x]] + heat[row1 + xp[x]]) >> 2
                heat[row + x] = avg - random.getrandbits(1)  if avg > 0 else 0

        # Draw
        for y in range(FH):
            row = y * FW
            for x in range(FW):
                bm[x, y] = heat[row + x]

    return "done"


def _anim_game_of_life(duration):
    """Conway's Game of Life with color cycling and auto-randomize on stagnation."""
    W, H = PANEL_WIDTH, PANEL_HEIGHT

    # Pre-compute wrap-around lookup tables — eliminates modulo from hot loop
    xm = [(x - 1) % W for x in range(W)]
    xp = [(x + 1) % W for x in range(W)]
    ym = [(y - 1) % H for y in range(H)]
    yp = [(y + 1) % H for y in range(H)]

    def randomize():
        return [1 if random.random() < 0.35 else 0 for _ in range(W * H)]

    current = randomize()
    nxt     = [0] * (W * H)
    ci      = 0

    bm  = displayio.Bitmap(W, H, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = _ANIM_COLORS[ci]
    grp = displayio.Group()
    grp.append(displayio.TileGrid(bm, pixel_shader=pal))
    _display.root_group = grp

    end      = time.monotonic() + duration
    gen      = 0
    prev_pop = -1
    stagnant = 0

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        pop = 0
        for y in range(H):
            rowm = ym[y] * W
            row  = y     * W
            rowp = yp[y] * W
            for x in range(W):
                xm1 = xm[x]; xp1 = xp[x]
                n = (current[rowm + xm1] + current[rowm + x] + current[rowm + xp1] +
                     current[row  + xm1]                     + current[row  + xp1] +
                     current[rowp + xm1] + current[rowp + x] + current[rowp + xp1])
                c = current[row + x]
                alive = 1 if n == 3 or (c and n == 2) else 0
                nxt[row + x] = alive
                pop += alive
                bm[x, y] = alive

        current, nxt = nxt, current
        gen += 1

        if gen % 15 == 0:
            ci     = (ci + 1) % len(_ANIM_COLORS)
            pal[1] = _ANIM_COLORS[ci]

        stagnant = stagnant + 1 if pop == prev_pop else 0
        prev_pop = pop
        if pop == 0 or stagnant > 8:
            current  = randomize()
            stagnant = 0

        # No sleep — runs at hardware speed; GoL is compute-bound anyway

    return "done"


def _bresenham(bm, x0, y0, x1, y1):
    """Draw a line on bm (value 1) using Bresenham's algorithm."""
    dx  = abs(x1 - x0);  dy  = abs(y1 - y0)
    sx  = 1 if x0 < x1 else -1
    sy  = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        if 0 <= x0 < PANEL_WIDTH and 0 <= y0 < PANEL_HEIGHT:
            bm[x0, y0] = 1
        if x0 == x1 and y0 == y1:
            break
        e2 = err * 2
        if e2 > -dy: err -= dy; x0 += sx
        if e2 <  dx: err += dx; y0 += sy


def _anim_cube(duration):
    """Spinning wireframe cube with perspective projection."""
    VERTS = [
        (-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
        (-1,-1, 1),(1,-1, 1),(1,1, 1),(-1,1, 1),
    ]
    EDGES = [
        (0,1),(1,2),(2,3),(3,0),   # back face
        (4,5),(5,6),(6,7),(7,4),   # front face
        (0,4),(1,5),(2,6),(3,7),   # connecting edges
    ]

    bm  = displayio.Bitmap(PANEL_WIDTH, PANEL_HEIGHT, 2)
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = _ANIM_COLORS[0]
    grp = displayio.Group()
    grp.append(displayio.TileGrid(bm, pixel_shader=pal))
    _display.root_group = grp

    end   = time.monotonic() + duration
    frame = 0
    ci    = 0

    while time.monotonic() < end:
        action = _poll()
        if action:
            return action

        bm.fill(0)
        ax = frame * 0.04
        ay = frame * 0.06

        # Rotate and project all 8 vertices
        cax, sax = math.cos(ax), math.sin(ax)
        cay, say = math.cos(ay), math.sin(ay)
        pts = []
        for vx, vy, vz in VERTS:
            ry  = vy * cax - vz * sax
            rz  = vy * sax + vz * cax
            rx  = vx * cay + rz * say
            rz2 = -vx * say + rz * cay
            d   = rz2 + 3.5
            pts.append((int(rx / d * 18 + PANEL_WIDTH  // 2),
                        int(ry / d * 18 + PANEL_HEIGHT // 2)))

        for i, j in EDGES:
            _bresenham(bm, pts[i][0], pts[i][1], pts[j][0], pts[j][1])

        if frame % 40 == 0:
            ci     = (ci + 1) % len(_ANIM_COLORS)
            pal[1] = _ANIM_COLORS[ci]

        frame += 1
        time.sleep(0.05)

    return "done"


def render_animation(msg):
    """Dispatch to an animation by type field. Default duration 10s."""
    anim     = msg.get("type", "fireworks")
    duration = float(msg.get("duration", 10))
    if anim == "rainbow":   return _anim_rainbow_pulse(duration)
    if anim == "dvd":       return _anim_dvd_bounce(duration)
    if anim == "dvd_text":  return _anim_dvd_text(duration)
    if anim == "matrix":    return _anim_matrix_rain(duration)
    if anim == "plasma":    return _anim_plasma(duration)
    if anim == "fire":      return _anim_fire(duration)
    if anim == "life":      return _anim_game_of_life(duration)
    if anim == "cube":      return _anim_cube(duration)
    return _anim_fireworks(duration)


# ---------------------------------------------------------------------------
# New renderers: bitmap, word, history, countdown
# ---------------------------------------------------------------------------

def _load_bmp(path):
    """Pure-Python 8-bit indexed BMP loader — avoids adafruit_imageload C crashes.

    Supports exactly the format image_pipeline.py produces:
    8-bit indexed, uncompressed, BITMAPINFOHEADER, any size.
    Returns (displayio.Bitmap, displayio.Palette).
    """
    import gc, struct
    gc.collect()
    with open(path, "rb") as f:
        data = f.read()

    if data[0:2] != b"BM":
        raise ValueError("Not a BMP")

    pixel_offset   = struct.unpack_from("<I", data, 10)[0]
    width          = struct.unpack_from("<i", data, 18)[0]
    height_raw     = struct.unpack_from("<i", data, 22)[0]
    bpp            = struct.unpack_from("<H", data, 28)[0]
    dib_size       = struct.unpack_from("<I", data, 14)[0]

    if bpp != 8:
        raise ValueError(f"Expected 8-bit BMP, got {bpp}-bit")

    bottom_up = height_raw > 0   # positive = rows stored bottom-to-top (standard)
    height    = abs(height_raw)

    # Color table: between end of DIB header and pixel data
    ct_start  = 14 + dib_size
    n_colors  = (pixel_offset - ct_start) // 4
    n_colors  = max(1, min(n_colors, 256))

    pal = displayio.Palette(n_colors)
    for i in range(n_colors):
        o = ct_start + i * 4
        pal[i] = (data[o + 2] << 16) | (data[o + 1] << 8) | data[o]  # BGRA→RGB

    # Rows are padded to 4-byte boundaries
    stride = (width + 3) & ~3
    bm     = displayio.Bitmap(width, height, n_colors)
    for y in range(height):
        src_y      = (height - 1 - y) if bottom_up else y
        row_start  = pixel_offset + src_y * stride
        for x in range(width):
            bm[x, y] = data[row_start + x]

    gc.collect()
    return bm, pal


def render_bitmap(msg):
    """Display a pre-converted BMP file from the board filesystem."""
    path    = msg.get("path", "")
    caption = msg.get("caption", "")
    if not path:
        return "done"
    try:
        image, palette = _load_bmp(path)
        tile  = displayio.TileGrid(image, pixel_shader=palette)
        group = displayio.Group()
        group.append(tile)

        if caption:
            cap_lbl = label.Label(terminalio.FONT, text=caption[:10], color=0xFFFFFF)
            cap_lbl.x = 1
            cap_lbl.y = PANEL_HEIGHT - 5
            group.append(cap_lbl)

        _display.root_group = group
        return _hold(8)
    except Exception as e:
        print(f"render_bitmap error: {e}")
        return "done"


def render_word(msg):
    """Show word of the day: word on top, scrolling definition below."""
    word  = msg.get("word", "")
    pos   = msg.get("pos", "")
    defn  = msg.get("definition", "")
    bg    = 0x000A10

    # Line 1: word (highlighted)
    word_lbl = label.Label(terminalio.FONT, text=word[:10].upper(), color=0x00CCFF)
    word_lbl.x = 2
    word_lbl.y = 6

    # Line 2: part of speech
    pos_lbl = label.Label(terminalio.FONT, text=pos[:12] if pos else "", color=0x888888)
    pos_lbl.x = 2
    pos_lbl.y = 15

    group = displayio.Group()
    group.append(_bg(bg))
    group.append(word_lbl)
    group.append(pos_lbl)
    _display.root_group = group
    result = _hold(3)
    if result and result != "done":
        return result

    # Then scroll definition
    if defn:
        return _show_text(defn, 0x00CCFF, bg)
    return "done"


def render_history(msg):
    """Show a This Day in History entry: year header then scrolling event text."""
    year = msg.get("year", "")
    text = msg.get("text", "")
    bg   = 0x0A0800

    year_str = str(year) if year else ""
    header   = f"In {year_str}:" if year_str else "On this day:"

    # Show year header briefly
    result = _show_text(header, 0xFFAA00, bg, hold_secs=2)
    if result and result != "done":
        return result

    # Then scroll the event text
    return _show_text(text, 0xFFAA00, bg)


def render_countdown(msg):
    """Live DD:HH:MM:SS countdown with narrow custom colons, updates every second."""
    name        = msg.get("name", "Event")
    target_date = msg.get("target_date", "")
    bg          = 0x080010

    # Compute target time using time.mktime.
    # tm_yday must be valid (1-366) — CircuitPython rejects 0 unlike CPython.
    target_secs = 0
    if len(target_date) == 10:
        try:
            yr = int(target_date[0:4])
            mo = int(target_date[5:7])
            dy = int(target_date[8:10])
            _dim = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
            if (yr % 4 == 0 and yr % 100 != 0) or (yr % 400 == 0):
                _dim[2] = 29
            yday = sum(_dim[1:mo]) + dy
            target_secs = time.mktime(time.struct_time((yr, mo, dy, 0, 0, 0, 0, yday, 0)))
        except Exception:
            pass

    def _remaining():
        return max(0, int(target_secs - time.time())) if target_secs else 0

    def _color(d):
        if d > 30: return 0x00AAFF
        if d > 7:  return 0xFFFF00
        if d > 0:  return 0xFFAA00
        return 0xFF6600

    def _fields(secs):
        d = min(secs // 86400, 99)
        h = (secs % 86400) // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return d, h, m, s

    # Layout: 4 two-digit fields × 12px + 3 narrow colons × 3px = 57px total
    # Centered: left margin = (64 - 57) // 2 = 3
    X0  = (PANEL_WIDTH - 57) // 2   # = 3
    CY  = 22                         # vertical center of countdown row
    CTY = CY - 4                     # top-left y for colon bitmaps (8px tall)

    def _make_colon(x, color):
        """3×8 bitmap with two 1×2 dot blocks — visually a narrow colon."""
        bm  = displayio.Bitmap(3, 8, 2)
        pal = displayio.Palette(2)
        pal[0] = 0x000000
        pal[1] = color
        bm[1, 2] = 1;  bm[1, 3] = 1   # top dot
        bm[1, 5] = 1;  bm[1, 6] = 1   # bottom dot
        return displayio.TileGrid(bm, pixel_shader=pal, x=x, y=CTY), pal

    secs = _remaining()
    d, h, m, s = _fields(secs)
    col = _color(d)

    lbl_d = label.Label(terminalio.FONT, text=f"{d:02d}", color=col, x=X0,      y=CY)
    lbl_h = label.Label(terminalio.FONT, text=f"{h:02d}", color=col, x=X0 + 15, y=CY)
    lbl_m = label.Label(terminalio.FONT, text=f"{m:02d}", color=col, x=X0 + 30, y=CY)
    lbl_s = label.Label(terminalio.FONT, text=f"{s:02d}", color=col, x=X0 + 45, y=CY)
    tg1, pal1 = _make_colon(X0 + 12, col)
    tg2, pal2 = _make_colon(X0 + 27, col)
    tg3, pal3 = _make_colon(X0 + 42, col)

    # Name label — scroll by moving label.x (NOT a Group, Groups crash on negative x)
    name_lbl = label.Label(terminalio.FONT, text=name, color=0xCCCCCC)
    name_w   = len(name) * 6
    name_lbl.x = max(0, (PANEL_WIDTH - name_w) // 2) if name_w <= PANEL_WIDTH else PANEL_WIDTH
    name_lbl.y = 6

    group = displayio.Group()
    group.append(_bg(bg))
    group.append(name_lbl)
    group.append(lbl_d)
    group.append(tg1)
    group.append(lbl_h)
    group.append(tg2)
    group.append(lbl_m)
    group.append(tg3)
    group.append(lbl_s)
    _display.root_group = group

    def _update():
        nonlocal col
        secs = _remaining()
        d, h, m, s = _fields(secs)
        new_col = _color(d)
        lbl_d.text = f"{d:02d}"
        lbl_h.text = f"{h:02d}"
        lbl_m.text = f"{m:02d}"
        lbl_s.text = f"{s:02d}"
        if new_col != col:
            col = new_col
            for lb in (lbl_d, lbl_h, lbl_m, lbl_s):
                lb.color = new_col
            for pal in (pal1, pal2, pal3):
                pal[1] = new_col

    prev = -1
    end  = time.monotonic() + 30

    if name_w <= PANEL_WIDTH:
        # Name fits — hold static with live countdown
        while time.monotonic() < end:
            action = _poll()
            if action:
                return action
            cur = int(time.monotonic())
            if cur != prev:
                prev = cur
                _update()
            time.sleep(0.05)
    else:
        # Name too long — scroll continuously for the full hold period
        name_lbl.x = PANEL_WIDTH
        while time.monotonic() < end:
            action = _poll()
            if action:
                return action
            cur = int(time.monotonic())
            if cur != prev:
                prev = cur
                _update()
            name_lbl.x -= 1
            if name_lbl.x < -name_w:
                name_lbl.x = PANEL_WIDTH   # wrap back to start
            time.sleep(SCROLL_DELAY)
    return "done"


def render_quote(msg):
    """Show a Michael Scott quote in coral orange."""
    text  = msg.get("text", "")
    color = 0xFF6633
    bg    = 0x0A0300

    return _show_text(text, color, bg)


def render_jeopardy(msg):
    """Show a Jeopardy clue: category + value → clue → answer reveal."""
    clue     = msg.get("clue", "")
    answer   = msg.get("answer", "")
    category = msg.get("jeopardy_category", "JEOPARDY")
    value    = msg.get("value", 0)

    # Jeopardy blue palette
    bg          = 0x000033
    cat_color   = 0xFFCC00   # gold
    val_color   = 0x888866   # dimmed gold
    clue_color  = 0xFFFFFF   # white
    ans_color   = 0x44FF88   # green

    # ── Phase 1: category name + dollar value ─────────────────────────────────
    # Category on 1-2 centred lines when it fits; otherwise scroll it across the
    # top while the value stays put underneath.
    chars    = PANEL_WIDTH // 6
    cat_lines = _wrap(category, chars)
    fits     = len(cat_lines) <= 2 and all(len(l) <= chars for l in cat_lines)
    val_text = f"${value}" if value else "JEOPARDY"

    grp = displayio.Group()
    grp.append(_bg(bg))

    if fits:
        cat_ys = [9] if len(cat_lines) == 1 else [6, 15]
        for line, y in zip(cat_lines, cat_ys):
            lbl = label.Label(terminalio.FONT, text=line, color=cat_color)
            lbl.x = (PANEL_WIDTH - len(line) * 6) // 2
            lbl.y = y
            grp.append(lbl)
        val_y = 22 if len(cat_lines) == 1 else 25
    else:
        cat_lbl = label.Label(terminalio.FONT, text=category, color=cat_color)
        cat_lbl.x = PANEL_WIDTH
        cat_lbl.y = 9
        grp.append(cat_lbl)
        val_y = 22

    val_lbl = label.Label(terminalio.FONT, text=val_text, color=val_color)
    val_lbl.x = max(0, (PANEL_WIDTH - len(val_text) * 6) // 2)
    val_lbl.y = val_y
    grp.append(val_lbl)
    _display.root_group = grp

    if fits:
        result = _hold(2.5)
    else:
        result = _scroll_label(cat_lbl)   # one full pass so the whole name is read
    if result and result != "done":
        return result

    # ── Phase 2: the clue ─────────────────────────────────────────────────────
    result = _show_text(clue, clue_color, bg, hold_secs=3)
    if result and result != "done":
        return result

    # ── Phase 3: animated "..." pause ────────────────────────────────────────
    for dots in (".", "..", "..."):
        dot_lbl = label.Label(terminalio.FONT, text=dots, color=cat_color)
        dot_lbl.x = (PANEL_WIDTH - len(dots) * 6) // 2
        dot_lbl.y = PANEL_HEIGHT // 2 - 3
        g = displayio.Group()
        g.append(_bg(bg))
        g.append(dot_lbl)
        _display.root_group = g
        result = _hold(0.5)
        if result and result != "done":
            return result

    # ── Phase 4: answer reveal ────────────────────────────────────────────────
    return _show_text(f"A: {answer}", ans_color, bg, hold_secs=4)
