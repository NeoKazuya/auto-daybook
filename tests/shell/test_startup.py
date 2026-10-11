"""Exercise the shipped entry point with real Qt windows in a fresh process."""

import os
import subprocess
import sys


def test_shell_startup_renders_both_windows_without_a_graphics_driver():
    probe = """
from PySide6.QtCore import QTimer
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from daybook.shell import _app, __main__

original = _app.build
def build(bridge):
    assert QQuickWindow.sceneGraphBackend() == "software", "software backend must be selected before creating windows"
    engine, fullscreen, bubble = original(bridge)
    for window in (fullscreen, bubble):
        assert window.rendererInterface().graphicsApi() == QSGRendererInterface.Software, "Shell still requires a graphics driver"
        window.show()
        image = window.grabWindow()
        assert not image.isNull(), "software window did not render"
        window.hide()
    QTimer.singleShot(0, lambda: __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.quit())
    return engine, fullscreen, bubble
_app.build = build
raise SystemExit(__main__.main())
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("QSG_", "QT_QUICK_", "QMLSCENE_"))
    }
    env.update(QT_QPA_PLATFORM="offscreen", DAYBOOK_TOKEN="startup-test")
    result = subprocess.run(
        [sys.executable, "-c", probe, "--port", "0"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
