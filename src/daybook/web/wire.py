"""HTTP/SSE contract shared with Lane B.

Times are UTC ISO 8601; active/latency values are seconds. SSE uses named events
with JSON data matching EVENT_MODELS. Clients refresh affected resources on ID
notifications and resync from /api/status when reconnecting. Every model has a
validated example, also published in OpenAPI. No backend state lives here.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daybook import events
from daybook.journal import ActivityKind, CaptureState
from daybook.skills import SkillEntry

# A deliberate tray Quit stops the daemon; every other Shell exit is restarted.
SHELL_QUIT_EXIT_CODE = 20


class WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", from_attributes=True, populate_by_name=True
    )


class Window(WireModel):
    app: str
    title: str


class Observation(WireModel):
    id: str
    started_at: datetime
    last_seen_at: datetime
    window: Window
    seen: str
    visible_text: str
    segment: str | None
    active: float


class Segment(WireModel):
    id: str
    title: str
    kind: ActivityKind
    intent: str
    authored: Literal["model", "user"]
    open: bool
    started_at: datetime | None
    ended_at: datetime | None
    active: float
    observations: list[Observation]


class CaptureSpan(WireModel):
    state: CaptureState
    start: datetime
    end: datetime | None


class SignalReading(WireModel):
    signal: str
    value: bool | str | float | None
    at: datetime
    tick: str


class TimelineResponse(WireModel):
    segments: list[Segment]
    capture_spans: list[CaptureSpan]


class SearchHit(WireModel):
    segment_id: str | None
    observation_id: str | None
    at: datetime
    kind: ActivityKind | None
    title: str
    snippet: str


class SearchResponse(WireModel):
    hits: list[SearchHit]


class Total(WireModel):
    label: str
    active: float


class TotalsResponse(WireModel):
    totals: list[Total]


class NowResponse(WireModel):
    capture: CaptureState
    segment: Segment | None
    observation: Observation | None
    readings: list[SignalReading]


class SegmentPatch(WireModel):
    title: str | None = None
    kind: ActivityKind | None = None
    intent: str | None = None


class ObservationPatch(WireModel):
    seen: str | None = None
    visible_text: str | None = Field(default=None, max_length=300)


class TimeWindow(WireModel):
    start: datetime = Field(validation_alias="from", serialization_alias="from")
    end: datetime = Field(validation_alias="to", serialization_alias="to")

    @model_validator(mode="after")
    def ordered(self) -> TimeWindow:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("timestamps need a UTC offset")
        if self.start >= self.end:
            raise ValueError("from must be before to")
        return self


class MutationResponse(WireModel):
    ok: bool = True
    deletion_pending: bool = False


class UpcomingItem(WireModel):
    """A deadline or event. source is the screen note it came from, or null if
    the user added it; forgetting that note removes the item."""

    id: str
    title: str
    due_at: datetime
    source: str | None
    added_at: datetime
    kind: Literal["deadline", "event"] = "deadline"


# GET /api/upcoming lists items due from this long ago on; older ones are refused.
UPCOMING_OVERDUE_SECONDS = 3600


class UpcomingResponse(WireModel):
    items: list[UpcomingItem]


class UpcomingPost(WireModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    due_at: datetime
    kind: Literal["deadline", "event"] = "deadline"

    @model_validator(mode="after")
    def aware(self):
        if self.due_at.tzinfo is None:
            raise ValueError("due_at must include a timezone")
        return self


class ChatRequest(WireModel):
    message: str = Field(min_length=1, max_length=10000)


class SkillError(WireModel):
    location: str
    message: str


class Signal(WireModel):
    name: str
    ask: str
    type: Literal["bool", "enum", "number", "music.mood"]
    values: list[str] = Field(default_factory=list)
    descriptions: dict[str, str] = Field(default_factory=dict)
    minimum: float | None = None
    maximum: float | None = None


class Step(WireModel):
    action: str
    arguments: dict[str, object]


class Rule(WireModel):
    when: str
    cooldown: float
    steps: list[Step]


class Parameter(WireModel):
    type: str
    required: bool
    default: str | float | None
    values: list[str]


class ActionSpec(WireModel):
    name: str
    description: str
    parameters: dict[str, Parameter]


class SkillResponse(WireModel):
    id: str
    name: str
    goal: str
    text: str
    hash: str
    origin: Literal["builtin", "user"]
    created_by: Literal["builtin", "user", "chat"]
    request: str | None
    signals: list[Signal]
    rules: list[Rule]
    allow: list[str]
    # Daybook lists the skill reads besides the screen, such as "upcoming".
    reads: list[str] = Field(default_factory=list)
    errors: list[SkillError]
    enabled: bool
    approved: bool
    status: Literal["active", "needs_review", "disabled", "invalid"]


class SkillsResponse(WireModel):
    entries: list[SkillResponse]
    active_signals: int
    max_active_signals: int = 12
    catalog: list[ActionSpec]
    moods: dict[str, str]


class SkillPut(WireModel):
    text: str


class SkillParseRequest(WireModel):
    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    text: str


class SkillParseResponse(WireModel):
    valid: bool
    errors: list[SkillError]
    entry: SkillResponse


class ApprovalRequest(WireModel):
    hash: str


class AnswerResponse(WireModel):
    type: Literal["answer"] = "answer"
    text: str
    cites: list[str]
    window: TimeWindow


class DraftResponse(WireModel):
    type: Literal["draft"] = "draft"
    entry: SkillResponse
    summary: str


class PlainResponse(WireModel):
    type: Literal["plain"] = "plain"
    text: str


ChatResponse = Annotated[
    AnswerResponse | DraftResponse | PlainResponse, Field(discriminator="type")
]


class PrivacySettings(WireModel):
    paused: bool
    exclude_apps: list[str]
    exclude_titles: list[str]


class PrivacyPut(PrivacySettings):
    # Omit only for an intentional unconditional complete replacement.
    expected: PrivacySettings | None = None


class PrivacyResponse(PrivacySettings):
    keep_frames: bool = False
    deletion_pending: bool = False
    screenshots_stored: Literal["none", "debug"] = "none"


class GatewayStatus(WireModel):
    model: str
    reachable: bool
    in_flight: str | None
    queue_depth: int
    latency: float | None
    calls_per_minute: int
    background_calls_per_minute: int


class MusicPlayRequest(WireModel):
    mood: str = Field(min_length=1, max_length=80)


class MusicStatus(WireModel):
    mood: str | None
    title: str | None
    playing: bool


class OverlayRequest(WireModel):
    title: str
    body: str
    dismiss: str = "OK"
    minutes: float = 1
    style: Literal["calm", "alert"] = "calm"
    layout: Literal["fullscreen", "bubble"] = "fullscreen"


class OverlayState(WireModel):
    id: str
    request: OverlayRequest
    owner: str
    expires_at: datetime


class StatusResponse(WireModel):
    gateway: GatewayStatus
    music: MusicStatus
    overlay: OverlayState | None
    capture: CaptureState
    shell_connected: bool
    mock: bool


class TickResponse(WireModel):
    recorded: bool
    tick: str | None


class ErrorResponse(WireModel):
    detail: str


class ObservationAddedPayload(WireModel):
    id: str


class ObservationExtendedPayload(WireModel):
    id: str


class SegmentChangedPayload(WireModel):
    id: str


class TickRecordedPayload(WireModel):
    tick: str


class CaptureStateChangedPayload(WireModel):
    state: CaptureState


class SkillFiredPayload(WireModel):
    skill: str
    rule: int
    key: str


class SkillsChangedPayload(WireModel):
    id: str


class OverlayShownPayload(WireModel):
    id: str
    request: OverlayRequest


class OverlayHiddenPayload(WireModel):
    id: str


class MusicChangedPayload(WireModel):
    mood: str | None
    title: str | None


class StatusChangedPayload(WireModel):
    component: str
    status: str


EVENT_MODELS: dict[str, type[WireModel]] = {
    model.__name__.removesuffix("Payload"): model
    for model in (
        ObservationAddedPayload,
        ObservationExtendedPayload,
        SegmentChangedPayload,
        TickRecordedPayload,
        CaptureStateChangedPayload,
        SkillFiredPayload,
        SkillsChangedPayload,
        OverlayShownPayload,
        OverlayHiddenPayload,
        MusicChangedPayload,
        StatusChangedPayload,
    )
}


def serialize_event(event: events.Event) -> tuple[str, str]:
    name = type(event).__name__
    return name, EVENT_MODELS[name].model_validate(asdict(event)).model_dump_json(
        by_alias=True
    )


def skill_from_domain(entry: SkillEntry) -> SkillResponse:
    skill = entry.skill
    return SkillResponse(
        id=entry.id,
        name=skill.name if skill else entry.id,
        goal=skill.goal if skill else "",
        text=entry.text,
        hash=entry.hash,
        origin=entry.source.origin,
        created_by=skill.created_by if skill else entry.source.origin,
        request=skill.request if skill else None,
        signals=[
            Signal(
                name=s.id.name,
                ask=s.ask,
                type=s.type,
                values=list(s.values),
                descriptions=dict(s.descriptions or {}),
                minimum=s.minimum,
                maximum=s.maximum,
            )
            for s in skill.signals
        ]
        if skill
        else [],
        rules=[
            Rule(
                when=r.when,
                cooldown=r.cooldown,
                steps=[
                    Step(action=s.action, arguments=dict(s.arguments)) for s in r.steps
                ],
            )
            for r in skill.rules
        ]
        if skill
        else [],
        allow=list(skill.allow) if skill else [],
        reads=list(skill.reads) if skill else [],
        errors=[SkillError.model_validate(e) for e in entry.errors],
        enabled=entry.enabled,
        approved=entry.approved,
        status=entry.status,
    )


W = TypeVar("W", bound=WireModel)
EXAMPLES: dict[type[WireModel], dict[str, object]] = {}


def example(model: type[W]) -> W:
    return model.model_validate(EXAMPLES[model])


def _example(cls: type[WireModel], **data: object) -> dict[str, object]:
    result = cls.model_validate(data).model_dump(mode="json", by_alias=True)
    EXAMPLES[cls] = result
    cls.model_config["json_schema_extra"] = {"examples": [result]}
    return result


_window = _example(Window, app="Code", title="test_journal.py — auto-daybook")
_observation = _example(
    Observation,
    id="o-code",
    started_at="2026-10-10T10:00:00Z",
    last_seen_at="2026-10-10T10:04:00Z",
    window=_window,
    seen="A Python test file is open beside a terminal showing one failed assertion.",
    visible_text="test_forget_range",
    segment="s-code",
    active=240,
)
_segment = _example(
    Segment,
    id="s-code",
    title="Fixing journal tests",
    kind="coding",
    intent="Likely checking how deletion updates the timeline.",
    authored="model",
    open=True,
    started_at=_observation["started_at"],
    ended_at=_observation["last_seen_at"],
    active=240,
    observations=[_observation],
)
_span = _example(
    CaptureSpan,
    state="paused",
    start="2026-10-10T09:55:00Z",
    end="2026-10-10T10:00:00Z",
)
_reading = _example(
    SignalReading,
    signal="music.mood",
    value="focus",
    at="2026-10-10T10:04:00Z",
    tick="tick-1",
)
_example(TimelineResponse, segments=[_segment], capture_spans=[_span])
_search_hit = _example(
    SearchHit,
    segment_id="s-code",
    observation_id="o-code",
    at=_observation["started_at"],
    kind="coding",
    title="Fixing journal tests",
    snippet="test_forget_range",
)
_example(SearchResponse, hits=[_search_hit])
_total = _example(Total, label="coding", active=240)
_example(TotalsResponse, totals=[_total])

_example(
    NowResponse,
    capture="running",
    segment=_segment,
    observation=_observation,
    readings=[_reading],
)
_example(SegmentPatch, title="Checking note deletion")
_example(ObservationPatch, seen="The test file and a failed assertion are visible.")
_time = _example(
    TimeWindow, **{"from": "2026-10-10T09:00:00Z", "to": "2026-10-10T11:00:00Z"}
)
_example(MutationResponse, ok=True, deletion_pending=False)
_upcoming = _example(
    UpcomingItem,
    id="u-cse130",
    title="CSE 130 Assignment 3",
    due_at="2026-10-11T06:59:00Z",
    source="o-calendar",
    added_at="2026-10-10T21:05:00Z",
)
_example(UpcomingResponse, items=[_upcoming])
_example(UpcomingPost, title="CSE 130 Assignment 3", due_at="2026-10-11T06:59:00Z")
_example(ChatRequest, message="What did I do in the last hour?")
_example(
    SkillError,
    location="rules[0].when",
    message="trigger references an undeclared signal",
)
_signal = _example(
    Signal, name="in_meeting", ask="Is the user currently in a video call?", type="bool"
)
_step = _example(
    Step,
    action="overlay.show",
    arguments={
        "title": "Meeting’s over",
        "body": "Stand up and stretch.",
        "minutes": 1,
        "layout": "fullscreen",
    },
)
_rule = _example(Rule, when="in_meeting becomes false", cooldown=600, steps=[_step])
_parameter = _example(Parameter, type="str", required=True, default=None, values=[])
_action = _example(
    ActionSpec,
    name="overlay.show",
    description="Show a fullscreen reminder or speech bubble; use casual body text for bubbles",
    parameters={
        "title": _parameter,
        "body": _parameter,
        "layout": dict(
            type="enum",
            required=False,
            default="fullscreen",
            values=["fullscreen", "bubble"],
        ),
    },
)
DRAFT_TEXT = """---
id: stand-after-meeting
name: Stand up after meetings
created_by: chat
request: remind me to stand up every time I finish a meeting
signals:
  in_meeting:
    ask: Is the user currently in a video call or online meeting?
    type: bool
rules:
  - when: in_meeting becomes false
    cooldown: 10m
    do:
      - overlay.show:
          title: Meeting's over
          body: Stand up and stretch for a minute.
          dismiss: Done
          minutes: 1
allow: [overlay.show]
---
Remind me to stand up after a meeting.
"""

_skill = _example(
    SkillResponse,
    id="stand-after-meeting",
    name="Stand up after meetings",
    goal="Remind me to stand up after a meeting.",
    text=DRAFT_TEXT,
    hash=hashlib.sha256(DRAFT_TEXT.encode()).hexdigest(),
    origin="user",
    created_by="chat",
    request="remind me to stand up every time I finish a meeting",
    signals=[_signal],
    rules=[_rule],
    allow=["overlay.show"],
    errors=[],
    enabled=False,
    approved=False,
    status="needs_review",
)
MOCK_MOODS = {
    "focus": "concentrated work",
    "calm": "reading and research",
    "energetic": "light admin",
    "break": "not working",
}
_example(
    SkillsResponse,
    entries=[_skill],
    active_signals=0,
    catalog=[_action],
    moods=MOCK_MOODS,
)
_example(SkillPut, text=DRAFT_TEXT)
_example(SkillParseRequest, id="stand-after-meeting", text=DRAFT_TEXT)
_example(SkillParseResponse, valid=True, errors=[], entry=_skill)
_example(ApprovalRequest, hash=_skill["hash"])
_example(
    AnswerResponse,
    text="You spent 4 minutes working on journal tests. There are no notes for the paused interval.",
    cites=["s-code"],
    window=_time,
)
_example(DraftResponse, entry=_skill, summary="Show a reminder when a meeting ends.")
_example(
    PlainResponse, text="Ask about your day, or describe a skill you want to create."
)
_privacy = _example(
    PrivacySettings,
    paused=False,
    exclude_apps=["KeePassXC", "1Password", "Bitwarden"],
    exclude_titles=["*Private Browsing*", "*Incognito*", "*Daybook ·*"],
)
_example(PrivacyPut, **_privacy, expected=_privacy)
_example(PrivacyResponse, **_privacy)
_gateway = _example(
    GatewayStatus,
    model="qwen3.6:35b",
    reachable=True,
    in_flight=None,
    queue_depth=0,
    latency=2.9,
    calls_per_minute=4,
    background_calls_per_minute=4,
)
_example(MusicPlayRequest, mood="focus")
_music = _example(MusicStatus, mood="focus", title="Cipher", playing=True)
_overlay_request = _example(
    OverlayRequest,
    title="Time for a break",
    body="Stand up, look away, and get some water.",
    dismiss="I'm back",
    minutes=2,
    layout="fullscreen",
)
_overlay = _example(
    OverlayState,
    id="overlay-demo",
    request=_overlay_request,
    owner="break-demo",
    expires_at="2026-10-10T10:06:00Z",
)
_example(
    StatusResponse,
    gateway=_gateway,
    music=_music,
    overlay=None,
    capture="running",
    shell_connected=False,
    mock=True,
)
_example(TickResponse, recorded=True, tick="tick-1")
_example(ErrorResponse, detail="needs_review")
for _model in (ObservationAddedPayload, ObservationExtendedPayload):
    _example(_model, id="o-code")
_example(SegmentChangedPayload, id="s-code")
_example(TickRecordedPayload, tick="tick-1")
_example(CaptureStateChangedPayload, state="paused")
_example(SkillFiredPayload, skill="break-demo", rule=0, key="streak:tick-1")
_example(SkillsChangedPayload, id="stand-after-meeting")
_example(OverlayShownPayload, id="overlay-demo", request=_overlay_request)
_example(OverlayHiddenPayload, id="overlay-demo")
_example(MusicChangedPayload, mood="calm", title="Evening reading")
_example(StatusChangedPayload, component="model", status="ready")
