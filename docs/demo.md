# Demo

The live demo runs on the GX10. A presenter really uses the machine while the audience watches the notes build up, the music follow the activity, a chat-made skill fire, and the break overlay appear. Allow about 8 minutes, including tick waits. The behavior behind each step is defined in [ARCHITECTURE.md §11.2](ARCHITECTURE.md#112-end-to-end-acceptance).

## Before the demo

- [ ] The GX10 is set up per [setup.md](setup.md), the screen is unlocked, and automatic screen lock is off for the session (Settings → Privacy → Screen Lock).
- [ ] A monitor is connected, the speakers are audible, and the volume is checked.
- [ ] The existing Qwen3.8 vLLM service is healthy: `cd ~/gx10-model-plan && ./gx10.sh status`. Verify this instance's model profile using [setup §6](setup.md#6-the-model); existing config files are preserved. Keep Ollama stopped.
- [ ] The integration owner has handed over the real screen; launch the separate demo instance using the commands below. Its fresh `DAYBOOK_HOME` starts with no notes and uses `tick_interval = "30s"` (still at most 2 ticks per minute).
- [ ] Skills:
  - `music` active
  - `break` disabled
  - `break-demo` approved (built-in, unmodified) but **disabled**; it's enabled at step 6
  - no user-created meeting reminder yet (it's created at step 4)
- [ ] Windows ready:
  - VS Code with this repo open on a file with a visible failing test
  - a PDF (a paper or the docs) in a viewer
  - a video call that's actually joined, for example a Google Meet or Jitsi call with a teammate on their phone, showing participants and a "Leave call" button. A "ready to join" page isn't enough: it correctly reads as *not* in a meeting, so the reminder wouldn't fire. If the network is off, use a local page that looks like an active call (participant tiles, a running call timer, a "Leave call" button) and keeps the microphone open
  - the Daybook web UI
  - a password manager window, for step 8
- [ ] **Settings → Screenshots** shows "Screenshots stored: none" and the footer shows the model and calls per minute. In **Settings → Apps**, check that the password manager used in step 8 is blocked by its actual app name or window title.

## Prepare and launch the demo instance

Run these commands as `asus` on the GX10, after arranging an exclusive real-screen
slot with the integration owner. The installed `daybook.service` may already own
capture; another presenter or agent may instead be using an isolated instance.
Wait for that owner to stop their instance before taking over. A capture-lock or
port-in-use error means the handoff is incomplete; coordinate with the owner.

Start a dedicated tmux session. If it already exists, attach to it and inspect it
instead of creating a second demo:

```sh
tmux new -s daybook-demo
# To reconnect later: tmux attach -t daybook-demo
```

Inside that session, use the installed checkout and its existing virtualenv.
Remember whether the production service was active, then stop that service for
the agreed demo slot. Run this handoff block only once per slot:

```sh
cd -- "$(cat "$HOME/.config/daybook-gx10/app-dir")"
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/share/daybook-gx10/desktop-env.sh"
test -x .venv/bin/daybook
export DAYBOOK_DEMO_SERVICE_WAS_ACTIVE=0
if systemctl --user is-active --quiet daybook.service; then
    export DAYBOOK_DEMO_SERVICE_WAS_ACTIVE=1
fi
systemctl --user stop daybook.service
```

Prepare a new, separate home before every rehearsal. This uses the shipped
Qwen3.8 defaults and sets the demo interval before the daemon starts:

```sh
export DAYBOOK_HOME="$(mktemp -d "$HOME/.daybook-demo.XXXXXX")"
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from daybook.app import DEFAULT_CONFIG

config = DEFAULT_CONFIG.replace('tick_interval = "90s"', 'tick_interval = "30s"')
with (Path(os.environ["DAYBOOK_HOME"]) / "daybook.toml").open("x") as output:
    output.write(config)
PY
tmux set-environment -t daybook-demo DAYBOOK_HOME "$DAYBOOK_HOME"
printf 'Demo home: %s\n' "$DAYBOOK_HOME"
```

Run the configured-model assertion in [setup §6](setup.md#6-the-model) from this
shell, using this `DAYBOOK_HOME`. Then launch the real instance in the foreground:

```sh
.venv/bin/daybook run --port 8765
```

The normal `run` mode owns capture and enables real music/tray/overlay effects;
no extra `gx10 run` wrapper is needed. Open `http://127.0.0.1:8765` on the GX10.
Detach with **Ctrl-b, d** if needed; tmux keeps the daemon running. Keep this
session until the production-service handback below. These commands leave
`~/.daybook` and `~/.config/daybook-gx10/app-dir` intact.

## Script

| # | Presenter does | Audience sees | Says |
|---|---|---|---|
| 1 | Shows the Daybook UI: empty Daily Book timeline, model footer, Settings → Screenshots | A local model, nothing stored yet | "Everything runs on this box. Screenshots never touch disk; only text notes are kept." |
| 2 | Works in VS Code for ~1 min: reads the failing test, edits a line | A "coding" segment appears with an inferred intent; focus music starts | "It writes notes from what's on screen, and separately guesses intent. You can see which is which." |
| 3 | Switches to the PDF and reads for ~1 min | A new "reading" segment; music changes to calm within about 30–60 s | "The music skill asks the model one question: which mood fits right now? Code picks the tracks." |
| 4 | In the Sticky Board: "remind me to stand up every time I finish a meeting"; click **Review & enable** | In Skills, review the question, rule, reminder layout/text, and permission (`overlay.show`), then click **Approve & enable** | "Skills are short text files. This one was just written by the model, and nothing runs until I approve exactly this version." |
| 5 | Sits in the joined call for ~1 min, then leaves it and goes back to VS Code | A "meeting" segment, then the reminder shown in the approved draft. A casual stand-up reminder defaults to a bubble; dismiss it | "It fired once, when the meeting ended." |
| 6 | Enables `break-demo`, then codes for ~2.5 min (the first reading arrives within 30 s of enabling, then the streak needs 2 min) | The full-screen break overlay with break music. Dismiss it; focus music returns on the next tick | "The real break reminder waits 50 minutes. This demo copy waits 2." |
| 7 | In the Sticky Board: "what did I do in the last 10 minutes?" | An answer with durations and links into the timeline | "Recall uses the same notes. Durations come from code, not the model." |
| 8 | Brings the blocked password manager forward, waits for the footer to say "Private window", then pauses from the tray | The timeline shows a "private" gap, then a "paused" gap; the tray icon changes. Use **Settings → Notes → Last 15 minutes** and confirm deletion | "Exclusions, pause, and delete are built in." |
| 9 | (If the network can be turned off) disables Wi‑Fi and repeats step 3 briefly | It still works | "Fully offline." |

**Pacing:** steps 3, 5, and 6 wait on the tick (up to ~30–60 s after a switch). Fill that time by talking, or press **Tick now** in Daily Book (`POST /api/tick`) to bring the reaction forward. Say so if you do.

## If something goes wrong

For CLI fallbacks, use a second GX10 terminal while the demo daemon stays running.
Read `DAYBOOK_HOME` from the demo tmux session and use the same checkout/virtualenv:

```sh
cd -- "$(cat "$HOME/.config/daybook-gx10/app-dir")"
export "$(tmux show-environment -t daybook-demo DAYBOOK_HOME)"
```

The CLI reads that home's runtime descriptor and token to reach the running demo.

| Problem | Fallback |
|---|---|
| The model is slow or unreachable | The footer shows it. Press **Tick now**, or keep talking; the gateway recovers on its own. Last resort: the fallback recording |
| The overlay doesn't appear | Check the enabled skill and its rule in **Skills**, then run `.venv/bin/daybook overlay test` in the fallback terminal. Tell the audience this is a manual overlay test |
| The tray is missing | Use **Settings → Monitoring** to turn monitoring off |
| Music is silent | Check the volume, then `.venv/bin/daybook music play focus` in the fallback terminal |
| The chat draft is invalid | Turn on **Settings → Developer mode**, then show the draft's parse errors in **Skills**. Paste a rehearsed skill file, **Save edits**, review it, and approve it |

## Rehearsal

- [ ] Run the full script at least three times on the GX10, in the demo room if possible.
- [ ] Time each step, and note where the tick wait feels long.
- [ ] Record one clean run as the fallback video (screen plus audio), with a label saying it's a recording.
- [ ] Reset between runs using the stop/reset sequence below.

## Stop, reset, and hand back

Attach to the demo's tmux session if detached:

```sh
tmux attach -t daybook-demo
```

In the pane running `.venv/bin/daybook run`, press **Ctrl-C once** and wait for the
shell prompt. This lets Daybook stop its shell/music children and release capture.
The shipped executable is `daybook`; it has no `stop` subcommand.

For another rehearsal, rerun the **Prepare a new, separate home** block and the
foreground launch command in that same shell. Keep the old demo homes for any
rehearsal evidence; a new home also removes the previously generated meeting skill
from the next run. Keep the production service stopped throughout the reserved
slot, and keep the original `DAYBOOK_DEMO_SERVICE_WAS_ACTIVE` value.

After the last run has stopped and the integration owner is ready for handback,
restore the service only if it was active when this slot began:

```sh
unset DAYBOOK_HOME
if [ "$DAYBOOK_DEMO_SERVICE_WAS_ACTIVE" = 1 ]; then
    systemctl --user start daybook.service
    systemctl --user is-active daybook.service
fi
unset DAYBOOK_DEMO_SERVICE_WAS_ACTIVE
exit
```

Expected service output is `active` when restarting it. If it was already inactive,
leave it inactive and hand the slot back to its owner. No `gx10 enable-app` call or
production-home reset is needed for rehearsals.
