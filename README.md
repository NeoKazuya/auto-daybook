# Daybook

Daybook watches your screen, keeps text notes on what you're working on, and runs skills: small rule files that act on those notes. It comes with a few default skills. One plays focus music while you code and calmer music when you switch to a PDF. Another covers the screen with a break reminder after 50 minutes of focused work. You can turn these off, edit them, or write your own. To add one, type a request like "remind me to stand up every time I finish a meeting" and Daybook drafts the skill for you to approve. You can also ask it what you did this afternoon.

Runs locally on an ASUS GX10 (NVIDIA GB10, 128 GB) with [`nvidia/Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4) on vLLM. NVFP4 cuts the 125B model from about 360 GB in BF16 to 133 GB. Combined with reading its n-gram table from SSD, that gets it running in about 104 GB.

## How it works

Every 5 seconds Daybook checks the active window. When the screen changes, it sends one screenshot to the model and saves an observation: the app and window title from X11, plus a short description of what's visible. At this step the model describes the screen and doesn't guess what you're trying to do.

Two separate text-only model calls use those observations:

1. **Skill checks** answer every enabled skill's question from the current screen evidence, such as "Is the user doing focused work?" (yes/no) or "Which music fits right now?" (a mood). Checks run as fresh evidence becomes available, with at least 10 seconds between model attempts. They use a short response with reasoning disabled and don't wait for history grouping.
2. **History ticks** group new observations into activity segments, such as "Fixing the journal test" (coding), and write a guess at your intent. The UI labels that guess as inferred. The default interval is 90 seconds, configurable down to 30 seconds; switching apps can bring a tick forward.

The 10-second minimum is a check interval, not a guaranteed action time. Capturing a changed screen, waiting for the model, and any delay specified in the skill's actions all contribute to the response time.

Skills never call the model. Plain Python code checks each skill's rules against the stored answers and runs the actions that skill is allowed to use. Ten skills cost the same number of model calls as one, and the same answers always trigger the same rules.

## Skills

A skill is a Markdown file with YAML front matter. Daybook includes three default skills in `skills/builtin/`: `music` (mood music), `break` (the 50-minute break reminder), and `break-demo` (the same reminder after 2 minutes, for demos). This is `break`:

```yaml
---
id: break
name: Break reminder
signals:
  focused:
    ask: >
      Is the user currently concentrating on an intentional work or study task,
      such as coding, debugging, writing, or deliberate document reading and study,
      rather than casual browsing, chatting, watching media, or idling?
    type: bool
rules:
  - when: focused is true for 50m
    do:
      - overlay.show:
          title: Time for a break
          body: >-
            You've been focused for $focused.for. Stand up,
            look away from the screen, get some water.
          dismiss: I'm back
          minutes: 5
      - music.play_mood: { mood: break, min_dwell: 0s }
allow: [overlay.show, music.play_mood]
---
After a long stretch of focused work, cover the screen with a break reminder
and switch to break music.
```

Rules have four trigger forms: `updates`, `becomes X`, `is X for 50m`, and `every 30m`. Actions come from a fixed list: show or hide an overlay, play music by mood, stop music, send a notification, and open a URL, launch an app, or focus a window. A skill can only use the actions in its `allow` list. Skills written from chat can't include code. A new or edited skill doesn't run until you approve that exact version.

The full format is in [docs/skills.md](docs/skills.md).

## Privacy

- Screenshots stay in memory and are never saved, so the disk holds only the text notes you can see and delete.
- Pause from the tray icon. Model calls already in progress are discarded.
- Block apps or window titles in Settings. Daybook checks for blocked windows before it reads any pixels and never stores their titles.
- A locked screen, or 5 minutes without input, stops capture. The timeline shows these as gaps.
- Edit or delete any note, the last 15 minutes, or everything. Deleting also clears the search index and the SQLite write-ahead log.
- Logs record timings and IDs. They never include prompts, window titles, or note text.

## The app

`daybook run` starts one process that captures the screen, runs ticks and skills, and serves a web UI at `http://127.0.0.1:8765`. It also starts a Qt tray icon and overlay, and mpv for music.

The web UI has six tabs:

- **Table of Contents**: a summary of the day
- **Daily Book**: the timeline
- **Sticky Board**: chat, recall, and new skill requests
- **Skills**: review, approve, and edit skills
- **Index Tags**: search your notes
- **Settings**

## Running it

You need Linux with X11, Python 3.12, [uv](https://docs.astral.sh/uv/), mpv, and an OpenAI-compatible endpoint for a model that accepts images. The defaults target the GX10: display `:1`, and vLLM at `127.0.0.1:18300` serving the model above as `qwen3.8-flash-next`. For the full machine setup, see [docs/setup.md](docs/setup.md).

```sh
uv sync
uv run daybook run              # watch the real screen
uv run daybook run --mock-api   # the UI with example data, without the screen or model

# use recorded frames in place of the screen
uv run daybook run --replay fixtures/replay/coding-reading
```

Only one instance can watch the real screen at a time. For a second instance, use `--replay` or `--no-capture` with its own `DAYBOOK_HOME`. On first run, Daybook writes its config to `$DAYBOOK_HOME/daybook.toml` (default `~/.daybook`).

Other commands:

```sh
uv run daybook skill check FILE    # parse a skill and print errors
uv run daybook music play focus    # play a mood from the music library
uv run daybook overlay test        # show a test overlay in the running app
```

## Development

`scripts/check.sh` runs ruff, pyright, pytest, and an import-boundary check. Most tests use fakes for the screen, model, music player, and desktop, so they run without a display or a GPU.

Everyone on this team works through coding agents. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the design and the build plan
- [docs/demo.md](docs/demo.md): the live demo script
- [docs/gx10.md](docs/gx10.md): access to the shared machine
