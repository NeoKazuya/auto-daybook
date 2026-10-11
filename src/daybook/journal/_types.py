"""Frozen domain values; timestamps are aware UTC and durations seconds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

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

    @property
    def id(self) -> str:
        """Stable identity shared by mind, readings, events, and firing claims."""
        if self.evidence_until.tzinfo is None:
            raise ValueError("evidence_until must be timezone-aware")
        return "tick:" + self.evidence_until.astimezone(UTC).isoformat()


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
