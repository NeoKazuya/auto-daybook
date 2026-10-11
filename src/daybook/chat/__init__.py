"""Recall and declarative skill drafting contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from daybook.journal import Journal, TimeRange
from daybook.model import ModelGateway
from daybook.skills import SkillBook, SkillEntry


@dataclass(frozen=True)
class Answer:
    text: str
    cites: tuple[str, ...]
    window: TimeRange


@dataclass(frozen=True)
class DraftReady:
    entry: SkillEntry
    summary: str


@dataclass(frozen=True)
class Plain:
    text: str


def resolve_window(message: str, now: datetime) -> TimeRange:
    raise NotImplementedError


class Chat:
    def __init__(
        self, journal: Journal, gateway: ModelGateway, skills: SkillBook
    ) -> None:
        raise NotImplementedError

    async def respond(self, message: str) -> Answer | DraftReady | Plain:
        raise NotImplementedError
