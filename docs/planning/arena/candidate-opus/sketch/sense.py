"""daybook.sense — looks at the screen and writes observations. Owns the privacy settings.

Loop (every 5 s, cheap: no model call unless something changed):

    poll  -> locked?      -> CaptureState.LOCKED,   nothing captured
          -> paused?      -> CaptureState.PAUSED,   nothing captured
          -> active window matches an exclusion or is our own overlay
                          -> WITHHELD / OVERLAY,     nothing captured (window title not even stored)
          -> grab a 32x18 thumbnail; black?  -> LOCKED (backstop for the grounding's black frames)
          -> same window + thumbnail within 3% of the last observation  -> journal.extend_observation
          -> else, if >= 10 s since the last model observation (or the app changed):
                 gateway.call(OBSERVE, builder)   # builder re-checks the gate, grabs the full frame, JPEGs it
                 journal.add_observation(draft); mind.nudge(app_switched)

The full frame exists only as `bytes` inside the builder and the HTTP request, then is garbage. Nothing is
written to disk unless `debug.keep_frames = true` in the config (off by default, shown in the UI if on).

Layout when built:
    daybook/sense/__init__.py   exports: Sense, Screen, X11Screen, ReplayScreen, Exclusion, PrivacySettings
    daybook/sense/_x11.py       mss grab, python-xlib _NET_ACTIVE_WINDOW / WM_CLASS / _NET_WM_NAME, loginctl LockedHint
    daybook/sense/_prompt.py    the observation prompt + its private pydantic output model
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn, Protocol

from .journal import CaptureState, Journal, ObservationDraft, WindowInfo
from .model import ModelGateway

OWN_WINDOW_CLASS = "daybook-shell"  # the overlay/tray process sets this WM_CLASS; always treated as OVERLAY


@dataclass(frozen=True)
class Exclusion:
    """Matches the active window. app is a glob on WM_CLASS ('KeePassXC', '*signal*'); title is a case-insensitive substring."""

    app: str | None = None
    title: str | None = None

    def matches(self, window: WindowInfo) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class PrivacySettings:
    """Persisted by sense in $DAYBOOK_HOME/privacy.json; sense is its only writer."""

    paused: bool
    exclusions: tuple[Exclusion, ...]

    @staticmethod
    def defaults() -> PrivacySettings:
        """Not paused. Excludes password managers, and our own web UI tab (title contains 'Daybook ·')."""
        raise NotImplementedError


@dataclass(frozen=True)
class Frame:
    at: datetime
    thumb: bytes  # 32x18 grayscale; used for change detection and the black-frame check

    def is_black(self) -> bool:
        raise NotImplementedError

    def differs_from(self, other: Frame, threshold: float = 0.03) -> bool:
        raise NotImplementedError


class Screen(Protocol):
    """The only source of pixels and window facts. X11Screen on the box; ReplayScreen everywhere else."""

    def locked(self) -> bool: ...
    def active_window(self) -> WindowInfo | None: ...
    def thumbnail(self) -> Frame: ...
    def jpeg(self, max_px: int, quality: int) -> bytes: ...


class X11Screen:
    """DISPLAY=:1, XAUTHORITY=/run/user/1000/gdm/Xauthority. Lock = `loginctl show-session <id> -p LockedHint`."""

    def __init__(self, display: str, xauthority: Path) -> None:
        raise NotImplementedError


class ReplayScreen:
    """Plays a folder of (png, window.json, locked) steps. For tests and for dev instances that must not
    capture the shared real screen (only one capture loop on the real screen at a time; see docs/gx10.md)."""

    def __init__(self, folder: Path) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class Capture:
    window: WindowInfo


@dataclass(frozen=True)
class Skip:
    state: CaptureState


def gate(settings: PrivacySettings, locked: bool, window: WindowInfo | None) -> Capture | Skip:
    """Pure. The whole privacy decision, in one place, before any pixels are read.

    TODO:
      if locked: Skip(LOCKED)
      if settings.paused: Skip(PAUSED)
      if window is None: Skip(LOCKED)            # no active window = lock screen / greeter
      if window.app == OWN_WINDOW_CLASS: Skip(OVERLAY)
      if any(e.matches(window) for e in settings.exclusions): Skip(WITHHELD)
      return Capture(window)
    """
    raise NotImplementedError


class Sense:
    def __init__(self, screen: Screen, gateway: ModelGateway, journal: Journal, settings_path: Path,
                 on_observed: Callable[[bool], None]) -> None:
        """on_observed(app_switched) is mind.nudge, wired in app.py."""
        raise NotImplementedError

    async def run(self) -> NoReturn:
        raise NotImplementedError

    def privacy(self) -> PrivacySettings:
        raise NotImplementedError

    def set_privacy(self, settings: PrivacySettings) -> PrivacySettings:
        """Persist, apply on the next poll (<= 5 s), and journal.set_capture_state if it changes the state."""
        raise NotImplementedError


def observe_prompt_text(window: WindowInfo) -> str:
    """The instruction: describe only what is visible. No guesses about goals or feelings; that's the tick's job.

    Output schema (private _ObserveOut): {"seen": str (<= 2 sentences), "visible_text": str (<= 300 chars)}.
    The window app/title are given to the model as facts and are stored from X11, not from the model.
    """
    raise NotImplementedError


def to_draft(at: datetime, window: WindowInfo, out: object) -> ObservationDraft:
    raise NotImplementedError
