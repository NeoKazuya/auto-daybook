"""The tray icon (ARCHITECTURE §6.10): capture state, Pause/Resume, Open Daybook, Quit.

A StatusNotifierItem through Qt; on GNOME it needs the AppIndicator extension.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from PySide6.QtCore import QObject, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from daybook.web.wire import SHELL_QUIT_EXIT_CODE

LOG = logging.getLogger(__name__)

Icon = Literal["recording", "paused", "private", "idle", "offline"]

# Capture states (wire CaptureState) in the web UI's words (static/js/app.js).
STATES: dict[str, tuple[Icon, str]] = {
    "running": ("recording", "Recording"),
    "paused": ("paused", "Paused"),
    "withheld": ("private", "Private window"),
    "locked": ("idle", "Screen locked"),
    "away": ("idle", "Away"),
    "overlay": ("idle", "Overlay up"),
}


@dataclass(frozen=True)
class TrayView:
    icon: Icon
    text: str
    pause_label: Literal["Pause", "Resume"]
    connected: bool


def tray_view(connected: bool, capture: str | None, paused: bool | None) -> TrayView:
    """What the icon, its tooltip, and the Pause/Resume item say."""
    if not connected:
        return TrayView("offline", "Daybook isn’t running", "Pause", False)
    if paused is None:  # privacy not read yet
        paused = capture == "paused"
    # The pause switch wins: capture reports it within one poll, the switch at once.
    icon, words = (
        STATES["paused"] if paused else STATES.get(capture or "", ("idle", "Starting"))
    )
    return TrayView(icon, f"Daybook: {words}", "Resume" if paused else "Pause", True)


_COLORS: dict[Icon, str] = {
    "recording": "#d97757",
    "paused": "#e5e5e5",
    "private": "#ffb59e",
    "idle": "#8a8a8a",
    "offline": "#8a8a8a",
}


def draw_icon(kind: Icon) -> QIcon:
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(_COLORS[kind])
    box = QRectF(10, 10, 44, 44)
    if kind == "paused":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(16, 12, 11, 40), 3, 3)
        painter.drawRoundedRect(QRectF(37, 12, 11, 40), 3, 3)
    elif kind in ("private", "offline"):
        painter.setPen(QPen(color, 6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box)
        if kind == "private":
            painter.drawLine(16, 48, 48, 16)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(box if kind == "recording" else box.adjusted(8, 8, -8, -8))
    painter.end()
    return QIcon(pixmap)


class TrayBackend(Protocol):
    origin: str

    def privacy(self) -> dict[str, Any]: ...

    def set_paused(self, paused: bool) -> dict[str, Any]: ...


def open_in_browser(url: str) -> None:
    subprocess.Popen(
        ["xdg-open", url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class Tray(QObject):
    """Mirrors the capture state; every change goes through the HTTP API."""

    # Results from worker threads, delivered on the GUI thread.
    privacyLoaded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        backend: TrayBackend,
        open_url: Callable[[str], None] = open_in_browser,
    ) -> None:
        super().__init__()
        self._backend = backend
        self._open_url = open_url
        self._connected = False
        self._capture: str | None = None
        self._paused: bool | None = None
        # Requests are numbered so an older read can't undo a newer toggle.
        self._sent = self._applied = 0

        self.menu = QMenu()
        self.status_action = self.menu.addAction("")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.pause_action = self.menu.addAction("Pause")
        self.pause_action.triggered.connect(self._toggle_pause)
        self.open_action = self.menu.addAction("Open Daybook")
        self.open_action.triggered.connect(self._open)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("Quit Daybook")
        self.quit_action.triggered.connect(self._quit)

        self.icon = QSystemTrayIcon()
        self.icon.setContextMenu(self.menu)
        self.privacyLoaded.connect(self._on_privacy)
        self.failed.connect(self._on_failed)
        self._render()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            LOG.warning("tray_unavailable: is the AppIndicator extension enabled?")
        self.icon.show()

    @property
    def view(self) -> TrayView:
        return tray_view(self._connected, self._capture, self._paused)

    # --- events from the client thread (same hand-off as the overlay) -----------

    @Slot(str, object)
    def receive(self, kind: str, payload: Any) -> None:
        data: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
        if kind == "status":
            self._connected = True
            self._capture = str(data.get("capture") or "") or None
            self._load_privacy()
        elif kind == "CaptureStateChanged":
            self._capture = str(data.get("state") or "") or None
            self._load_privacy()
        elif kind == "Disconnected":
            self._connected = False
            self._paused = None
        else:
            return
        self._render()

    # --- menu actions -----------------------------------------------------------

    def _toggle_pause(self) -> None:
        view = self.view
        if not view.connected:
            return
        paused = view.pause_label == "Pause"
        verb = "pause" if paused else "resume"
        self._background(lambda: self._backend.set_paused(paused), f"Couldn’t {verb}")

    def _open(self) -> None:
        try:
            self._open_url(self._backend.origin)
        except OSError:
            self.failed.emit("Couldn’t open the browser. Go to " + self._backend.origin)

    def _quit(self) -> None:
        # daybookd shuts down on this code; any other exit restarts the shell.
        QApplication.exit(SHELL_QUIT_EXIT_CODE)

    # --- plumbing ---------------------------------------------------------------

    def _load_privacy(self) -> None:
        self._background(self._backend.privacy, None)

    def _background(
        self, call: Callable[[], dict[str, Any]], failure: str | None
    ) -> None:
        self._sent += 1
        seq = self._sent

        def work() -> None:
            try:
                result = call()
            except Exception as error:
                LOG.warning("tray_request_failed: %s", type(error).__name__)
                result = None
            try:
                self.privacyLoaded.emit((seq, result))
                if result is None and failure:
                    self.failed.emit(f"{failure}: Daybook isn’t responding.")
            except RuntimeError:
                pass  # the tray was deleted while the shell quit

        threading.Thread(target=work, daemon=True).start()

    @Slot(object)
    def _on_privacy(self, loaded: tuple[int, Any]) -> None:
        seq, privacy = loaded
        if seq < self._applied:
            return
        self._applied = seq
        if isinstance(privacy, Mapping) and isinstance(privacy.get("paused"), bool):
            self._paused = privacy["paused"]
            self._render()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.icon.showMessage("Daybook", message, QSystemTrayIcon.MessageIcon.Warning)

    def _render(self) -> None:
        view = self.view
        self.icon.setIcon(draw_icon(view.icon))
        self.icon.setToolTip(view.text)
        self.status_action.setText(view.text)
        self.pause_action.setText(view.pause_label)
        self.pause_action.setEnabled(view.connected)
