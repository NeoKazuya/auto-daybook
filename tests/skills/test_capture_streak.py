"""Capture history constrains real skill firing keys without desktop effects."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from daybook.act import build_catalog
from daybook.events import Bus
from daybook.journal import Journal, Outcome, SignalId, SignalReading, Tick
from daybook.skills import SkillBook

SKILL = """---
id: reminder
name: Focus reminder
signals:
  focused:
    ask: Is the user focused on work?
    type: bool
rules:
  - when: focused is true for 2m
    do:
      - notify.send: {title: Rest, body: 'Focused for $focused.for'}
allow: [notify.send]
---
Remind me once after sustained focus.
"""


@pytest.fixture
def runtime(tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    calls = []

    async def notify(context, parameters):
        calls.append(parameters)
        return Outcome("notify.send", True)

    def open_book():
        return SkillBook(
            tmp_path,
            tmp_path / "builtin",
            tmp_path / "journal.db",
            journal.portal,
            build_catalog({"focus": "work"}, {"notify.send": notify}),
            bus,
            clear_readings=journal.clear_skill_readings,
        )

    book = open_book()
    entry = book.save("reminder", SKILL)
    book.approve(entry.id, entry.hash)
    base = datetime.now(UTC) + timedelta(seconds=1)
    journal.set_capture_state("running", base)
    yield journal, book, calls, base, open_book
    journal.close()


async def record(journal, book, base, seconds, value=True):
    at = base + timedelta(seconds=seconds)
    reading = SignalReading(SignalId("reminder", "focused"), value, at, "")
    tick = Tick(at, journal.epoch, at, (), (), (reading,))
    assert journal.record_tick(tick)
    await book.react(tick)
    await book.react(tick)
    return tick


def firing_keys(path):
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT key FROM firings ORDER BY at")]


async def test_overlay_dismissal_keeps_firing_key_across_restart(runtime, tmp_path):
    journal, book, calls, base, open_book = runtime
    first = await record(journal, book, base, 0)
    await record(journal, book, base, 60)
    await record(journal, book, base, 120)
    assert len(calls) == 1
    journal.set_capture_state("overlay", base + timedelta(seconds=130))
    journal.set_capture_state("running", base + timedelta(seconds=160))
    book = open_book()
    for seconds in (180, 240, 300):
        await record(journal, book, base, seconds)
    assert len(calls) == 1
    assert firing_keys(tmp_path / "journal.db") == [f"streak:{first.id}"]


async def test_short_overlay_preserves_elapsed_streak_duration(runtime, tmp_path):
    journal, book, calls, base, _ = runtime
    first = await record(journal, book, base, 0)
    await record(journal, book, base, 60)
    journal.set_capture_state("overlay", base + timedelta(seconds=70))
    journal.set_capture_state("running", base + timedelta(seconds=100))
    await record(journal, book, base, 120)
    assert calls == [{"title": "Rest", "body": "Focused for 2 min"}]
    assert firing_keys(tmp_path / "journal.db") == [f"streak:{first.id}"]


async def test_false_then_true_after_overlay_starts_new_streak(runtime, tmp_path):
    journal, book, calls, base, _ = runtime
    first = await record(journal, book, base, 0)
    await record(journal, book, base, 60)
    await record(journal, book, base, 120)
    journal.set_capture_state("overlay", base + timedelta(seconds=130))
    journal.set_capture_state("running", base + timedelta(seconds=160))
    await record(journal, book, base, 180, False)
    next_streak = await record(journal, book, base, 210)
    await record(journal, book, base, 270)
    assert len(calls) == 1
    await record(journal, book, base, 330)
    assert len(calls) == 2
    assert firing_keys(tmp_path / "journal.db") == [
        f"streak:{first.id}",
        f"streak:{next_streak.id}",
    ]


@pytest.mark.parametrize("capture", ["paused", "locked", "withheld", "away"])
async def test_short_privacy_or_away_gap_starts_new_streak(runtime, tmp_path, capture):
    journal, book, calls, base, _ = runtime
    first = await record(journal, book, base, 0)
    await record(journal, book, base, 60)
    await record(journal, book, base, 120)
    journal.set_capture_state(capture, base + timedelta(seconds=130))
    journal.set_capture_state("running", base + timedelta(seconds=160))
    next_streak = await record(journal, book, base, 180)
    await record(journal, book, base, 240)
    assert len(calls) == 1
    await record(journal, book, base, 300)
    assert len(calls) == 2
    assert firing_keys(tmp_path / "journal.db") == [
        f"streak:{first.id}",
        f"streak:{next_streak.id}",
    ]


async def test_long_overlay_starts_new_streak(runtime, tmp_path):
    journal, book, calls, base, _ = runtime
    first = await record(journal, book, base, 0)
    await record(journal, book, base, 60)
    await record(journal, book, base, 120)
    journal.set_capture_state("overlay", base + timedelta(seconds=130))
    journal.set_capture_state("running", base + timedelta(seconds=431))
    next_streak = await record(journal, book, base, 440)
    await record(journal, book, base, 500)
    assert len(calls) == 1
    await record(journal, book, base, 560)
    assert len(calls) == 2
    assert firing_keys(tmp_path / "journal.db") == [
        f"streak:{first.id}",
        f"streak:{next_streak.id}",
    ]


@pytest.mark.parametrize("reading_gap,expected_firings", [(300, 1), (301, 2)])
async def test_short_overlay_does_not_extend_missing_reading_limit(
    runtime, tmp_path, reading_gap, expected_firings
):
    journal, book, calls, base, _ = runtime
    first = await record(journal, book, base, 0)
    await record(journal, book, base, 60)
    await record(journal, book, base, 120)
    journal.set_capture_state("overlay", base + timedelta(seconds=130))
    journal.set_capture_state("running", base + timedelta(seconds=160))
    resumed = await record(journal, book, base, 120 + reading_gap)
    await record(journal, book, base, 180 + reading_gap)
    await record(journal, book, base, 240 + reading_gap)
    assert len(calls) == expected_firings
    expected_keys = [f"streak:{first.id}"]
    if expected_firings == 2:
        expected_keys.append(f"streak:{resumed.id}")
    assert firing_keys(tmp_path / "journal.db") == expected_keys
