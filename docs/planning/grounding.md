# Grounding: the GX10 as it is

Phase A ("ground the problem") of PStack's `architect` workflow. These facts were observed on the machine on 2026-10-10 between 16:45 and 17:05 PT, over SSH via Tailscale (`asus@1s6m`, `100.123.175.71`). Each fact lists how it was observed. Re-check anything that matters before relying on it; the machine is shared and changes during the event.

Facts are **observed** unless marked **inferred**.

## Machine

| Fact | Value | How observed |
|---|---|---|
| Hostname | `1S6M` (Tailscale name `1s6m`, `1s6m.taild3a24b.ts.net`) | `hostname`, `tailscale status` |
| OS | Ubuntu 24.04.4 LTS, aarch64 | `/etc/os-release`, `uname -m` |
| CPU | 20 cores | `nproc` |
| Memory | 121 GiB total, ~115 GiB available at the time | `free -g` |
| GPU | NVIDIA GB10, driver 580.142. `nvidia-smi` reports memory as `[N/A]` (unified memory) | `nvidia-smi --query-gpu=...` |
| Disk | 916 GB NVMe, 540 GB free | `df -h /` |
| Python | 3.12.3 system Python; `pip3` available; `uv` not installed | `python3 --version`, `command -v` |
| Installed | `docker`, `nvidia-ctk`, `git`, `tmux`, `node`/`npm`, `ollama`, `notify-send`, `xprop`, `xwininfo`, `gnome-screenshot`, Pillow 10.2 | `command -v`, `pip3 list` |
| Not installed | `gh`, `uv`, `xdotool`, `wmctrl`, `playerctl`, `mpv`, `ffmpeg`, `scrot` | `command -v` |
| sudo | `sudo -n true` succeeded with no prompt (**inferred:** passwordless sudo for `asus`) | `sudo -n true` |

## Desktop session

| Fact | Value | How observed |
|---|---|---|
| Display server | **X11** (GNOME on Xorg via `gdm-x-session`), display `:1`. GDM's `WaylandEnable=false` line is commented out, so the X11 session was chosen at login, not forced | `loginctl show-session 6 -p Type` → `x11`; `ps` shows `Xorg`, `mutter-x11-frames`; `/tmp/.X11-unix/X1` |
| X auth | `XAUTHORITY=/run/user/1000/gdm/Xauthority` | `/proc/<gnome-shell pid>/environ` |
| Monitor | One, 1920×1080 (`USB-C-2`) | `xrandr --listmonitors` |
| Lock state at check time | Locked, idle, monitor off (DPMS) | `loginctl ... LockedHint=yes`, `xset q` → `Monitor is Off`, `org.gnome.ScreenSaver.GetActive` → `true` |
| Capture while locked | `gnome-screenshot` succeeds but returns an all-black image (pixel extrema `(0,0)` per channel), and `_NET_ACTIVE_WINDOW` gives no window | `/tmp/vlm_test.py` run, `PIL.Image.getextrema()` |

**Implications:**

- X11 means the standard tools (`xdotool`, `wmctrl`, `xprop`, direct screenshots) work. The Wayland restrictions don't apply as long as the team keeps logging into the Xorg session.
- Capture, window control, and overlays must run with `DISPLAY=:1` and the session's `XAUTHORITY`, even when started from an SSH shell.
- The capture loop must check the lock state and skip capture while the screen is locked. Otherwise the notes fill up with "the screen is black".

## Models and serving

### Qwen3.8-Flash-Next (the target), not ready yet

| Fact | Value | How observed |
|---|---|---|
| Plan | `~/gx10-model-plan/` (README, PLAN, RUNBOOK, `gx10.sh`, `accept.py`) serves `nvidia/Qwen3.8-Flash-Next-NVFP4` with vLLM v0.31 and a recipe from `blazux/qwen3.8-Flash-DGX` | read on the box |
| Endpoint when up | `http://<gx10>:18300/v1` (Caddy proxy, bearer key in `~/.config/qwen38/api-key`), model `qwen3.8-flash-next`, 32,768-token context per request, `reasoning_effort` low/medium/xhigh | `RUNBOOK.md`, `config.env` |
| Size | 124 GiB checkpoint (48 GiB of it an n-gram table read from NVMe), `GPU_MEM=0.80` | `PLAN.md` §3, §5 |
| Download | In progress in container `qwen38-dl`: 12 of 25 files after 1 h 33 min, latest rate ~10 min per file, on venue Wi‑Fi (~40 Mbit/s per the plan) | `docker ps`, download log |
| Vision | **Not verified.** The plan lists verified image input as something it gives up; `accept.py --vision` exists | `PLAN.md` §4.3 |
| Coexistence | The plan says "one large model at a time" | `PLAN.md` §10 |

**Inferred:** while Qwen3.8 is served, there is not enough memory to run a second large model alongside it. Switching models means stopping one and starting the other.

### Vision models already on the box

| Model | Where | Vision | Size |
|---|---|---|---|
| Qwen3.6-35B-A3B | Ollama `qwen3.6:35b`; GGUF + `mmproj-F16` in `~/unsloth/` for llama.cpp | yes (Ollama capabilities) | 22.4 GB Q4 GGUF; 34 GB resident in Ollama at 262K context |
| Gemma 4 26B-A4B | Ollama `gemma4:26b`; GGUF + `mmproj-BF16` | yes | 17.0 GB |
| Nemotron 3 Nano Omni 30B-A3B | Ollama `nemotron3:33b`; GGUF + `mmproj-BF16` | yes (+ audio) | 23.9 GB |
| Nemotron 3 Super | Ollama `nemotron-3-super` | no | — |

Runtimes: Ollama listens on `127.0.0.1:11434` (OpenAI-compatible at `/v1`). llama.cpp build 8999 is at `~/llama.cpp` (`llama-server`, `llama-mtmd-cli`), with launch scripts `~/run_qwen3.6.sh` and `~/run_gemma4.sh` on port 8000. Observed with `ollama list`/`ollama show`, `find`, `llama-server --version`, `ss -ltnp`.

### First vision test (Qwen3.6-35B via Ollama)

Script: `/tmp/vlm_test.py` on the GX10. It runs `gnome-screenshot`, downsizes to 1280 px wide (JPEG q80), and sends the image to `/v1/chat/completions` with `reasoning_effort: none`, `max_tokens: 400`, asking for a JSON activity note.

| Measure | Result |
|---|---|
| Capture + resize | 2.28 s (includes `gnome-screenshot` startup) |
| JPEG size | 14 KB (the frame was black, so this is not representative) |
| Prompt tokens per image | ~954 |
| Latency, model already loaded | 2.9 s for ~100 output tokens (2 calls) |
| Output | Valid JSON with every requested key. Correctly said the screen was black |

**Still to test:** a real, unlocked desktop with content, to judge note quality. Also the cost of thinking mode, and the other two vision models.

## Other things running

- Ports `18789`/`18791` (loopback): a Python `MainThread` process, pid 2461. **Inferred:** OpenClaw, from `~/run_openclaw.sh`. Not related to this project.
- Port `11000` (loopback): returns a redirect (`<a href="/">Found</a>`). Unknown.
- `~/Music` is empty. Music for the music skill still has to be added.
