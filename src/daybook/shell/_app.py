"""The Qt side of the shell: one pre-created overlay window driven by the controller."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow

from ._client import Backend
from ._controller import OverlayController, View, countdown

QML = Path(__file__).with_name("qml") / "Overlay.qml"
# The web UI's bundled fonts; the shell reads files, never the web package.
FONTS = Path(__file__).parents[1] / "web" / "static" / "fonts"


def load_fonts() -> tuple[str, str]:
    """Register the bundled faces; return (serif, mono) families to use."""
    families: set[str] = set()
    for file in sorted(FONTS.glob("*.woff2")) + sorted(FONTS.glob("*.ttf")):
        id = QFontDatabase.addApplicationFont(str(file))
        if id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(id))
    serif = "Literata" if "Literata" in families else "Georgia"
    mono = "JetBrains Mono" if "JetBrains Mono" in families else "Menlo"
    return serif, mono


class OverlayBridge(QObject):
    """What QML binds to (as `overlay`), and the only place the view changes."""

    changed = Signal()
    shownChanged = Signal()
    # Thread-safe hand-off from the client thread to the GUI thread.
    incoming = Signal(str, object)

    def __init__(
        self,
        controller: OverlayController,
        backend: Backend | None,
        serif: str,
        mono: str,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._backend = backend
        self._serif, self._mono = serif, mono
        self._view = View(None, 0)
        self.incoming.connect(self._receive)

    # --- properties for QML -------------------------------------------------

    def _overlay_field(self, name: str, default: str = "") -> str:
        overlay = self._view.overlay
        return getattr(overlay, name) if overlay else default

    @Property(bool, notify=shownChanged)
    def shown(self) -> bool:
        return self._view.visible

    @Property(str, notify=changed)
    def label(self) -> str:
        return "Daybook" if self._view.visible else ""

    @Property(str, notify=changed)
    def title(self) -> str:
        return self._overlay_field("title")

    @Property(str, notify=changed)
    def body(self) -> str:
        return self._overlay_field("body")

    @Property(str, notify=changed)
    def dismissLabel(self) -> str:
        return self._overlay_field("dismiss", "OK")

    @Property(bool, notify=changed)
    def alert(self) -> bool:
        return self._overlay_field("style") == "alert"

    @Property(str, notify=changed)
    def countdown(self) -> str:
        return countdown(self._view.remaining)

    @Property(str, constant=True)
    def serifFamily(self) -> str:
        return self._serif

    @Property(str, constant=True)
    def monoFamily(self) -> str:
        return self._mono

    # --- updates ------------------------------------------------------------

    def _apply(self, view: View) -> None:
        was = self._view.visible
        self._view = view
        self.changed.emit()
        if view.visible != was:
            self.shownChanged.emit()

    @Slot(str, object)
    def _receive(self, kind: str, payload: Any) -> None:
        if kind == "status":
            self._apply(self._controller.resync(payload))
        else:
            self._apply(self._controller.handle(kind, payload))

    @Slot()
    def tick(self) -> None:
        self._apply(self._controller.view())

    @Slot()
    def dismiss(self) -> None:
        id = self._controller.dismiss()
        self._apply(self._controller.view())
        if id and self._backend is not None:
            backend = self._backend
            threading.Thread(target=backend.dismiss, args=(id,), daemon=True).start()


def follow(window: QQuickWindow, bridge: OverlayBridge) -> None:
    """Show the pre-created window full-screen and focused, or hide it."""

    def sync() -> None:
        if bridge.shown and not window.isVisible():
            window.showFullScreen()
            window.raise_()
            window.requestActivate()
        elif not bridge.shown and window.isVisible():
            window.hide()

    bridge.shownChanged.connect(sync)


def build(
    bridge: OverlayBridge,
) -> tuple[QQmlApplicationEngine, QQuickWindow]:
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("overlay", bridge)
    engine.load(str(QML))
    roots = engine.rootObjects()
    if not roots or not isinstance(roots[0], QQuickWindow):
        raise RuntimeError(f"could not load {QML}")
    window = roots[0]
    follow(window, bridge)
    timer = QTimer(window)
    timer.setInterval(250)
    timer.timeout.connect(bridge.tick)
    timer.start()
    return engine, window
