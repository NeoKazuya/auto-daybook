import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta

from daybook import app
from daybook.events import Bus
from daybook.journal import Journal, ObservationDraft, WindowInfo


async def test_retention_runs_on_entry_and_periodically_then_stops(tmp_path):
    now = datetime(2026, 10, 11, tzinfo=UTC)
    wakes = asyncio.Queue()
    sleeping = asyncio.Queue()

    async def sleep(seconds: float) -> None:
        assert seconds == 60
        sleeping.put_nowait(None)
        await wakes.get()

    with closing(Journal(tmp_path / "journal.db", Bus())) as journal:
        for days, text in [(31, "expired"), (29, "recent")]:
            journal.add_observation(
                ObservationDraft(
                    now - timedelta(days=days), WindowInfo("Code", text), text
                ),
                journal.epoch,
            )
        async with app.observation_retention(journal, clock=lambda: now, sleep=sleep):
            assert not journal.portal.search("expired")
            assert journal.portal.search("recent")
            await sleeping.get()
            now += timedelta(days=2)
            wakes.put_nowait(None)
            await sleeping.get()
            assert not journal.portal.search("recent")
        # Teardown cancels the sleeper before Journal closes.
        assert sleeping.empty()
