# GX10 setup

How to bring the GX10 to the state [ARCHITECTURE.md](ARCHITECTURE.md) assumes. Access and the rules for sharing the machine are in [gx10.md](gx10.md), and the machine's current state is recorded in [planning/grounding.md](planning/grounding.md). Run everything as `asus`. Each step says how to check it worked.

The venue Wi‑Fi is shared with the Qwen3.8 download (124 GiB). Keep downloads to what's listed here.

## 1. System packages

```sh
sudo apt-get update
sudo apt-get install -y xdotool wmctrl mpv gh libxcb-cursor0 gnome-shell-extension-appindicator
```

- `xdotool` and `wmctrl` are the default desktop-control backend.
- `mpv` plays the music.
- `gh` is needed for the agent workflow in CONTRIBUTING.md.
- `libxcb-cursor0` is required by Qt 6's X11 plugin; without it, PySide6 windows fail to start.

**Check:** `command -v xdotool wmctrl mpv gh` prints four paths.

## 2. Tray icon support

GNOME hides tray icons unless the AppIndicator extension is enabled. On 2026-10-10, only `user-theme` was enabled.

```sh
export DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

If the extension isn't found, GNOME Shell hasn't picked up the new package yet. Restart the shell at the machine: on X11, press `Alt+F2`, type `r`, and press Enter. Then run the enable command again. Logging out and back in also works; choose the normal Ubuntu (Xorg) session.

**Check:** `gnome-extensions list --enabled` includes `ubuntu-appindicators@ubuntu.com`.

## 3. GitHub access

Everyone shares the `asus` Unix account, so a single `gh auth login` would make every agent act as one person. Instead, each person gives their agent sessions their own token:

```sh
# in your own tmux session
read -rs -p 'GitHub token: ' GH_TOKEN && export GH_TOKEN   # hidden prompt: keeps the token out of the shared shell history
# use a fine-grained token limited to NeoKazuya/auto-daybook (contents + pull requests + issues)
gh auth status
```

**Check:** `gh auth status` shows your own account. The repo is private, so this has to work before cloning.

## 4. Python environment

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs uv to ~/.local/bin
source "$HOME/.local/bin/env"                      # put uv on PATH in this shell
gh repo clone NeoKazuya/auto-daybook ~/auto-daybook   # private repo: uses GH_TOKEN from step 3
cd ~/auto-daybook && uv sync                        # Python 3.12 + dependencies, including PySide6-Essentials (~84 MB)
```

**Check:** `uv run daybook --help` works. `scripts/check.sh` passes (once the Contracts work has landed).

Agents work in their own worktrees next to `~/auto-daybook` (see [CONTRIBUTING.md](../CONTRIBUTING.md)). Each development instance sets its own `DAYBOOK_HOME`, for example `~/.daybook-<agent>`.

## 5. Music library

An agent downloads 20–30 tracks under licenses that allow use (CC BY or CC0 preferred; check each track's license on its page) into `~/Music/daybook/<mood>/` for each mood, and writes `~/Music/daybook/library.toml` in the format in [skills.md](skills.md#music-library). Aim for at least 5 tracks per mood. Good sources:

- Kevin MacLeod / incompetech.com (CC BY 4.0)
- Free Music Archive (per-track license)
- ccMixter

Record the license and source URL for every track. CC BY requires attribution, and the demo's credits page can list them from `library.toml`.

**Check:** `uv run daybook music check` passes. `uv run daybook music play focus` is audible from the GX10's speakers or headphones.

## 6. The model

The default backend is Ollama with `qwen3.6:35b`, already installed (see [grounding](planning/grounding.md)).

```sh
ollama run qwen3.6:35b "" >/dev/null   # warm it up; the first load takes a while
ollama ps                             # should list qwen3.6:35b
```

Confirm the model server doesn't log request contents (the privacy design in [ARCHITECTURE.md §7](ARCHITECTURE.md#7-privacy)):

```sh
systemctl show ollama -p Environment   # OLLAMA_DEBUG must not be set
```

Switching to Qwen3.8 means following `~/gx10-model-plan/RUNBOOK.md`, then setting `[model]` in `daybook.toml` as described in [ARCHITECTURE.md §5](ARCHITECTURE.md#5-model-use-and-budget). Only switch after `accept.py --vision` passes. Never run both large models at once.

## 7. Running daybook

```sh
tmux new -s daybook
export DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
cd ~/auto-daybook && uv run daybook run
```

Open `http://127.0.0.1:8765` in a browser on the GX10 desktop.

Only one real-screen instance can run at a time. Development instances use `--replay fixtures/replay/<scenario>` or `--no-capture`, with their own `--port`.

## Don'ts

- Don't switch the login session to Wayland, and don't upgrade Ubuntu. Screen capture and window control depend on the Xorg session.
- Don't start a second large model while one is serving.
- Don't install PySide6-Addons (QtWebEngine). The design doesn't need it, and it's another 64 MB over shared Wi‑Fi.
