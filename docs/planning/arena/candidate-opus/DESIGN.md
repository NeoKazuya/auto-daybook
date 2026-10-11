# Candidate `opus`: skills ask questions, the tick answers them all at once

## Problem

We need a local agent that watches the GX10's one X11 screen, writes notes in two layers (raw observations, then model-rolled activity segments), and lets skills read those notes and act. Three skills must work live by Sunday: music that follows the activity, a break overlay after long focus, and a skill the user asks for in chat. Recall answers come from the same notes. The shape isn't obvious because of one hard constraint: **there is exactly one model, it's serial, and each call takes about 3 s.** Capture, rollup, every skill's "what's going on?", chat, recall, and skill generation all compete for it. If each skill reasons over the notes with its own model call, the load grows with every enabled skill, and every chat-made skill makes the music react more slowly. Other constraints from Phase A: X11 on `:1` with the session's `XAUTHORITY`; locked screens capture black frames; the model backend is swappable (Ollama today, possibly vLLM Qwen3.8 later, with vision unverified); the computer-control framework is undecided (D15), so actions need a plug-in point; skills are plain-text files with an optional Python hook (D3); 14–16 build hours across up to 3 owners on one shared box.

## Usage (caller's view)

Full call sites are in [`sketch/usage.py`](sketch/usage.py). Here is the summary.

```sh
daybook run                      # one process: capture, tick, skills, web UI on :8765; spawns mpv and the Qt shell
daybook run --replay fixtures/   # dev instance on a shared box: replays recorded screens, never touches :1
daybook skill check my.skill.md  # same parser the loader and the generator use
```

A skill is a question plus a reaction. This is the whole break reminder:

```yaml
signals:
  focused: { ask: "Is the user doing sustained, focused work ...?", type: bool }
rules:
  - when: focused is true for 50m
    do:
      - overlay.show: { title: Time for a break, body: "Focused for $focused.for.", minutes: 5 }
      - music.play_mood: { mood: break }
allow: [overlay.show, music.play_mood]
```

Code that reads the notes goes through one `Portal`:

```python
seg = portal.now().segment                                   # what's happening now
portal.segments(TimeRange.today())                           # the timeline
portal.totals(TimeRange.last(hours=1), by="title")           # "how long did I work on this?" (code does the math)
portal.context(window, budget_tokens=8000, detail="segments")  # notes as prompt text, used by recall and the tick
```

Chat has one entry point, and approval is bound to the exact bytes the user reviewed:

```python
reply = await chat.respond("remind me to stand up every time I finish a meeting")  # -> DraftReady(entry, summary)
book.approve(entry.id, entry.source_hash)   # StaleApproval if the file changed after review
```

## Shape

### The load-bearing decision

**Skills never call the model.** A skill declares *signals*: named questions with a type (`bool`, `enum`, `number`, or a catalog type such as `music.mood`). About every 90 s, the **mind** makes one text-only model call that does two jobs. It rolls the new observations into a segment, and it answers every active skill's questions in one JSON object whose schema is generated from those questions. The answers are stored as `SignalReading` rows. Then each skill's rules are checked by a **pure** trigger evaluator over the reading history, and matching rules run named actions from one **catalog**. Background model load is therefore fixed: one observation call per screen change plus one tick per ~90 s, however many skills exist. A chat-created skill adds about 30 prompt tokens to the tick, not a model call.

### Data structures first (see [`sketch/journal.py`](sketch/journal.py))

- `Observation(started_at, last_seen_at, window: WindowInfo, seen, visible_text, segment | None)`. `window` comes from X11, not the model. `seen` is description only. While the screen stays unchanged (thumbnail diff < 3%), sense pushes `last_seen_at` forward without a model call, so time spent is `sum(observation.active)`.
- `Segment(title, kind, intent, authored: "model" | "user", open)`. **Times are derived** from member observations at read time, never stored. Deleting an observation shrinks its segment automatically.
- `SignalReading(signal: SignalId(skill, name), value | None, at, tick)`. These rows are the history that triggers read. "Held for 50 minutes" is computed from readings, not timers, so it survives a restart.
- `CaptureSpan(state: running | paused | locked | withheld | overlay)`. The timeline shows honest gaps, and recall won't invent activity in them.
- `Firing(skill, rule, key)`, unique in the ledger. This makes rules at-most-once and idempotent.

Access patterns traced: the timeline reads segments by time range and joins observations by `segment` (indexed). Triggers read `readings(signal, range)` (index on signal, at). Recall reads `context()` plus `totals()`, with FTS5 for "the auth bug". The tick reads `observations(unassigned_only)`. None of these needs a cache or a second copy.

### Runtime picture

| Process | Loop | Cadence |
|---|---|---|
| `daybookd` (asyncio, one Python process) | **sense**: lock, pause, and exclusion gate; 32×18 thumbnail; dedupe; observe | poll every 5 s; model observe on change, at most one per 10 s, immediately on app switch |
| | **mind**: tick (rollup + signals) → `journal.record_tick` → `skills.react` | every 90 s, 20 s after an app switch, at least 30 s apart, only when there are new observations |
| | **web**: FastAPI + SSE + static UI | on request |
| `daybook.shell` (Qt child) | tray + reusable full-screen overlay, a view of `/api/events` | event-driven |
| `mpv --idle` (child) | music, over a JSON IPC socket | event-driven |
| Ollama / vLLM (external) | the one model | serial |

All model calls go through `ModelGateway.call(priority, prompt)`: one call in flight at a time, ordered INTERACTIVE > TICK > OBSERVE. Capture passes a *builder*, so the screenshot is taken only when its slot is granted. The frame is never queued and never written to disk.

| Call | Where | Input | ~Latency | Rate cap | Typical s/min |
|---|---|---|---|---|---|
| observe | sense | 1 image (~950 tok) + ~150 tok | 3 s | 6/min (≤4 typical when busy) | 3–9 |
| tick | mind | text, 1.5–3K tok, signal schema | 2–4 s | 2/min | 2–3 |
| chat route | chat | ~300 tok | 1 s | on demand | — |
| recall | chat | ≤ 8K tok | 4–10 s | on demand | — |
| skill draft | skills | ~3K tok, effort low | 10–20 s | on demand | — |

Background load is about 5–12 s per minute (10–20% duty). A chat request waits behind at most one in-flight call (~3 s). The footer of the UI shows `GatewayStatus.calls_last_minute`, so the budget is visible live.

### Module map

| Package | Owns (single writer of) | Public surface | Could own |
|---|---|---|---|
| `daybook.journal` (+ `events.py`) | `journal.db` notes tables, FTS5, the event bus | `Journal` (writes by named caller), `Portal` (reads), types | A |
| `daybook.model` | endpoint config, priority slot, wire dialects, JSON-schema output, repair retry | `ModelGateway.call`, `Prompt`, `Priority`, `ScriptedGateway` | A |
| `daybook.sense` | screen access, `privacy.json` (pause, exclusions), the gate, dedupe, observe prompt | `Sense`, `Screen` (`X11Screen`, `ReplayScreen`), `gate()` | A |
| `daybook.mind` | tick scheduling, rollup + signal prompt, segment close on gaps | `Mind.run/nudge/tick_now`, `parse_tick` | B |
| `daybook.skills` | skill files, approvals + firings tables, trigger evaluation, rule running, generator | `SkillBook`, `parse_skill`, `evaluate`, `draft_skill`, `HookContext` | B |
| `daybook.chat` | routing, recall prompt, window resolution | `Chat.respond` | B |
| `daybook.act` | the action catalog, music library + mpv, overlay state, `DesktopControl` (xdo / cua) | `Catalog`, `Actions`, `build_catalog`, `OverlayRequest` | C |
| `daybook.shell` | Qt tray + overlay window (stateless view) | `python -m daybook.shell` | C |
| `daybook.web` | routes ↔ JSON, static UI | HTTP contract in [`sketch/web.py`](sketch/web.py) | C |
| `daybook.app` | config, composition, child processes, CLI, capture flock | `daybook ...` | A |

Each package exports through `__init__.py`. Internals live in `_*.py`. `scripts/check_boundaries.py` (part of the local checks) fails on any `from daybook.<x>._…` import from outside `<x>`, per *importable internals*.

### Skill file format

The format is `<id>.skill.md`: YAML front matter followed by a Markdown goal. An optional sibling file `<id>.hook.py` provides async functions that run as rule steps (`- hook: run`) with a `HookContext(portal, actions, skill, occurrence)`. The trigger grammar has exactly four forms:

- `<sig> updates`: level-triggered, every tick with a known value.
- `<sig> becomes <v>`: an edge; it needs a prior known reading within 5 min, so startup is never an edge.
- `<sig> is <v> for <dur>`: a streak with no gap over 5 min. It fires once per streak, keyed on the streak's start.
- `every <dur>`: fires only on ticks, so only while the user is present.

Arguments can be literals, `$sig`, or `$sig.for`. Full grammar and load-time checks are in [`sketch/skills.py`](sketch/skills.py). The three files, written out in full:

[`sketch/skills/music.skill.md`](sketch/skills/music.skill.md)

```markdown
---
id: music
name: Mood music
signals:
  mood:
    ask: Which kind of music fits what the user is doing right now?
    type: music.mood        # a catalog type: values and their descriptions come from ~/Music/daybook/library.toml
rules:
  - when: mood updates      # level-triggered: every tick with a known value; play_mood is a no-op if already playing that mood
    do:
      - music.play_mood: { mood: $mood }
allow: [music.play_mood]
---
Play local music that matches what I'm doing, and change it when my activity changes.
Focus music while I code or debug, calm music while I read, break music when I'm not working.
```

[`sketch/skills/break.skill.md`](sketch/skills/break.skill.md)

```markdown
---
id: break
name: Break reminder
signals:
  focused:
    ask: >
      Is the user doing sustained, focused work (coding, debugging, writing, studying)
      rather than browsing, chatting, watching media, or idling?
    type: bool
rules:
  - when: focused is true for 50m   # a streak breaks on a gap > 5 min (lock, pause, away), so a real break resets it
    do:
      - overlay.show:
          title: Time for a break
          body: You've been focused for $focused.for. Stand up, look away from the screen, get some water.
          dismiss: I'm back
          minutes: 5
      - music.play_mood: { mood: break }
allow: [overlay.show, music.play_mood]
---
After a long stretch of focused work, cover the screen with a break reminder and switch to break music.
```

[`sketch/skills/stand-after-meeting.skill.md`](sketch/skills/stand-after-meeting.skill.md), generated from chat:

```markdown
---
id: stand-after-meeting
name: Stand up after meetings
created_by: chat
request: remind me to stand up every time I finish a meeting
signals:
  in_meeting:
    ask: >
      Is the user currently in a video call or online meeting
      (Zoom, Google Meet, Microsoft Teams, Jitsi, a Discord or Slack call, or similar)?
    type: bool
rules:
  - when: in_meeting becomes false
    cooldown: 10m
    do:
      - overlay.show:
          title: Meeting's over
          body: Stand up and stretch for a minute before the next thing.
          dismiss: Done
          minutes: 1
allow: [overlay.show]
---
Every time I finish a meeting, remind me to stand up.
```

How music and break interact without coordination code: the overlay's window class is `daybook-shell`, so sense records `CaptureState.OVERLAY` and makes no observations while it's up. No observations means no tick, so the music skill can't override the break music. When the user dismisses the overlay, new observations arrive, the next tick says `mood=focus`, and `music.updates` switches the music back. The level trigger plus an idempotent `play_mood` heals itself without any state machine.

### Portal interface

`Portal` (in [`sketch/journal.py`](sketch/journal.py)) is read-only. It is used by the UI, recall, the mind, and hooks.

- `now() -> Situation`: capture state, open segment, latest observation, and the latest reading per signal.
- `segments(range)`, `observations(range, unassigned_only=False)`, `capture_spans(range)`.
- `search(text, range?) -> [Hit]` (FTS5).
- `totals(range, by=kind|title|app) -> [Total]`. **All duration arithmetic lives here.**
- `readings(signal, range)`: the trigger evaluator's only input.
- `context(range, budget_tokens, detail)`: the one renderer of notes into prompt text. No caller formats notes for the model on its own.

Declarative skills don't touch the portal at all. They read signals, and the signals came from the portal through the mind. Hooks and recall use it directly.

### Invariants and where they live

- **Types:** `Continue | Start` is the only way to change segments. `SignalId` is always namespaced. `Actions` is a capability that holds only the skill's allow list. `Prompt[T]` returns a `T` and never a dict.
- **Boundary validation:** `parse_skill` is the only parser, used by the loader, the editor, and the generator. `parse_tick` coerces each answer to its declared type, and an out-of-type answer becomes `None`. The model's JSON is validated inside the gateway against the output schema.
- **Single source of truth:** the catalog is the only action list (the generator prompt, the review UI, and validation all derive from it). `library.toml` is the only mood list (the `music.mood` type is derived from it, and `daybook music check` fails if the folders disagree). Segment times are derived from observations.
- **Idempotency:**
  - `record_tick` is unique on its newest observation.
  - `set_capture_state` doesn't write a duplicate row for the same state.
  - Firings are claimed by `(skill, rule, occurrence key)` before running.
  - `play_mood` is a no-op for the current mood.
  - `overlay.show` replaces the current overlay.
- **Ownership:** sense owns `privacy.json`, the journal owns the notes tables, skills owns approvals and firings, and the overlay owns "which overlay is up". The model owns segment text until a user edits it (`authored`). After that, the user owns it.

### Interface depth

The public surface is small: `gateway.call`, `Portal` (8 reads), `Journal` (writes by named caller), `SkillBook` (entries, questions, approve, disable, write, delete, react), `Catalog.allowed().do`, and `chat.respond`. Behind it sit priority scheduling, wire dialects, JSON repair, change detection, the privacy gate, rollup, per-tick schema generation, edge and streak detection, approval hashing, mpv IPC, and Qt. A skill author learns a 4-form grammar and a catalog. They never learn prompts, the database, or timing.

### What it deliberately does not do

- Skills don't call the model.
- There's no free-form clicking (desktop actions are named).
- There's no plugin sandbox for hooks.
- Screenshots aren't kept or queued, and there's no vector DB or embeddings (FTS5 is enough for one day of notes).
- There's no multi-screen support.

### Red-flag screen

- **Shallow module:** passes. Each package hides a policy behind 1–7 calls.
- **Information leakage:** passes. YAML stays in `skills._format`, HTTP and the wire dialect stay in `model._wire`, and SQL stays in `journal._sql`. `web.py` is the only place JSON shapes exist.
- **Temporal decomposition:** passes. The mind is not split into "rollup" and "signals" stages, because it's one call that owns "interpretation".
- **Pass-through:** the risk was `Portal` forwarding to SQL. It earns its place with `context()`, `totals()`, and derived segment times.
- **Split ownership:** checked. See the ownership list above. Approvals and firings live in `journal.db` but are tables owned and written only by `skills._ledger`.
- **Two ways:**
  - Hooks run only as rule steps, so there's one firing path.
  - There's one parser and one chat entry point.
  - Segment edits go only through `Journal`.
- **Hand-synced lists:**
  - The catalog derives the generator prompt.
  - `library.toml` derives the mood enum.
  - The event types live in one file.

## Synthesis decision

## Tradeoffs accepted

- We accept that skills react only at tick boundaries (about 30–90 s after a change) in exchange for a fixed model budget that doesn't grow with the number of skills.
- We accept that a batched tick answers each question a little less carefully than a dedicated call would, in exchange for one call instead of N. The cap is 4 signals per skill and 12 in total.
- We accept a 4-form trigger grammar that can't express everything ("only on weekdays", "if X and Y"). The generator falls back to a hook, or says it can't, in exchange for skill files a person can review in ten seconds.
- We accept at-most-once firing (a crash mid-rule drops that firing) in exchange for never showing a duplicate overlay or restarting the music twice.
- We accept that hooks run in-process with full Python. Review-before-enable plus hash binding is the control, not a sandbox.
- We accept no saved screenshots (note quality can only be debugged live or with `debug.keep_frames`) in exchange for a privacy claim that is simply true.
- We accept recomputing segment times on every read (cheap at hackathon scale) in exchange for no stored times that can drift.
- We accept one Python process for capture, tick, and web (a crash takes all three down; `daybook run` restarts under tmux or a `systemd --user` unit) in exchange for one SQLite writer and no internal wire protocol. Only Qt is split out, because it needs its own main thread.

## Alternatives considered

- **Agentic skills: each skill reads the portal and calls the model itself.** This is the obvious reading of "the skill infers the situation and decides". It lost because the surface is wide: every skill author writes prompts, parses output, and chooses a cadence. The load is also unbounded, with N skills meaning N calls per cycle on a serial ~3 s model, so a chat-made skill would visibly slow the music. Our shape pulls inference into one callee, and the skill surface becomes questions plus rules.
- **A fixed situation schema** (the tick always outputs `activity`, `mood`, `focused`, `in_meeting`, and so on), with skills only matching on it. The interface is smaller, but chat-created skills can't ask anything new ("am I on a call with my manager?"), and every new skill means editing the core schema, which is a shared-contract change for every request. Declared signals keep the core closed and skills open.
- **Microservices over HTTP** (capture daemon, notes API, skill runner, and UI as separate processes sharing SQLite). This adds multi-writer SQLite, internal wire types, and more startup choreography for no isolation benefit on one box. We rejected it in favor of one asyncio process plus a Qt view.
- **Code-first skills** (the model writes Python for every chat request). It's more expressive, but a person can't review generated code in a demo moment, and the allow list would be decorative. Declarative files with optional hooks keep review honest.

## Open questions and risks

- **Demo pacing:** is a 50-minute break threshold edited live to `2m` on stage acceptable, or should the demo config carry a "demo minutes" multiplier? (We lean toward editing the file live. It also shows that editing a skill makes it need review again.)
- Should withheld (excluded-app) time show as a "private" span on the timeline, or show nothing at all?
- Can a chat-generated skill include a hook in the demo, or do we restrict chat to declarative skills and keep hooks for hand-written ones?
- **Risk:** batched signal quality with qwen3.6 at `reasoning_effort: none` is unmeasured. The first real tick on recorded observations (build hour ~4) is the go/no-go. The fallback is effort `low` on ticks only (+1–3 s, still within budget).
- **Risk:** the tray needs GNOME's AppIndicator extension, and the Qt shell needs `DBUS_SESSION_BUS_ADDRESS` when launched from SSH. Check on the box in the first hour. If the tray fails, the web UI's pause toggle covers the demo.
- **Risk:** QtWebEngine on aarch64 PySide6 may be heavy or fail under X11 from SSH. The fallback is a QLabel overlay that renders from the event payload (the shell docstring describes it).
- **Risk:** only one person can run the real capture loop, and the screen must be unlocked by someone physically at the machine. Everyone else develops on `--replay`. The capture flock enforces this.
- Should we switch to Qwen3.8 if it finishes downloading? Only if `accept.py --vision` passes by build hour ~10. Otherwise stay on qwen3.6. It's a one-line config change either way.

## Build order (≈16 h, three lanes)

**H0–1.5, contracts PR (shared, merge first):** package skeleton plus local checks (ruff, pyright, pytest, `check_boundaries`). It also includes:

- `journal` types and the `Portal`/`Journal` signatures
- `skills` types plus `parse_skill`, tested against the three example files
- the catalog names and params in `act`
- `Prompt`, `Priority`, and `ScriptedGateway`
- the route list in `web.py`
- recorded `fixtures/replay/` (20 frames: VS Code, a PDF, YouTube, a Meet tab, plus one locked frame)

After this, the three lanes run in parallel, each tested against fakes until the integration milestones.

| Hours | Lane A: notes pipeline | Lane B: interpretation | Lane C: effects + UI |
|---|---|---|---|
| 1.5–4 | `ModelGateway` on Ollama (test: a fixture PNG returns a parsed observation in < 5 s); `journal` + FTS (tmp-DB unit tests) | `evaluate()` table tests (updates, becomes, held-for with gaps, every); `SkillBook` approve/hash/stale | install mpv; download 20–30 CC tracks with `library.toml` (D19); `MusicPlayer` (test: `daybook music play focus`, then `calm`, which fades and switches) |
| 4–6 | `X11Screen` + `gate` + dedupe (test: lock state on the box; replay run writes observations, unchanged frames extend) | `Mind.tick_now` with `ScriptedGateway`, then the real model on replay observations (**go/no-go on signal quality**) | Qt shell overlay + tray (test: `daybook overlay test` on `:1`, the overlay appears and sense reports OVERLAY) |
| 6 | **M1: music follows activity end to end on the real screen** (VS Code → focus, PDF → calm) | | |
| 6–9 | privacy: `privacy.json`, exclusions, capture spans; `observe-once` CLI | `react` + ledger + cooldown; break skill with `2m` (test: overlay + break music, then focus resumes after dismiss) | web timeline + edit/delete/forget + SSE live updates |
| 9 | **M2: break reminder live** | | |
| 9–12 | budget footer, restart supervisor, gateway resilience (model down → skip, UI shows it) | `draft_skill` + `Chat.respond` + `recall` + `resolve_window` tests on the demo's exact questions | chat page, skills review card + editor with live parse, privacy page |
| 12 | **M3: chat-created stand-up skill enabled and firing on a Meet tab close; recall answers "what did I do in the last hour?"** | | |
| 12–16 | full rehearsal ×3 with the demo script, fixes, notes-quality prompt tuning, a fallback recording of the demo | | |

## Next implementation step

Write the contracts PR: `parse_skill` and the `evaluate` signatures with the three example skill files as fixtures, plus the journal types, so all three lanes can start against them.
