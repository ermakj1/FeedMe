# Ideas

Things we might build later. Not prioritized. Bugs and concrete work items live
in GitHub issues.

## Already built

Feeds: weather, stock, jokes, BBC headlines, NASA image, word of the day,
on-this-day history, countdowns (with yearly events), Michael Scott quotes,
Jeopardy (incl. Celebrity), Hermes bridge.
Animations: fireworks, rainbow, DVD bounce, DVD text, matrix rain, fire,
plasma, life, cube.
Integrations: Home Assistant (motion webhook), Director web UI, `/interrupt`.

## Planned — camera (XIAO Vision AI camera kit, arrived Sep 2026)

Grove Vision AI V2 + XIAO ESP32-C3, living in or near the panel frame.

- **True/false trivia with gestures** — panel shows a statement, player answers
  with thumbs up/down (or left/right arm). Panel glows green/red on the answer,
  falls back to revealing the answer after a timeout. Questions from Open
  Trivia DB. Needs the Director to call the panel's `/interrupt`-style
  immediate display rather than the queue.
- **Face recognition on the Pi** — the camera detects a person and sends the
  frame to the Pi; the Pi (`face_recognition`/dlib) decides who it is.
  - `faces/<name>/` holds known faces; unknown faces are saved to
    `faces/unknown/` so they can be named later (no photos uploaded anywhere).
  - Director UI page to review unknown faces and assign names; the service
    reloads known faces automatically.
  - Use it for personalised messages ("Hi Mila — 3 days to your birthday").

## Feed ideas

- **Sports scores** — live game scores for teams you follow (NHL, NFL, NBA all
  have free APIs). Flash the panel on a goal/score.
- **Package tracking** — "UPS out for delivery" type alerts
- **Calendar feed** — pull from Google Calendar automatically
- **Crypto prices** — same pattern as stock feed
- **Home alerts** — doorbell, garage door, etc. triggered by Home Assistant

## Display ideas

- **Gradient text** — headline text that fades from white to the category color
  (complex on CircuitPython, requires per-character color)
- **Pixel art icons** for news, calendar, text categories (currently only
  weather has icons)
- **Smoother scrolling** — subpixel or variable speed based on text length
- **AM/PM indicator** — small colored dot on the clock instead of text
- **Weekday vs weekend clock color** — different palette on weekends

## Animation ideas

- **Starfield** — white dots flying toward viewer (warp speed)
- **Snake** — simple snake game playing itself (auto-pilot)
- **Confetti** — multicolor squares falling and tumbling
- **Lava lamp** — slow blobs of color rising and merging
- **Pac-Man chase** — tiny Pac-Man being chased across the panel

## Ops ideas

- **Smart outlet power-cycle** — TP-Link outlet via Home Assistant, driven by
  `scripts/board_health.py` (may be unnecessary now that the panel has a
  hardware watchdog).
- **Static DHCP lease / friendly hostname** for the panel so the Director does
  not depend on mDNS.
