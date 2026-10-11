# Lane B: the web UI and the shell

**Owner:** the frontend agent. Claim each task on the [Tracker](https://github.com/NeoKazuya/auto-daybook/issues/1) under its ID.

**Owns:** `src/daybook/web/static/` (the whole web UI) and `src/daybook/shell/` (the tray icon and the full-screen overlay). Nothing else. See the ownership table in [README.md](README.md#ownership-by-path).

**Spec:** [ARCHITECTURE.md](../ARCHITECTURE.md) §8 (the UI), §6.10 (the shell), and §6.9 (routes and security). The data contract is `src/daybook/web/wire.py`. [demo.md](../demo.md) shows what the audience will see.

**Starts after:** [Contracts (C1)](contracts.md) is merged. From then on, `daybook run --mock-api --port 878x` gives a working API with example data and a live event stream, so nothing in this lane waits on Lane A until B7.

## Constraints

- **Plain HTML, CSS, and ES modules, with no build step** (ARCHITECTURE §6.9). One page with tabs, each titled `Daybook · <tab>`. Capture excludes that title, so the UI never ends up in the notes.
- **Data only through the API.** Use the HTTP routes and the SSE stream at `/api/events?client=ui`, shaped by `wire.py`. Don't import backend modules into the shell either; it uses HTTP and SSE with `client=shell`.
- **Send the token.** Every mutating request sends `X-Daybook-Token`, which is embedded in the served HTML.
- **Readable on a projector.** The demo runs live on a projector, so use large type, strong contrast, and nothing important in small grey text.
- **Plain wording.** UI copy describes what happened and what the user can do. Label inferred intent as *inferred*.

## Tasks

| ID | Task | Spec | Done when |
|---|---|---|---|
| B1 | **Page frame and timeline.** Tabs, the footer, and the timeline: segment cards (title, kind, inferred intent, active time) that expand to observations; capture spans as labeled gaps (paused, locked, away, private, overlay); inline edit and delete for segments and observations; live updates over SSE | §8 Timeline, footer | Against `--mock-api`: scripted events appear without reload; edits and deletes show immediately; a delete returns the right confirmation copy ("Delete segment and its source notes") |
| B2 | **Chat.** A message box; recall answers with citations that scroll to and highlight the cited segments; a skill draft renders as a review card (see B3) with Approve & enable; `/ask` and `/skill` prefixes; a busy state while the model works | §8 Chat, §6.8 | The mock's recall and draft replies both render; citations jump to the timeline; the busy state shows during the request |
| B3 | **Skills.** The skill list with status (active, needs review, disabled, invalid). The review card shows the goal, the signals as plain questions, the rules in words ("When *in a meeting* becomes *false*: show an overlay…"), the permissions, and the raw file. Editor with live parse errors (`POST /api/skills/parse {id, text}`). Approve & enable posts the reviewed hash; Enable, Disable, Delete; a 409 (stale or needs review) reloads and explains | §8 Skills, [skills.md](../skills.md) | Every status renders; a stale approval shows the new text; parse errors point at the line or field |
| B4 | **Privacy page and footer.** Pause toggle, exclusions editor (app and title patterns), "forget last 15 minutes", "forget everything" with confirmation, "deletion pending" state, "screenshots stored: none". Footer: capture state, now playing, model name, reachability, calls per minute | §8 Privacy, §7 | All controls round-trip against the mock; the footer updates from events |
| B5 | **Shell: overlay and tray.** One pre-created Qt Quick (QML) window: frameless, always on top, full screen, reused so it appears in under 300 ms. Renders title, body, countdown, and dismiss; `calm` and `alert` styles; translucent dark backdrop and fade-in; `Esc` dismisses. Tray icon showing capture state, with Pause/Resume, Open Daybook, and Quit. Resyncs from `GET /api/status` on every connect. WM_CLASS `daybook-shell` | §6.10 | Against `--mock-api`: the scripted overlay event shows and hides it, and it can be dismissed. On the real `:1` screen (holding the capture lock): it covers a maximized window, `Esc` works, the countdown auto-hides it, and the tray icon is visible and pauses capture |
| B6 | **Design pass.** One consistent visual language across the web UI and the overlay; projector readability; empty states (no notes yet, no skills, model offline); loading and error states for every request | §8, [demo.md](../demo.md) | Walk through the demo script on the mock: every screen is legible from across a room, and no state shows raw JSON or a blank page |
| B7 | **Real backend.** Run the same UI against a `--replay` instance, then the real one, as Lane A's handlers land (A11). Fix UI assumptions the mock hid; send contract gaps to Lane A as described in [README.md](README.md#how-the-lanes-meet) | §11.2 items 1, 4, 5, 6 | Every page works on a replay instance with no console errors; the demo script's UI steps work on the real instance |

B1–B6 can run in parallel once C1 merges. B5's real-screen checks need the capture lock; the shell's process management comes with A10, and until then start it by hand with `python -m daybook.shell --port 878x`. B7 follows Lane A's progress.

## Optional pickup

If Lane A hasn't claimed **A9 (chat)**, Lane B may claim it. That covers routing, the recall prompt, and drafting, so the chat UX can be tuned end to end. It's then a Lane A path owned by Lane B for that task: follow §6.8, use `ScriptedGateway` for tests, and stay within the model budget in ARCHITECTURE §5. Post the claim on the Tracker so the Codex wave skips it.

## Test setup

- `daybook run --mock-api --port 878x` with `DAYBOOK_HOME=~/.daybook-b-<agent>`, then open `http://127.0.0.1:878x` in the GX10's browser. Or forward the port over SSH and view it from your laptop: `ssh -L 8780:127.0.0.1:8780 asus@1s6m`.
- For real-screen shell tests: hold the capture lock (the wrapper in [gx10-unattended.md](../gx10-unattended.md)) so nothing is capturing while the overlay is up.
- Screenshots for PRs come from the browser or the overlay on a mock instance. No real notes appear in them.

## Requests to Lane A

When the UI needs data the API doesn't provide:

1. Open a shared-contract PR that adds the field or route to `wire.py`, with a mock example.
2. Post `Task: (A) implement <route/field> for real — requested by Lane B (#<pr>)` on the Tracker.
3. Keep building against the mock. Don't work around a missing field by computing it in the browser from other data, because that creates a second source of truth.
