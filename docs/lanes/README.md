# Work lanes

The build is split into two lanes. Each lane builds its whole part, start to end, to a finished, demoable state. The lanes run at the same time without editing the same files. [ARCHITECTURE.md](../ARCHITECTURE.md) is the spec for *what* each module does; these docs say *who* builds what, how to test without the other lane, and where the lanes meet.

| Brief | Owner | Builds |
|---|---|---|
| [lane-a.md](lane-a.md) | The Codex wave | The complete backend: notes, model, screen capture, tick, skills, actions, music, chat, app, and the real API. Its first PR is the shared skeleton (types, `web/wire.py`, `--mock-api`) that Lane B builds on |
| [lane-b.md](lane-b.md) | The frontend agent | The complete web UI and the shell (tray and full-screen overlay), against `--mock-api` first and then the real backend |

Each lane is one task on the [Tracker](https://github.com/NeoKazuya/auto-daybook/issues/1). How a lane divides its own work is up to whoever runs it. Each brief has a scope checklist and an acceptance, not a sequence of steps.

**Claims inside a lane.** The lane is claimed once (`Claimed: Lane A …`). Inside the lane, each agent posts a `Claimed:` line for the PR it's working on, with the paths it touches, before starting, and a `Done:` line when it merges. That's for overlap checks between agents in the same lane, not a plan; agents add these as they go. A lane is `Done:` only when its whole scope checklist is done.

**Moving chat to Lane B.** Lane B may take `chat` (see [lane-b.md](lane-b.md#optional-chat)) by posting `Claimed: chat, moved from Lane A to Lane B`, as long as no Lane A agent has an open `Claimed:` line on `src/daybook/chat/`. Lane A agents check for that line before starting chat.

## Ownership by path

Only the owning lane edits a path. The import-boundary check (`scripts/check_boundaries.py`) enforces the package boundaries.

| Path | Owner |
|---|---|
| `src/daybook/journal/`, `events.py` | Lane A |
| `src/daybook/model/` | Lane A |
| `src/daybook/sense/` | Lane A |
| `src/daybook/mind/` | Lane A |
| `src/daybook/skills/`, `skills/builtin/` | Lane A |
| `src/daybook/act/` | Lane A |
| `src/daybook/chat/` | Lane A (Lane B may take it instead, see [lane-b.md](lane-b.md#optional-chat)) |
| `src/daybook/app.py` | Lane A |
| `src/daybook/web/` route handlers and `_mock.py` | Lane A |
| `src/daybook/web/static/` | **Lane B** |
| `src/daybook/shell/` | **Lane B** |
| `scripts/record_fixture.py`, `fixtures/` | Lane A |
| `tests/` | The lane that owns the code under test: `tests/web_static/` and `tests/shell/` are Lane B's; `tests/chat/` follows `src/daybook/chat/`; everything else is Lane A's |
| `scripts/gx10/`, `docs/gx10-unattended.md` | GX10 setup (already merged in #3) |

**Shared contracts.** Either lane may change these, but only through a shared-contract PR that waits for review (CONTRIBUTING step 5). Post a Tracker comment so the other lane sees it:

- `src/daybook/web/wire.py`: request and response bodies, SSE payloads, and their examples
- the public `__init__.py` of any package: types and signatures
- the event list in `events.py`
- the action catalog's names and parameters
- [skills.md](../skills.md) and [ARCHITECTURE.md](../ARCHITECTURE.md)

## How the lanes meet

- **Through `wire.py` only.** Lane B reads data only from HTTP routes and SSE, as defined in `wire.py`. It never imports backend modules. Lane A never edits static UI files.
- **When Lane B needs something new** (a field, a route, an event), it opens a shared-contract PR that adds it to `wire.py` and to the mock examples, and posts `Task: (Lane A) implement <route/field> for real — requested by Lane B` on the Tracker. Lane B keeps building against the mock, and Lane A wires the real handler.
- **When Lane A changes a wire shape,** the same PR updates the mock examples, so Lane B's mock keeps matching.
- **Switching to the real backend** happens route by route as Lane A's handlers go live. The same UI runs against a `--replay` instance, then the real one.

## Shared machine rules

Everyone works on the one GX10 ([gx10.md](../gx10.md)).

| | Real instance | Lane A dev instances | Lane B dev instances |
|---|---|---|---|
| Port | 8765 | 8770–8779 | 8780–8789 |
| `DAYBOOK_HOME` | `~/.daybook` | `~/.daybook-a-<agent>` | `~/.daybook-b-<agent>` |
| Mode | real capture (holds the capture lock) | `--replay` or `--no-capture` | `--mock-api`, later `--replay` |

- Only one process watches the real screen. The capture lock (`/tmp/daybook-capture.lock`) enforces it. Desktop tests (overlay, tray, `desktop.*` actions) take the same lock, using the wrapper in [gx10-unattended.md](../gx10-unattended.md).
- Development instances never draw on the screen or play sound unless started with `--real-effects`. Use that only while holding the lock.

## Finished

The system is finished when the end-to-end acceptance in [ARCHITECTURE.md §11.2](../ARCHITECTURE.md#112-end-to-end-acceptance) passes. Lane A owns the behavior behind every item; Lane B owns what the user sees for items 1, 4, 5, and 6. Both lanes rehearse the [demo](../demo.md) together.
