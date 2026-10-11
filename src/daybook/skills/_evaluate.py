"""Deterministic trigger matching; history never grants permission to act."""

from __future__ import annotations

from datetime import datetime

from daybook.journal import SignalReading

from . import Occurrence, Rule


def same(a: object, b: object) -> bool:
    numeric = type(a) in (int, float) and type(b) in (int, float)
    return (numeric or type(a) is type(b)) and a == b


def streak(
    history: list[SignalReading], *, max_gap: float = 300
) -> tuple[SignalReading, float] | None:
    if not history or history[-1].value is None:
        return None
    last = first = history[-1]
    for previous in reversed(history[:-1]):
        if (
            previous.value is None
            or not same(previous.value, last.value)
            or (first.at - previous.at).total_seconds() > max_gap
        ):
            break
        first = previous
    return first, (last.at - first.at).total_seconds()


def match(
    rule: Rule, history: list[SignalReading], *, max_gap: float, now: datetime
) -> Occurrence | None:
    rows = sorted((r for r in history if r.at <= now), key=lambda r: r.at)
    current = [r for r in rows if r.at == now]
    if not current:
        return None
    values = {r.signal.name: r.value for r in current}
    trigger = rule.trigger
    if trigger.form == "every":
        bucket = int(now.timestamp() // trigger.seconds)
        return Occurrence(f"bucket:{bucket}", now, values)
    rows = [r for r in rows if r.signal.name == trigger.signal]
    if not rows or rows[-1].at != now or rows[-1].value is None:
        return None
    last = rows[-1]
    if trigger.form == "updates":
        return Occurrence(last.tick, now, values)
    if not same(last.value, trigger.value):
        return None
    if trigger.form == "becomes":
        previous = next((r for r in reversed(rows[:-1]) if r.value is not None), None)
        if (
            previous is None
            or (now - previous.at).total_seconds() > max_gap
            or previous.value == last.value
        ):
            return None
        return Occurrence(last.tick, now, values)
    held = streak(rows, max_gap=max_gap)
    if held is None or held[1] < trigger.seconds:
        return None
    return Occurrence(f"streak:{held[0].tick}", now, values, held[1])
