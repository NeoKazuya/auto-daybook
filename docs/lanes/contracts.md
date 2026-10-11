# Contracts (C1)

One PR, done by one agent before either lane starts. It turns [ARCHITECTURE.md](../ARCHITECTURE.md) §6 into a package that imports, type-checks, and runs its checks, with every public interface in place and every body stubbed. Both lanes then build against it in parallel. It's shared-contract work, so it waits for review (CONTRIBUTING step 5).

Read before starting: [ARCHITECTURE.md](../ARCHITECTURE.md) §4, §6, §9, §10, [skills.md](../skills.md), and [design-red-flags.md](../agents/design-red-flags.md). The arena sketches are historical; don't copy them.

## Deliverables

### 1. Project and checks

- `pyproject.toml`:
  - Python 3.12, managed with `uv`, src layout (`src/daybook/`)
  - console script `daybook = "daybook.app:main"`
  - runtime dependencies: `fastapi`, `uvicorn`, `httpx`, `pydantic`, `pyyaml`, `mss`, `python-xlib`, `pillow`, `PySide6-Essentials`
  - dev dependencies: `ruff`, `pyright`, `pytest`, `pytest-asyncio`
  - ruff and pyright configuration (strict pyright on `src/`)
- `scripts/check.sh` (already exists) runs green.
- `scripts/check_boundaries.py` implements the rules in ARCHITECTURE §10:
  - no import of `daybook.<x>._*` from outside `<x>`
  - `sqlite3` only in `journal` and `skills._ledger`
  - `httpx` only in `model` and `shell`
  - `PySide6` only in `shell`
  - `Xlib` and `mss` only in `sense`

  Each rule has a test with a deliberately bad import.

### 2. Packages with public interfaces

One package per row of the ARCHITECTURE §6 module table: `journal`, `model`, `sense`, `mind`, `skills`, `act`, `chat`, `web`, `shell`, plus `events.py` and `app.py`.

- **Public `__init__.py`.** Each package's `__init__.py` exports exactly the public surface in §6, as typed signatures. Bodies `raise NotImplementedError`. Docstrings state intent and invariants in a sentence or two, with a pointer to the §6 subsection.
- **Real data types.** The types in ARCHITECTURE §4 are frozen dataclasses with real fields, not stubs:
  - notes: `Observation`, `Segment`, `SignalReading`, `CaptureSpan`, `Tick`
  - skills: `SkillState`, `Firing`, `SkillSource`
  - model: `Prompt`, `Priority`, `GatewayStatus`
  - actions: `OverlayRequest`, `Outcome`
  - capture: `CaptureState`, including `AWAY`
- **Event list.** `events.py` has every event in §6.2 as a dataclass, plus a working `Bus` (it's small, and every lane needs it).
- **Action catalog.** `act` defines the catalog's `ActionSpec` list for every action in §6.7, with real names, parameters, and descriptions; only the `run` callables are stubs. `build_catalog()` returns it.
- **`skills/builtin/`.** `music.skill.md`, `break.skill.md`, and `break-demo.skill.md`, exactly as in [skills.md](../skills.md).

### 3. `web/wire.py` and `--mock-api`

These are real code, not stubs, because Lane B builds on them from the start.

- **`wire.py`.** A pydantic model for every request body, response body, and SSE event payload of every route in §6.9, each with one realistic example via `model_config` / `json_schema_extra`. The examples tell one coherent story: a morning of coding, reading, and a meeting, with one paused span.
- **Route stubs.** `web` has every route from §6.9 registered with FastAPI, with request and response models from `wire.py`. Handlers delegate to a `Backend` protocol. The token and `Origin` checks are implemented.
- **`_mock.py`.** A `MockBackend` serves the examples:
  - edits, deletes, approvals, enable/disable, and privacy changes are kept in memory, so the UI sees its changes take effect
  - `POST /api/chat` returns a canned recall answer, or, if the message mentions "remind", a canned skill draft
  - `/api/events` emits a scripted loop every 3–5 s: a new observation, a segment change, a capture state change, a skill firing, an overlay shown and hidden, and a music change
- **`app.py`** has a minimal CLI. `daybook run --mock-api [--port N]` works end to end. Every other subcommand exists and exits with "not built yet".

### 4. Fixture format

`fixtures/replay/README.md` defines the `ReplayScreen` folder format (Lane A's fixture recorder writes it, and `sense` reads it):

- step files named in time order
- per step: the frame PNG, `window.json` (active window, plus visible windows with geometry, stacking, and opacity), `locked`, and `idle_ms`
- a `labels.json` with the expected kind, mood, focused, and in-meeting values per step

It also includes one tiny hand-made example scenario (3 steps), so `sense` and `mind` tests can start before real fixtures exist.

## Done when

- `scripts/check.sh` passes: ruff, format, pyright, pytest, and the boundary check.
- `python -c "import daybook.journal, daybook.model, daybook.sense, daybook.mind, daybook.skills, daybook.act, daybook.chat, daybook.web, daybook.shell"` succeeds.
- Every `wire.py` example validates against its own model (test).
- `daybook run --mock-api --port 8780` serves every route in §6.9: a test calls each once with the token. `/openapi.json` lists them all. `/api/events` streams events.
- The three built-in skill files exist and match [skills.md](../skills.md) byte for byte in their code blocks (test).
- A mutating request without the token, or with a foreign `Origin`, gets 403 (test).
- The PR body lists every public interface added, so reviewers can compare it against §6.

After merge: post `Done: C1 …` on the Tracker. Both lanes start.
