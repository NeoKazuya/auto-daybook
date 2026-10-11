"""Ordered, nonblocking domain events. Serialization belongs to web/wire.py."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from daybook.act import OverlayRequest
    from daybook.journal import CaptureState


@dataclass(frozen=True)
class ObservationAdded:
    id: str


@dataclass(frozen=True)
class ObservationExtended:
    id: str


@dataclass(frozen=True)
class SegmentChanged:
    id: str


@dataclass(frozen=True)
class TickRecorded:
    tick: str


@dataclass(frozen=True)
class CaptureStateChanged:
    state: CaptureState


@dataclass(frozen=True)
class SkillFired:
    skill: str
    rule: int
    key: str


@dataclass(frozen=True)
class SkillsChanged:
    id: str


@dataclass(frozen=True)
class OverlayShown:
    id: str
    request: OverlayRequest


@dataclass(frozen=True)
class OverlayHidden:
    id: str


@dataclass(frozen=True)
class MusicChanged:
    mood: str | None
    title: str | None


@dataclass(frozen=True)
class StatusChanged:
    component: str
    status: str


Event = (
    ObservationAdded
    | ObservationExtended
    | SegmentChanged
    | TickRecorded
    | CaptureStateChanged
    | SkillFired
    | SkillsChanged
    | OverlayShown
    | OverlayHidden
    | MusicChanged
    | StatusChanged
)


class Bus:
    """Use on one asyncio loop; each subscription has its own bounded queue."""

    def __init__(self, capacity: int = 128) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._queues: set[asyncio.Queue[Event]] = set()

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self.capacity)
        self._queues.add(queue)
        try:
            yield queue
        finally:
            self._queues.discard(queue)

    def publish(self, event: Event) -> None:
        for queue in self._queues:
            if queue.full():
                queue.get_nowait()
                logging.getLogger(__name__).warning(
                    "subscriber_overflow event=%s", type(event).__name__
                )
            queue.put_nowait(event)
