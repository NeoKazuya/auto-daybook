# Arena synthesis record

PStack `arena` Phases C–F for the architecture. The task given to every runner is in [TASK.md](TASK.md). The result is [docs/ARCHITECTURE.md](../../ARCHITECTURE.md).

## Runners

| Runner | Engine and model | Effort | Ran | Output |
|---|---|---|---|---|
| `codex` | Codex CLI 0.159.2, `gpt-6-astra` | high, fast service tier | 16:56–17:01 PT | [candidate-codex/](candidate-codex/) |
| `opus` | Claude Code, `claude-opus-5-5` | high | 17:06–17:17 PT | [candidate-opus/](candidate-opus/) |

Run history, recorded so the result can be judged honestly:

- **First launch (16:52):** used `fanout_start`, which ran Codex at the config default (`medium`) and mapped `opus` to `claude-opus-5` with no effort flag. The user asked for top settings, so both runs were stopped after about 3 minutes. Their partial output was discarded.
- **Second Opus launch (16:56):** ran at `xhigh`. It spent more than 9 minutes in one reasoning pass with nothing written. It had also read this file (the rubric) because the file sat in the arena folder. That run was stopped and discarded.
- **Final Opus run:** this file and the finished Codex candidate were moved out of the folder for the whole run. Opus saw neither.
- **Codex:** its log shows it only listed this file's name in a directory listing and never opened it.

## Rubric

Written before reading any candidate. Runners didn't see it. Criterion 1 was reworded on 2026-10-10 at the user's direction (Codex orchestrates the build), still before any candidate was read. Score each criterion 1–5.

1. **Modular from the start.** Build orchestration is Codex's job, not this design's. The design only has to make that easy: clear modules behind small typed contracts, each buildable and testable on its own with fakes or replays (no real screen or model needed for most work), and no module reaching into another's internals.
2. **Demo reliability.** The music skill, break overlay, chat-created skill, and recall work end to end with any OpenAI-compatible vision model. The design degrades sensibly on a locked screen, a slow or swapped model, a missing music tag, or a bad generated skill file.
3. **Model budget.** One shared model at about 3 s per image call. The design states its cadence and calls per minute and stays within them. Simple filtering (change detection, exclusions, timestamps) is done in code, not by the model.
4. **Interface depth and ownership.** One owner per piece of state (notes, skills, settings). Small portal and action interfaces that hide real work. No design red flags (split ownership, two ways to do one task, hand-synced lists, importable internals).
5. **Extensibility the product promises.** A new skill is a text file (plus an optional hook) with no core changes. Allowed actions are whitelisted per skill. A computer-control framework could replace the in-house actions behind the same interface.
6. **Privacy built into the structure.** Pause, exclusions, screenshot deletion, and note editing are enforced at the right boundary, not by convention.

## Cross-judge

Skipped at the user's direction (2026-10-10), to save time. The parent (Claude Opus 5.5, this session) scored the candidates directly against the rubric.

## Are the candidates structurally distinct?

Yes. They differ on the load-bearing decision, so this is not one shape under two names:

- **Codex: the model decides.** Each skill states its trigger as natural-language prose (`when:`). Every 30 s, one joint model call reads the context and the due skills' goals and returns typed action proposals. Code enforces grants, cooldowns, and deduplication scopes. Notes carry explicit coverage records, evidence tokens, and a memory epoch that invalidates stale work.
- **Opus: the model answers questions, code decides.** Each skill declares typed *signals* (questions). One tick call every ~90 s does the note rollup and answers every active signal. A pure evaluator then runs a 4-form trigger grammar over the stored readings. Segment times are derived from observations.

## Scores

| Criterion | codex | opus | Notes |
|---|---:|---:|---|
| 1 Modular | 4 | 5 | Both have clear modules and an import-boundary check. Opus also gives every module a fake: `ReplayScreen`, `ScriptedGateway`, a pure `evaluate()`, and `--replay` dev instances that never touch the real screen. Codex relies on text fixtures and fake adapters with less detail. |
| 2 Demo reliability | 4 | 5 | Opus's triggers are deterministic code over typed readings, so the same notes always fire the same way. Codex's trigger conditions are interpreted by the model on every tick, which is less predictable on stage. Opus's music/break interplay heals itself: the overlay suppresses capture, so the music skill can't override break music. Codex has more failure handling, but more of it is machinery that can itself fail. |
| 3 Model budget | 4 | 5 | Opus: background load is fixed at one observe call per screen change (thumbnail diff, at most one per 10 s) plus one tick per ~90 s, however many skills exist. The live budget shows in the UI. Codex: a fixed 15 s capture regardless of change, plus a separate skill-decision call (≈6.5 calls/min). It deliberately deferred change detection. |
| 4 Interface depth and ownership | 4 | 4 | Both are deep. Codex's evidence tokens and epochs spread concurrency rules across modules. Opus keeps skill-owned tables (approvals, firings) inside `journal.db`; that's acceptable because only `skills._ledger` writes them. |
| 5 Extensibility | 4 | 4 | Codex's prose triggers can express anything, but only as reliably as the model reads them. Opus's 4-form grammar is limited, but any new question becomes a signal with no core change, and the grammar is reviewable in seconds. Both have a single action registry and a pluggable desktop backend. |
| 6 Privacy | 5 | 4 | Codex is stronger: it fails closed when the lock state is unknown, suppresses capture if *any* visible window is excluded (not just the active one), makes pause a barrier that discards in-flight work, keeps metadata-only logs, uses SQLite `secure_delete`, and adds Origin and token checks on the local HTTP API. Opus checks only the active window, and pause doesn't cancel in-flight work. |
| **Total** | **25** | **27** | |

## Pick

**Base: `opus`.** It's the one a future maintainer, or Codex orchestrating agents, can extend most easily without breaking invariants. The model-budget property (no per-skill model calls) and the deterministic trigger evaluator make the demo predictable, and every module has a fake. It loses only on privacy, and that gap is fixed by grafting from Codex.

## Grafts

All taken from `codex` and folded into the Opus shape by hand:

1. **Exclusions cover every visible window, not just the active one.** A screenshot shows everything on screen, so sense enumerates mapped, viewable top-level windows on the current desktop and withholds the capture if any of them matches an exclusion. *Adapted:* there's no X server grab (rejected below), so a window that appears between the check and the grab can slip through. A second check after the grab discards the frame if the window set changed.
2. **Fail closed.** If the lock state can't be read, or the active window can't be read, treat it as locked. A black thumbnail also counts as locked (Opus already did this).
3. **Pause is a barrier.** Sense keeps a consent generation counter that increments on pause, resume, or any exclusion change. The gateway drops queued OBSERVE work, and any model result that started under an older generation is discarded before it reaches the journal. Pause also hides an active overlay. Skills don't react while paused. Music keeps playing (pausing capture shouldn't silence the room); the user stops it from the UI or tray.
4. **Gateway hardening.** At most 2 consecutive INTERACTIVE calls before the oldest waiting background call runs, so ticks don't starve during a chat burst. At most one pending OBSERVE: a newer request replaces an older one, and the frame is grabbed only once the slot is granted. A 40 s timeout per request. A failure becomes a gap and a visible status, never a retry storm.
5. **Local HTTP hardening.** Bind to `127.0.0.1` only. Every mutating route requires a per-launch token and a matching `Origin`. The shell gets the token through its environment, and the web page gets it embedded in the HTML.
6. **Logs carry no content.** Logs hold timings, states, IDs, and error kinds. They never contain prompts, screenshots, window titles, or note text. Model servers are run with request logging off, and setup verifies that.
7. **SQLite `secure_delete=ON`**, so forgotten notes are overwritten on disk.
8. **Chat writes declarative skills only.** Hooks are developer-written Python, reviewed in the repo. This answers an open question in the Opus candidate.
9. **Demo variant instead of a live edit.** A separately reviewed `break-demo` skill uses a 2-minute threshold. The real `break` keeps 50 minutes. This answers another open Opus question; editing on stage would force a re-review mid-demo.

## Rejected

- **Codex: grabbing the X server during capture.** Freezing the whole display, even briefly, risks visible stutter during a live demo. A post-grab recheck gets most of the benefit.
- **Codex: evidence tokens.** At-most-once firing claims and derived segment times already cover the races that matter for actions. *Revised after the PR #2 review:* a single journal **forget epoch** was adopted, because the review showed an in-flight observe or tick could write back just-forgotten content. Only sense and mind carry it, not every module.
- **Codex: model-interpreted trigger prose and a joint decision call.** Less predictable, and it costs a second background call stream.
- **Codex: a separate `skills.sqlite`.** One database file with tables owned per module is simpler to back up and forget from.
- **Codex: always restarting paused.** Pause state is persisted in `privacy.json` and restored as it was.
- **Opus: rendering the overlay with QtWebEngine.** The overlay is drawn with Qt Quick (QML) from the event payload instead. That avoids an embedded Chromium on aarch64 and a 64 MB extra wheel, while still giving animation and styling.

## Verification

Checked on the GX10 on 2026-10-10 after synthesis. Each item tests an assumption the chosen design depends on:

| Assumption | Result | How |
|---|---|---|
| A tray icon will show on GNOME | **No, not yet.** Only `user-theme` is enabled, so StatusNotifierItem icons won't show until the AppIndicator extension is installed and enabled. Added as a required setup step ([docs/setup.md](../../setup.md)) | `gnome-extensions list --enabled` |
| The Qt shell can reach the session D-Bus from SSH | Yes, `/run/user/1000/bus` exists | `ls -la /run/user/1000/bus` |
| PySide6 installs on aarch64 | Yes. PyPI has `PySide6-Essentials 6.12.0` (`manylinux_2_39_aarch64`, 84 MB), which includes Qt Quick. Ubuntu 24.04 ships glibc 2.39. `Addons` (QtWebEngine) isn't needed | PyPI JSON API |
| Vision model reachable with JSON output | Yes. `qwen3.6:35b` through Ollama's OpenAI-compatible API returned valid JSON in 2.9 s | [grounding](../grounding.md) |
| X11 lock state and active window are readable from SSH | Yes, with `DISPLAY=:1` and the session `XAUTHORITY`. While locked, the active window is empty and frames are black, which is why the gate treats both as locked | [grounding](../grounding.md) |

Not yet verified, and called out as risks in the architecture:

- the quality of the batched signal answers on real screens
- whether QML full-screen always-on-top works under GNOME's X11 compositor
- mpv on the box (not installed yet)
