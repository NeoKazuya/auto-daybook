# Computer control: research notes

Input to the architecture: how skills act on the GX10 desktop. Researched 2026-10-10. **[V]** = verified against the linked source. **[I]** = inference. The desktop session was confirmed as X11 ([grounding](grounding.md)), so the X11 column applies. The Wayland findings are kept only as a fallback.

## Recommended stack (X11)

| Capability | Tool | Notes |
|---|---|---|
| Screenshot | `mss` (pure Python) | Grabs the X screen directly in milliseconds, versus about 2.3 s for `gnome-screenshot` startup ([grounding](grounding.md)) |
| Active window (title, class, PID) | `python-xlib` reading `_NET_ACTIVE_WINDOW`, `_NET_WM_PID`, `WM_CLASS`, `_NET_WM_NAME`; or `xdotool`/`xprop` via subprocess | `xprop` is installed. `xdotool` and `wmctrl` need `apt install` |
| List, focus, and launch windows | `wmctrl -lpx`, `wmctrl -ia <id>` or `xdotool windowactivate`; launch with `gtk-launch <app>.desktop` or `subprocess.Popen(start_new_session=True)` | |
| Music | `mpv --idle --input-ipc-server=<sock>` driven by `python-mpv` or JSON over the socket. Optionally `mpv-mpris` + `playerctl` | Works the same on X11 and Wayland [I]. Not installed yet |
| Full-screen overlay | PySide6 window (`FramelessWindowHint`, `WindowStaysOnTopHint`, `showFullScreen()`) hosting a `QWebEngineView` that renders our own HTML/CSS | PySide6 6.12.0 has an aarch64 wheel [V]. Fallback: a Chromium `--kiosk --app=<url>` window (Chromium is snap-only on 24.04 [V]) |
| Keyboard and mouse input | `xdotool` or `pynput` | |
| VLM click grounding (stretch) | Qwen3-VL-class models output click points on a 0–999 normalized grid [V, Qwen3-VL tech report, arXiv 2511.21631]. Map with `x_screen = x * W / 999` | Reliable for single clicks, not multi-step tasks [I]. Prefer named skill actions and keep free-form clicks as a stretch goal |

## Frameworks

| Framework | Verdict | Why |
|---|---|---|
| Anthropic computer-use demo (`anthropics/claude-quickstarts/computer-use-demo`) | **Borrow** the action layer | `computer.py` wraps xdotool plus screenshots and scales coordinates [V]. Skip its Docker or virtual-display setup |
| trycua/cua | Try for an hour at most | Ships a linux-arm64 driver and MCP server, but its own matrix says "Real Xorg: Not yet validated" [V] |
| Agent S (S3) | Borrow prompt ideas only | A full planner+grounder loop is too much for 24 hours [I] |
| screenpipe | Borrow the design idea only | A local REST API over SQLite of frames, OCR and app names. No ARM64 build and a non-commercial license [V] |
| UI-TARS-desktop, Open Interpreter, OpenAdapt | Avoid | Wrong platform or a different direction now [V] |

**Conclusion [I]:** write a thin in-house control module (mss, python-xlib, xdotool/wmctrl, python-mpv, PySide6), using the Anthropic demo's action layer as a reference. Expect about 4–6 hours across the team.

## Facts behind the X11 choice

- DGX OS (the only OS the GX10 supports) defaults to Xorg. Its Ubuntu base lacks the explicit-sync support Wayland needs on this hardware [V, NVIDIA forum, 2025-10-20; ASUS FAQ].
- Ubuntu 24.04 keeps the "Ubuntu on Xorg" session. 25.10 removed it, so **don't upgrade the OS during the event** [V, Ubuntu Discourse, 2025-06].
- Under GNOME Wayland, outside apps can't call the screenshot D-Bus API directly (they have to go through a portal), GTK4 has no always-on-top, and layer-shell isn't supported [V]. This is why staying on X11 matters.

## Sources

- NVIDIA forum, DGX OS Wayland vs X11: https://forums.developer.nvidia.com/t/dgx-os-support-for-wayland-over-x11/348199
- ASUS GX10 OS FAQ: https://www.asus.com/us/support/faq/1056142/
- Ubuntu 25.10 drops GNOME on Xorg: https://discourse.ubuntu.com/t/ubuntu-25-10-drops-support-for-gnome-on-xorg/62538
- GTK4 keep-above discussion: https://discourse.gnome.org/t/gtk4-and-set-keep-above-and-other-similar-apis-that-used-to-be-tied-to-x11/12447
- Anthropic computer-use demo: https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo
- trycua/cua: https://github.com/trycua/cua
- Agent S: https://github.com/simular-ai/Agent-S
- screenpipe: https://github.com/screenpipe/screenpipe
- Qwen3-VL technical report: https://arxiv.org/pdf/2511.21631
