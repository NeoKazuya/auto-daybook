"""Immutable facts passed between capture policy and screen adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from daybook.journal import WindowInfo


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
