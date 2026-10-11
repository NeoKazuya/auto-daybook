# Architecture

Daybook (working name) is a local AI agent on the GX10. While it's enabled, it watches the screen, writes notes about what the user is doing, and lets **skills** act on those notes. In the demo, music follows the activity, a full-screen break reminder appears after long focus, a skill requested in chat works, and recall answers questions about the day. Everything runs on one machine with one local vision model, and screenshots never touch disk.

This document is the whole design and the whole build plan. It was synthesized from two independent candidates (PStack `architect` + `arena`). Scores, grafts, and rejections are in [planning/arena/SYNTHESIS.md](planning/arena/SYNTHESIS.md). The facts it relies on are in [planning/grounding.md](planning/grounding.md), and every product decision is in [planning/decisions.md](planning/decisions.md).

Companion docs:

| Doc | What it covers |
|---|---|
| [skills.md](skills.md) | The skill file format, a shared contract: the grammar, the action catalog, approval, and full examples |
| [setup.md](setup.md) | Bringing the GX10 to a working state: packages, tray extension, Python environment, music library, model |
| [demo.md](demo.md) | The live demo script and rehearsal checklist |
| [gx10.md](gx10.md) | Access to the shared machine and the rules for using it |

## Contents

1. [Constraints](#1-constraints)
2. [The core idea](#2-the-core-idea)
3. [Runtime](#3-runtime)
4. [Data model](#4-data-model)
5. [Model use and budget](#5-model-use-and-budget)
6. [Modules](#6-modules)
7. [Privacy](#7-privacy)
8. [User interface](#8-user-interface)
9. [Repository, configuration, and commands](#9-repository-configuration-and-commands)
10. [Development and testing](#10-development-and-testing)
11. [Build plan](#11-build-plan)
12. [Risks and open questions](#12-risks-and-open-questions)

## 1. Constraints

These are fixed. The rest of the document is built on them.

- **One machine.** The GX10 (Ubuntu 24.04, aarch64, 121 GiB unified memory) is both the computer being watched and the computer doing the watching. It has one 1920×1080 screen, running GNOME on **X11** on display `:1` (`XAUTHORITY=/run/user/1000/gdm/Xauthority`). Developers work on it over SSH via Tailscale.
- **One model.** Only one large model fits in memory, so every model call in the system shares it, one call at a time. Today that's `qwen3.6:35b` in Ollama: about 2.9 s per screenshot call and about 950 prompt tokens per 1280-px image. Later it may be Qwen3.8-Flash-Next on vLLM (32K context, key-authenticated, vision not yet verified). The backend is chosen by config and spoken to through an OpenAI-compatible API.
- **Python core, a local web UI, a tray icon, and a reusable full-screen overlay.**
- **Skills are text files** with goals, triggers, and allowed actions, plus optional developer-written Python hooks. Users create skills by asking in chat. Chat-made skills are declarative only.
- **Two-layer notes.** Observations record what was on screen; segments record the model's interpretation, including inferred intent. The two stay separable.
- **Privacy:** pause and resume, app and window exclusions, screenshots kept only in memory, and notes the user can edit and delete.
- **Computer control** through named actions behind one plug-in point. xdotool and wmctrl by default, or trycua/cua if a trial on the real Xorg session passes. No free-form clicking.
- **The capture loop must skip a locked screen** (it produces black frames) and excluded windows.

## 2. The core idea

**Skills never call the model.** A skill declares *signals*: named, typed questions such as "Is the user doing sustained, focused work?" (`bool`) or "Which kind of music fits right now?" (`music.mood`). About every 90 seconds, one text-only model call, the **tick**, does two jobs:

1. **Rollup.** It folds new observations into the current activity segment, or starts a new segment, with a title, a kind, and an inferred intent.
2. **Signals.** It answers every active skill's questions in one JSON object. The JSON schema is generated from those questions, so the answers come back typed.

The answers are stored as readings. A **pure** evaluator then checks each skill's rules against the reading history. There are four trigger forms: *updates*, *becomes X*, *is X for duration*, and *every duration*. Matching rules run named actions from one catalog, limited to the actions that skill is allowed.

Consequences:

- **Fixed model load.** Background load is one observe call per screen change plus one tick per ~90 s, however many skills are enabled. A chat-made skill adds a few prompt tokens to the tick, not a model call.
- **Predictable triggers.** Given the same readings, the same rules fire. That makes the demo predictable and the evaluator unit-testable without a model.
- **Expressive without core changes.** Any new question a user wants answered becomes a signal. The core schema never changes.

## 3. Runtime

```text
GNOME Xorg :1 ─────────────────────────────────────────────────────────────┐
   ▲ screenshot (RAM only)        ▲ window facts            ▲ overlay/tray │
   │                              │                         │              │
┌──┴──────────────────────────────┴──────────────┐   ┌──────┴───────────┐  │
│ daybookd (one Python asyncio process)          │   │ daybook.shell    │  │
│                                                │   │ (Qt process)     │  │
│  sense ──observe──► ModelGateway ◄──tick── mind│   │ tray + overlay   │  │
│    │                    ▲  ▲                │  │   └──────▲───────────┘  │
│    ▼                    │  │                ▼  │          │ SSE + token  │
│  journal (SQLite) ◄─────┼──┼───── record_tick  │          │              │
│    │ Portal (reads)     │  │                │  │   ┌──────┴───────────┐  │
│    ▼                    │  │                ▼  │   │ browser tab      │  │
│  chat ──recall/draft────┘  │   skills.react    │   │ web UI           │  │
│                            │        │          │   └──────▲───────────┘  │
│  web (FastAPI, 127.0.0.1) ─┼────────┼──────────┼──────────┘              │
│                            │        ▼          │                         │
│                            │   act (catalog) ──┼─► mpv (music), shell ───┘
│                            │                   │   (overlay), xdotool/cua
└────────────────────────────┼───────────────────┘
                             ▼
                Ollama :11434 / vLLM :18300   (one model, external)
```

| Process | What runs | Cadence |
|---|---|---|
| `daybookd` | **sense:** the privacy gate, change detection, observe calls | polls every 5 s; at most one model observe per 10 s; immediately on app switch |
| | **mind:** the tick (rollup + signals), then `skills.react` | every 90 s; 20 s after an app switch; at least 30 s apart; only when there are new observations |
| | **web:** FastAPI with Server-Sent Events (SSE) and the static UI | on request |
| `daybook.shell` | Qt: tray icon and the full-screen overlay. It's a stateless view of `daybookd` events | event-driven |
| `mpv --idle` | music playback over a JSON IPC socket | event-driven |
| Ollama or vLLM | the one model (external to the app) | serial |

`daybookd` spawns `mpv` and the shell, and restarts either if it exits. It runs inside `tmux` on the GX10, with the display environment from [gx10.md](gx10.md). Only one `daybookd` may watch the real screen, enforced with an flock on `/tmp/daybook-capture.lock`. Development instances run with `--replay` or `--no-capture` and their own `DAYBOOK_HOME`.

## 4. Data model

All notes live in one SQLite file, `$DAYBOOK_HOME/journal.db`, in WAL mode with `secure_delete=ON` and an FTS5 index created with FTS5's `secure-delete` option. The `journal` package is its only writer, except for the `skill_state` and `firings` tables, which only `skills` writes. There is no image column anywhere.

| Type | Fields | Rules |
|---|---|---|
| `Observation` | `id`, `started_at`, `last_seen_at`, `window: WindowInfo(app, title)`, `seen` (1–2 sentences), `visible_text` (≤300 chars), `segment` (or none) | **What was on screen.** `window` comes from X11, not the model. `seen` describes only what's visible and never guesses intent. While the screen stays unchanged, sense moves `last_seen_at` forward without a model call. `active = last_seen_at − started_at`. A new observation's `started_at` is the poll at which it was triggered, and the previous observation is extended to that same poll, so consecutive observations meet exactly and nothing is counted twice. `segment is None` means it hasn't been rolled up yet |
| `Segment` | `id`, `title`, `kind: ActivityKind`, `intent`, `authored: model\|user`, `open`; derived: `started_at`, `ended_at`, `active`, `observations` | **The interpretation.** Times are derived from member observations at read time and never stored, so deleting an observation shrinks its segment. Once a user edits the text, `authored = user` and ticks never overwrite it |
| `SignalReading` | `signal: SignalId(skill, name)`, `value: bool\|str\|float\|None`, `at`, `tick` | One row per signal per tick. `None` means the model couldn't tell or answered outside the type. "Held for 50 minutes" is computed from readings, so it survives a restart |
| `CaptureSpan` | `state: running\|paused\|locked\|away\|withheld\|overlay`, `start`, `end` | Why capture was or wasn't happening. The timeline shows gaps honestly, and recall won't invent activity inside them |
| `Tick` | `at`, `epoch`, `evidence_until`, `observations` (may be empty), `segment: Continue\|Start` (or none), `readings` | Applied in one transaction. Unique on `evidence_until`, the newest `last_seen_at` it read, so re-applying is a no-op. A tick with no new observations still writes readings, which is how streaks keep growing on a still screen. Rejected whole if `epoch` is older than the journal's forget epoch |
| `SkillState` | `skill`, `enabled`, `approved_hash` (or none), `at` | Enablement and approval are stored separately. A skill is **active** only if `enabled` is true *and* its current hash is approved. Disabling keeps the approval, so re-enabling needs no new review; any edit changes the hash, so the skill needs review again |
| `Firing` | `skill`, `rule`, `key`, `at`, `outcomes` | Unique on (skill, rule, key). This makes each rule fire at most once per occurrence |

`ActivityKind` is one of: `coding`, `writing`, `reading`, `browsing`, `meeting`, `chatting`, `media`, `admin`, `other`.

Main access patterns, each served by an index and needing no cache:

- **Timeline:** segments by time range, joined to observations by `segment`.
- **Triggers:** `readings(signal, range)` by `(signal, at)`.
- **Recall:** `context()`, `totals()`, plus FTS5 search.
- **Tick:** unassigned observations.

## 5. Model use and budget

Every model call goes through `ModelGateway.call(priority, prompt)`. The gateway allows one call in flight, ordered INTERACTIVE > TICK > OBSERVE. Its rules:

- **Starvation guard:** at most 2 INTERACTIVE calls in a row before the oldest waiting background call runs.
- **No queued screenshots.** At most one OBSERVE can be pending; a newer request replaces it. Capture passes a *builder*, so the screenshot is taken only once the slot is granted, and frames are never queued.
- **Structured output.** Callers get a parsed pydantic value, never raw JSON. If the output fails schema validation, the gateway retries once with the validation error included.
- **Failures don't snowball.** Each request times out after 40 s. A failure becomes a gap and a visible status, never a retry storm.
- **Live status.** `GatewayStatus` reports reachability, what's in flight, queue depth, latency, and calls per minute. The UI shows it in a footer.

| Call | Made by | Input | Effort | Latency | Rate cap |
|---|---|---|---|---|---|
| observe | sense | 1 image (1280 px JPEG, q80, ~950 tokens) + window facts, ~150 tokens | none | ~3 s | 6/min |
| tick | mind | text, 1.5–3K tokens; output schema built from the active signals | none, or low if quality needs it | 2–4 s | 2/min |
| route | chat | ~300 tokens | none | ~1 s | on demand |
| recall | chat | up to 8K tokens of notes and totals | none | 4–10 s | on demand |
| draft skill | chat | ~3K tokens (format guide, catalog, examples) | low | 10–20 s | on demand |

**Background load:** typically 5–12 model-seconds per minute; at most about 24 when the screen changes constantly. A chat request waits behind at most one call already running (about 3 s).

**Backend swap:** change `[model]` in `daybook.toml` (`base_url`, `model`, `api_key_file`, `dialect`). The `dialect` (`ollama`, `vllm`, or `openai`) only controls how reasoning effort and JSON-schema output are spelled in the request. Switching to Qwen3.8 is allowed once `~/gx10-model-plan/accept.py --vision` passes and the replay fixtures produce equal or better notes.

## 6. Modules

Each module is a package with a public `__init__.py`. Internals live in `_*.py` files, and importing another package's `_*` module fails the local checks. This section is the authority for every interface. The arena sketches in [planning/arena/](planning/arena/) are a **historical record** of the candidates: several of their contracts were changed during synthesis and review (active-time formula, AWAY, the forget epoch, `SkillSource`, separate enablement, built-in-only hooks). Don't copy them; write the Contracts work from this document.

| Package | Single owner of | Public surface |
|---|---|---|
| `daybook.journal` | `journal.db` notes tables, FTS5 | `Journal` (writes by named caller), `Portal` (reads), the data types |
| `daybook.events` | the list of domain events, the in-process bus | `Bus`, event dataclasses |
| `daybook.model` | endpoint config, the single slot, request formats per dialect, structured output | `ModelGateway`, `Prompt`, `Priority`, `ScriptedGateway` |
| `daybook.sense` | the screen, `privacy.json`, the gate, change detection, the observe prompt | `Sense`, `Screen` (`X11Screen`, `ReplayScreen`), `gate()`, `PrivacySettings` |
| `daybook.mind` | tick scheduling, the tick prompt, closing a segment after a gap | `Mind.run/nudge/tick_now`, `parse_tick` |
| `daybook.skills` | skill files, the `skill_state` and `firings` tables, the trigger evaluator, the generator | `SkillBook`, `parse_skill`, `evaluate`, `draft_skill`, `HookContext` |
| `daybook.act` | the action catalog, the music library and player, overlay state, desktop control | `Catalog`, `Actions`, `build_catalog`, `OverlayRequest`, `DesktopControl` |
| `daybook.chat` | intent routing, the recall prompt, time-window resolution | `Chat.respond` |
| `daybook.web` | HTTP routes, the JSON wire format, the static UI | `create_app` |
| `daybook.shell` | the Qt tray and overlay window (no state of its own) | `python -m daybook.shell` |
| `daybook.app` | config, wiring, child processes, the CLI, the capture lock | `daybook …` |

### 6.1 `journal`: the notes

**Owns** all notes tables and the only renderer of notes into prompt text.

**`Portal`** (read-only; used by the UI, recall, the mind, and hooks):

- `now() -> Situation`: capture state, open segment, latest observation, latest reading per signal.
- `segments(range)`, `observations(range, unassigned_only=False)`, `capture_spans(range)`.
- `search(text, range=None) -> [Hit]`: FTS5 over observation `seen`, `visible_text`, and title, plus segment title and intent.
- `totals(range, by="kind"|"title"|"app") -> [Total]`. All duration arithmetic lives here; the model never adds up times.
- `readings(signal, range)`: the trigger evaluator's only input.
- `context(range, budget_tokens, detail="segments"|"observations") -> str`: one line per segment, with observations indented if requested, and capture spans included as "10:10–10:25 paused". Under budget pressure it drops the oldest observation lines first, then the oldest segments. It estimates tokens as characters ÷ 3.5.

**`Journal`** (writes):

- **sense:** `add_observation`, `extend_observation`, `trim_after(t)` (when going away or after a failed observe), `set_capture_state` (idempotent). `trim_after(t)` deletes every observation that started after `t` (with its FTS rows), cuts the one spanning `t` back to `t`, deletes signal readings and ticks after `t`, deletes segments left with no observations, and closes the open segment so resumed work starts a new one, all in one transaction. Firings are kept: they record effects that really happened, and a streak that restarts after the trim gets a new occurrence key.
- **mind:** `record_tick` (one transaction; idempotent; rejected if started before the last forget) and `close_open_segment`.
- **web, on user request:**
  - `edit_segment` (sets `authored=user`), `edit_observation`
  - `forget_observation`, `forget_segment` (also deletes its observations), `forget_range`, `forget_all`

Every commit publishes a domain event on the bus.

**Forgetting part of a segment.** If a forget removes some of a segment's observations, and the segment's text is model-authored, its title and intent are cleared to `(summary removed after forget)`, along with their search entries, in the same transaction, because they may describe the forgotten activity. An open segment gets new text on the next tick; a closed one keeps the placeholder. User-authored text is kept.

**Forget barrier.** The journal keeps a **forget epoch** that every forget *and every `trim_after`* increments in the same transaction as the delete. Sense and mind read `journal.epoch` when a model call starts and pass it with the result (`add_observation(draft, epoch)`, `Tick.epoch`). The journal rejects any write whose epoch is older, so a result computed from content that was just forgotten can't be written back. Rejections are logged as `discarded_after_forget` and published as a status event.

**Done when:**

- Unit tests on a temporary database cover each Portal read.
- `record_tick` is shown idempotent: applying it twice gives one result, and observations deleted mid-tick are skipped.
- Deleting an observation shrinks its segment's derived times. Deleting a segment removes its observations and their FTS rows.
- A user-authored segment survives a later `Continue`.
- A tick or observation started before a `forget_range` is rejected after it, and nothing from the forgotten range reappears.
- After `forget_range` returns, a byte search of `journal.db` and `journal.db-wal` for a unique string from the forgotten notes finds nothing, including in the title or intent of a segment that only partly overlapped the range.
- A tick with no new observations writes readings and no segment change.
- `context()` respects its budget and includes capture spans.
- `totals()` matches hand-computed durations on a fixture.

### 6.2 `events`: the bus

**Owns** the only list of event types:

- `ObservationAdded`, `ObservationExtended`, `SegmentChanged`, `TickRecorded`
- `CaptureStateChanged`, `SkillFired`, `SkillsChanged`
- `OverlayShown`, `OverlayHidden`, `MusicChanged`
- `StatusChanged`

The bus is in-process pub/sub. The web SSE route is the only place events are serialized.

**Done when:** subscribers receive events in publish order, a slow subscriber doesn't block publishers (its queue is bounded and the oldest events are dropped and logged), and every event type has a JSON serializer covered by a test.

### 6.3 `model`: the one door to the model

**Owns** endpoint configuration, the priority slot, the request format for each dialect, output validation, and the repair retry. Behavior is as described in §5.

`Prompt[T]` holds the system and user text, the output type (`str` or a pydantic model), images as in-memory JPEG bytes, effort, and `max_tokens`. Output models are private to each caller.

`ScriptedGateway` is the test double. It returns queued replies and records the prompts it received.

**Done when:**

- A fixture PNG sent to the real Ollama endpoint returns a parsed observation in under 5 s.
- Priority order and the starvation guard are proven with a fake transport.
- A pending OBSERVE is replaced by a newer one, and a builder that returns `None` releases the slot.
- Invalid JSON triggers exactly one repair retry.
- A timeout raises `ModelUnavailable` within 40 s.
- Each dialect produces the correct request body (snapshot tests).
- `GatewayStatus` reports calls per minute accurately.

### 6.4 `sense`: the screen and privacy settings

**Owns** `privacy.json` (pause and exclusions; sense is its only writer), the `Screen` abstraction, the privacy gate, change detection, and the observe prompt.

**Loop**, every 5 s:

1. **Gate (pure, before any pixels are read).** Each check skips the capture and records the state shown:
   - locked, or the lock state can't be read → `LOCKED`
   - paused → `PAUSED`
   - no active window → `LOCKED`
   - our own overlay is up → `OVERLAY`
   - no keyboard or mouse input for 5 minutes (X11 idle time from the XScreenSaver extension) → `AWAY`. Idle time is ignored while any app is recording from the microphone (`pactl list short source-outputs` isn't empty), so listening in a call doesn't count as away
   - any excluded window has a visible part on screen → `WITHHELD`. "Visible" is computed from the stacking order (`_NET_CLIENT_LIST_STACKING`) and window geometry: an excluded window counts only if its rectangle isn't fully covered by the windows above it. A covering window that sets `_NET_WM_WINDOW_OPACITY` below fully opaque doesn't count as covering. A password manager or the Daybook UI hidden behind VS Code doesn't block capture
2. **Thumbnail.** Grab a 32×18 grayscale thumbnail. If it's black → `LOCKED`.
3. **Keep time continuous, but never across a gap.** If the previous poll was also `RUNNING`, extend the latest observation (`extend_observation`) whether or not the screen changed, so totals don't lose the seconds between a change and the next model call. If the previous poll was in any other state, or the journal's forget epoch changed since the previous poll (a forget may have removed the latest observations), don't extend: the next observation starts fresh, and step 4 skips the cooldown. When `AWAY` begins, everything after the last input time is removed with `trim_after(last_input)`: later observations (for example, from a video that kept changing) and readings, so the idle minutes count as neither activity nor focus.
   **A failed observe also breaks continuity.** If an observe triggered by a change times out, fails validation, or is discarded, sense calls `trim_after(t)` with `t` = the poll where the change was first seen, then stops extending until a new observation is written. The time in between is a gap, not credit to the previous activity.
4. **New observation** if the window changed, the thumbnail differs by 3% or more, or capture just resumed after a gap. At least 10 s must have passed since the last observe; an app switch or a resume skips that wait. Call the gateway with an OBSERVE builder. The builder rechecks the gate, grabs the full frame with `mss`, resizes it to 1280 px, and encodes it as JPEG. **After the grab, it reruns the full gate** (lock, pause, overlay, away, and the stacking, geometry, and visibility check for exclusions) against the post-grab state. If any check fails, or the window set or stacking changed, it discards the frame.
5. **Write the result.** `add_observation(draft, epoch)`, then `mind.nudge(app_switched)`. A result is discarded if the consent generation changed while it was in flight (see [§7](#7-privacy)), and the journal rejects it if a forget happened since the call started.

**Observe prompt:** describe only what's visible, in 1–2 sentences, plus up to 300 characters of salient on-screen text. No guesses about goals or feelings; that's the tick's job. The window app and title are given to the model as facts.

**Default exclusions:**

- password managers (`KeePassXC`, `1Password`, `Bitwarden`)
- windows whose title contains `Private Browsing` or `Incognito`
- the app's own web UI (title contains `Daybook ·`)

Our overlay is recognized by `_NET_WM_PID` matching the shell process that `daybookd` spawned, with WM_CLASS `daybook-shell` as a backup check.

**Screens:**

- `X11Screen` uses `mss` for pixels and `python-xlib` for `_NET_ACTIVE_WINDOW`, `_NET_CLIENT_LIST_STACKING`, `WM_CLASS`, `_NET_WM_NAME`, `_NET_WM_PID`, window geometry, the map state, and input idle time (`Xlib.ext.screensaver.query_info`). The lock state comes from `loginctl show-session <seat0 session> -p LockedHint`.
- `ReplayScreen` plays a folder of steps (a PNG, `window.json`, a lock flag, and visible windows) at real or accelerated speed.

**Done when:**

- `gate()` has a table test for every branch, including an unreadable lock state, idle input, an excluded window that's partly visible (withheld), and one that's fully covered (not withheld). The visibility calculation is a pure function with its own geometry tests.
- After a pause, lock, or away span, the first running poll starts a new observation; the gap's time is attributed to nothing. Going away removes every observation and reading after the last input time, including several observations created by a changing screen. A failed observe after a change leaves a gap rather than extending the previous observation.
- The black-frame check passes against the real locked-screen capture from grounding.
- A replay run writes observations, and unchanged frames extend instead of calling the model. A changed frame inside the 10 s cooldown still extends the current observation, so `totals()` has no gap.
- A frame whose window set changed after the grab is discarded.
- On the GX10, an unlocked screen yields a correct observation, and a locked screen yields `LOCKED` with no model call.
- `privacy.json` round-trips, and changes apply within one poll.
- No screenshot bytes reach disk or logs (checked by a test that asserts no files are written during a replay run with `keep_frames=false`).

### 6.5 `mind`: the tick

**Owns** when interpretation happens and how its prompt is built.

**Schedule:**

- A tick runs every 90 s (30 s in demo mode, `tick_interval` in `daybook.toml`), or 20 s after an app switch (or after an overlay is dismissed).
- Ticks are never less than 30 s apart.
- A tick runs only when there is new evidence since the last tick: either new unassigned observations, or the latest observation was extended (the user is still there on an unchanged screen). No new evidence (locked, paused, withheld, overlay up, or away) means no tick, no readings, and no firings.
- When there are no new observations, the tick still answers every signal and writes readings, but makes no segment decision. That keeps `is … for` streaks and `every` triggers working while someone reads or types on a still screen.
- After a gap longer than 5 minutes, the open segment is closed.

**Tick prompt** (text only, ~1.5–3K tokens):

- the rules for segments: prefer `Continue` unless the task clearly changed; state intent briefly, and hedge when unsure
- `portal.context(last 30 min, detail="segments")` for continuity
- the open segment and its last 3 observations
- the new observations, with times and window facts, or, if there are none, a note that the screen has stayed the same since `<time>`
- one line per active question, for example `break.focused (bool): Is the user doing sustained, focused work …?`. Enum lines list each value with its description

The output model is built per tick from the active questions, so the JSON schema constrains each signal's type.

`parse_tick` is pure:

- out-of-type answers become `None`
- missing answers become `None`
- extra keys are ignored

Then `journal.record_tick(...)` runs, followed by `skills.react(recorded)`.

**Done when:**

- `parse_tick` is table-tested: type coercion, missing and extra keys, and Continue vs Start when there's no open segment.
- The schedule is tested with a fake clock: interval, debounce, spacing, and no tick without evidence.
- With `ScriptedGateway`, a full tick writes a segment and readings and calls `react`.
- With the real model on the replay fixtures, signals match the expected labels on at least 80% of ticks. That's the quality gate; the fallback is effort `low` for ticks only.

### 6.6 `skills`: format, approval, triggers, and generator

**Owns** the skill files (`skills/builtin/` in the repo, `$DAYBOOK_HOME/skills/` for user skills), the `skill_state` and `firings` tables, the only parser, the trigger evaluator, and the chat skill generator. The full format is in [skills.md](skills.md).

- **`parse_skill(text, *, source, catalog, hook_text) -> Skill | [SkillError]`.** The only parser of a single file. The loader, the UI editor's live check, the CLI, and the generator all use it. `source` is a `SkillSource(path, origin: builtin|user)` built by `SkillBook` from where the file was loaded. It checks that:
  - `id` matches the file name in `source.path`
  - every `do` action is in `allow`
  - every allowed action is in the catalog
  - arguments match the catalog's parameter types, including catalog types such as `music.mood`
  - every referenced signal is declared
  - a skill has at most 4 signals
  - a hook step appears only when `source.origin` is `builtin`, and has a matching function in `<id>.hook.py`. Hooks in user or chat-made skills are parse errors, and `.hook.py` files outside `skills/builtin/` are never loaded
- **`evaluate(rule, history, max_gap=5 min, now) -> Occurrence | None`.** Pure. It covers the four trigger forms and the keys that make firing idempotent.
- **Approval and enablement** (stored separately, in `skill_state`).
  - A skill is active only if it is enabled *and* `sha256(skill file + hook file)` is approved.
  - `approve(id, hash)` approves that exact hash and enables the skill. `disable(id)` and `enable(id)` flip only `enabled`. Enabling a skill whose current hash isn't approved is refused, so it has to be reviewed.
  - **Cross-skill limit.** `SkillBook` (not the parser) enforces at most 12 active signals in total: `approve` and `enable` are refused with a clear error if they would exceed it, and `entries()` reports the current total.
  - Any edit, whether by hand, in the UI, or by the generator, returns it to "needs review".
  - Approving an outdated hash fails with `StaleApproval`.
  - Built-in skills in `skills/builtin/` are part of the repo and count as approved; they're reviewed through normal PRs, not in the app. New installs start with only `music` enabled; every other built-in starts approved but disabled.
- **`react(tick)`.** For each active skill and each rule:
  1. evaluate the rule
  2. check its cooldown
  3. claim the firing key (`INSERT OR IGNORE`; at most once)
  4. run the steps through `catalog.allowed(skill.allow)`

  A failing step is recorded and the rule continues with its next step. Hook steps time out after 10 s. Nothing reacts while paused.
- **`draft_skill(request)`.**
  - The prompt has the format guide, `catalog.describe_for_prompt()`, and the built-in skills verbatim. It produces a declarative skill only, with no hook.
  - If parsing fails, the generator retries once with the errors included. If it still fails, the draft is saved as invalid so the user can fix it.
  - The result is written with `created_by: chat` and the original `request`, in "needs review" status, along with a one-line summary.

**Done when:**

- The parser accepts the built-in skills and the generated example from [skills.md](skills.md), and rejects a table of bad files with precise error locations.
- `evaluate` is table-tested for all four forms, including gaps, startup (an edge needs a prior reading), and streak keys.
- Approve, edit, and approve again works; the stale hash case is covered. Disable then enable keeps the approval.
- `react` fires once per occurrence (proven by applying a tick twice), and cooldown is respected.
- A disallowed action can't run, even from a hook.
- The generator turns the demo request into a valid skill with the real model, in under 30 s.

### 6.7 `act`: actions, music, overlay, and desktop

**Owns** the catalog, the only list of actions. Validation, the generator prompt, the review UI, and enforcement all derive from it. Adding an action means adding one entry to `build_catalog`.

| Action | Parameters | Behavior |
|---|---|---|
| `music.play_mood` | `mood: music.mood`, `min_dwell = "60s"` | No-op if that mood is already playing. Refused (ok, with a "dwell" detail) if *the same skill* last changed the mood less than `min_dwell` ago; another skill's change (such as the break) doesn't reset this skill's clock, so music returns right after a break. **While an overlay is up, only the skill that showed it can change the music**, so the break's music isn't replaced by another skill firing on the same tick. Otherwise it fades the volume out over 1.5 s, loads a shuffled, looping playlist from the mood's folder, and fades back in |
| `music.stop` | — | Stops playback |
| `overlay.show` | `title`, `body`, `dismiss = "OK"`, `minutes = 1`, `style = calm\|alert` | Replaces any current overlay. Auto-hides after `minutes` |
| `overlay.hide` | — | |
| `notify.send` | `title`, `body` | `notify-send`, for low-key nudges |
| `desktop.open_url` | `url` | Through the `DesktopControl` backend |
| `desktop.launch` | `app` (desktop file ID) | `gtk-launch` |
| `desktop.focus` | `app` (WM_CLASS glob) | `wmctrl`/`xdotool`, or the cua backend |

**Catalog types:**

- **`music.mood`.** Its values come from `~/Music/daybook/library.toml`, which is the only list of moods: `focus`, `calm`, `energetic`, `break`, each with a one-line description. `daybook music check` fails if a mood folder and a `[moods.*]` entry disagree, or if a track has no license and source recorded.

**Backends:**

- **`MusicPlayer`.** One `mpv --idle --no-video --input-ipc-server=$DAYBOOK_HOME/mpv.sock` child. It restarts mpv if the socket dies.
- **`Overlay`.** Holds which overlay is up. `show()` publishes `OverlayShown(id, request)`. If no shell is connected, it returns `ok=False` with a reason.
- **`DesktopControl`.**
  - `XdoDesktop` is the default.
  - `CuaDesktop` wraps trycua/cua's driver, and is built only if a time-boxed trial on the real Xorg session passes. Config chooses between them.
  - Skills reach it only through the three `desktop.*` actions.

`Actions` is a capability object. It holds one skill's allow list, raises `NotAllowed` for anything else, and never raises for an action that fails (it returns `ok=False`).

**Done when:**

- `daybook music play focus` then `daybook music play calm` audibly fades and switches on the GX10.
- `min_dwell` refusals are tested, including that a break's mood change doesn't delay the music skill's next change. When `break` and `music` fire on the same tick, in either order, break music plays until the overlay is dismissed.
- `daybook music check` passes on the downloaded library.
- An overlay request round-trips to a connected shell, and fails cleanly without one.
- Every `desktop.*` action works under `XdoDesktop` on `:1`.
- The catalog's prompt description lists every action and the current moods.
- If the cua trial is run: either `CuaDesktop` passes the same action tests, or the trial result is recorded in [decisions.md](planning/decisions.md) and the backend is dropped.

### 6.8 `chat`: recall and skill requests

**Owns** routing and recall. `Chat.respond(message)` returns one of three replies:

- `Answer(text, cites, window)`
- `DraftReady(entry, summary)`
- `Plain(text)`

**Routing:** one short INTERACTIVE call returns `recall`, `make_skill`, or `other`. Messages starting with `/ask` or `/skill` skip the router, as a demo safety net.

**Recall:**

1. Resolve the time window in code from a rule table: "today", "last hour", "this morning", "yesterday", "last N minutes". The default is today.
2. Build the context:
   - `portal.context(window, 8000, detail = observations if the window is 2 h or less, else segments)`
   - `portal.totals(window, by="title")` and `by="kind"`, rendered as a table
   - search hits if the question names something specific
3. Make one call. The answer is 1–5 sentences, cites segment IDs, takes durations only from the totals table, and says "I don't have notes for that" when the notes lack it.

**Done when:**

- `resolve_window` is tested on the demo's exact questions.
- Routing is tested on 10 example messages with the real model.
- Recall over a fixture day answers "what did I do in the last hour?" with correct segments and durations.
- A question about a paused span says there are no notes for it.

### 6.9 `web`: routes and the UI

**Owns** the HTTP contract and the static UI. It is plain HTML with ES modules and no build step. Each route calls one method on the module that owns the data, and holds no logic. The functions here that turn domain types into JSON are the only place the wire format is defined.

**Security:**

- It binds to `127.0.0.1:8765`.
- Every mutating route requires the per-launch token (`X-Daybook-Token`) and a same-origin `Origin` header.
- The token is embedded in the served HTML and passed to the shell through its environment.

**Routes** (from the Opus candidate's `web.py`):

| Method and path | Calls |
|---|---|
| `GET /api/timeline?from&to` | `portal.segments`, `capture_spans` |
| `GET /api/now` | `portal.now` |
| `PATCH/DELETE /api/segments/{id}` | `edit_segment`, `forget_segment` |
| `PATCH/DELETE /api/observations/{id}` | `edit_observation`, `forget_observation` |
| `POST /api/forget {from,to}`, `POST /api/forget-all` | `forget_range`, `forget_all` |
| `POST /api/chat {message}` | `chat.respond` |
| `GET /api/skills`, `PUT /api/skills/{id}`, `POST /api/skills/{id}/approve {hash}` (409 if stale), `POST /api/skills/{id}/enable` (409 if needs review), `POST /api/skills/{id}/disable`, `DELETE /api/skills/{id}`, `POST /api/skills/parse {text}` | `SkillBook` methods and `parse_skill` |
| `GET/PUT /api/privacy` | `sense.privacy`, `set_privacy` |
| `POST /api/overlay/{id}/dismiss` | `overlay.hide` |
| `GET /api/status` | gateway, music, overlay, and capture status |
| `GET /api/events?client=ui\|shell` | SSE stream of every bus event |
| `POST /api/tick` | `mind.tick_now` (debug and demo button) |

**Done when:**

- Route tests run against fakes.
- A mutating call without the token or with a foreign `Origin` is rejected.
- SSE delivers live timeline updates to the browser.
- Every UI page in [§8](#8-user-interface) works against a replay instance.

### 6.10 `shell`: the tray and overlay process

**Owns** nothing persistent. It's a separate process because Qt needs the main thread and its own event loop. It subscribes to `/api/events?client=shell`.

- **Overlay.** One pre-created **Qt Quick (QML)** window. It's frameless, always on top, and full-screen on `:1`, and it's reused so it appears in under 300 ms.
  - It renders the `OverlayRequest`: title, body, a countdown for `minutes`, and a dismiss button.
  - It has two styles (`calm`, `alert`), with a translucent dark backdrop and a fade-in.
  - Dismiss posts `/api/overlay/{id}/dismiss`, and so does pressing `Esc`.
  - The window class is `daybook-shell`.
- **Tray.** A StatusNotifierItem icon. It needs GNOME's AppIndicator extension; see [setup.md](setup.md).
  - The icon shows the capture state: recording, paused, private, or locked.
  - The menu has Pause/Resume (`PUT /api/privacy`), Open Daybook (`xdg-open http://127.0.0.1:8765`), and Quit.
- **Environment** when spawned:
  - `DISPLAY=:1`
  - `XAUTHORITY=/run/user/1000/gdm/Xauthority`
  - `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`
  - `DAYBOOK_TOKEN`

**Done when:**

- `daybook overlay test` shows the overlay full-screen on the GX10 above a maximized window. It dismisses by button and by `Esc`, and auto-hides on its countdown.
- While the overlay is up, sense reports `OVERLAY`.
- The tray icon is visible, reflects pause within 5 s, and pauses capture from its menu.
- If `daybookd` restarts, the shell reconnects.

### 6.11 `app`: composition and CLI

**Owns** configuration (`daybook.toml`), wiring every module together, child processes, the capture lock, and the CLI ([§9](#9-repository-configuration-and-commands)). It's the only module that imports every other one.

**Done when:**

- `daybook run` starts every loop, mpv, and the shell.
- A second real-screen instance fails fast with a message pointing at `--replay`.
- Killing mpv or the shell restarts it.
- Each CLI subcommand works.
- A missing config file is created with defaults.

## 7. Privacy

Each control is enforced at one boundary:

| Control | Enforced by | How |
|---|---|---|
| Pause / resume | `sense` | `privacy.json` is the persisted switch. Pausing:<ul><li>increments the **consent generation**</li><li>makes the gateway drop pending OBSERVE work</li><li>discards any model result started under an older generation before it reaches the journal</li><li>hides the overlay</li><li>stops skills from reacting</li></ul>Music keeps playing; the user stops it separately |
| Exclusions | `sense.gate` | Checked against the visible part of every window before any pixels are read, and the full gate runs again after the grab. Withheld time is a `WITHHELD` capture span. Excluded window titles are never stored or logged. Changing exclusions increments the consent generation |
| Locked screen | `sense.gate` | `LockedHint`, an empty active window, an unreadable state, or a black thumbnail all count as locked. Fails closed |
| Away | `sense.gate` | 5 minutes without input is `AWAY`: no capture, no ticks, and the idle stretch is trimmed from the last observation |
| Screenshots | `sense` + `model` | Frames exist only as bytes inside the observe builder and the HTTP request. Nothing is queued or written to disk. `debug.keep_frames` is off by default and shown in the UI if turned on |
| Edit / delete notes | `journal` | Edits mark segments as user-authored. Deletion of an observation, a segment, a time range, or everything removes the rows and their FTS entries in one transaction. Then the journal runs `PRAGMA wal_checkpoint(TRUNCATE)`, so the old pages don't stay in the write-ahead log. `secure_delete=ON` and FTS5 `secure-delete` overwrite freed content. If the checkpoint can't truncate because a reader is active, it retries after the reader finishes, and the privacy page shows "deletion pending" until it succeeds. This removes note text from the database files; it doesn't claim to defeat forensic recovery from the SSD itself. A deleted observation can't come back through a later tick, and the forget epoch makes the journal reject any observe or tick result that started before the forget |
| Logs | every module | Logs hold timings, states, IDs, and error kinds. Never prompts, frames, titles, or note text |
| Local API | `web` | Loopback only, plus the per-launch token and `Origin` check |
| Model server | setup | Request logging off on Ollama/vLLM, verified in [setup.md](setup.md) |

Retention: notes stay until the user deletes them. The privacy page offers "forget last 15 minutes" and "forget everything".

## 8. User interface

One web page with tabs, titled `Daybook · <tab>`. That title is excluded from capture by default.

- **Timeline.** Today's segments as cards with title, kind, intent (labeled *inferred*), and active time. Each card expands to its observations. Capture spans (paused, locked, private, overlay) show as gaps. Segments and observations are editable inline and can be deleted. Updates arrive live over SSE.
- **Chat.** The recall answer, with citations that link to timeline segments. A skill draft renders as a review card.
- **Skills.** A list showing each skill's status (active, needs review, disabled, invalid). The review card shows:
  - the goal
  - the signals as plain questions
  - the rules in words ("When *in a meeting* becomes *false*: show an overlay…")
  - the permissions (from the catalog)
  - the raw file

  Under the card are an editor with live parse errors and Approve & enable (posts the reviewed hash), Enable / Disable (once approved), and Delete buttons.
- **Privacy.** Pause toggle, exclusions editor, "screenshots stored: none", the forget buttons, and the model status.
- **Footer** on every page. Capture state, now playing, model name, reachability, and calls per minute.

The overlay and tray are described in [§6.10](#610-shell-the-tray-and-overlay-process).

## 9. Repository, configuration, and commands

```text
pyproject.toml            # Python 3.12, uv; deps: fastapi, uvicorn, httpx, pydantic, pyyaml, mss, python-xlib, pillow, PySide6-Essentials
src/daybook/
  app.py  events.py
  journal/  model/  sense/  mind/  skills/  act/  chat/  web/ (static/)  shell/ (overlay.qml)
skills/builtin/           # music, break, break-demo (approved by being in the repo)
fixtures/replay/<scenario>/   # recorded steps: frame PNGs, window.json, lock flags, expected labels
scripts/check.sh          # the local checks
scripts/check_boundaries.py
tests/
docs/
```

`$DAYBOOK_HOME` (default `~/.daybook`; each development instance uses its own, such as `~/.daybook-<agent>`) holds `daybook.toml`, `journal.db`, `privacy.json`, `skills/`, and `mpv.sock`.

```toml
# daybook.toml
port = 8765
display = ":1"
xauthority = "/run/user/1000/gdm/Xauthority"
music_dir = "~/Music/daybook"
desktop_backend = "xdo"        # or "cua"
keep_frames = false
tick_interval = "90s"         # "30s" for the live demo, so short streaks confirm on time

[model]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3.6:35b"
api_key_file = ""              # "~/.config/qwen38/api-key" for vLLM
dialect = "ollama"             # "vllm" | "openai"
```

**CLI:**

```sh
daybook run [--replay DIR] [--no-capture] [--port N]   # daybookd: capture, tick, skills, web; spawns mpv and the shell
daybook observe-once                                   # one real capture -> printed observation (not saved)
daybook tick-once                                      # one tick on current unassigned observations, prints readings
daybook record DIR [--minutes N]                       # record real steps into a replay fixture (frames saved; dev only)
daybook skill check FILE                               # parse_skill + errors
daybook music check | play MOOD                        # library consistency; play a mood
daybook overlay test                                   # show a test overlay through a running daybookd
```

`daybook record` is the one deliberate exception to "frames never touch disk". It exists to build fixtures, it's labeled as such, and it refuses to run while `daybookd` is capturing.

## 10. Development and testing

- **Local checks** (`scripts/check.sh`, referenced from [CONTRIBUTING.md](../CONTRIBUTING.md)): `ruff check`, `ruff format --check`, `pyright`, `pytest -q`, and `check_boundaries.py`. The boundary check fails on:
  - any import of `daybook.<x>._*` from outside `<x>`
  - `sqlite3` outside `journal` and `skills._ledger`
  - `httpx` outside `model` and `shell`
  - `PySide6` outside `shell`
  - `Xlib` or `mss` outside `sense`
- **Fakes, so most work needs neither the screen nor the model:**
  - `ReplayScreen`
  - `ScriptedGateway`
  - a temporary-file journal
  - a fake `DesktopControl`
  - a recorded mpv IPC transcript
- **Replay fixtures** are recorded on the GX10 with `daybook record` and committed with expected labels (kind, mood, focused, in_meeting). At minimum:
  - **coding → reading:** VS Code, then a PDF
  - **meeting → work:** a Meet tab, then the editor
  - **focus streak:** 3+ minutes of coding
  - **lock and exclusion:** a locked frame, then a password manager in the background

  The fixtures are the quality gate for prompts and for any model swap.
- **Real-screen work** happens only when nobody else is running a capture loop (the flock enforces this), and only with the screen unlocked by someone at the machine.

## 11. Build plan

The plan is the complete system above, built module by module. Every module's scope and "Done when" checks are in [§6](#6-modules). This section adds the work that isn't a module, the real dependencies between pieces, and the end-to-end acceptance that defines "finished".

### 11.1 Work items

| Work item | Full scope | Depends on |
|---|---|---|
| **Contracts** | The package skeleton, `pyproject.toml`, `scripts/check.sh`, and `check_boundaries.py`. Every public type and signature from §6 as stubs. The events list, catalog action names and parameters, and the three built-in skill files. This is a shared-contract PR: it waits for review | — |
| **GX10 setup** | Everything in [setup.md](setup.md): apt packages, the AppIndicator extension, `uv`, `gh`, the music library (20–30 CC-licensed tracks with `library.toml`), confirming model server logging is off | — |
| **Replay fixtures** | The four scenarios in §10, recorded on the GX10 with expected labels | Contracts (`daybook record` needs `sense`'s `Screen`), GX10 setup |
| `journal` + `events` | §6.1, §6.2 | Contracts |
| `model` | §6.3 | Contracts |
| `sense` | §6.4 | Contracts; fixtures for replay tests |
| `mind` | §6.5 | Contracts; tested with `ScriptedGateway` and fakes; the real-model quality gate needs fixtures |
| `skills` | §6.6 | Contracts |
| `act` | §6.7 | Contracts; the music tests need the library from GX10 setup |
| `chat` | §6.8 | Contracts |
| `web` + UI | §6.9, §8 | Contracts; tested against fakes |
| `shell` | §6.10 | Contracts; the tray needs the AppIndicator extension |
| `app` | §6.11 | Contracts; full wiring needs the other modules |
| **cua trial** | At most one hour: install trycua/cua's linux-arm64 driver, and run the `desktop.*` action tests through a `CuaDesktop` adapter on the real `:1` session. Record the result in decisions.md | Contracts, GX10 setup |
| **Demo** | [demo.md](demo.md): the presenter script, the pre-approved `break-demo` skill, and rehearsals on the GX10 | End-to-end acceptance |

Everything after Contracts can be built at the same time against fakes. The only real ordering is: Contracts first, then the end-to-end checks once the modules exist.

### 11.2 End-to-end acceptance

The system is finished when all of these pass live on the GX10, with the real model, unlocked screen, and real music:

1. **Notes:** ten minutes of real use produce a timeline with sensible segments and inferred intents. Locked and paused time show as gaps, and `totals()` matches the wall clock within one poll interval per segment.
2. **Music follows activity:** switching from VS Code to reading a PDF changes the music from focus to calm within about 30–60 s. Switching back returns to focus. The music never changes more often than `min_dwell`.
3. **Break:** with `break-demo` (2 minutes), focused work brings up the full-screen overlay and break music. Dismissing it returns to focus music on the next tick. A second overlay doesn't appear for the same streak.
4. **Chat-created skill:** typing "remind me to stand up every time I finish a meeting" produces a review card within 30 s. After enabling it, leaving a Meet tab for the editor shows the stand-up overlay once. Editing the file returns it to "needs review".
5. **Recall:** "what did I do in the last hour?" answers with the right segments and durations, with links into the timeline. Asking about a paused stretch says there are no notes.
6. **Privacy:**
   - **Pause** (tray and web) stops capture within 5 s, and no observation is written for work started before the pause.
   - **Exclusions:** a password manager visible in the background withholds capture.
   - **Locked screen:** a locked screen writes no observations.
   - **No screenshots on disk:** `find $DAYBOOK_HOME /tmp -newer <start> \( -name '*.jpg' -o -name '*.png' \)` finds nothing.
   - **Forgetting:** "forget last 15 minutes" removes those notes from the timeline and from search.
7. **Offline:** with the network disabled, items 1–5 still work.
8. **Budget:** the footer shows at most 8 background calls per minute during normal use.

## 12. Risks and open questions

| Risk | Mitigation |
|---|---|
| Batched signal answers are poor with `qwen3.6:35b` at effort `none` | The quality gate on replay fixtures in §6.5. The fallback is effort `low` on ticks only (+1–3 s, within budget). Keep each skill's questions concrete |
| The QML overlay isn't kept above full-screen apps under GNOME's X11 window manager | Tested by the `shell` done-when check. The fallback is to raise it with `wmctrl -r <title> -b add,above` after showing |
| The tray doesn't show | The AppIndicator extension setup step, then a GNOME Shell restart (X11: `Alt+F2`, `r`). The web UI's pause toggle covers the demo regardless |
| Qwen3.8 isn't ready, or can't read images | Stay on `qwen3.6:35b`. Switching is a config change gated on `accept.py --vision` plus the fixtures |
| Venue Wi‑Fi is shared with the 124 GiB Qwen3.8 download | The music download and package installs are small. Avoid large downloads (for example, PySide6-Addons isn't used) |
| One screen and one capture loop for everyone | `--replay` development instances and the capture flock. Real-screen checks happen at the machine |
| A translucent window that doesn't set `_NET_WM_WINDOW_OPACITY` (some apps draw transparency through a 32-bit visual instead) could cover an excluded window and still show it | Known limitation. Treating every 32-bit-visual window as see-through would withhold capture behind Chrome and Electron apps. Keep translucent terminals and widgets off in the demo; revisit with a per-app opacity list if needed |
| Passive time (a long video, reading without scrolling) counts as away after 5 minutes without input | Microphone use counts as presence, so calls are covered. Five minutes keeps normal reading inside the threshold. Tune it from the replay fixtures if needed |
| A "meeting ended" false positive from a brief tab switch | `becomes false` needs a prior known reading within 5 min, and the tick sees the last 30 min of context. The demo uses a clear switch from Meet to the editor |

Open questions for the user:

- **Product name.** `daybook` is the working name; renaming the package is cheap before the Contracts PR merges.
- **Withheld time on the timeline.** Should it show as a "private" gap, which is the current design, or not appear at all?
