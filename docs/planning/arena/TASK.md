# Arena task: architecture for the local AI activity-notes app

Every runner gets this same task. You are one of several independent runners, each on a different model. Do not look at other candidates' folders.

## Read first

1. `docs/planning/arena/_pstack/architect-SKILL.md`: the workflow you're inside (you are doing Phase B, "Sketch").
2. `docs/planning/arena/_pstack/runner-prompt.md`: your design discipline. Follow it.
3. `docs/planning/arena/_pstack/rationale-template.md`: the shape of your `DESIGN.md`.
4. `docs/planning/arena/_pstack/design-red-flags.md`: screen your design against these.
5. Grounding (Phase A, already done; treat it as fact unless marked inferred):
   - `docs/planning/grounding.md`: the machine, desktop session, models, and the first vision test
   - `docs/planning/decisions.md`: every product and process decision so far (decided rows are constraints)
   - `docs/planning/computer-control.md`: research on control tools and frameworks
   - `docs/gx10.md`, `CONTRIBUTING.md`: how the team works
6. Product summary: below.

## Product

A local AI agent that, while enabled, takes notes on what the user does on their computer by sending screenshots to a local vision-language model. The notes are a shared memory that every **skill** reads through one common interface (the "portal"). When a skill runs, it uses the notes to infer the user's situation and intent, decides what would help, and acts within the actions it is allowed. Users can ask for new skills in chat, and the model writes them. Recall uses the same memory: "What did I do today?", "What just happened?", "How long did I work on this?"

Event: UCSC AI Hackathon, Local AI track (models on hardware with ≤128 GB, with emphasis on privacy, latency, and offline use). 24-hour build, demo on Sunday 2026-10-11. Planning started at about 17:00 on Saturday 2026-10-10, so there are roughly 14–18 build hours left for **2–3 people, each directing coding agents**, all working over SSH on one shared GX10.

Demo must show:

- **Music skill (centerpiece):** the model infers a mood from the notes (e.g. focus while debugging, calm while reading), and code picks local tracks tagged with that mood and plays them. Music changes as the activity changes.
- **Break reminder:** after a long stretch of focused work (from the notes), it shows a **reusable full-screen overlay** (better than a notification) and can switch the music to break music.
- **Chat-created skill:** the user asks in the web UI ("remind me to stand up every time I finish a meeting"), the model writes a skill file, the user reviews and enables it, and it works.
- **Recall:** answers from the notes, e.g. "what did I do in the last hour?"
- **Local and private:** a pause control, app and window exclusions, screenshots deleted after the model reads them, and notes the user can edit or delete.
- The notes themselves: a visible timeline of observations and activity segments.

Decided constraints (see `decisions.md` for the full list):

- Everything runs on the GX10: Ubuntu 24.04 aarch64, GNOME on **X11** display `:1`, 121 GiB unified memory, one 1920×1080 screen.
- Python core plus a local web app, plus a tray icon.
- Skills are plain-text files (goal, triggers, allowed actions) with an optional Python code hook.
- Two-layer notes: raw observations per capture, rolled up by the model every few minutes into activity segments with inferred intent. Observation and interpretation stay distinguishable.
- **Model backend:** any OpenAI-compatible vision endpoint, swappable by config. Today that's Ollama `qwen3.6:35b` (~2.9 s per screenshot call, ~950 prompt tokens per 1280-px image). Later it's Qwen3.8-Flash-Next on vLLM (32K context, key-authenticated, vision unverified). Only one large model fits in memory, so every model call in the system shares one model.
- **Computer control:** the user wants a fully built framework if one fits. None clearly does (trycua/cua is the closest, but it isn't validated on real Xorg). Design the action layer so such a framework could be plugged in, with in-house named actions as the fallback.
- The capture loop must skip a locked screen (it captures black frames) and excluded apps.

## What to produce

Write only inside `docs/planning/arena/candidate-<your-runner-name>/`:

- `DESIGN.md`: your design package, following `rationale-template.md` (Problem, Usage, Shape, Tradeoffs accepted, Alternatives considered, Open questions and risks, Next implementation step). Leave "Synthesis decision" empty. Also include:
  - a **module map**: modules or packages, what each owns, and who could own it among 3 people
  - the **runtime picture**: processes, loops, cadence, and where each model call happens, with a rough model-call budget per minute
  - the **skill file format**, with the music, break, and one chat-generated skill written out in full
  - the **portal interface** that skills and recall use
  - a **build order** for the next ~16 hours, with what can be built in parallel and what each piece is tested against
- `sketch/`: Python type and signature sketches (`.py` files) with `raise NotImplementedError` bodies and `# TODO` pseudocode, per the runner prompt. Usage first. Types are the contract.

Be concrete. Don't hedge against other runners. Producing a different shape from what another model would produce is the point. Don't modify any file outside your candidate folder, and don't run anything on the GX10.
