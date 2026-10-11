"""Pure parsing and per-request output schemas; no model or storage side effects."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, create_model

from daybook.journal import (
    ActivityKind,
    Continue,
    SignalReading,
    SignalValue,
    Start,
    Tick,
)
from daybook.skills import Signal


class SegmentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["continue", "start"]
    title: str = Field(min_length=1, max_length=200)
    kind: ActivityKind
    intent: str = Field(max_length=600)


def output_model(
    signals: tuple[Signal, ...], *, has_observations: bool = False
) -> type[BaseModel]:
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
    readings = create_model(
        "TickSignals", __config__=ConfigDict(extra="forbid"), **fields
    )
    return create_model(
        "TickAnswer",
        __config__=ConfigDict(extra="forbid"),
        segment=(SegmentAnswer if has_observations else type(None), ...),
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
    segment = None
    answer = raw.get("segment")
    if observations and isinstance(answer, dict):
        try:
            parsed = SegmentAnswer.model_validate(answer)
            constructor = (
                Continue if parsed.mode == "continue" and has_open_segment else Start
            )
            segment = constructor(parsed.title, parsed.kind, parsed.intent)
        except ValueError:
            pass
    tick = Tick(at, epoch, evidence_until, observations, segment, ())
    answers = raw.get("signals")
    answers = answers if isinstance(answers, dict) else {}
    readings = tuple(
        SignalReading(
            signal.id, value_for(signal, answers.get(str(signal.id))), at, tick.id
        )
        for signal in signals
    )
    return replace(tick, readings=readings)
