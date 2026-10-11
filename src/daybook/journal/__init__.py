"""Notes contracts. Only Journal writes notes; Portal owns reads and durations.

Timestamps are timezone-aware UTC datetimes; durations are seconds. Implementations
arrive after the shared skeleton. Mutations return False for a stale epoch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from daybook.events import Bus

ActivityKind = Literal[
    "coding",
    "writing",
    "reading",
    "browsing",
    "meeting",
    "chatting",
    "media",
    "admin",
    "other",
]
CaptureState = Literal["running", "paused", "locked", "away", "withheld", "overlay"]
SignalValue = bool | str | float | None


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class WindowInfo:
    app: str
    title: str


@dataclass(frozen=True)
class ObservationDraft:
    started_at: datetime
    window: WindowInfo
    seen: str
    visible_text: str = ""


@dataclass(frozen=True)
class Observation:
    id: str
    started_at: datetime
    last_seen_at: datetime
    window: WindowInfo
    seen: str
    visible_text: str
    segment: str | None

    @property
    def active(self) -> float:
        return (self.last_seen_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class Segment:
    id: str
    title: str
    kind: ActivityKind
    intent: str
    authored: Literal["model", "user"]
    open: bool
    observations: tuple[Observation, ...]

    @property
    def started_at(self) -> datetime | None:
        return min((o.started_at for o in self.observations), default=None)

    @property
    def ended_at(self) -> datetime | None:
        return max((o.last_seen_at for o in self.observations), default=None)

    @property
    def active(self) -> float:
        return sum(o.active for o in self.observations)


@dataclass(frozen=True)
class SignalId:
    skill: str
    name: str

    def __str__(self) -> str:
        return f"{self.skill}.{self.name}"


@dataclass(frozen=True)
class SignalReading:
    signal: SignalId
    value: SignalValue
    at: datetime
    tick: str


@dataclass(frozen=True)
class CaptureSpan:
    state: CaptureState
    start: datetime
    end: datetime | None


@dataclass(frozen=True)
class Continue:
    title: str
    kind: ActivityKind
    intent: str


@dataclass(frozen=True)
class Start:
    title: str
    kind: ActivityKind
    intent: str


@dataclass(frozen=True)
class Tick:
    at: datetime
    epoch: int
    evidence_until: datetime
    observations: tuple[str, ...]
    segment: Continue | Start | None
    readings: tuple[SignalReading, ...]


@dataclass(frozen=True)
class SkillState:
    skill: str
    enabled: bool
    approved_hash: str | None
    at: datetime


@dataclass(frozen=True)
class Outcome:
    action: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Firing:
    skill: str
    rule: int
    key: str
    at: datetime
    outcomes: tuple[Outcome, ...]


@dataclass(frozen=True)
class Situation:
    capture: CaptureState
    segment: Segment | None
    observation: Observation | None
    readings: tuple[SignalReading, ...]


@dataclass(frozen=True)
class Hit:
    id: str
    kind: Literal["segment", "observation"]
    text: str
    segment: str | None


@dataclass(frozen=True)
class Total:
    label: str
    active: float


class Portal:
    def now(self) -> Situation:
        raise NotImplementedError

    def segments(self, range: TimeRange) -> list[Segment]:
        raise NotImplementedError

    def observations(
        self, range: TimeRange, unassigned_only: bool = False
    ) -> list[Observation]:
        raise NotImplementedError

    def capture_spans(self, range: TimeRange) -> list[CaptureSpan]:
        raise NotImplementedError

    def search(self, text: str, range: TimeRange | None = None) -> list[Hit]:
        raise NotImplementedError

    def totals(
        self, range: TimeRange, by: Literal["kind", "title", "app"] = "kind"
    ) -> list[Total]:
        raise NotImplementedError

    def readings(self, signal: SignalId, range: TimeRange) -> list[SignalReading]:
        raise NotImplementedError

    def context(
        self,
        range: TimeRange,
        budget_tokens: int,
        detail: Literal["segments", "observations"] = "segments",
    ) -> str:
        raise NotImplementedError


class Journal:
    def __init__(self, path: Path, bus: Bus) -> None:
        raise NotImplementedError

    @property
    def epoch(self) -> int:
        raise NotImplementedError

    @property
    def portal(self) -> Portal:
        raise NotImplementedError

    @property
    def deletion_pending(self) -> bool:
        raise NotImplementedError

    def add_observation(
        self, draft: ObservationDraft, epoch: int
    ) -> Observation | None:
        raise NotImplementedError

    def extend_observation(self, id: str, until: datetime) -> None:
        raise NotImplementedError

    def trim_after(self, at: datetime) -> None:
        raise NotImplementedError

    def set_capture_state(self, state: CaptureState, at: datetime) -> None:
        raise NotImplementedError

    def record_tick(self, tick: Tick) -> bool:
        raise NotImplementedError

    def close_open_segment(self) -> None:
        raise NotImplementedError

    def edit_segment(
        self,
        id: str,
        *,
        title: str | None = None,
        kind: ActivityKind | None = None,
        intent: str | None = None,
    ) -> Segment:
        raise NotImplementedError

    def edit_observation(
        self, id: str, *, seen: str | None = None, visible_text: str | None = None
    ) -> Observation:
        raise NotImplementedError

    def forget_observation(self, id: str) -> None:
        raise NotImplementedError

    def forget_segment(self, id: str) -> None:
        raise NotImplementedError

    def forget_range(self, range: TimeRange) -> None:
        raise NotImplementedError

    def forget_all(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
