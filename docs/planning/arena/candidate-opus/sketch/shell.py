"""daybook.shell — the only Qt process: tray icon + reusable full-screen overlay. `python -m daybook.shell --port 8765`.

Why a separate process: Qt wants the main thread and its own event loop; daybookd is asyncio. The shell
holds no state of its own. It is a view of daybookd:

    subscribe GET /api/events?client=shell (SSE)
      OverlayShown(id, request) -> one pre-created frameless, always-on-top, fullscreen QWebEngineView
                                   loads http://127.0.0.1:<port>/overlay/<id> and shows (reused, so it appears < 300 ms)
      OverlayHidden(id)         -> hide
      CaptureStateChanged       -> tray icon: recording / paused / private / locked
    tray menu: Pause / Resume -> PUT /api/privacy ; Open Daybook -> xdg-open http://127.0.0.1:<port> ; Quit shell

WM_CLASS is set to "daybook-shell" so sense never observes the overlay (CaptureState.OVERLAY).
Env when spawned by daybookd: DISPLAY=:1, XAUTHORITY=/run/user/1000/gdm/Xauthority,
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus (the tray uses StatusNotifierItem over D-Bus).
Fallback if QtWebEngine misbehaves on aarch64: render the overlay with plain QLabels from the event payload.
"""

from __future__ import annotations


def main(port: int) -> None:
    raise NotImplementedError
