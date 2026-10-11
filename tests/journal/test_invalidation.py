"""Public Journal invalidation events distinguish changed history from new notes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from daybook.events import Bus, StatusChanged
from daybook.journal import (
    Journal,
    ObservationDraft,
    SignalId,
    SignalReading,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)

AT = datetime(2026, 10, 11, 12, tzinfo=UTC)
INVALIDATED = StatusChanged("journal", "notes_invalidated")
CHANGED = StatusChanged("journal", "notes_changed")


@pytest.fixture
def history(tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    note = journal.add_observation(
        ObservationDraft(AT, WindowInfo("Code", "tests.py"), "Original evidence"),
        journal.epoch,
    )
    assert note is not None
    journal.extend_observation(note.id, AT + timedelta(minutes=2))
    assert journal.record_tick(
        Tick(
            AT + timedelta(minutes=2),
            journal.epoch,
            AT + timedelta(minutes=2),
            (note.id,),
            Start("Coding", "coding", "Repair tests"),
            (
                SignalReading(
                    SignalId("test", "focused"), True, AT + timedelta(minutes=2), ""
                ),
            ),
        )
    )
    segment = journal.portal.now().segment
    assert segment is not None
    try:
        yield SimpleNamespace(bus=bus, journal=journal, note=note, segment=segment)
    finally:
        journal.close()


def emitted(history, operation):
    with history.bus.subscribe() as queue:
        operation()
        return [queue.get_nowait() for _ in range(queue.qsize())]


@pytest.mark.parametrize(
    "mutation",
    [
        "segment_edit",
        "observation_edit",
        "observation_forget",
        "segment_forget",
        "range_forget",
        "all_forget",
        "trim",
    ],
)
def test_existing_evidence_change_invalidates_once_after_commit(history, mutation):
    journal = history.journal
    operations = {
        "segment_edit": lambda: journal.edit_segment(
            history.segment.id, title="Changed title"
        ),
        "observation_edit": lambda: journal.edit_observation(
            history.note.id, seen="Changed evidence"
        ),
        "observation_forget": lambda: journal.forget_observation(history.note.id),
        "segment_forget": lambda: journal.forget_segment(history.segment.id),
        "range_forget": lambda: journal.forget_range(
            TimeRange(AT, AT + timedelta(minutes=1))
        ),
        "all_forget": journal.forget_all,
        "trim": lambda: journal.trim_after(AT + timedelta(minutes=1)),
    }
    events = emitted(history, operations[mutation])
    assert events.count(INVALIDATED) == 1
    assert events[-1] == CHANGED
    # Consumers querying in response to the event see the committed result.
    if mutation == "observation_edit":
        assert journal.portal.now().observation.seen == "Changed evidence"
    if mutation == "trim":
        assert journal.portal.now().observation.last_seen_at == AT + timedelta(
            minutes=1
        )


def test_normal_capture_tick_and_skill_readings_do_not_invalidate_recall(history):
    journal = history.journal
    with history.bus.subscribe() as queue:
        note = journal.add_observation(
            ObservationDraft(
                AT + timedelta(minutes=3), WindowInfo("Firefox", "Docs"), "New evidence"
            ),
            journal.epoch,
        )
        assert note is not None
        journal.extend_observation(note.id, AT + timedelta(minutes=4))
        assert journal.record_tick(
            Tick(
                AT + timedelta(minutes=4),
                journal.epoch,
                AT + timedelta(minutes=4),
                (note.id,),
                Start("Reading", "reading", "Reference"),
                (),
            )
        )
        journal.set_capture_state("paused", AT + timedelta(minutes=5))
        journal.clear_skill_readings("test")
        events = [queue.get_nowait() for _ in range(queue.qsize())]
    assert CHANGED in events
    assert INVALIDATED not in events


def test_noop_trim_and_identical_edits_do_not_invalidate_recall(history):
    journal = history.journal
    journal.edit_segment(history.segment.id, title="User title")
    journal.edit_observation(history.note.id, seen="User evidence")
    journal.close_open_segment()
    for operation in [
        lambda: journal.trim_after(AT + timedelta(hours=1)),
        lambda: journal.edit_segment(history.segment.id, title="User title"),
        lambda: journal.edit_observation(history.note.id, seen="User evidence"),
    ]:
        assert INVALIDATED not in emitted(history, operation)


def test_rolled_back_edit_publishes_no_invalidation_or_note_change(history):
    # This exercises rollback after edit_segment has already bumped the epoch.
    # An owned SQLite trigger rejects the update; no event may escape rollback.
    import sqlite3

    writer = sqlite3.connect(history.journal.path)
    try:
        writer.execute(
            "CREATE TRIGGER owned_update_failure BEFORE UPDATE ON segments "
            "BEGIN SELECT RAISE(ABORT, 'owned rollback proof'); END"
        )
        writer.commit()
    finally:
        writer.close()
    with history.bus.subscribe() as queue:
        epoch = history.journal.epoch
        old = history.journal.portal.now().segment
        with pytest.raises(sqlite3.IntegrityError, match="owned rollback proof"):
            history.journal.edit_segment(history.segment.id, title="Must roll back")
        assert queue.empty()
        assert history.journal.epoch == epoch
        assert history.journal.portal.now().segment == old
