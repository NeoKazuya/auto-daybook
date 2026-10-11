"""daybook.journal — the notes. The only writer of observations, segments, signal readings, and capture state.

Owns one SQLite file ($DAYBOOK_HOME/journal.db, WAL) and its FTS5 index. Everyone else reads through
`Portal`. Writers are named: sense writes observations and capture state, mind writes ticks, the web
routes write user edits and deletions. After every commit the journal publishes a domain event on the
bus (the web SSE stream serializes it; nobody else re-announces writes).

Layout when built:
    daybook/journal/__init__.py   exports: Journal, Portal, the types below
    daybook/journal/_schema.py    CREATE TABLE statements (private)
    daybook/journal/_sql.py       queries, row -> type mapping (private)
    daybook/journal/_render.py    Portal.context() text rendering (private)

Invariants:
- Observation = what was on screen (window facts + what the model saw). Segment = interpretation
  (title, kind, intent). The two never share a field, so observation and interpretation stay separable.
- Segment times are derived from member observations at read time. Deleting an observation shrinks its
  segment automatically. There is no stored start/end to keep in sync.
- Every observation belongs to at most one segment. `segment is None` means "not yet rolled up"; the
  next tick picks up exactly those, so a crashed tick loses nothing.
- Once a user edits a segment's text, `authored == "user"` and ticks never overwrite its text again.
- Screenshots never reach this module. There is no image column.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Literal, NewType

ObservationId = NewType("ObservationId", int)
SegmentId = NewType("SegmentId", int)
TickId = NewType("TickId", int)
SkillId = NewType("SkillId", str)  # slug: [a-z0-9-]{1,40}; validated in skills.parse_skill


@dataclass(frozen=True)
class SignalId:
    """A skill's question, namespaced by skill: `break.focused`. Two skills may both ask 'focused'."""

    skill: SkillId
    name: str

    def __str__(self) -> str:
        return f"{self.skill}.{self.name}"


SignalValue = bool | str | float  # unknown is `None` at use sites, never a sentinel string


class ActivityKind(str, Enum):
    CODING = "coding"
    WRITING = "writing"
    READING = "reading"
    BROWSING = "browsing"
    MEETING = "meeting"
    CHATTING = "chatting"
    MEDIA = "media"
    ADMIN = "admin"
    OTHER = "other"


class CaptureState(str, Enum):
    """Why capture is or isn't happening. Sense writes transitions; the timeline shows them as spans."""

    RUNNING = "running"
    PAUSED = "paused"  # user pressed pause (tray or web)
    LOCKED = "locked"  # session LockedHint, or a black frame
    WITHHELD = "withheld"  # active window matched an exclusion
    OVERLAY = "overlay"  # our own overlay is up; never observe ourselves


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime

    @staticmethod
    def last(*, minutes: int = 0, hours: int = 0) -> TimeRange:
        raise NotImplementedError

    @staticmethod
    def today() -> TimeRange:
        """Local midnight to now."""
        raise NotImplementedError


@dataclass(frozen=True)
class WindowInfo:
    """Facts from the window manager, not the model. app = WM_CLASS class (e.g. 'Code', 'firefox')."""

    app: str
    title: str


@dataclass(frozen=True)
class ObservationDraft:
    """What sense hands the journal. `seen` and `visible_text` describe the screen; they never guess intent."""

    at: datetime
    window: WindowInfo
    seen: str  # 1–2 sentences: "VS Code on auth.py; terminal shows 3 failing pytest tests"
    visible_text: str  # <= 300 chars of salient on-screen text, for search


@dataclass(frozen=True)
class Observation:
    id: ObservationId
    started_at: datetime
    last_seen_at: datetime  # extended by sense while the screen stays the same (no model call)
    window: WindowInfo
    seen: str
    visible_text: str
    segment: SegmentId | None

    @property
    def active(self) -> timedelta:
        """last_seen_at - started_at + one poll interval. Summing these is 'time spent'."""
        raise NotImplementedError


Authored = Literal["model", "user"]


@dataclass(frozen=True)
class Segment:
    id: SegmentId
    title: str  # "Debugging the login test"
    kind: ActivityKind
    intent: str  # inferred: "trying to get the auth tests green before the demo"
    authored: Authored
    open: bool
    # Derived at read time from member observations:
    started_at: datetime
    ended_at: datetime
    active: timedelta
    observations: tuple[ObservationId, ...]


@dataclass(frozen=True)
class SignalReading:
    signal: SignalId
    value: SignalValue | None  # None = the model couldn't tell, or answered outside the type
    at: datetime
    tick: TickId


# --- What mind hands the journal, once per tick, applied in one transaction.


@dataclass(frozen=True)
class Continue:
    """The new observations extend the open segment. Text fields are ignored if the segment is user-authored."""

    segment: SegmentId
    title: str
    kind: ActivityKind
    intent: str


@dataclass(frozen=True)
class Start:
    """Close the open segment (if any) and start a new one with the new observations."""

    title: str
    kind: ActivityKind
    intent: str


@dataclass(frozen=True)
class Tick:
    at: datetime
    observations: tuple[ObservationId, ...]  # the unassigned ones mind read; non-empty
    segment: Continue | Start
    readings: Mapping[SignalId, SignalValue | None]


@dataclass(frozen=True)
class RecordedTick:
    id: TickId
    at: datetime
    readings: tuple[SignalReading, ...]


# --- Read results


@dataclass(frozen=True)
class Situation:
    """'What is going on right now', in one read. What hooks and the status bar use."""

    at: datetime
    capture: CaptureState
    segment: Segment | None  # the open one
    latest: Observation | None
    signals: Mapping[SignalId, SignalReading]  # latest reading per signal


@dataclass(frozen=True)
class Hit:
    kind: Literal["observation", "segment"]
    id: int
    at: datetime
    snippet: str


@dataclass(frozen=True)
class Total:
    label: str  # a kind, a segment title, or an app, depending on `by`
    active: timedelta


@dataclass(frozen=True)
class CaptureSpan:
    state: CaptureState
    start: datetime
    end: datetime | None


class Portal:
    """Read-only view of the notes. The one interface the UI, recall, the mind, and skill hooks read through.

    Deliberately small: seven reads plus `context()`, which renders notes into prompt text under a token
    budget so no caller formats notes for the model on its own.
    """

    def now(self) -> Situation:
        raise NotImplementedError

    def segments(self, within: TimeRange) -> list[Segment]:
        raise NotImplementedError

    def observations(self, within: TimeRange, *, unassigned_only: bool = False, limit: int = 500) -> list[Observation]:
        raise NotImplementedError

    def capture_spans(self, within: TimeRange) -> list[CaptureSpan]:
        raise NotImplementedError

    def search(self, text: str, within: TimeRange | None = None, limit: int = 20) -> list[Hit]:
        """FTS5 over observation seen/visible_text/title and segment title/intent."""
        raise NotImplementedError

    def totals(self, within: TimeRange, by: Literal["kind", "title", "app"]) -> list[Total]:
        """Sum of Observation.active grouped by `by`, largest first. All duration math lives here."""
        raise NotImplementedError

    def readings(self, signal: SignalId, within: TimeRange) -> list[SignalReading]:
        """Oldest first. The trigger evaluator's whole input."""
        raise NotImplementedError

    def context(self, within: TimeRange, *, budget_tokens: int, detail: Literal["segments", "observations"]) -> str:
        """Notes as compact prompt text, newest detail kept when over budget.

        TODO:
          lines = one line per segment: "09:02–09:47 (41m active) [coding] Debugging login test — intent: ..."
          if detail == "observations": indent member observations under each segment: "  09:05 Code — seen"
          include capture spans as "10:10–10:25 paused" so the model doesn't invent activity in gaps
          estimate tokens as len(text) / 3.5; drop oldest observation lines first, then oldest segments
        """
        raise NotImplementedError


class Journal:
    def __init__(self, path: Path, bus: Bus) -> None:
        raise NotImplementedError

    @property
    def portal(self) -> Portal:
        raise NotImplementedError

    # --- sense
    def add_observation(self, draft: ObservationDraft) -> ObservationId:
        raise NotImplementedError

    def extend_observation(self, id: ObservationId, seen_at: datetime) -> None:
        """Screen unchanged since this observation: push last_seen_at forward. No model call happened."""
        raise NotImplementedError

    def set_capture_state(self, state: CaptureState, at: datetime) -> None:
        """Idempotent: writing the current state again is a no-op, so sense can call it every poll."""
        raise NotImplementedError

    # --- mind
    def record_tick(self, tick: Tick) -> RecordedTick:
        """One transaction: assign observations to a segment, write readings, publish events.

        Idempotent: ticks are unique on max(tick.observations); re-applying returns the existing
        RecordedTick. Observations deleted meanwhile are skipped. If all were deleted, the segment
        decision is dropped and only readings are written.
        TODO:
          BEGIN
          if Start: UPDATE segments SET open=0 WHERE open=1; INSERT new segment open=1
          if Continue: UPDATE text WHERE id=? AND authored='model'
          UPDATE observations SET segment=? WHERE id IN (...) AND segment IS NULL
          INSERT ticks, INSERT signal_readings
          COMMIT; bus.publish(SegmentChanged(...)), bus.publish(TickRecorded(...))
        """
        raise NotImplementedError

    def close_open_segment(self) -> None:
        """Called by mind after a gap (lock, pause, away > 5 min). Idempotent."""
        raise NotImplementedError

    # --- user edits (web routes)
    def edit_segment(self, id: SegmentId, *, title: str | None = None, intent: str | None = None,
                     kind: ActivityKind | None = None) -> Segment:
        """Sets authored='user'. From then on the model never rewrites this segment's text."""
        raise NotImplementedError

    def edit_observation(self, id: ObservationId, *, seen: str) -> Observation:
        raise NotImplementedError

    def forget_observation(self, id: ObservationId) -> None:
        raise NotImplementedError

    def forget_segment(self, id: SegmentId) -> None:
        """Deletes the segment, all its observations, and their FTS rows."""
        raise NotImplementedError

    def forget_range(self, within: TimeRange) -> int:
        """'Delete the last 15 minutes.' Removes observations, then segments left empty, then readings. Returns rows removed."""
        raise NotImplementedError


class Bus:
    """Lives in daybook/events.py with every event dataclass (one list of events); shown here for brevity.

    In-process pub/sub of domain events (dataclasses). The web SSE route is the only serializer.

    Events: ObservationAdded, ObservationExtended, SegmentChanged, TickRecorded, CaptureStateChanged,
    SkillFired, OverlayShown, OverlayHidden, MusicChanged, SkillsChanged.
    """

    def publish(self, event: object) -> None:
        raise NotImplementedError

    def subscribe(self, kinds: Sequence[type] | None = None) -> AsyncEventStream:
        raise NotImplementedError


class AsyncEventStream:
    def __aiter__(self) -> AsyncEventStream:
        raise NotImplementedError

    async def __anext__(self) -> object:
        raise NotImplementedError
