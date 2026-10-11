"""Tick scheduling and pure parsing contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal, Mapping

from daybook.journal import Journal, Tick
from daybook.model import ModelGateway
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
    ) -> None:
        from ._runner import Runner

        self._runner = Runner(
            journal,
            gateway,
            skills,
            tick_interval,
            effort,
            clock,
            sleep,
            evidence_ready,
        )

    async def run(self) -> None:
        await self._runner.run()

    def nudge(self, app_switched: bool = False) -> None:
        self._runner.nudge(app_switched)

    async def tick_now(self) -> Tick | None:
        return await self._runner.tick_now()
