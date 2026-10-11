"""The tray mirrors the capture state and changes it only through the API."""

import threading
import time

from PySide6.QtCore import QTimer

from daybook.shell._tray import Tray, TrayView, tray_view
from daybook.web.wire import SHELL_QUIT_EXIT_CODE


class FakeBackend:
    origin = "http://127.0.0.1:8781"

    def __init__(self) -> None:
        self.paused = False
        self.puts: list[bool] = []

    def settings(self):
        return {"paused": self.paused, "exclude_apps": [], "exclude_titles": []}

    def privacy(self):
        return self.settings()

    def set_paused(self, paused: bool):
        self.puts.append(paused)
        self.paused = paused
        return self.settings()


def wait_for(app, condition, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "timed out"
        app.processEvents()
        time.sleep(0.01)


def test_view_shows_capture_state_and_the_pause_switch():
    assert tray_view(False, None, None) == TrayView(
        "offline", "Daybook isn’t running", "Pause", False
    )
    assert tray_view(True, "running", False) == TrayView(
        "recording", "Daybook: Recording", "Pause", True
    )
    assert tray_view(True, "withheld", False).icon == "private"
    assert tray_view(True, "locked", False).text == "Daybook: Screen locked"
    # Paused while locked is still paused; before privacy loads, capture decides.
    assert tray_view(True, "locked", True).pause_label == "Resume"
    assert tray_view(True, "paused", None).icon == "paused"


def test_pause_and_resume_go_through_the_api(app):
    backend = FakeBackend()
    tray = Tray(backend, lambda url: None)
    assert not tray.pause_action.isEnabled()

    tray.receive("status", {"capture": "running", "overlay": None})
    wait_for(app, lambda: tray._paused is False)
    assert tray.icon.toolTip() == "Daybook: Recording"
    assert tray.pause_action.isEnabled() and tray.pause_action.text() == "Pause"

    tray.pause_action.trigger()
    wait_for(app, lambda: tray.pause_action.text() == "Resume")
    assert backend.puts == [True]
    assert tray.icon.toolTip() == "Daybook: Paused"

    tray.pause_action.trigger()
    wait_for(app, lambda: tray.pause_action.text() == "Pause")
    assert backend.puts == [True, False]

    tray.receive("Disconnected", {})
    assert not tray.pause_action.isEnabled()
    assert tray.icon.toolTip() == "Daybook isn’t running"


def test_a_pause_from_the_web_ui_shows_up(app):
    backend = FakeBackend()
    tray = Tray(backend, lambda url: None)
    tray.receive("status", {"capture": "running"})
    wait_for(app, lambda: tray._paused is False)
    backend.paused = True  # paused elsewhere; sense then reports the new state
    tray.receive("CaptureStateChanged", {"state": "paused"})
    wait_for(app, lambda: tray.pause_action.text() == "Resume")
    assert backend.puts == []


def test_open_daybook_opens_the_port_the_shell_uses(app):
    opened: list[str] = []
    tray = Tray(FakeBackend(), opened.append)
    tray.open_action.trigger()
    assert opened == ["http://127.0.0.1:8781"]


def test_quit_exits_with_the_code_daybookd_shuts_down_on(app):
    tray = Tray(FakeBackend(), lambda url: None)
    QTimer.singleShot(0, tray.quit_action.trigger)
    assert app.exec() == SHELL_QUIT_EXIT_CODE


class SlowReadBackend(FakeBackend):
    """Reads privacy, then answers only once released: a read that lands late."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def privacy(self):
        seen = super().privacy()
        self.release.wait(2)
        return seen


def test_an_older_read_cannot_undo_a_newer_pause(app):
    backend = SlowReadBackend()
    tray = Tray(backend, lambda url: None)
    tray.receive("status", {"capture": "running"})  # starts a read: not paused
    tray.pause_action.trigger()
    wait_for(app, lambda: tray.pause_action.text() == "Resume")
    backend.release.set()  # the old read now answers "not paused"
    time.sleep(0.1)
    app.processEvents()
    assert tray.pause_action.text() == "Resume"
