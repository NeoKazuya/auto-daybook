"""Tick scheduling, Upcoming extraction, and pure parsing contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal, Mapping

from daybook.journal import Journal, Tick, UpcomingDraft
from daybook.model import ModelGateway, Priority
from daybook.skills import Signal, SkillBook


def parse_tick(
    raw: Mapping[str, object],
    *,
    signals: tuple[Signal, ...],
    at: datetime,
    epoch: int,
    evidence_until: datetime,
    observations: tuple[str, ...],
    has_open_segment: bool,
) -> Tick:
    from ._tick import parse

    return parse(
        raw,
        signals=signals,
        at=at,
        epoch=epoch,
        evidence_until=evidence_until,
        observations=observations,
        has_open_segment=has_open_segment,
    )


class Mind:
    def __init__(
        self,
        journal: Journal,
        gateway: ModelGateway,
        skills: SkillBook,
        tick_interval: float = 90,
        *,
        effort: Literal["none", "low"] = "none",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        evidence_ready: Callable[[str, tuple[str, ...]], bool] = lambda *_: True,
        upcoming: bool = False,
    ) -> None:
        from ._runner import Runner
        from ._signals import SignalRunner

        self._runner = Runner(
            journal,
            gateway,
            skills,
            tick_interval,
            effort,
            clock,
            sleep,
            evidence_ready,
            upcoming,
        )

        self._signals = SignalRunner(self._runner)

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._signals.run())
            tasks.create_task(self._runner.run())

    def nudge(self, app_switched: bool = False) -> None:
        self._runner.nudge(app_switched)

    async def tick_now(self) -> Tick | None:
        # The explicit debug action checks skills first, then updates history.
        try:
            active = bool(self._runner.skills.active_signals())
        except Exception:
            return None
        readings = await self._signals.check_now()
        if active and readings is None:
            return None
        history = await self._runner.tick_now()
        return readings or history

    async def scan_upcoming(self) -> None:
        """Extract Upcoming items from notes of recorded ticks (run() does this)."""
        await self._runner.scan_upcoming()


async def extract_upcoming(
    gateway: ModelGateway, message: str, *, now: datetime
) -> list[UpcomingDraft]:
    """Items the user names in a chat message, such as 'CSE 130 due at 11:59'."""
    from ._upcoming import extract

    return await extract(
        gateway, message=message, now=now, priority=Priority.INTERACTIVE
    )
