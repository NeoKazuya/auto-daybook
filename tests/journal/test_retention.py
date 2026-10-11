from contextlib import closing
from datetime import UTC, datetime, timedelta

from daybook.events import Bus
from daybook.journal import (
    Journal,
    ObservationDraft,
    SegmentAssignment,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)

AT = datetime(2026, 10, 11, tzinfo=UTC)


def add(journal, at, word):
    note = journal.add_observation(
        ObservationDraft(at, WindowInfo("Firefox", word), word), journal.epoch
    )
    assert note is not None
    return note


def test_retention_removes_expired_notes_and_stale_summary_but_keeps_recent(tmp_path):
    with closing(Journal(tmp_path / "journal.db", Bus())) as journal:
        old = add(journal, AT - timedelta(days=31), "expiredsentinel")
        recent = add(journal, AT - timedelta(days=1), "recentsentinel")
        assert journal.record_tick(
            Tick(
                AT,
                journal.epoch,
                AT,
                (old.id, recent.id),
                (
                    SegmentAssignment(
                        (old.id, recent.id),
                        Start("expiredsummary", "browsing", "expiredintent"),
                    ),
                ),
                (),
            )
        )
        epoch = journal.epoch
        assert journal.prune_observations(AT - timedelta(days=30)) == 1
        assert not journal.portal.search("expiredsentinel")
        assert not journal.portal.search("expiredsummary")
        assert journal.portal.search("recentsentinel")
        assert journal.epoch == epoch + 1
        assert (
            journal.add_observation(
                ObservationDraft(AT, WindowInfo("Code", "stale"), "expiredsentinel"),
                epoch,
            )
            is None
        )
        assert journal.prune_observations(AT - timedelta(days=30)) == 0
        assert journal.epoch == epoch + 1
    with closing(Journal(tmp_path / "journal.db", Bus())) as reopened:
        assert not reopened.portal.search("expiredsentinel")
        assert reopened.portal.search("recentsentinel")


def test_retention_clips_crossing_observation_and_keeps_exact_boundary(tmp_path):
    cutoff = AT - timedelta(days=30)
    with closing(Journal(tmp_path / "journal.db", Bus())) as journal:
        crossing = add(journal, cutoff - timedelta(seconds=10), "crossing")
        journal.extend_observation(crossing.id, cutoff + timedelta(seconds=10))
        assert journal.prune_observations(cutoff) == 0
        notes = journal.portal.observations(TimeRange(cutoff - timedelta(days=1), AT))
        assert len(notes) == 1
        assert notes[0].started_at == cutoff
        assert notes[0].last_seen_at == cutoff + timedelta(seconds=10)
        assert not journal.portal.observations(
            TimeRange(cutoff - timedelta(days=1), cutoff)
        )
        boundary = add(journal, cutoff, "boundary")
        assert journal.prune_observations(cutoff) == 0
        assert any(
            n.id == boundary.id
            for n in journal.portal.observations(TimeRange(cutoff, AT))
        )
