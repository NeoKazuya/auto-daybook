# Lane B: build the UI and the shell

**Goal:** build the complete user-facing side of Daybook, start to end:

- the web app (timeline, chat, skills, privacy, and footer)
- the shell process (tray icon and the reusable full-screen overlay)

At the end, someone who has never seen Daybook can run the [demo](../demo.md) on the GX10, on a projector, and every screen is clear, live, and polished.

**Owner:** the frontend agent. Claim the lane once on the [Tracker](https://github.com/NeoKazuya/auto-daybook/issues/1).

**Owns:** `src/daybook/web/static/` and `src/daybook/shell/`, and nothing else (see [README.md](README.md#ownership-by-path)).

**Spec:**

- [ARCHITECTURE.md](../ARCHITECTURE.md) §8 for the UI, §6.10 for the shell, and §6.9 for routes and security.
- `src/daybook/web/wire.py` is the data contract.
- [skills.md](../skills.md) defines what the skill review card shows.

**Starts when:** Lane A's first PR (the shared skeleton) merges. From then on, `daybook run --mock-api` gives a working API with example data and a live event stream. Nothing in this lane waits on the backend until it's time to switch to it.

## Constraints

- **No build step.** Plain HTML, CSS, and ES modules (§6.9). One page with tabs, each titled `Daybook · <tab>`. Capture excludes that title, so the UI never shows up in the notes.
- **Data only through the API.** The UI and the shell talk only to the HTTP routes and the SSE stream (`/api/events?client=ui` or `client=shell`), as shaped by `wire.py`. They never import backend modules. This applies to the UI and the shell only: if Lane B takes chat, chat uses the Python interfaces §6.8 names (`Portal`, the journal epoch, `ModelGateway`, and the skill generator), like any backend module.
- **Token on every change.** Every mutating request sends `X-Daybook-Token`, which is embedded in the served HTML.
- **Projector-readable.** Use large type and strong contrast. Nothing important goes in small grey text.
- **Plain copy.** Say what happened and what the user can do. Label inferred intent as *inferred*.

## Scope: what exists when Lane B is done

This is a checklist, not an order.

- [ ] **Timeline.** Segment cards show the title, kind, inferred intent, and active time, and expand to their observations. Capture spans appear as labeled gaps: paused, locked, away, private, overlay. Segments and observations can be edited and deleted inline, with the right confirmation copy ("Delete segment and its source notes"). Updates arrive live over SSE.
- [ ] **Chat.** Recall answers show citations that jump to and highlight timeline segments. A skill draft renders as the review card with Approve & enable. The `/ask` and `/skill` prefixes work. A busy state shows while the model works.
- [ ] **Skills.**
  - The list shows each skill's status: active, needs review, disabled, or invalid.
  - The review card shows the goal, the signals as plain questions, the rules in words ("When *in a meeting* becomes *false*: show an overlay…"), the permissions, and the raw file.
  - The editor shows live parse errors.
  - Approve & enable posts the reviewed hash. Enable, Disable, and Delete work, and a 409 reloads and explains.
- [ ] **Privacy and footer.**
  - Privacy page: pause toggle, exclusions editor, "forget last 15 minutes", "forget everything" with confirmation, the "deletion pending" state, and "screenshots stored: none".
  - Footer: capture state, now playing, model name and reachability, and calls per minute.
- [ ] **Overlay.**
  - One pre-created Qt Quick (QML) window: frameless, always on top, and full screen, appearing in under 300 ms.
  - It shows the title, body, countdown, and dismiss button, in `calm` and `alert` styles, on a translucent backdrop with a fade-in.
  - `Esc` dismisses it.
  - WM_CLASS is `daybook-shell`.
  - On every connect, it resyncs from `GET /api/status`.
- [ ] **Tray.** The icon shows the capture state. Its menu has Pause/Resume, Open Daybook, and Quit.
- [ ] **States.** Empty states (no notes yet, no skills, model offline), plus loading and error states for every request. No raw JSON and no blank screens.
- [ ] **One visual language** across the web UI and the overlay, readable from across a room.
- [ ] **Running on the real backend.** The same UI works against a `--replay` instance, and then the real instance, as Lane A's routes go live. The UI parts of ARCHITECTURE §11.2 items 1, 4, 5, and 6 pass on the GX10.

## Optional: chat

If Lane A hasn't claimed **chat** (routing, recall, skill drafting; §6.8), Lane B may take it, to tune the chat experience end to end. Post the claim on the Tracker first. Follow §6.8, use `ScriptedGateway` for tests, and stay within the model budget in §5.

## Working setup

- `daybook run --mock-api --port 8780–8789` with `DAYBOOK_HOME=~/.daybook-b-<agent>`. Open it in the GX10's browser, or forward the port: `ssh -L 8780:127.0.0.1:8780 asus@1s6m`.
- Until Lane A wires up the shell's process management, start the shell by hand with `DAYBOOK_HOME=~/.daybook-b-<agent> python -m daybook.shell --port 878x`. It reads the token the running instance wrote to `$DAYBOOK_HOME/token`.
- For real-screen overlay and tray checks, hold the capture lock (see [gx10-unattended.md](../gx10-unattended.md)).
- Take PR screenshots from a mock instance, so no real notes appear in them.

## When the UI needs data the API doesn't have

1. Open a shared-contract PR that adds the field or route to `wire.py`, with a mock example.
2. Post `Task: (Lane A) implement <route/field> for real — requested by Lane B (#<pr>)` on the Tracker.
3. Keep building against the mock. Don't derive the missing data in the browser; that creates a second source of truth.
