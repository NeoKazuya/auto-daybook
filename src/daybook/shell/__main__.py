"""python -m daybook.shell --port 8765

Started by daybookd (with DAYBOOK_TOKEN) or by hand (token from $DAYBOOK_HOME/token).
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading


def main() -> int:
    parser = argparse.ArgumentParser(prog="daybook.shell")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # X11 WM_CLASS comes from argv[0] and the application name; sense matches
    # "daybook-shell" (and our PID) to report the overlay capture state.
    sys.argv[0] = "daybook-shell"
    from PySide6.QtGui import QGuiApplication

    from ._app import OverlayBridge, build, load_fonts
    from ._client import Backend, read_token
    from ._controller import OverlayController

    app = QGuiApplication(sys.argv)
    app.setApplicationName("daybook-shell")
    app.setDesktopFileName("daybook-shell")
    app.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    backend = Backend(args.port, read_token())
    serif, mono = load_fonts()
    bridge = OverlayBridge(OverlayController(), backend, serif, mono)
    engine, _window, _bubble = build(bridge)

    stop = threading.Event()
    thread = threading.Thread(
        target=backend.run,
        args=(
            lambda status: bridge.incoming.emit("status", status),
            lambda name, data: bridge.incoming.emit(name, data),
            stop,
        ),
        daemon=True,
    )
    thread.start()
    try:
        return app.exec()
    finally:
        stop.set()
        del engine


if __name__ == "__main__":
    raise SystemExit(main())
