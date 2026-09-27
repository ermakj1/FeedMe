# LED Display

A 64×32 RGB LED matrix display system with two components: **Director** (Pi) and **Panel** (board).

---

## Components

### Panel
The LED matrix itself. Runs CircuitPython on an Adafruit MatrixPortal S3. Accepts messages over WiFi via HTTP, maintains a queue, and renders each message on the display. Knows nothing about where messages come from — it just displays them.

- Hostname: `matrixportal.local` (port 8080)
- Code lives in `board/`

### Director
The Raspberry Pi service that manages all the feeds and sends content to the Panel. Runs in Docker. Provides a web management UI and handles scheduling, config, and feed health.

- Web UI: `http://<pi-ip>:8099`
- Code: `manage.py` + `feeds/`

---

## Hardware

| Part | Details |
|------|---------|
| [Adafruit MatrixPortal S3](https://www.adafruit.com/product/5778) | ESP32-S3 controller |
| 64×32 RGB LED Matrix | HUB75 interface |
| [Mini PIR Motion Sensor](https://www.adafruit.com/product/4871) | Wired to A3 |
| Raspberry Pi (any model) | Runs Director in Docker |

### PIR Wiring

| PIR pin | MatrixPortal S3 |
|---------|----------------|
| VIN     | 3.3V           |
| GND     | GND            |
| OUT     | A3             |

---

## Setup

### Panel (first time)
1. Flash CircuitPython 10.x onto the MatrixPortal S3
2. Copy `settings.toml.example` to `/Volumes/CIRCUITPY/settings.toml` and fill in WiFi credentials
3. Deploy board code: `scripts/deploy.sh` (USB auto-detected, or `--wifi`)

### Director (Pi)
```bash
git clone <repo> ~/LED_testing
cd ~/LED_testing
docker compose up -d
```

The Director web UI will be at `http://<pi-ip>:8099`.

---

## Deploying to the Panel

```bash
scripts/deploy.sh              # auto-detects USB or WiFi
scripts/deploy.sh --wifi       # force WiFi deploy
scripts/deploy.sh --with-lib   # include lib/ folder
```

WiFi deploy pushes files directly to the Panel's `/upload` endpoint and triggers a soft reload.

---

## Feeds

All feeds run on the Director (Pi) and post messages to the Panel.

| Feed | Description | Default interval |
|------|-------------|-----------------|
| `weather.py` | Current conditions + forecast from Open-Meteo | 30 min |
| `stock.py` | Stock prices from Yahoo Finance (market hours) | 5 min |
| `jokes.py` | Random jokes from JokeAPI | 30 min |
| `animations.py` | Decorative animations | 20 min |
| `nasa.py` | NASA Astronomy Picture of the Day | 24 hr |
| `wordofday.py` | Word of the day + definition | 24 hr |
| `history.py` | This day in history facts | 60 min |
| `countdown.py` | Days until configured events | 60 min |
| `quotes.py` | Michael Scott quotes | 60 min |

All feeds are configured and toggled from the Director web UI.

---

## Panel endpoints (port 8080)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Current queue as JSON |
| `/add` | POST | Add a message to the queue |
| `/delete` | POST | Delete a message by id |
| `/clear` | POST | Clear the queue |
| `/reorder` | POST | Reorder queue by id list |
| `/schema` | GET | Message format reference |
| `/log` | GET | Live log viewer |
| `/upload` | POST | Upload a file (used by deploy script) |
| `/reload` | POST | Soft-reload the Panel |
| `/wake` | POST | Wake from sleep |
| `/register` | POST | Register a motion callback URL |
| `/pir/enable` | POST | Enable motion sensor |
| `/pir/disable` | POST | Disable motion sensor |
| `/usb/enable` | POST | Enable USB drive (reboots) |
| `/usb/disable` | POST | Disable USB drive (reboots) |

---

## Message categories

POST to `http://matrixportal.local:8080/add`:

```json
{"category": "weather", "condition": "sunny", "high": 75, "low": 52, "precip": 10, "city": "Kirkland"}
{"category": "stock",   "symbol": "MSFT", "price": 415.00, "change": 0.28}
{"category": "joke",    "setup": "Why did the...", "delivery": "Because..."}
{"category": "history", "year": 1969, "text": "Apollo 11 lands on the moon"}
{"category": "word",    "word": "ephemeral", "pos": "adjective", "definition": "lasting a very short time"}
{"category": "countdown","name": "Summer", "days": 42}
{"category": "quote",   "text": "That's what she said."}
{"category": "news",    "text": "Headline here"}
{"category": "animation","type": "fireworks", "duration": 10}
{"category": "bitmap",  "path": "/nasa.bmp", "caption": "Nebula"}
```

All messages accept `ttl_minutes` (default 60).

---

## Panel behavior

- **Clock** shown when queue is empty. Color shifts with time of day.
- **Sleep** after 5 min of no PIR motion. UP button wakes, DOWN button sleeps.
- **Category headers** flash briefly before each message so you know what's coming (JOKE, NEWS, HISTORY, etc.).
- **Scroll speed** ~80 px/sec.

---

## Project structure

```
board/          Panel code (deployed to MatrixPortal)
  code.py       Main loop, HTTP server, queue management
  renderers.py  One render function per message category
  ui.html       Panel's built-in web UI

feeds/          Director feed scripts (run on Pi)
  config.json   Shared config (board URL, intervals, enabled flags)
  util.py       Shared helpers (single_instance, is_network_error)
  weather.py
  stock.py
  jokes.py
  animations.py
  nasa.py
  wordofday.py
  history.py
  countdown.py
  quotes.py
  image_pipeline.py

scripts/
  deploy.sh         Deploy board/ files to Panel (USB or WiFi)
  watchdog.py       Standalone feed runner (alternative to Docker)
  led-feeds.service Systemd unit for watchdog (alternative to Docker)

manage.py       Director entry point
ui_manage.html  Director web UI
Dockerfile      Director container definition
docker-compose.yml
```

---

## Known-good baseline (rollback point)

As of **2026-09-27** the project was working pretty well overall, with the
occasional random Panel crash/freeze over the previous few months (mostly
suspected WiFi drops in the garage and unhandled exceptions in the main loop).

That state is tagged **`baseline-2026-09-27`** (commit `0832a69`). A batch of
stability/cleanup changes was started after this point (see GitHub issues
opened on 2026-09-27). If those changes make things worse, roll back with:

```bash
git checkout baseline-2026-09-27 -- board/ manage.py feeds/ ui_manage.html
```

Then redeploy the Panel (`scripts/deploy.sh`) and the Director
(`scripts/update-director.sh` on the Pi after committing/pushing).
