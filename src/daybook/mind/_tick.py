"""Pure parsing and per-request output schemas; no model or storage side effects."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from daybook.journal import (
    ActivityKind,
    Continue,
    SegmentAssignment,
    SignalReading,
    SignalValue,
    Start,
    Tick,
)
from daybook.skills import Signal


class SegmentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[str] = Field(min_length=1)
    mode: Literal["continue", "start"]
    title: str = Field(min_length=1, max_length=200)
    kind: ActivityKind
    intent: str = Field(max_length=600)


def signal_model(signals: tuple[Signal, ...]) -> type[BaseModel]:
    fields = {}
    for signal in signals:
        type_: Any
        if signal.type == "bool":
            type_ = Annotated[bool, Field(strict=True)]
        elif signal.type == "number":
            type_ = Annotated[
                float, Field(strict=True, ge=signal.minimum, le=signal.maximum)
            ]
        else:
            type_ = Literal[signal.values]  # pyright: ignore[reportInvalidTypeForm]
        fields[str(signal.id)] = (type_ | None, ...)
    return create_model("TickSignals", __config__=ConfigDict(extra="forbid"), **fields)


def output_model(
    signals: tuple[Signal, ...], *, observations: tuple[str, ...] = ()
) -> type[BaseModel]:
    readings = signal_model(signals)

    # Membership and chronology belong to the request, never to model invention.
    # Validation runs inside the gateway too, so malformed partitions use its
    # normal structured-output repair before any journal mutation.
    class PartitionedAnswer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        segments: list[SegmentAnswer]

        @model_validator(mode="after")
        def partition(self):
            actual = tuple(id for group in self.segments for id in group.observations)
            if actual != observations:
                raise ValueError(
                    "segments must partition every supplied observation ID exactly once, in supplied order"
                )
            return self

    id_type: Any = Literal[observations] if observations else str  # pyright: ignore[reportInvalidTypeForm]
    group = create_model(
        "SegmentGroup",
        __base__=SegmentAnswer,
        observations=(list[id_type], Field(min_length=1)),  # pyright: ignore[reportInvalidTypeForm]
    )
    groups: Any = list[group]  # pyright: ignore[reportInvalidTypeForm]
    return create_model(
        "TickAnswer",
        __base__=PartitionedAnswer,
        segments=(
            groups,
            Field(min_length=1 if observations else 0, max_length=len(observations)),
        ),
        signals=(readings, ...),
    )


def value_for(signal: Signal, value: object) -> SignalValue:
    if signal.type == "bool":
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        return None
    if signal.type in ("enum", "music.mood"):
        return value if isinstance(value, str) and value in signal.values else None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if (
        not math.isfinite(number)
        or signal.minimum is None
        or signal.maximum is None
        or not signal.minimum <= number <= signal.maximum
    ):
        return None
    return number


def parse(
    raw: Mapping[str, object],
    *,
    signals: tuple[Signal, ...],
    at: datetime,
    epoch: int,
    evidence_until: datetime,
    observations: tuple[str, ...],
    has_open_segment: bool,
) -> Tick:
    plan = output_model((), observations=observations).model_validate(
        {"segments": raw.get("segments"), "signals": {}}
    )
    assignments = []
    for item in plan.model_dump()["segments"]:
        answer = SegmentAnswer.model_validate(item)
        constructor = (
            Continue
            if answer.mode == "continue" and has_open_segment and not assignments
            else Start
        )
        assignments.append(
            SegmentAssignment(
                tuple(answer.observations),
                constructor(answer.title, answer.kind, answer.intent),
            )
        )
    tick = Tick(at, epoch, evidence_until, observations, tuple(assignments), ())
    answers = raw.get("signals")
    answers = answers if isinstance(answers, dict) else {}
    readings = tuple(
        SignalReading(
            signal.id, value_for(signal, answers.get(str(signal.id))), at, tick.id
        )
        for signal in signals
    )
    return replace(tick, readings=readings)
