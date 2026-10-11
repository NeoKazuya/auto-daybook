# Decision log

Answers from the team during architecture planning, in order. Each entry records the question, the answer, and what it implies for the design. The source of the product idea is `Local_AI_Project_Handoff (2).docx` (the project handoff, not in the repo).

Status: **decided** (the user answered), **default** (proposed by the planner and not objected to), or **open**.

## Process

| # | Date | Question | Answer | Status | Implication |
|---|---|---|---|---|---|
| P1 | 2026-10-10 | How do PRs flow? | Straight to `main`. No staging branch, no freeze, no GitHub Actions (to save Actions quota) | decided | [CONTRIBUTING.md](../../CONTRIBUTING.md). Checks run locally |
| P2 | 2026-10-10 | Who runs the workflow? | People only talk to their agents. Agents plan, claim, branch, verify, review, merge, and report back in fixed Plan / Done / Blocked formats | decided | CONTRIBUTING.md, "For agents" and "Talking to your person" |
| P3 | 2026-10-10 | When does review block a merge? | Every PR stays open through completed review. Fix confirmed findings in the same PR, rerun checks, then merge; no timeout-based merge exception | decided | Updated by the user: no merge-first/follow-up-PR review cycle; see [CONTRIBUTING.md](../../CONTRIBUTING.md) steps 5–6 |
| P4 | 2026-10-10 | When do agents stop and ask? | Only for irreversible actions, anything outside the repo, conflicting claims, and product direction (PStack `principle-never-block-on-the-human`) | decided | CONTRIBUTING.md, "Don't block on your person" |
| P5 | 2026-10-10 | Which engineering procedures? | Adopt PStack concepts from the canonical source (`cursor/plugins@e43c7ee2`), not inferred versions | decided | `docs/agents/*.md`, [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) |
| P6 | 2026-10-10 | How is the architecture planned? | With PStack `architect`: ground, then at least two structurally distinct candidates from different model families, then judge with a rubric and combine. Codex cross-checks the docs | decided | This folder; `docs/ARCHITECTURE.md` to follow |
| P7 | 2026-10-10 | Credentials in the repo? | Yes. It's a hackathon machine, so the GX10 login goes in [docs/gx10.md](../gx10.md) | decided | — |
| P9 | 2026-10-10 | How is the build split? | Two lanes, each building its part start to end: Lane A, the Codex wave (the complete backend; its first PR is the shared skeleton), and Lane B, a frontend agent (the web UI and the shell). One Tracker task per lane, no sequenced sub-tasks. Lanes meet only through `web/wire.py`; `--mock-api` lets Lane B start right after the skeleton | decided | [lanes/](../lanes/README.md); ARCHITECTURE §6.9, §11.1 |

## Product and system

| # | Date | Question | Answer | Status | Implication |
|---|---|---|---|---|---|
| D1 | 2026-10-10 | Where does the watched activity happen? | Everything on the GX10: it is the desktop being watched, and it runs capture, model, storage, portal, and actions | decided | One machine and one screen. No network hop for screenshots |
| D2 | 2026-10-10 | Language? | Python core (capture, model client, storage, portal API, skills) plus a small web UI | decided | — |
| D3 | 2026-10-10 | What is a skill? | Both: a plain-text skill file (goal, triggers, allowed actions) by default, with an optional Python code hook for exact logic | decided | The skill file format is a shared contract |
| D4 | 2026-10-10 | Team | 2–3 people, each with coding agents. No code existed beforehand | decided | Module boundaries sized for about 3 owners |
| D5 | 2026-10-10 | Where do people develop? | Everyone SSHes into the GX10, over Tailscale | decided | Shared box: per-agent worktrees, and only one capture loop on the real screen at a time |
| D6 | 2026-10-10 | Demo centerpiece | **No game.** The music skill picks music based on what you're doing | decided | The handoff's game/focus example is out of the demo |
| D7 | 2026-10-10 | Second built-in skill | Break reminder | decided | — |
| D8 | 2026-10-10 | Break reminder action | Build a **reusable full-screen overlay** feature, better than a standard notification | decided | The overlay is a shared action that any skill can use |
| D9 | 2026-10-10 | Skill creation in the demo | The user asks in chat, the model writes a skill file, and the user reviews and enables it | decided | Needs a chat UI and a skill-file generator with validation |
| D10 | 2026-10-10 | Music source | Local files for now | decided | `~/Music` is empty (see [grounding](grounding.md)), so the team must add tracks |
| D11 | 2026-10-10 | How the music is chosen | The model infers a mood from the notes, and code picks tracks with that tag | decided | Track tagging is needed (a manifest or folder names) |
| D12 | 2026-10-10 | Model for development | Test on an already-downloaded model first, while Qwen3.8 finishes downloading. It must be able to read images | decided | Model backend behind an OpenAI-compatible interface; Qwen3.6-35B via Ollama tested OK (see grounding) |
| D13 | 2026-10-10 | Computer control | User asked "what about computer control?" Research recommends a thin in-house module (mss, python-xlib, xdotool/wmctrl, python-mpv, PySide6 overlay), borrowing the Anthropic computer-use demo's action layer | default | See [computer-control.md](computer-control.md). Free-form VLM clicking is a stretch goal |
| D14 | 2026-10-10 | Where does the user interact? | Local web app plus a tray icon (pause and status) | decided | The tray needs an X11 tray host on GNOME (**inferred:** GNOME hides legacy tray icons without the AppIndicator extension; check on the box) |
| D15 | 2026-10-10 | How far should computer control go? | Use XdoDesktop for the three named desktop actions. The optional cua trial failed repeated app launch on the real GX10 session; see the trial result below | decided | Do not build CuaDesktop for this hackathon; retain the named-action interface and xdotool/wmctrl/gtk-launch implementation |
| D16 | 2026-10-10 | Note shape | Two layers: raw observations on every capture, rolled up by the model every few minutes into activity segments with inferred intent. Skills and recall read segments; raw observations stay available for detail and correction | decided | Observation vs interpretation stays separable, as the handoff asks |
| D17 | 2026-10-10 | Privacy controls for the demo | All four: pause/resume, app and window exclusions, delete screenshots after the model reads them, edit and delete notes in the UI | decided | Screenshots are transient. Exclusions are checked before capture or before the model call |
| D18 | 2026-10-10 | GitHub repo | `NeoKazuya/auto-daybook`, private (already created by the user) | decided | Make it public at submission if the event requires that |
| D19 | 2026-10-10 | Music library | An agent downloads ~20–30 royalty-free, CC-licensed tracks into `~/Music` on the GX10, sorted by mood | decided | The venue Wi‑Fi is shared with the Qwen3.8 download. Record each track's license and source |
| D20 | 2026-10-10 | Demo format | Live on the GX10. A presenter really uses the machine while the music changes and the overlay appears | decided | Needs a monitor at the demo, a rehearsed script, and an unlocked screen |
| D21 | 2026-10-10 | Team | Just the user (`kazuya`) for now; others may join later | decided | Branches are `kazuya/<topic>`. Module boundaries still assume up to 3 owners |
| D22 | 2026-10-10 | OS or desktop changes? | None. Keep the stock Ubuntu GNOME on Xorg session; don't switch to Wayland or upgrade the OS during the event | decided | See [grounding](grounding.md) |
| P8 | 2026-10-10 | Who orchestrates the build? | Codex orchestrates the parallel build work. The architecture just needs to be modular from the start so that's doable; it doesn't plan who builds what | decided | Module boundaries and contracts matter; people-ownership and parallel schedules don't |
| D23 | 2026-10-10 | Architecture | The Opus candidate is the base: skills declare typed signals, one tick per ~90 s does rollup and answers every signal, and a pure 4-form trigger grammar decides. Privacy hardening is grafted from the Codex candidate | default | [ARCHITECTURE.md](../ARCHITECTURE.md), [arena/SYNTHESIS.md](arena/SYNTHESIS.md) |
| D24 | 2026-10-10 | Overlay rendering | Qt Quick (QML) in the shell process, not QtWebEngine | default | Avoids embedded Chromium on aarch64 and the 64 MB PySide6-Addons download |
| D25 | 2026-10-10 | Can chat-made skills include Python hooks? | No. Chat writes declarative skills only; hooks are developer-written and reviewed in the repo | default | [skills.md](../skills.md) |
| D26 | 2026-10-10 | Break timing in the live demo | A separately approved `break-demo` skill (2 min). The real `break` keeps 50 min; nothing is edited on stage | default | [demo.md](../demo.md) |
| D27 | 2026-10-10 | Does pause stop the music? | No. Pause stops capture and skill reactions and hides the overlay. Music keeps playing until the user stops it | default | [ARCHITECTURE.md §7](../ARCHITECTURE.md#7-privacy) |
| D28 | 2026-10-10 | Note retention | Notes are kept until the user deletes them, with "forget last 15 minutes" and "forget everything" on the privacy page | default | — |
| D29 | 2026-10-10 | Tray on GNOME | Requires enabling the AppIndicator extension (only `user-theme` was enabled) | default | [setup.md](../setup.md) step 2 |

## Still open

- Product name (`daybook` is the working name).
- Whether withheld (excluded-app) time shows as a "private" gap on the timeline (current design) or not at all.
- Whether to switch to Qwen3.8 once it downloads (gated on `accept.py --vision` and the replay fixtures).

## GX10 cua trial — 2026-10-10

**Result: no; use `XdoDesktop`.** This closes D15 for the hackathon build.
The optional adapter was tested for about 10 minutes, within the one-hour limit
in ARCHITECTURE §11.1. No `CuaDesktop` is shipped.

- Downloaded the official [cua-driver 0.34.1 Linux ARM64 release](https://github.com/trycua/cua/releases/tag/cua-driver-rs-v0.34.1), verified its archive against the release's SHA256SUMS, and extracted it into an isolated trial directory on the GX10. No service or extension was installed.
- `gx10 status` passed. The test ran on the existing unlocked GNOME/Xorg `:1` session through `gx10 run`, holding the capture lock. Telemetry was disabled.
- A temporary daemon with a private socket successfully listed the desktop windows. A Python `CuaDesktop` trial adapter called the driver CLI for the named desktop operations.
- `launch_app` with the desktop ID `org.gnome.TextEditor` opened the editor and returned a visible window. Repeating the same launch while the editor was already running failed after 8 seconds with `launch_handoff_timeout`, `effect: refused`, and `handoff: dbus_activation`; the existing editor window remained visible. This fails the required `desktop.launch` behavior.
- The focus and URL tests were not reached after this failure. The temporary daemon was stopped and the lock released. No screenshots or recordings were written by the trial.

The result is a failure of this release in the tested session, not a claim that
all cua versions fail on Xorg. The existing xdotool/wmctrl/gtk-launch backend
remains the implementation for the three named desktop actions.
