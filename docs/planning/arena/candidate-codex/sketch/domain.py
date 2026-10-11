"""Domain values shared by owners; no provider, SQL, HTTP, or Qt types.

Frozen values are snapshots, never a second mutable source of truth. Constructors
at input boundaries reject naive dates, inverted ranges, unknown enums, excessive
text, invalid references, and non-finite confidence. No Python code is generated
from these definitions at runtime.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, NewType

ObservationId = NewType("ObservationId", str)
SegmentId = NewType("SegmentId", str)
EpisodeId = NewType("EpisodeId", str)
SkillId = NewType("SkillId", str)
Digest = NewType("Digest", str)
RunId = NewType("RunId", str)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ObservationRef:
    id: ObservationId
    version: int


@dataclass(frozen=True)
class SegmentRef:
    id: SegmentId
    version: int


NoteRef = ObservationRef | SegmentRef


class Activity(StrEnum):
    FOCUS = "focus"
    READING = "reading"
    MEETING = "meeting"
    BREAK = "break"
    OTHER = "other"
    UNKNOWN = "unknown"


class Mood(StrEnum):
    FOCUS = "focus"
    CALM = "calm"
    UPBEAT = "upbeat"
    BREAK = "break"


@dataclass(frozen=True)
class Observation:
    ref: ObservationRef
    captured_at: datetime
    app: str
    visible_facts: str
    # User correction replaces visible_facts in the current view, preserving origin.
    origin: Literal["model", "user"]


@dataclass(frozen=True)
class Segment:
    ref: SegmentRef
    window: TimeRange
    activity: Activity
    summary: str
    inferred_intent: str
    evidence: tuple[ObservationRef, ...]
    episode: EpisodeId
    origin: Literal["model", "user"]


@dataclass(frozen=True)
class SegmentDraft:
    """Model interpretation before Portal assigns identity and episode continuity."""
    window: TimeRange
    activity: Activity
    summary: str
    inferred_intent: str
    evidence: tuple[ObservationRef, ...]


@dataclass(frozen=True)
class EvidenceToken:
    """Minted by Portal; epoch changes on edits/deletes, never reused after restart."""
    epoch: str
    segments: tuple[SegmentRef, ...]


@dataclass(frozen=True)
class MeetingEnded:
    episode: EpisodeId
    previous: SegmentRef
    following: SegmentRef


@dataclass(frozen=True)
class FocusStreak:
    episode: EpisodeId
    observed_seconds: int
    # An estimate from samples; gaps >30 s, pause, lock, or exclusions end it.
    window: TimeRange


@dataclass(frozen=True)
class ContextQuery:
    window: TimeRange
    text: str = ""
    limit: int = 40
    before: datetime | None = None
    include_observations: bool = False


@dataclass(frozen=True)
class Context:
    token: EvidenceToken
    requested: TimeRange
    segments: tuple[Segment, ...]
    observations: tuple[Observation, ...]
    focus: FocusStreak | None
    meetings_ended: tuple[MeetingEnded, ...]
    observed_seconds: int
    gaps: tuple[TimeRange, ...]
    newest_observation_at: datetime | None
    interpreted_through: datetime | None
    truncated: bool
    next_before: datetime | None


class ActionName(StrEnum):
    MUSIC = "music.set_mood"
    OVERLAY = "overlay.show"


@dataclass(frozen=True)
class SetMood:
    mood: Mood
    hold_seconds: int  # bounded 0..300; break override expires, not sticky forever


@dataclass(frozen=True)
class ShowOverlay:
    title: str
    body: str
    dismiss_label: str
    timeout_seconds: int  # bounded 5..120, always Escape-dismissible


Action = SetMood | ShowOverlay


class RepeatScope(StrEnum):
    MOOD_CHANGE = "mood_change"
    FOCUS_STREAK = "focus_streak"
    MEETING_END = "meeting_end"
    ACTIVITY_EPISODE = "activity_episode"


@dataclass(frozen=True)
class Trigger:
    every_seconds: int
    when: str
    once_per: RepeatScope
    minimum_focus_seconds: int = 0


@dataclass(frozen=True)
class TrustedHook:
    """An installed, reviewed hook name; never a generated path or source string."""
    name: str


@dataclass(frozen=True)
class SkillSpec:
    id: SkillId
    digest: Digest
    title: str
    goal: str
    trigger: Trigger
    allowed_actions: frozenset[ActionName]
    priority: int
    cooldown_seconds: int
    hook: TrustedHook | None


@dataclass(frozen=True)
class Proposal:
    skill: SkillId
    digest: Digest
    episode: EpisodeId
    reason: str
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Decision:
    proposals: tuple[Proposal, ...]


@dataclass(frozen=True)
class Citation:
    segment: SegmentRef
    text: str


@dataclass(frozen=True)
class Answer:
    text: str
    citations: tuple[Citation, ...]
    observed_seconds: int
    gaps: tuple[TimeRange, ...]


@dataclass(frozen=True)
class Receipt:
    run: RunId
    status: Literal["applied", "skipped", "failed", "unknown"]
    explanation: str
