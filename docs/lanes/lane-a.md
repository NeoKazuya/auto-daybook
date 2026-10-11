# Lane A: build the core

**Goal:** build the complete working Daybook backend, start to end. It watches the screen, writes notes, runs the tick and the skills, plays music, drives the overlay and desktop actions, answers chat, and serves the API that the UI uses. At the end, `daybook run` on the GX10 passes the end-to-end acceptance in [ARCHITECTURE.md §11.2](../ARCHITECTURE.md#112-end-to-end-acceptance), with Lane B's UI on top.

**Owner:** the Codex wave. How the work is divided among Codex's agents, and in what order, is Codex's call. This brief says what has to exist when it's done. Claim the lane once on the [Tracker](https://github.com/NeoKazuya/auto-daybook/issues/1); each PR says `Part of #1` and names the lane.

**Owns:** every path except `src/daybook/web/static/` and `src/daybook/shell/` (see [README.md](README.md#ownership-by-path)).

**Spec:** [ARCHITECTURE.md](../ARCHITECTURE.md) is the spec for every module, including its "Done when" checks. [skills.md](../skills.md) is the skill format. If this brief and ARCHITECTURE disagree, ARCHITECTURE wins.

## The first PR: the shared skeleton

Lane B builds against the API from the start, so the first thing Lane A merges is the skeleton that both lanes share. It's shared-contract work, so it waits for review (CONTRIBUTING step 5). It contains:

- **Project and checks.**
  - `pyproject.toml`: Python 3.12 with `uv`, src layout, console script `daybook = "daybook.app:main"`.
    - runtime dependencies: `fastapi`, `uvicorn`, `httpx`, `pydantic`, `pyyaml`, `mss`, `python-xlib`, `pillow`, `PySide6-Essentials`
    - dev dependencies: `ruff`, `pyright`, `pytest`, `pytest-asyncio`
  - `scripts/check_boundaries.py`, enforcing the rules in ARCHITECTURE §10, with a test per rule.
  - `scripts/check.sh` runs green.
- **Every package with its public interface** from §6. Signatures are typed, and their bodies may be stubs. The data types in §4 are real frozen dataclasses. `events.py` has every event and a working `Bus`. The action catalog lists every action in §6.7 with its real parameters. `skills/builtin/` holds the three built-in skills from [skills.md](../skills.md).
- **`web/wire.py` and `--mock-api`, as working code.**
  - `wire.py` has a pydantic model, with a realistic example, for every request, response, and SSE payload in §6.9.
  - Every route is registered with the token and `Origin` checks.
  - `daybook run --mock-api --port N` serves the examples, keeps edits in memory, answers chat with a canned recall answer or skill draft, and streams a scripted loop of events.
- **The replay fixture format.** `fixtures/replay/README.md` defines it: per step, a frame PNG, `window.json` (active window, plus visible windows with geometry, stacking, and opacity), `locked`, and `idle_ms`. It also has `labels.json` and a 3-step hand-made example.

When it merges, post on the Tracker so Lane B can start. Everything after it is Lane A building the real thing behind those interfaces.

## Scope: what exists when Lane A is done

Each item is complete when its ARCHITECTURE "Done when" checks pass. This is a checklist, not an order.

- [ ] **Notes and events:** the journal with derived segment times, the forget epoch, `trim_after`, partial-forget clearing, FTS5 with `secure-delete`, and WAL truncation, plus the bus (§6.1, §6.2, §4).
- [ ] **Model gateway:** one slot, priorities, the starvation guard, a single pending observe, structured output with one repair retry, timeouts, dialects, and live status (§6.3, §5).
- [ ] **Screen capture and privacy gate:** X11 and replay screens; the lock, pause, away, overlay, and visible-exclusion gate, including the rerun after the grab; continuity rules; the consent generation (§6.4, §7).
- [ ] **The tick:** schedule (including the 30 s demo interval), prompt, per-tick schema, `parse_tick`, readings on a still screen, and the quality gate on fixtures (§6.5).
- [ ] **Skills:** the parser with `SkillSource`, the four-form evaluator, `SkillBook` with separate enablement and the cross-skill limit, `react` with firing claims, the generator, and built-in-only hooks (§6.6, [skills.md](../skills.md)).
- [ ] **Actions:** the catalog; the music player with per-skill `min_dwell` and overlay ownership; overlay state; the xdotool desktop backend; fake backends for development instances (§6.7).
- [ ] **Music library:** 20–30 CC-licensed tracks in mood folders with `library.toml`, and `daybook music check` passing ([setup.md](../setup.md) §5).
- [ ] **Chat:** routing, recall with the epoch check and code-computed totals, and skill drafting (§6.8). *Lane B may take this one; check the Tracker first.*
- [ ] **App:** config, wiring, the CLI, child processes, the capture lock, `--replay`, `--no-capture`, `--real-effects`, and the startup service through `gx10 enable-app` (§6.11, §9).
- [ ] **Real API:** every route answering from the real modules with exactly the `wire.py` shapes, plus SSE from the bus (§6.9).
- [ ] **Fixtures:** `scripts/record_fixture.py` and the four scenarios in §10, recorded on the GX10.
- [ ] **cua trial:** at most an hour. Either a `CuaDesktop` that passes the desktop action tests, or a recorded "no" in decisions.md (§11.1).
- [ ] **End to end:** ARCHITECTURE §11.2 items 1–8 pass live on the GX10, together with Lane B.

## Working setup

- **Without the screen or the model.** Most work runs on `ReplayScreen`, `ScriptedGateway`, a temporary journal, and fake desktop, music, and overlay backends.
- **Development instances:** `daybook run --replay fixtures/replay/<scenario> --port 8770–8779` with `DAYBOOK_HOME=~/.daybook-a-<agent>`. These never touch the real screen or speakers.
- **Real model:** Ollama `qwen3.6:35b` at `http://127.0.0.1:11434/v1`. It was already verified on a real screenshot ([gx10-unattended.md](../gx10-unattended.md)). Don't start a second large model.
- **Real screen and effects:** hold the capture lock with the wrapper in [gx10-unattended.md](../gx10-unattended.md), and use `--real-effects`. Run `gx10 status` first.

## Watch for

- **One model for everything.** Capture, the tick, and chat all share it; keep within the caps in §5.
- **Quality gate.** If the tick's answers match fewer than 80% of fixture labels, raise the tick's effort to `low` before changing anything else.
- **Wire changes.** Change `wire.py` and its mock example in the same shared-contract PR, and post on the Tracker so Lane B sees it.
- **Privacy is product behavior.** The §7 checks are acceptance items. The hackathon cost test in [review-triage.md](../agents/review-triage.md) applies only to hardening against threats outside the product.
