"""Notes and privacy storage, with read-only Portal views.

TimeRange is [start, end). Reads select overlapping observations, totals clip
activity to that interval, and forget_range removes every overlapping note.
Only Journal writes notes. Skills owns its separate ledger tables.
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
    SignalId,
    SignalReading,
    SignalValue,
    Situation,
    SkillState,
    Start,
    Tick,
    TimeRange,
    Total,
    WindowInfo,
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
    "Journal",
    "Portal",
]
