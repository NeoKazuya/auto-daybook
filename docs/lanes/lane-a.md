# Lane A: the core

**Owner:** the Codex wave. One orchestrating agent hands tasks to worker agents, each in its own worktree, and every task is claimed on the [Tracker](https://github.com/NeoKazuya/auto-daybook/issues/1) under its ID.

**Owns:** everything except the web UI's static files and the shell. See the ownership table in [README.md](README.md#ownership-by-path).

**Spec:** each task's full scope and "Done when" checks are in the [ARCHITECTURE.md](../ARCHITECTURE.md) section it names. This brief adds only the order, the test setup, and what to watch for. If this brief and ARCHITECTURE disagree, ARCHITECTURE wins; fix this brief.

**Starts after:** [Contracts (C1)](contracts.md) is merged.

## Tasks

| ID | Task | Spec | Paths | Needs first |
|---|---|---|---|---|
| A1 | Notes store and event bus | §6.1, §6.2, §4 | `journal/`, `events.py` | — |
| A2 | Model gateway | §6.3, §5 | `model/` | — |
| A3 | Skills: parser, trigger evaluator, `SkillBook`, generator | §6.6, [skills.md](../skills.md) | `skills/`, `skills/builtin/` | — |
| A4 | Actions: catalog, music player and library, overlay state, desktop backends | §6.7 | `act/` | A5 for the real-music checks |
| A5 | Music library: 20–30 CC-licensed tracks with `library.toml` on the GX10 | [setup.md](../setup.md) §5, [skills.md](../skills.md#music-library) | `~/Music/daybook/` (not in the repo) | — |
| A6 | Fixture recorder and the four replay scenarios | §10 | `scripts/record_fixture.py`, `fixtures/replay/` | — |
| A7 | Screen capture and privacy gate | §6.4, §7 | `sense/` | A6 for replay tests beyond the C1 example |
| A8 | The tick | §6.5 | `mind/` | A1, A2, A3 for integration (fakes before that) |
| A9 | Chat: routing, recall, skill drafting | §6.8 | `chat/` | A1, A2, A3 for integration. **Lane B may claim this instead** |
| A10 | App wiring, CLI, child processes, capture lock, startup service | §6.11, §9 | `app.py` | all of A1–A9 for full wiring |
| A11 | Real web route handlers (replacing the mock backend per route) | §6.9 | `web/` except `static/` and `wire.py` | the module each route calls |
| A12 | cua trial (at most one hour) | §11.1 | `act/_desktop.py`, decisions.md | A4 |
| A13 | Integration, prompt tuning, and end-to-end acceptance | §11.2, §6.5 quality gate | any Lane A path | everything above |

A1, A2, A3, A4, A5, A6, and A7 can all start as soon as C1 merges. A8 and A9 can start too, against `ScriptedGateway` and fakes, then integrate as A1–A3 land. "Needs first" only marks a real dependency. It doesn't order the rest.

## Test setup

- **Without the screen or the model:** `ReplayScreen` with the fixtures, `ScriptedGateway`, a temporary journal, and the fake `DesktopControl`, music, and overlay backends. Most tests run this way.
- **Development instances:** `daybook run --replay fixtures/replay/<scenario> --port 877x` with `DAYBOOK_HOME=~/.daybook-a-<agent>`. They never touch the real screen or speakers.
- **Real model:** Ollama `qwen3.6:35b` at `http://127.0.0.1:11434/v1` (already installed and verified with a real screenshot; see [gx10-unattended.md](../gx10-unattended.md)). Only one large model fits in memory, so don't start another.
- **Real screen and effects** (A4 music, A7 X11 capture, A10, A12, A13): hold the capture lock, using the wrapper in [gx10-unattended.md](../gx10-unattended.md), and use `--real-effects`. `gx10 status` and `gx10 smoke` check the desktop first.
- **Startup service:** once `app` runs, `gx10 enable-app /home/asus/auto-daybook` turns on the `daybook` user service (from #3).

## What to watch for

- **Model budget.** A2, A7, A8, and A9 share one model. Keep within the call caps in §5, and report `calls_last_minute` in `GatewayStatus`.
- **The quality gate (A8 and A13).** The tick's signal answers must match fixture labels on at least 80% of ticks. If they don't, raise the tick's effort to `low` before changing anything else.
- **Wire shapes.** A11 must return exactly the `wire.py` models. If a real handler needs a different shape, change `wire.py` and its mock example together in a shared-contract PR, and post on the Tracker so Lane B sees it.
- **Privacy behavior is product behavior.** The checks in §7 are acceptance items, not hardening. Apply the hackathon cost test from [review-triage.md](../agents/review-triage.md) only to findings about threats outside the product.

## Handoffs to Lane B

| When | What Lane B gets |
|---|---|
| C1 merges | `--mock-api` with every route and a scripted event stream |
| A11 lands for a route | that route answering from real data on a `--replay` instance |
| A10 lands | `daybook run` with the shell spawned, so Lane B can test the overlay and tray end to end |
| A Lane B request lands | the real handler behind a `wire.py` change Lane B asked for |
