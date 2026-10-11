"""The Qt side of the shell: two pre-created overlay windows driven by the controller."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QFontDatabase, QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow

from ._client import Backend
from ._controller import OverlayController, View, countdown

LOG = logging.getLogger(__name__)
QML = Path(__file__).with_name("qml") / "Overlay.qml"
BUBBLE_QML = Path(__file__).with_name("qml") / "Bubble.qml"
# Sense masks windows of this class out of every frame and keeps capturing; any
# other shell window counts as a full-screen overlay and pauses capture.
BUBBLE_CLASS = "daybook-bubble"
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


class _XClassHint(ctypes.Structure):
    _fields_ = [("res_name", ctypes.c_char_p), ("res_class", ctypes.c_char_p)]


def set_wm_class(window: QWindow, name: str) -> bool:
    """Give one window its own WM_CLASS; Qt gives every window the app's class.

    Returns False if the class couldn't be set on X11. Other platforms (offscreen
    tests) have no X server to read it, so there is nothing to set.
    """
    if QGuiApplication.platformName() != "xcb":
        return True
    try:
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    except OSError:
        LOG.warning("bubble_class_unset: libX11 not found")
        return False
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XSetClassHint.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_XClassHint),
    ]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)  # the same $DISPLAY Qt uses
    if not display:
        LOG.warning("bubble_class_unset: cannot open display")
        return False
    try:
        hint = _XClassHint(name.encode(), name.encode())
        x11.XSetClassHint(display, int(window.winId()), ctypes.byref(hint))
        # Applied by the X server before Qt's own map request for this window.
        x11.XSync(display, 0)
    finally:
        x11.XCloseDisplay(display)
    return True


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
        self._bubble_ok = True
        self.incoming.connect(self._receive)

    # --- properties for QML -------------------------------------------------

    def _overlay_field(self, name: str, default: str = "") -> str:
        overlay = self._view.overlay
        return getattr(overlay, name) if overlay else default

    def _layout(self) -> str | None:
        overlay = self._view.overlay
        if overlay is None:
            return None
        # A bubble that Sense can't recognise would be captured, so without its own
        # window class it falls back to the full-screen layout, which pauses capture.
        return overlay.layout if self._bubble_ok else "fullscreen"

    @Property(bool, notify=shownChanged)
    def shown(self) -> bool:
        return self._view.visible

    @Property(bool, notify=shownChanged)
    def fullscreen(self) -> bool:
        return self._layout() == "fullscreen"

    @Property(bool, notify=shownChanged)
    def bubble(self) -> bool:
        return self._layout() == "bubble"

    @Property(str, notify=changed)
    def bubbleText(self) -> str:
        # The bubble speaks the skill's body; a body-less request still says something.
        return self._overlay_field("body") or self._overlay_field("title")

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
        was = self._layout()
        self._view = view
        self.changed.emit()
        if self._layout() != was:
            self.shownChanged.emit()

    def set_bubble_supported(self, ok: bool) -> None:
        was = self._layout()
        self._bubble_ok = ok
        if self._layout() != was:
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


def place_bubble(bubble: QWindow) -> None:
    """Top-right corner of the main display, clear of the top bar."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    margin = round(24 * max(area.height(), 600) / 1080)
    bubble.setPosition(
        area.x() + area.width() - bubble.width() - margin, area.y() + margin
    )


def follow(window: QQuickWindow, bubble: QQuickWindow, bridge: OverlayBridge) -> None:
    """Show the full-screen window (focused) or the bubble (unfocused), or neither."""

    def sync() -> None:
        if bridge.fullscreen and not window.isVisible():
            window.showFullScreen()
            window.raise_()
            window.requestActivate()
        elif not bridge.fullscreen and window.isVisible():
            window.hide()
        if bridge.bubble and not bubble.isVisible():
            place_bubble(bubble)
            set_wm_class(bubble, BUBBLE_CLASS)
            bubble.show()
        elif not bridge.bubble and bubble.isVisible():
            bubble.hide()

    bridge.shownChanged.connect(sync)


def build(
    bridge: OverlayBridge,
) -> tuple[QQmlApplicationEngine, QQuickWindow, QQuickWindow]:
    """Load both windows hidden; return the engine, full-screen window, and bubble."""
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("overlay", bridge)
    engine.load(str(QML))
    engine.load(str(BUBBLE_QML))
    roots = engine.rootObjects()
    if len(roots) != 2 or not all(isinstance(r, QQuickWindow) for r in roots):
        raise RuntimeError(f"could not load {QML.name} and {BUBBLE_QML.name}")
    window, bubble = roots
    assert isinstance(window, QQuickWindow) and isinstance(bubble, QQuickWindow)
    # Qt then marks the window so the window manager doesn't focus it on show.
    bubble.setProperty("_q_showWithoutActivating", True)
    bridge.set_bubble_supported(set_wm_class(bubble, BUBBLE_CLASS))
    follow(window, bubble, bridge)
    timer = QTimer(window)
    timer.setInterval(250)
    timer.timeout.connect(bridge.tick)
    timer.start()
    return engine, window, bubble
