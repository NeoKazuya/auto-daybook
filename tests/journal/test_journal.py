"""Journal acceptance through its public API, against real temporary SQLite files."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from daybook.events import (
    Bus,
    ObservationAdded,
    SegmentChanged,
    StatusChanged,
    TickRecorded,
)
from daybook.journal import (
    Continue,
    Journal,
    ObservationDraft,
    SignalId,
    SignalReading,
    SignalValue,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)

BASE = datetime(2026, 10, 10, 10, tzinfo=UTC)
SIGNAL = SignalId("break", "focused")
RANGE = TimeRange(BASE, BASE + timedelta(hours=1))


def time(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


@pytest.fixture
def journal(tmp_path: Path):
    value = Journal(tmp_path / "journal.db", Bus())
    yield value
    value.close()


def observe(
    journal: Journal, start: float, end: float, text: str = "Editor", app: str = "Code"
):
    note = journal.add_observation(
        ObservationDraft(
            time(start), WindowInfo(app, f"{text} window"), f"{text} is visible", text
        ),
        journal.epoch,
    )
    assert note is not None
    journal.extend_observation(note.id, time(end))
    return note


def tick(
    journal: Journal,
    ids: tuple[str, ...],
    at: float,
    decision: Continue | Start | None = None,
    value: SignalValue = True,
    signal=SIGNAL,
):
    return Tick(
        time(at),
        journal.epoch,
        time(at),
        ids,
        decision,
        (SignalReading(signal, value, time(at), "ignored-id"),),
    )


def segment(
    journal: Journal,
    notes: tuple[str, ...],
    at: float = 60,
    title: str = "Coding",
    intent: str = "Implementing",
):
    assert journal.record_tick(tick(journal, notes, at, Start(title, "coding", intent)))
    value = journal.portal.now().segment
    assert value is not None
    return value


def test_portal_empty_and_validation(journal: Journal):
    portal = journal.portal
    assert portal.now().capture == "running"
    assert portal.now().segment is None
    assert portal.now().observation is None
    assert portal.now().readings == ()
    assert portal.segments(RANGE) == []
    assert portal.observations(RANGE) == []
    assert portal.capture_spans(RANGE) == []
    assert portal.readings(SIGNAL, RANGE) == []
    assert portal.search("editor") == []
    assert portal.totals(RANGE) == []
    assert portal.context(RANGE, 500) == ""
    with pytest.raises(ValueError):
        portal.observations(TimeRange(time(20), time(10)))
    with pytest.raises(ValueError):
        journal.add_observation(
            ObservationDraft(datetime(2026, 1, 1), WindowInfo("", ""), ""), 0
        )


def test_observation_continuity_and_visible_limit(journal: Journal):
    first = observe(journal, 0, 30)
    second = observe(journal, 20, 50, "PDF", "Evince")
    journal.extend_observation(first.id, time(100))
    notes = journal.portal.observations(RANGE)
    assert notes[0].last_seen_at == notes[1].started_at == time(20)
    assert notes[0].active == 20
    assert notes[1].active == 30
    third = journal.add_observation(
        ObservationDraft(time(60), WindowInfo("Code", "test"), "x", "y" * 400),
        journal.epoch,
    )
    assert third is not None and len(third.visible_text) == 300
    assert journal.portal.observations(TimeRange(time(20), time(50))) == [notes[1]]
    assert journal.portal.observations(TimeRange(time(50), time(50))) == []
    assert second.id == notes[1].id


def test_tick_id_utc_and_idempotency(journal: Journal):
    note = observe(journal, 0, 60)
    value = tick(journal, (note.id,), 60, Start("Coding", "coding", "A patch"))
    offset = value.evidence_until.astimezone(timezone(timedelta(hours=-7)))
    equivalent = Tick(
        value.at, value.epoch, offset, value.observations, value.segment, value.readings
    )
    assert equivalent.id == value.id == "tick:2026-10-10T10:01:00+00:00"
    with journal._bus.subscribe() as queue:
        assert journal.record_tick(value)
        assert journal.record_tick(equivalent) is False
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    assert sum(isinstance(event, TickRecorded) for event in events) == 1
    assert journal.portal.readings(SIGNAL, RANGE)[0].tick == value.id
    assert len(journal.portal.segments(RANGE)) == 1
    assert journal.portal.observations(RANGE, unassigned_only=True) == []


def test_missing_observation_ids_skipped_and_empty_tick_readings(journal: Journal):
    first = observe(journal, 0, 60)
    journal.forget_observation(first.id)
    good = observe(journal, 70, 90)
    current = segment(journal, (first.id, good.id), 90)
    assert [o.id for o in current.observations] == [good.id]
    empty = tick(journal, (), 120, Start("Should not appear", "other", ""), value=None)
    assert journal.record_tick(empty)
    assert journal.portal.now().segment == current
    assert [r.value for r in journal.portal.readings(SIGNAL, RANGE)] == [True, None]
    assert journal.portal.readings(SIGNAL, TimeRange(time(90), time(120)))[
        0
    ].at == time(90)


def test_user_edits_survive_continue_and_restart(journal: Journal):
    first = observe(journal, 0, 60)
    current = segment(journal, (first.id,))
    before = journal.epoch
    edited = journal.edit_segment(
        current.id, title="My title", kind="writing", intent="My intent"
    )
    assert journal.epoch == before + 1
    assert edited.authored == "user"
    second = observe(journal, 60, 120)
    assert journal.record_tick(
        tick(journal, (second.id,), 120, Continue("Wrong", "reading", "Wrong intent"))
    )
    kept = journal.portal.now().segment
    assert kept is not None
    assert kept.title == "My title"
    assert kept.active == 120
    journal.close()
    reopened = Journal(journal.path, Bus())
    try:
        assert reopened.epoch == before + 1
        kept = reopened.portal.now().segment
        assert kept is not None and kept.authored == "user"
        assert len(reopened.portal.readings(SIGNAL, RANGE)) == 2
        assert reopened.portal.search("My title")[0].id == current.id
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "operation",
    [
        "observation",
        "segment",
        "range",
        "all",
        "edit_segment",
        "edit_observation",
        "trim",
        "clear_skill",
    ],
)
def test_every_barrier_rejects_inflight_results(
    journal: Journal, operation: str, caplog
):
    first = observe(journal, 0, 60)
    current = segment(journal, (first.id,))
    pending = tick(
        journal, (first.id,), 120, Continue("Returned secret", "reading", "")
    )
    epoch = journal.epoch
    if operation == "observation":
        journal.forget_observation(first.id)
    elif operation == "segment":
        journal.forget_segment(current.id)
    elif operation == "range":
        journal.forget_range(TimeRange(time(0), time(60)))
    elif operation == "all":
        journal.forget_all()
    elif operation == "edit_segment":
        journal.edit_segment(current.id, title="Edited")
    elif operation == "edit_observation":
        journal.edit_observation(first.id, seen="Edited")
    elif operation == "trim":
        journal.trim_after(time(30))
    else:
        journal.clear_skill_readings("break")
    assert journal.epoch == epoch + 1
    with caplog.at_level("INFO"), journal._bus.subscribe() as queue:
        assert journal.record_tick(pending) is False
        assert (
            journal.add_observation(
                ObservationDraft(
                    time(150), WindowInfo("Code", "secret"), "Returned secret"
                ),
                epoch,
            )
            is None
        )
        events = [queue.get_nowait() for _ in range(queue.qsize())]
    assert (
        sum(e == StatusChanged("journal", "discarded_after_forget") for e in events)
        == 2
    )
    assert "discarded_after_forget" in caplog.text
    assert "Returned secret" not in caplog.text
    assert journal.portal.search("Returned secret") == []


def test_derived_times_and_partial_forget(journal: Journal):
    first = observe(journal, 0, 30)
    second = observe(journal, 30, 60, "Book")
    third = observe(journal, 60, 90, "Browser")
    current = segment(
        journal,
        (first.id, second.id, third.id),
        90,
        "Private summary",
        "Private intent",
    )
    assert (
        current.started_at == time(0)
        and current.ended_at == time(90)
        and current.active == 90
    )
    journal.forget_observation(first.id)
    kept = journal.portal.segments(RANGE)[0]
    assert (
        kept.started_at == time(30) and kept.ended_at == time(90) and kept.active == 60
    )
    assert (
        kept.kind == "other"
        and kept.title == kept.intent == "(summary removed after forget)"
    )
    assert journal.portal.search("Private") == []
    fourth = observe(journal, 90, 120)
    assert journal.record_tick(
        tick(journal, (fourth.id,), 120, Continue("New text", "writing", "New goal"))
    )
    kept = journal.portal.now().segment
    assert kept is not None and kept.title == "New text"
    journal.forget_segment(current.id)
    assert journal.portal.segments(RANGE) == journal.portal.observations(RANGE) == []
    assert journal.portal.search("Book") == []


def test_partial_forget_keeps_user_text(journal: Journal):
    first = observe(journal, 0, 30)
    second = observe(journal, 30, 60)
    current = segment(journal, (first.id, second.id))
    journal.edit_segment(current.id, title="My retained text")
    journal.forget_observation(first.id)
    kept = journal.portal.now().segment
    assert kept is not None and kept.title == "My retained text"
    assert journal.portal.search("retained")[0].id == current.id


def test_forget_overlap_and_byte_erasure(journal: Journal):
    secret = "zzforgottenuniquestring7d82"
    first = observe(journal, 0, 30, "Kept")
    second = observe(journal, 30, 60, secret)
    third = observe(journal, 60, 90, "Later")
    segment(journal, (first.id, second.id, third.id), 90, secret, f"Intent {secret}")
    assert journal.portal.search(secret)
    journal.forget_range(TimeRange(time(40), time(50)))
    assert [o.id for o in journal.portal.observations(RANGE)] == [first.id, third.id]
    assert journal.portal.search(secret) == []
    assert not journal.deletion_pending
    for path in (journal.path, Path(str(journal.path) + "-wal")):
        assert not path.exists() or secret.encode() not in path.read_bytes()


def test_edit_updates_search_and_erases_replaced_text(journal: Journal):
    secret = "zzoldcontenterased88"
    first = observe(journal, 0, 30, secret)
    edited = journal.edit_observation(
        first.id, seen="New wording", visible_text="New wording"
    )
    # The active window title is immutable evidence, so edit can't erase it.
    assert edited.window.title == f"{secret} window"
    assert journal.portal.search("New wording")[0].id == first.id
    journal.forget_observation(first.id)
    assert journal.portal.search(secret) == []


def test_trim_removes_idle_activity_readings_and_closes(journal: Journal):
    first = observe(journal, 0, 60)
    segment(journal, (first.id,), 60)
    second = observe(journal, 60, 120, "Idle video")
    assert journal.record_tick(
        tick(journal, (second.id,), 120, Continue("Video", "media", "Watching"))
    )
    third = observe(journal, 120, 180, "Idle video 2")
    assert journal.record_tick(
        tick(journal, (third.id,), 180, Continue("Video", "media", "Watching"))
    )
    journal.trim_after(time(30))
    assert len(journal.portal.observations(RANGE)) == 1
    assert journal.portal.observations(RANGE)[0].last_seen_at == time(30)
    assert journal.portal.now().segment is None
    assert journal.portal.readings(SIGNAL, RANGE) == []
    assert journal.portal.totals(RANGE)[0].active == 30
    new = observe(journal, 210, 240)
    new_segment = segment(journal, (new.id,), 240)
    assert new_segment.id != journal.portal.segments(RANGE)[0].id
    assert sum(t.active for t in journal.portal.totals(RANGE)) == 60


def test_totals_all_groups_clipped_no_double_count(journal: Journal):
    first = observe(journal, 0, 60)
    segment(journal, (first.id,), 60, "Build")
    second = observe(journal, 60, 120, "PDF", "Evince")
    assert journal.record_tick(
        tick(journal, (second.id,), 120, Start("Read", "reading", "Understand"))
    )
    observe(journal, 180, 240, "Unassigned", "Code")
    clipped = TimeRange(time(30), time(210))
    assert {t.label: t.active for t in journal.portal.totals(clipped)} == {
        "coding": 30,
        "reading": 60,
        "other": 30,
    }
    assert {t.label: t.active for t in journal.portal.totals(clipped, "title")} == {
        "Build": 30,
        "Read": 60,
        "Unassigned": 30,
    }
    assert {t.label: t.active for t in journal.portal.totals(clipped, "app")} == {
        "Code": 60,
        "Evince": 60,
    }
    assert len(journal.portal.observations(RANGE, unassigned_only=True)) == 1


def test_capture_spans_and_context_budget(journal: Journal):
    journal.set_capture_state("running", time(0))
    journal.set_capture_state("paused", time(60))
    journal.set_capture_state("paused", time(90))
    journal.set_capture_state("running", time(120))
    spans = journal.portal.capture_spans(RANGE)
    assert len(spans) == 3
    assert spans[1].start == time(60) and spans[1].end == time(120)
    assert spans[-1].end is None
    first = observe(journal, 0, 60, "Old details")
    segment(journal, (first.id,), 60, "Old segment")
    second = observe(journal, 120, 180, "Recent details")
    assert journal.record_tick(
        tick(
            journal, (second.id,), 180, Start("Recent segment", "reading", "Understand")
        )
    )
    full = journal.portal.context(RANGE, 1000, "observations")
    assert "paused" in full and "Old details" in full and "Recent details" in full
    segment_only = journal.portal.context(RANGE, 1000)
    assert "Old details" not in segment_only and "Recent segment" in segment_only
    for budget in (0, 1, 20, 60, 100, 150):
        text = journal.portal.context(RANGE, budget, "observations")
        assert len(text) <= budget * 3.5
        if "Old details" in text:
            assert "Recent details" in text
    assert journal.portal.now().capture == "running"


def test_search_ranges_and_plain_text_punctuation(journal: Journal):
    first = observe(journal, 0, 60, "Rust parser")
    current = segment(
        journal, (first.id,), 60, "Compiler investigation", "Understand Rust"
    )
    assert {hit.kind for hit in journal.portal.search("Rust")} == {
        "observation",
        "segment",
    }
    assert journal.portal.search('Rust" OR (broken)') == []
    assert journal.portal.search("!!!") == []
    assert (
        journal.portal.search("Compiler", TimeRange(time(0), time(10)))[0].id
        == current.id
    )
    assert journal.portal.search("Rust", TimeRange(time(60), time(90))) == []


def test_clear_skill_preserves_other_history(journal: Journal):
    assert journal.record_tick(tick(journal, (), 10, signal=SIGNAL))
    other = SignalId("music", "mood")
    assert journal.record_tick(tick(journal, (), 20, signal=other, value="focus"))
    journal.clear_skill_readings("break")
    assert journal.portal.readings(SIGNAL, RANGE) == []
    assert journal.portal.readings(other, RANGE)[0].value == "focus"
    assert journal.portal.now().readings[0].signal == other


async def test_checkpoint_pending_retries_after_reader(journal: Journal):
    import asyncio

    secret = "zzcheckpointpendingsecret88"
    observe(journal, 0, 60, secret)
    # Pin a WAL snapshot before delete, exactly the documented checkpoint blocker.
    reader = sqlite3.connect(journal.path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM observations").fetchall()
    try:
        journal.forget_range(TimeRange(time(0), time(60)))
        assert journal.deletion_pending
        assert journal.portal.search(secret) == []
        with journal._bus.subscribe() as events:
            reader.rollback()
            # Wait for the timer's success event without polling the property.
            assert await asyncio.wait_for(events.get(), timeout=1) == StatusChanged(
                "journal", "ready"
            )
        assert not journal.deletion_pending
        for path in (journal.path, Path(str(journal.path) + "-wal")):
            assert not path.exists() or secret.encode() not in path.read_bytes()
    finally:
        reader.close()


def test_domain_events_after_commits(tmp_path: Path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    try:
        with bus.subscribe() as queue:
            first = observe(journal, 0, 60)
            current = segment(journal, (first.id,))
            journal.forget_segment(current.id)
            events = [queue.get_nowait() for _ in range(queue.qsize())]
        assert isinstance(events[0], ObservationAdded)
        assert any(isinstance(e, SegmentChanged) for e in events)
        assert events[-1] == StatusChanged("journal", "notes_changed")
        assert journal.portal.observations(RANGE) == []
    finally:
        journal.close()


def test_forget_all_does_not_touch_skill_ledger(journal: Journal):
    # Ledger is independently owned and survives notes privacy operations.
    ledger = sqlite3.connect(journal.path)
    try:
        ledger.execute("CREATE TABLE firings (key TEXT PRIMARY KEY)")
        ledger.execute("INSERT INTO firings VALUES ('an-effect-that-happened')")
        ledger.commit()
        first = observe(journal, 0, 60)
        segment(journal, (first.id,))
        journal.forget_all()
        assert journal.portal.observations(RANGE) == []
        assert journal.portal.readings(SIGNAL, RANGE) == []
        assert (
            ledger.execute("SELECT key FROM firings").fetchone()[0]
            == "an-effect-that-happened"
        )
    finally:
        ledger.close()


def test_tick_failure_rolls_back_every_write(journal: Journal):
    first = observe(journal, 0, 60)
    invalid = tick(
        journal,
        (first.id,),
        60,
        Start("Invalid partial result", "coding", ""),
        value=float("nan"),
    )
    with pytest.raises(ValueError):
        journal.record_tick(invalid)
    assert journal.portal.now().segment is None
    assert journal.portal.observations(RANGE, unassigned_only=True)[0].id == first.id
    assert journal.portal.readings(SIGNAL, RANGE) == []
    assert journal.record_tick(
        tick(journal, (first.id,), 60, Start("Valid result", "coding", ""))
    )


def test_trim_rewinds_capture_spans_for_away(journal: Journal):
    journal.set_capture_state("running", time(0))
    journal.set_capture_state("withheld", time(120))
    journal.set_capture_state("running", time(180))
    journal.trim_after(time(90))
    journal.set_capture_state("away", time(90))
    spans = journal.portal.capture_spans(RANGE)
    assert len(spans) == 2
    assert spans[0].state == "running" and spans[0].end == time(90)
    assert spans[1].state == "away" and spans[1].start == time(90)
    assert spans[1].end is None


@pytest.mark.parametrize("state", ["paused", "locked", "away", "withheld", "overlay"])
def test_forget_all_preserves_current_capture_state(journal: Journal, state):
    journal.set_capture_state("running", time(0))
    journal.set_capture_state(state, time(10))
    observe(journal, 0, 10)
    journal.forget_all()
    assert journal.portal.now().capture == state
    spans = journal.portal.capture_spans(RANGE)
    assert len(spans) == 1 and spans[0].state == state and spans[0].end is None
    assert journal.portal.observations(RANGE) == []
    journal.close()
    reopened = Journal(journal.path, Bus())
    try:
        assert reopened.portal.now().capture == state
    finally:
        reopened.close()


@pytest.mark.parametrize("state", ["paused", "locked", "away", "withheld", "overlay"])
def test_failed_observe_trim_preserves_newer_capture_state(journal: Journal, state):
    journal.set_capture_state("running", time(0))
    observe(journal, 0, 100)
    journal.set_capture_state(state, time(50))
    journal.trim_after(time(20))
    assert journal.portal.now().capture == state
    spans = journal.portal.capture_spans(RANGE)
    assert len(spans) == 2
    assert spans[0].end == time(50)
    assert spans[1].state == state and spans[1].start == time(50)
    assert spans[1].end is None
    assert journal.portal.observations(RANGE)[0].last_seen_at == time(20)


def test_away_rewind_removes_span_starting_at_boundary(journal: Journal):
    journal.set_capture_state("running", time(0))
    journal.set_capture_state("withheld", time(120))
    journal.set_capture_state("running", time(180))
    journal.trim_after(time(120))
    journal.set_capture_state("away", time(120))
    spans = journal.portal.capture_spans(RANGE)
    assert [span.state for span in spans] == ["running", "away"]
    assert spans[0].end == spans[1].start == time(120)
    assert "withheld" not in journal.portal.context(RANGE, 1000)


def test_capture_reads_omit_empty_intervals(journal: Journal):
    journal.set_capture_state("running", time(0))
    journal.set_capture_state("withheld", time(20))
    journal.set_capture_state("locked", time(20))
    journal.set_capture_state("running", time(60))
    spans = journal.portal.capture_spans(RANGE)
    assert [span.state for span in spans] == ["running", "locked", "running"]
    assert all(span.end is None or span.end > span.start for span in spans)


def test_tick_frontier_survives_restart_and_tracks_forgetting(tmp_path):
    from datetime import UTC

    path = tmp_path / "frontier.db"
    at = datetime.now(UTC)
    journal = Journal(path, Bus())
    assert journal.portal.latest_tick_evidence_until() is None
    assert journal.record_tick(Tick(at, journal.epoch, at, (), None, ()))
    later = at + timedelta(seconds=30)
    assert journal.record_tick(Tick(later, journal.epoch, later, (), None, ()))
    journal.close()
    journal = Journal(path, Bus())
    assert journal.portal.latest_tick_evidence_until() == later
    journal.forget_range(TimeRange(later, later + timedelta(seconds=1)))
    assert journal.portal.latest_tick_evidence_until() == at
    journal.forget_all()
    assert journal.portal.latest_tick_evidence_until() is None
    journal.close()
