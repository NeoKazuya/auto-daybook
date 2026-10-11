"""Tick scheduling and pure parsing contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

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
    raise NotImplementedError


class Mind:
    def __init__(
        self,
        journal: Journal,
        gateway: ModelGateway,
        skills: SkillBook,
        tick_interval: float = 90,
    ) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        raise NotImplementedError

    def nudge(self, app_switched: bool = False) -> None:
        raise NotImplementedError

    async def tick_now(self) -> Tick | None:
        raise NotImplementedError
