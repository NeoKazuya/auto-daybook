# Demo

The live demo runs on the GX10. A presenter really uses the machine while the audience watches the notes build up, the music follow the activity, a chat-made skill fire, and the break overlay appear. Target length: about 5 minutes. The behavior behind each step is defined in [ARCHITECTURE.md §11.2](ARCHITECTURE.md#112-end-to-end-acceptance).

## Before the demo

- [ ] The GX10 is set up per [setup.md](setup.md), the screen is unlocked, and automatic screen lock is off for the session (Settings → Privacy → Screen Lock).
- [ ] A monitor is connected, the speakers are audible, and the volume is checked.
- [ ] The existing Qwen3.8 vLLM service is healthy: `cd ~/gx10-model-plan && ./gx10.sh status`. Verify this instance's model profile using [setup §6](setup.md#6-the-model); existing config files are preserved. Keep Ollama stopped.
- [ ] `daybook run` is running in tmux on the real screen, with a fresh `DAYBOOK_HOME` so the timeline starts empty, and `tick_interval = "30s"` in its `daybook.toml` (demo mode: still at most 2 ticks per minute).
- [ ] Skills:
  - `music` active
  - `break` disabled
  - `break-demo` approved (built-in, unmodified) but **disabled**; it's enabled at step 6
  - no `stand-after-meeting` yet (it's created at step 4)
- [ ] Windows ready:
  - VS Code with this repo open on a file with a visible failing test
  - a PDF (a paper or the docs) in a viewer
  - a video call that's actually joined, for example a Google Meet or Jitsi call with a teammate on their phone, showing participants and a "Leave call" button. A "ready to join" page isn't enough: it correctly reads as *not* in a meeting, so the reminder wouldn't fire. If the network is off, use a local page that looks like an active call (participant tiles, a running call timer, a "Leave call" button) and keeps the microphone open
  - the Daybook web UI
  - a password manager window, for step 8
- [ ] The privacy page shows "screenshots stored: none" and the footer shows the model and calls per minute.

## Script

| # | Presenter does | Audience sees | Says |
|---|---|---|---|
| 1 | Shows the Daybook UI: empty timeline, model footer, privacy page | A local model, nothing stored yet | "Everything runs on this box. Screenshots never touch disk; only text notes are kept." |
| 2 | Works in VS Code for ~1 min: reads the failing test, edits a line | A "coding" segment appears with an inferred intent; focus music starts | "It writes notes from what's on screen, and separately guesses intent. You can see which is which." |
| 3 | Switches to the PDF and reads for ~1 min | A new "reading" segment; music changes to calm within about 30–60 s | "The music skill asks the model one question: which mood fits right now? Code picks the tracks." |
| 4 | In chat: "remind me to stand up every time I finish a meeting" | A review card: the question it will ask, the rule in words, the permission (`overlay.show`). Click **Approve & enable** | "Skills are short text files. This one was just written by the model, and nothing runs until I approve exactly this version." |
| 5 | Sits in the joined call for ~1 min, then leaves it and goes back to VS Code | A "meeting" segment, then the full-screen "Meeting's over" overlay. Dismiss it | "It fired once, when the meeting ended." |
| 6 | Enables `break-demo`, then codes for ~2.5 min (the first reading arrives within 30 s of enabling, then the streak needs 2 min) | The full-screen break overlay with break music. Dismiss it; focus music returns on the next tick | "The real break reminder waits 50 minutes. This demo copy waits 2." |
| 7 | In chat: "what did I do in the last 10 minutes?" | An answer with durations and links into the timeline | "Recall uses the same notes. Durations come from code, not the model." |
| 8 | Brings the password manager forward, then pauses from the tray | The timeline shows a "private" gap, then a "paused" gap; the tray icon changes. Use "forget last 15 minutes" to show deletion | "Exclusions, pause, and delete are built in." |
| 9 | (If the network can be turned off) disables Wi‑Fi and repeats step 3 briefly | It still works | "Fully offline." |

**Pacing:** steps 3, 5, and 6 wait on the tick (up to ~30–60 s after a switch). Fill that time by talking, or press the **Tick now** debug button (`POST /api/tick`) to bring the reaction forward. Say so if you do.

## If something goes wrong

| Problem | Fallback |
|---|---|
| The model is slow or unreachable | The footer shows it. Press **Tick now**, or keep talking; the gateway recovers on its own. Last resort: the fallback recording |
| The overlay doesn't appear | Show the `SkillFired` entry in the timeline, and `daybook overlay test` from tmux |
| The tray is missing | Use the pause toggle on the privacy page |
| Music is silent | Check the volume, then `daybook music play focus` |
| The chat draft is invalid | Show the parse errors in the editor (the review step working). Have the expected skill file ready to paste |

## Rehearsal

- [ ] Run the full script at least three times on the GX10, in the demo room if possible.
- [ ] Time each step, and note where the tick wait feels long.
- [ ] Record one clean run as the fallback video (screen plus audio), with a label saying it's a recording.
- [ ] Reset between runs: stop `daybookd`, delete the demo `DAYBOOK_HOME`, start again.
