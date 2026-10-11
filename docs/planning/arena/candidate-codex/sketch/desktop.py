"""Own X11 knowledge and concrete effects, not skill or model policy.

Core capture adapter and Qt helper share this module's private X11 details.
ActionBackend is the sole replacement point for a tested computer-control
framework. Qt helper owns overlay + tray, mpv owns playback, core owns consent.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from .domain import Action, Receipt, RunId


@dataclass(frozen=True)
class PrivacyRules:
    excluded_app_classes: tuple[str, ...]
    excluded_title_patterns: tuple[str, ...]


@dataclass(frozen=True)
class CapturePermit:
    """Runtime-minted generation; unusable after pause or exclusion change."""
    generation: int


@dataclass(frozen=True)
class Frame:
    """Ephemeral bytes; never persisted, logged, included in events, or cached."""
    captured_at: datetime
    app: str
    jpeg: bytes
    permit: CapturePermit


class CaptureSource:
    async def acquire(self) -> Frame | None:
        """Fail closed for pause, lock, unknown session, own overlay, or excluded windows."""
        # TODO: core grants permit; single Xlib connection briefly grabs X server, checks
        # mapped windows, captures via that connection, and ungrabs in finally.
        # TODO: conservative whole-screen skip when ANY mapped excluded window exists.
        # TODO: check GNOME/login lock state before/after; reject unknown/black frames.
        # TODO: resize only after releasing X server; maximum one transient frame.
        raise NotImplementedError

    async def current(self, permit: CapturePermit) -> bool:
        """Gate recheck before sending pixels and committing their observation."""
        raise NotImplementedError


@dataclass(frozen=True)
class Effect:
    run: RunId
    generation: int
    action: Action


class ActionBackend(Protocol):
    async def execute(self, effect: Effect) -> Receipt:
        """Acknowledge actual application; duplicate run IDs do not repeat within a session."""
        # TODO: mood -> local manifest -> mpv; overlay -> reusable plain-text Qt component.
        # TODO: stale generations rejected; commands never contain shell or arbitrary code.
        raise NotImplementedError

    async def quiesce(self, generation: int) -> None:
        """Barrier: reject older effects, dismiss overlay, stop automated music."""
        # TODO: ordered IPC acknowledgment before runtime reports pause complete.
        raise NotImplementedError
