"""Notes and privacy storage, with read-only Portal views.

TimeRange is [start, end). Reads select overlapping observations, totals clip
activity to that interval, and forget_range removes every overlapping note.
Only Journal writes notes and the Upcoming list. Skills owns its separate ledger tables.
"""

from ._store import Journal, Portal
from ._types import (
    ActivityKind,
    CaptureSpan,
    CaptureState,
    Continue,
    Firing,
    Hit,
    Observation,
    ObservationDraft,
    Outcome,
    Segment,
    SegmentAssignment,
    SignalId,
    SignalReading,
    SignalValue,
    Situation,
    SkillState,
    Start,
    Tick,
    TimeRange,
    Total,
    UpcomingDraft,
    UpcomingItem,
    UpcomingKind,
    WindowInfo,
    due_phrase,
)

__all__ = [
    "ActivityKind",
    "CaptureState",
    "SignalValue",
    "TimeRange",
    "WindowInfo",
    "ObservationDraft",
    "Observation",
    "Segment",
    "SegmentAssignment",
    "SignalId",
    "SignalReading",
    "CaptureSpan",
    "Continue",
    "Start",
    "Tick",
    "SkillState",
    "Outcome",
    "Firing",
    "Situation",
    "Hit",
    "Total",
    "UpcomingDraft",
    "UpcomingItem",
    "UpcomingKind",
    "due_phrase",
    "Journal",
    "Portal",
]
