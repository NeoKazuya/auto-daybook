"""A grouped tick is one validated, atomic assignment of supplied evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from daybook.events import Bus, SegmentChanged, TickRecorded
from daybook.journal import (
    Continue,
    Journal,
    ObservationDraft,
    SegmentAssignment,
    SignalId,
    SignalReading,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)

AT = datetime(2026, 10, 11, 12, tzinfo=UTC)


@pytest.fixture
def batch(tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "db", bus)
    notes = []
    for index in range(3):
        at = AT + timedelta(seconds=index * 30)
        note = journal.add_observation(
            ObservationDraft(at, WindowInfo("App", str(index)), str(index)),
            journal.epoch,
        )
        assert note
        journal.extend_observation(note.id, at + timedelta(seconds=30))
        notes.append(note)
    ids = tuple(note.id for note in notes)
    at = AT + timedelta(seconds=90)
    tick = Tick(
        at,
        journal.epoch,
        at,
        ids,
        tuple(
            SegmentAssignment((id,), Start(str(index), kind, "Task"))
            for index, (id, kind) in enumerate(
                zip(ids, ("reading", "coding", "writing"))
            )
        ),
        (SignalReading(SignalId("test", "active"), True, at, ""),),
    )
    try:
        yield journal, bus, tick
    finally:
        journal.close()


@pytest.mark.parametrize(
    "fault",
    [
        "omitted",
        "duplicate",
        "foreign",
        "reordered",
        "expected_reordered",
        "empty_group",
        "missing",
        "assigned",
        "epoch",
    ],
)
def test_invalid_grouped_tick_has_no_partial_mutation(batch, fault):
    journal, bus, tick = batch
    first, second, third = tick.segments
    if fault == "omitted":
        tick = replace(tick, segments=(first, second))
    elif fault == "duplicate":
        tick = replace(tick, segments=(first, second, first))
    elif fault == "foreign":
        tick = replace(
            tick, segments=(first, second, replace(third, observations=("foreign",)))
        )
    elif fault == "reordered":
        tick = replace(tick, segments=(second, first, third))
    elif fault == "expected_reordered":
        tick = replace(
            tick,
            observations=tuple(reversed(tick.observations)),
            segments=tuple(reversed(tick.segments)),
        )
    elif fault == "empty_group":
        tick = replace(tick, segments=(replace(first, observations=()), *tick.segments))
    elif fault == "missing":
        # Also test storage membership, independent of the epoch guard.
        journal.forget_observation(tick.observations[1])
        tick = replace(tick, epoch=journal.epoch)
    elif fault == "assigned":
        assert journal.record_tick(
            replace(
                tick,
                evidence_until=AT + timedelta(seconds=30),
                observations=first.observations,
                segments=(first,),
                readings=(),
            )
        )
    else:
        journal.clear_skill_readings("test")
    before = journal.portal.observations(TimeRange(AT, AT + timedelta(minutes=5)))
    frontier = journal.portal.latest_tick_evidence_until()
    with bus.subscribe() as queue:
        assert journal.record_tick(tick) is False
        events = [queue.get_nowait() for _ in range(queue.qsize())]
        assert not any(
            isinstance(event, (TickRecorded, SegmentChanged)) for event in events
        )
    assert (
        journal.portal.observations(TimeRange(AT, AT + timedelta(minutes=5))) == before
    )
    assert journal.portal.latest_tick_evidence_until() == frontier
    assert (
        journal.portal.readings(
            SignalId("test", "active"), TimeRange(AT, AT + timedelta(minutes=5))
        )
        == []
    )


def test_all_groups_commit_before_events_and_reapplication_is_noop(batch):
    journal, bus, tick = batch
    with bus.subscribe() as queue:
        assert journal.record_tick(tick)
        events = [queue.get_nowait() for _ in range(queue.qsize())]
    assert sum(isinstance(event, TickRecorded) for event in events) == 1
    assert sum(isinstance(event, SegmentChanged) for event in events) >= 3
    segments = journal.portal.segments(TimeRange(AT, AT + timedelta(minutes=5)))
    assert [s.kind for s in segments] == ["reading", "coding", "writing"]
    assert [s.active for s in segments] == [30, 30, 30]
    assert [s.open for s in segments] == [False, False, True]
    assert journal.record_tick(tick) is False


def test_user_text_and_old_kind_survive_later_groups(batch):
    journal, _, tick = batch
    first, second, third = tick.segments
    assert journal.record_tick(
        replace(
            tick,
            evidence_until=AT + timedelta(seconds=30),
            observations=first.observations,
            segments=(first,),
            readings=(),
        )
    )
    current = journal.portal.now().segment
    assert current
    journal.edit_segment(current.id, title="My reading", intent="My goal")
    continued = replace(
        second, decision=Continue("Model title", "reading", "Model intent")
    )
    changed = replace(third, decision=Continue("New code", "coding", "New task"))
    assert journal.record_tick(
        replace(
            tick,
            epoch=journal.epoch,
            observations=second.observations + third.observations,
            segments=(continued, changed),
        )
    )
    segments = journal.portal.segments(TimeRange(AT, AT + timedelta(minutes=5)))
    assert [(s.title, s.kind, s.intent, s.active) for s in segments] == [
        ("My reading", "reading", "My goal", 60),
        ("New code", "coding", "New task", 30),
    ]


def test_later_group_failure_rolls_back_earlier_group_and_readings(batch):
    journal, bus, tick = batch
    broken = replace(tick.segments[1], decision=Start("Bad", "invalid", ""))  # type: ignore[arg-type]
    tick = replace(tick, segments=(tick.segments[0], broken, tick.segments[2]))
    with bus.subscribe() as queue:
        with pytest.raises(ValueError, match="unknown activity kind"):
            journal.record_tick(tick)
        assert queue.empty()
    assert journal.portal.latest_tick_evidence_until() is None
    assert journal.portal.now().segment is None
    assert journal.portal.now().readings == ()
    assert (
        len(
            journal.portal.observations(
                TimeRange(AT, AT + timedelta(minutes=5)), unassigned_only=True
            )
        )
        == 3
    )
