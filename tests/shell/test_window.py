"""Load the real QML offscreen and drive it the way the backend would."""

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt_gui = pytest.importorskip("PySide6.QtGui")
from PySide6.QtCore import QObject  # noqa: E402

from daybook.shell._app import FONTS, OverlayBridge, build, load_fonts  # noqa: E402
from daybook.shell._controller import OverlayController  # noqa: E402


class FakeBackend:
    def __init__(self) -> None:
        self.dismissed: list[str] = []

    def dismiss(self, id: str) -> bool:
        self.dismissed.append(id)
        return True


@pytest.fixture(scope="module")
def app():
    return qt_gui.QGuiApplication.instance() or qt_gui.QGuiApplication([])


def text(window, name: str) -> str:
    item = window.findChild(QObject, name)
    assert item is not None, name
    return item.property("text")


def status(style="calm", seconds=292):
    expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return {
        "overlay": {
            "id": "o1",
            "owner": "break",
            "expires_at": expires.isoformat(),
            "request": {
                "title": "Time for a break",
                "body": "<b>Stand up</b>, look away.",
                "dismiss": "I'm back",
                "minutes": 5,
                "style": style,
            },
        }
    }


def test_window_shows_hides_and_dismisses(app):
    backend = FakeBackend()
    bridge = OverlayBridge(OverlayController(), backend, *load_fonts())  # type: ignore[arg-type]
    engine, window = build(bridge)
    assert not window.isVisible()

    bridge.incoming.emit("status", status())
    app.processEvents()
    assert window.isVisible()
    assert text(window, "title") == "Time for a break"
    # Plain text: markup from a skill is shown literally, never rendered.
    assert text(window, "body") == "<b>Stand up</b>, look away."
    assert text(window, "countdown") in {"4:52", "4:51"}
    assert not bridge.alert

    bridge.dismiss()
    app.processEvents()
    assert not window.isVisible()

    bridge.incoming.emit(
        "OverlayShown", {"id": "o2", "request": status("alert")["overlay"]["request"]}
    )
    app.processEvents()
    assert window.isVisible() and bridge.alert
    bridge.incoming.emit("OverlayHidden", {"id": "o2"})
    app.processEvents()
    assert not window.isVisible()

    import time

    time.sleep(0.1)  # let the dismiss thread run
    assert backend.dismissed == ["o1"]
    del engine


@pytest.mark.skipif(not FONTS.is_dir(), reason="web fonts arrive with the UI PR (#25)")
def test_bundled_fonts_load(app):
    serif, mono = load_fonts()
    assert (serif, mono) == ("Literata", "JetBrains Mono")
