"""Load the real QML offscreen and drive it the way the backend would."""

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QObject, QPointF

from daybook.shell._app import FONTS, OverlayBridge, build, load_fonts
from daybook.shell._controller import OverlayController


class FakeBackend:
    def __init__(self) -> None:
        self.dismissed: list[str] = []

    def dismiss(self, id: str) -> bool:
        self.dismissed.append(id)
        return True


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
    engine, window, _bubble = build(bridge)
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


def item_rect(window, name: str) -> tuple[float, float, float, float]:
    item = window.findChild(QObject, name)
    assert item is not None, name
    corner = item.mapToScene(QPointF(0, 0))
    return corner.x(), corner.y(), corner.x() + item.width(), corner.y() + item.height()


def test_long_text_keeps_the_dismiss_controls_on_screen(app):
    # Chat-made skills can write any length; the countdown and button must stay
    # reachable and the message must not run off the top of the screen.
    bridge = OverlayBridge(OverlayController(), FakeBackend(), *load_fonts())  # type: ignore[arg-type]
    engine, window, _bubble = build(bridge)
    long = status()
    long["overlay"]["request"]["title"] = "Stand up and stretch " * 40
    long["overlay"]["request"]["body"] = "Look away from the screen for a while. " * 200
    long["overlay"]["request"]["dismiss"] = "I'm back " * 30
    bridge.incoming.emit("status", long)
    app.processEvents()
    assert window.isVisible()
    window.grabWindow()  # lay out and render once, as on a real screen
    for name in ("title", "body", "countdown", "dismissButton"):
        left, top, right, bottom = item_rect(window, name)
        on_screen = 0 <= left and right <= window.width() and 0 <= top
        assert on_screen and bottom <= window.height(), (name, left, top, right, bottom)
    bridge.dismiss()
    app.processEvents()
    del engine


def bubble_status(**request):
    shown = status()
    shown["overlay"]["request"].update(layout="bubble", **request)
    return shown


def click(window, name: str) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    item = window.findChild(QObject, name)
    assert item is not None, name
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y()))
    )


def test_bubble_layout_uses_its_own_unfocused_window(app):
    from PySide6.QtCore import Qt

    backend = FakeBackend()
    bridge = OverlayBridge(OverlayController(), backend, *load_fonts())  # type: ignore[arg-type]
    engine, window, bubble = build(bridge)
    flags = bubble.flags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    # Override-redirect windows are invisible to Sense, which then couldn't mask it.
    assert not flags & Qt.WindowType.X11BypassWindowManagerHint
    assert bubble.property("_q_showWithoutActivating") is True

    bridge.incoming.emit("status", bubble_status())
    app.processEvents()
    assert bubble.isVisible() and not window.isVisible()
    assert text(bubble, "bubbleText") == "<b>Stand up</b>, look away."
    assert text(bubble, "bubbleCountdown").startswith("goes away in 4:5")
    area = app.primaryScreen().availableGeometry()
    assert area.contains(bubble.geometry())
    assert bubble.width() < area.width() / 2  # clicks elsewhere reach the apps below

    # A full-screen request replaces the bubble, and the other way round.
    bridge.incoming.emit(
        "OverlayShown", {"id": "o2", "request": status()["overlay"]["request"]}
    )
    app.processEvents()
    assert window.isVisible() and not bubble.isVisible()
    bridge.incoming.emit(
        "OverlayShown", {"id": "o3", "request": bubble_status()["overlay"]["request"]}
    )
    app.processEvents()
    assert bubble.isVisible() and not window.isVisible()

    bubble.grabWindow()
    click(bubble, "bubbleLater")
    app.processEvents()
    assert not bubble.isVisible()
    bridge.incoming.emit(
        "OverlayShown", {"id": "o4", "request": bubble_status()["overlay"]["request"]}
    )
    app.processEvents()
    bubble.grabWindow()
    click(bubble, "bubbleDismiss")
    app.processEvents()
    assert not bubble.isVisible()

    import time

    time.sleep(0.1)  # let the dismiss threads run
    assert sorted(backend.dismissed) == ["o3", "o4"]
    del engine


def test_bubble_without_its_window_class_falls_back_to_fullscreen(app):
    bridge = OverlayBridge(OverlayController(), FakeBackend(), *load_fonts())  # type: ignore[arg-type]
    engine, window, bubble = build(bridge)
    bridge.set_bubble_supported(False)
    bridge.incoming.emit("status", bubble_status())
    app.processEvents()
    assert window.isVisible() and not bubble.isVisible()
    bridge.dismiss()
    app.processEvents()
    del engine


def test_bubble_with_an_empty_body_says_the_title(app):
    bridge = OverlayBridge(OverlayController(), FakeBackend(), *load_fonts())  # type: ignore[arg-type]
    engine, _window, bubble = build(bridge)
    bridge.incoming.emit("status", bubble_status(body=""))
    app.processEvents()
    assert text(bubble, "bubbleText") == "Time for a break"
    bridge.dismiss()
    app.processEvents()
    del engine
