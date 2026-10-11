"""Screen facts, privacy, and capture contracts; no pixels are persisted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from daybook.events import Bus
from daybook.journal import CaptureState, Journal, WindowInfo
from daybook.model import ModelGateway


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class VisibleWindow:
    id: str
    info: WindowInfo
    rect: Rect
    opacity: float = 1.0
    pid: int | None = None


@dataclass(frozen=True)
class ScreenState:
    active: WindowInfo | None
    windows: tuple[VisibleWindow, ...]  # bottom to top
    locked: bool | None
    idle_ms: int
    microphone_active: bool = False
    overlay: bool = False


@dataclass(frozen=True)
class PrivacySettings:
    paused: bool = False
    exclude_apps: tuple[str, ...] = ("KeePassXC", "1Password", "Bitwarden")
    exclude_titles: tuple[str, ...] = (
        "*Private Browsing*",
        "*Incognito*",
        "*Daybook ·*",
    )


class Screen(Protocol):
    def state(self) -> ScreenState: ...
    def thumbnail(self) -> bytes: ...  # 32 x 18 grayscale
    def grab(self) -> bytes: ...  # 1280px JPEG in RAM


class X11Screen:
    def __init__(self, display: str = ":1", shell_pid: int | None = None) -> None:
        raise NotImplementedError

    def state(self) -> ScreenState:
        raise NotImplementedError

    def thumbnail(self) -> bytes:
        raise NotImplementedError

    def grab(self) -> bytes:
        raise NotImplementedError


class ReplayScreen:
    def __init__(self, folder: Path, speed: float = 1.0) -> None:
        raise NotImplementedError

    def state(self) -> ScreenState:
        raise NotImplementedError

    def thumbnail(self) -> bytes:
        raise NotImplementedError

    def grab(self) -> bytes:
        raise NotImplementedError


def gate(state: ScreenState, privacy: PrivacySettings) -> CaptureState:
    raise NotImplementedError


class Sense:
    def __init__(
        self,
        screen: Screen,
        journal: Journal,
        gateway: ModelGateway,
        bus: Bus,
        home: Path,
        nudge: Callable[[bool], None],
    ) -> None:
        raise NotImplementedError

    @property
    def privacy(self) -> PrivacySettings:
        raise NotImplementedError

    @property
    def generation(self) -> int:
        raise NotImplementedError

    def set_privacy(self, settings: PrivacySettings) -> PrivacySettings:
        raise NotImplementedError

    async def poll(self, at: datetime | None = None) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        raise NotImplementedError
