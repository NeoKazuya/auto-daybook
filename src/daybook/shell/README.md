# Shell: the overlay (and later, the tray)

Lane B's desktop process (ARCHITECTURE §6.10). This note records the overlay design agreed for Daybook; the tray comes after.

## What the backend already does

`daybook.act.Overlay` owns all overlay state. A skill's `overlay.show` step records the request, saves it to `$DAYBOOK_HOME/overlay.json`, arms the countdown, and publishes `OverlayShown(id, request)`. Dismissing, or the countdown ending, publishes `OverlayHidden(id)`. `overlay.show` fails with `shell_disconnected` when no shell is listening on `/api/events?client=shell`.

The shell is only a view of that state:

1. Listen to `/api/events?client=shell`.
2. On every connect and reconnect, read `GET /api/status` first and show or hide to match. A missed event must never leave an overlay on screen with nothing to dismiss it.
3. `OverlayShown` shows the window; `OverlayHidden` hides it.
4. Dismissing hides the window at once and sends `POST /api/overlay/{id}/dismiss`, retried once on failure.
5. The countdown runs from `expires_at`, so a restarted shell shows the right time left. At zero the shell waits for `OverlayHidden`, and hides itself if it hasn't arrived within about 2 s.

Auth: the token comes from `DAYBOOK_TOKEN`, or from `$DAYBOOK_HOME/token` when the shell is started by hand. Requests send the `http://127.0.0.1:<port>` Origin.

## Two layouts, chosen by the skill

Each skill that shows an overlay picks its layout. There is no global setting, and `alert` doesn't override the choice.

### Full screen (default)

- One frameless, always-on-top, full-screen Qt Quick window on the main display. It's created at startup and kept hidden, so showing it takes well under 300 ms.
- **Full-bleed:** no card. The desk colour at about 90% opacity covers the screen. Content is left-aligned:
  - a small mono label with the skill's name;
  - a large Literata title (about 64 px on a 1080p screen);
  - the body;
  - a large mono countdown;
  - the dismiss button with the skill's label (`dismiss`), plus an "or press Esc" hint.
- Takes keyboard focus; Esc dismisses.
- `calm` uses the quiet palette. `alert` uses ember for the label, countdown and button.
- Fades in over 180 ms.
- Capture reports the `overlay` state while it's up (already on `main`, matched by the shell's PID or WM_CLASS `daybook-shell`).

### Speech bubble (casual)

- **Placement:** a bubble in the top-right quarter of the main display, with its tail pointing to a small Daybook mark in the upper-right corner, as if Daybook were talking from off-screen.
- **Never interrupts:**
  - it doesn't take keyboard focus;
  - the window is only the size of the bubble, so clicks elsewhere reach the apps underneath;
  - Esc isn't bound;
  - its buttons or the countdown dismiss it.
- **Contents:**
  - a "daybook" label;
  - the body, written by the skill in an ultra-casual voice ("Break time! You've been focusing really hard bro, make sure to take a break and get some water.");
  - a line for any action the skill is taking ("Opening Canvas: Calc HW 6");
  - "goes away in m:ss";
  - two buttons: a secondary one ("Later", "Not now") and the skill's `dismiss` label as the primary ("Bet", "Ok fine"). Both dismiss for now.
- **Capture keeps running** while the bubble is up, and the bubble is left out of what gets captured.

Text in both layouts is shown as plain text, never markup, since chat-made skills write it.

## Changes needed outside the shell (shared contracts / Lane A)

- **Action catalog, `overlay.show`:** a new parameter `layout: fullscreen | bubble`, default `fullscreen`. `OverlayRequest` in `act` and `wire.py` gets the same field. For a bubble, the skill's `body` *is* the casual text, so no second text field is needed.
- **skills.md:** document `layout`, and that bubble text is written in a casual voice. The chat skill generator's prompt should do the same.
- **Sense (capture):**
  - A bubble window must not put capture into the `overlay` state.
  - It should be kept out of the frame the model sees, for example by masking the bubble's rectangle before the frame is encoded.
  - Something has to tell the full-screen window and the bubble apart: a different window class (`daybook-bubble`), or the overlay's `layout`.
- **Web UI (Lane B, this repo):** the skills review card's rule wording should name the layout, as in "show a speech bubble…" or "show a full-screen message…".

## Build order

1. A pure-Python controller (events and status in, "what to show, time left" out) with unit tests in `tests/shell/`.
2. An event-stream client in a background thread that hands events to Qt.
3. The full-screen QML layout, tried on macOS against `daybook run --mock-api` (its `/api/overlay/test` works once a shell is connected).
4. The bubble QML layout, once `layout` exists in the contract.
5. GX10 checks, holding the capture lock:
   - it appears in under 300 ms;
   - Esc and the button both dismiss the full-screen layout;
   - the countdown hides it automatically;
   - it shows again after a shell or daemon restart;
   - the bubble never takes focus, and capture keeps running while it's up.

## Known risks

- **Focus:** GNOME may refuse to focus a new window, which breaks Esc on the full-screen layout. The fallback is a keyboard grab while it's visible.
- **Fonts:** checked: Qt loads the web UI's bundled WOFF2 files (`web/static/fonts`) directly, so the overlay uses the same Literata and JetBrains Mono. Without them it falls back to Georgia and Menlo.
- **Auto-start:** starting the shell from `daybook run` is `app.py` (Lane A). Until then: `python -m daybook.shell --port <port>`.
