"""X11 facts and RAM-only images, plus the version-one replay adapter."""

from __future__ import annotations

import bisect
import io
import json
import math
import subprocess
import time
from pathlib import Path

import mss
from PIL import Image
from Xlib import X, error
from Xlib import display as xdisplay
from Xlib.ext import screensaver  # noqa: F401 - registers XScreenSaver methods

from daybook.journal import WindowInfo

from ._gate import intersection
from ._types import Rect, ScreenState, VisibleWindow


def _jpeg(image: Image.Image) -> bytes:
    image = image.convert("RGB")
    if image.width > 1280:
        image = image.resize((1280, round(image.height * 1280 / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def _thumbnail(image: Image.Image) -> bytes:
    return image.convert("L").resize((32, 18)).tobytes()


def _command(*args: str) -> str:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class X11Screen:
    def __init__(self, display: str = ":1", shell_pid: int | None = None) -> None:
        self._name = display
        self._display = xdisplay.Display(display)
        self._root = self._display.screen().root
        self._shell_pid = shell_pid

    def set_shell_pid(self, pid: int | None) -> None:
        """App updates this identity when its shell child restarts."""
        self._shell_pid = pid

    def _property(self, window, name: str):
        prop = window.get_full_property(
            self._display.intern_atom(name), X.AnyPropertyType
        )
        return None if prop is None else prop.value

    def _facts(self, window) -> WindowInfo:
        wm_class = window.get_wm_class()
        app = wm_class[-1] if wm_class else "unknown"
        title = self._property(window, "_NET_WM_NAME")
        if title is None:
            title = window.get_wm_name() or ""
        if isinstance(title, bytes):
            title = title.decode("utf-8", errors="replace")
        return WindowInfo(str(app), str(title))

    def _locked(self) -> bool | None:
        sessions = _command("loginctl", "list-sessions", "--no-legend").splitlines()
        for row in sessions:
            fields = row.split()
            if "seat0" not in fields:
                continue
            locked = _command(
                "loginctl", "show-session", fields[0], "-p", "LockedHint", "--value"
            )
            if locked in ("yes", "no"):
                return locked == "yes"
        return None

    def state(self) -> ScreenState:
        try:
            geometry = self._root.get_geometry()
            bounds = Rect(0, 0, geometry.width, geometry.height)
            active_property = self._property(self._root, "_NET_ACTIVE_WINDOW")
            active_id = (
                int(active_property[0])
                if active_property is not None and len(active_property)
                else 0
            )
            active = None
            windows = []
            overlay = False
            stack = self._property(self._root, "_NET_CLIENT_LIST_STACKING")
            for wid in () if stack is None else stack:
                window = self._display.create_resource_object("window", int(wid))
                try:
                    attrs = window.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        continue
                    rect = window.get_geometry()
                    coords = self._root.translate_coords(window, 0, 0)
                    clipped = intersection(
                        Rect(coords.x, coords.y, rect.width, rect.height), bounds
                    )
                    if not clipped.width or not clipped.height:
                        continue
                    info = self._facts(window)
                    opacity_prop = self._property(window, "_NET_WM_WINDOW_OPACITY")
                    opacity = (
                        1.0
                        if opacity_prop is None
                        else max(0.0, min(1.0, int(opacity_prop[0]) / 0xFFFFFFFF))
                    )
                    pid_prop = self._property(window, "_NET_WM_PID")
                    pid = (
                        int(pid_prop[0])
                        if pid_prop is not None and len(pid_prop)
                        else None
                    )
                    windows.append(
                        VisibleWindow(str(int(wid)), info, clipped, opacity, pid)
                    )
                    if int(wid) == active_id:
                        active = info
                    if (
                        pid is not None
                        and pid == self._shell_pid
                        or info.app.lower() == "daybook-shell"
                    ):
                        overlay = True
                except error.XError:
                    continue  # a window closed while its facts were being read
            idle = int(self._root.screensaver_query_info().idle)
            return ScreenState(
                active,
                tuple(windows),
                self._locked(),
                idle,
                bool(_command("pactl", "list", "short", "source-outputs")),
                overlay,
            )
        except (error.XError, OSError, AttributeError):
            return ScreenState(None, (), None, 300_000)

    def _image(self) -> Image.Image:
        with mss.mss(display=self._name) as capture:
            shot = capture.grab(capture.monitors[0])
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def thumbnail(self) -> bytes:
        return _thumbnail(self._image())

    def grab(self) -> bytes:
        return _jpeg(self._image())


class ReplayScreen:
    def __init__(self, folder: Path, speed: float = 1.0) -> None:
        if speed <= 0 or not math.isfinite(speed):
            raise ValueError("replay speed must be positive and finite")
        self.folder = folder.resolve()
        steps = json.loads((self.folder / "steps.json").read_text())
        if not isinstance(steps, list) or not steps:
            raise ValueError("replay needs steps")
        self._times = [float(step["at"]) for step in steps]
        if (
            self._times[0] != 0
            or any(not math.isfinite(t) for t in self._times)
            or any(a >= b for a, b in zip(self._times, self._times[1:]))
        ):
            raise ValueError("replay offsets must increase strictly from zero")
        self._folders = [(self.folder / step["directory"]).resolve() for step in steps]
        if any(not path.is_relative_to(self.folder) for path in self._folders):
            raise ValueError("step escapes replay folder")
        self._started = time.monotonic()
        self.speed = speed

    def _step(self) -> Path:
        elapsed = max(0.0, time.monotonic() - self._started) * self.speed
        index = bisect.bisect_right(self._times, elapsed) - 1
        return self._folders[index]

    def state(self) -> ScreenState:
        folder = self._step()
        data = json.loads((folder / "window.json").read_text())
        locked = json.loads((folder / "locked").read_text())
        idle = json.loads((folder / "idle_ms").read_text())
        if (
            locked is not None
            and type(locked) is not bool
            or type(idle) is not int
            or idle < 0
        ):
            raise ValueError("invalid lock/idle replay facts")
        with Image.open(folder / "frame.png") as image:
            bounds = Rect(0, 0, image.width, image.height)
        windows = tuple(
            VisibleWindow(
                str(w["id"]),
                WindowInfo(**w["info"]),
                intersection(Rect(**w["rect"]), bounds),
                float(w.get("opacity", 1)),
                w.get("pid"),
            )
            for w in data["windows"]
        )
        return ScreenState(
            WindowInfo(**data["active"]) if data.get("active") else None,
            windows,
            locked,
            idle,
            bool(data.get("microphone_active", False)),
            bool(data.get("overlay", False)),
        )

    def thumbnail(self) -> bytes:
        with Image.open(self._step() / "frame.png") as image:
            return _thumbnail(image)

    def grab(self) -> bytes:
        with Image.open(self._step() / "frame.png") as image:
            return _jpeg(image)
