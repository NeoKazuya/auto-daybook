# Shell: the overlay and the tray

Lane B's desktop process (ARCHITECTURE §6.10). This note records the overlay design agreed for Daybook, and the tray.

## What the backend already does

`daybook.act.Overlay` owns all overlay state. A skill's `overlay.show` step records the request, saves it to `$DAYBOOK_HOME/overlay.json`, arms the countdown, and publishes `OverlayShown(id, request)`. Dismissing, or the countdown ending, publishes `OverlayHidden(id)`. `overlay.show` fails with `shell_disconnected` when no shell is listening on `/api/events?client=shell`.

The shell is only a view of that state:

1. Listen to `/api/events?client=shell`.
2. On every connect and reconnect, read `GET /api/status` first and show or hide to match. A missed event must never leave an overlay on screen with nothing to dismiss it.
3. `OverlayShown` shows the window; `OverlayHidden` hides it.
4. Dismissing hides the window at once and sends `POST /api/overlay/{id}/dismiss`, retried once on failure.
5. The countdown runs from `expires_at`, so a restarted shell shows the right time left. At zero the shell waits for `OverlayHidden`, and hides itself if it hasn't arrived within about 2 s.

Auth: the token comes from `DAYBOOK_TOKEN`, or from `$DAYBOOK_HOME/token` when the shell is started by hand. Requests send the `http://127.0.0.1:<port>` Origin.

`daybookd` starts the shell (`python -m daybook.shell --port <port>`) and restarts it if it exits, except when it exits with `wire.SHELL_QUIT_EXIT_CODE`: that's the tray's Quit, and `daybookd` shuts down.

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

- **Placement:** a bubble in the top-right corner of the main display, with its tail pointing to a small Daybook mark in the upper-right corner, as if Daybook were talking from off-screen.
- **Never interrupts:**
  - it doesn't take keyboard focus (`WindowDoesNotAcceptFocus`, shown without activating);
  - the window is only the size of the bubble, so clicks elsewhere reach the apps underneath;
  - Esc isn't bound;
  - its buttons or the countdown dismiss it.
- **Contents:**
  - a "daybook" label;
  - the body, written by the skill in an ultra-casual voice ("Break time! You've been focusing really hard bro, make sure to take a break and get some water."); the title if the body is empty;
  - "goes away in m:ss";
  - two buttons: "Later" and the skill's `dismiss` label as the primary ("Bet", "Ok fine"). Both dismiss.
- **Capture keeps running** while the bubble is up, and the bubble is left out of what gets captured: Sense blacks out every window with WM_CLASS `daybook-bubble` in RAM before encoding. Qt gives all of a process's windows one class, so the shell sets the bubble's class itself through libX11 before showing it. The bubble is a managed tool window, never override-redirect, so Sense can see it. If the class can't be set on X11, the request is shown full screen instead, which pauses capture.

Text in both layouts is shown as plain text, never markup, since chat-made skills write it.

## Tray

A StatusNotifierItem through `QSystemTrayIcon` (GNOME needs the AppIndicator extension; see setup.md).

- **Icon and tooltip:** the capture state in the web UI's words: Recording, Paused, Private window, Screen locked, Away, Overlay up, or "Daybook isn't running" while the event stream is down. The pause switch wins over the capture state, so a pause shows at once.
- **Menu:**
  - the same status line;
  - Pause / Resume: reads `GET /api/privacy` and `PUT`s it back with only `paused` changed;
  - Open Daybook: `xdg-open http://127.0.0.1:<port>`, the port the shell was started with;
  - Quit Daybook: exits with `wire.SHELL_QUIT_EXIT_CODE`, which stops `daybookd`, music, and capture.
- It reads privacy on every connect and every `CaptureStateChanged`, so a pause from the web UI shows within one capture poll.

## GX10 checks

Holding the capture lock, on the real `:1` display:

- the full-screen overlay appears in under 300 ms, Esc and the button both dismiss it, and the countdown hides it;
- it shows again after a shell or daemon restart;
- `xprop -id <bubble window>` shows `WM_CLASS "daybook-bubble"` and `WM_HINTS` input false; the bubble never takes focus, and capture keeps running with the bubble masked;
- the tray icon is visible, reflects a pause within 5 s, and pauses capture from its menu; Quit stops `daybookd`.

## Known risks

- **Focus:** GNOME may refuse to focus a new window, which breaks Esc on the full-screen layout. The fallback is a keyboard grab while it's visible.
- **Tray:** without the AppIndicator extension GNOME has no tray, and the shell logs `tray_unavailable`.
- **Fonts:** checked: Qt loads the web UI's bundled WOFF2 files (`web/static/fonts`) directly, so the overlay uses the same Literata and JetBrains Mono. Without them it falls back to Georgia and Menlo.
- **Started by hand** (for development): `DAYBOOK_HOME=… python -m daybook.shell --port <port>`.
