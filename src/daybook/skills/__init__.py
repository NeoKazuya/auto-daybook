"""Skill contracts. File provenance comes from the loader, never front matter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping

from daybook.act import Actions, Catalog
from daybook.events import Bus
from daybook.journal import Portal, SignalId, SignalReading, SignalValue, Tick
from daybook.model import ModelGateway


@dataclass(frozen=True)
class SkillSource:
    path: Path
    origin: Literal["builtin", "user"]


@dataclass(frozen=True)
class SkillError:
    location: str
    message: str


@dataclass(frozen=True)
class Signal:
    id: SignalId
    ask: str
    type: Literal["bool", "enum", "number", "music.mood"]
    values: tuple[str, ...] = ()
    descriptions: Mapping[str, str] | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Trigger:
    form: Literal["updates", "becomes", "is", "every"]
    signal: str | None = None
    value: SignalValue = None
    seconds: float = 0


@dataclass(frozen=True)
class Step:
    action: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class Rule:
    when: str
    trigger: Trigger
    steps: tuple[Step, ...]
    cooldown: float = 0


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    goal: str
    signals: tuple[Signal, ...]
    rules: tuple[Rule, ...]
    allow: tuple[str, ...]
    created_by: Literal["builtin", "user", "chat"] = "user"
    request: str | None = None


@dataclass(frozen=True)
class SkillEntry:
    id: str
    text: str
    hash: str
    source: SkillSource
    skill: Skill | None
    errors: tuple[SkillError, ...]
    enabled: bool
    approved: bool
    status: Literal["active", "needs_review", "disabled", "invalid"]


@dataclass(frozen=True)
class Occurrence:
    key: str
    at: datetime
    values: Mapping[str, SignalValue]
    held_seconds: float = 0


@dataclass(frozen=True)
class HookContext:
    portal: Portal
    actions: Actions
    occurrence: Occurrence


class StaleApproval(Exception):
    pass


class NeedsReview(Exception):
    pass


class SignalLimit(Exception):
    pass


def parse_skill(
    text: str, *, source: SkillSource, catalog: Catalog, hook_text: str | None = None
) -> Skill | list[SkillError]:
    from ._parser import parse

    return parse(text, source=source, catalog=catalog, hook_text=hook_text)


def evaluate(
    rule: Rule, history: list[SignalReading], *, max_gap: float = 300, now: datetime
) -> Occurrence | None:
    raise NotImplementedError


async def draft_skill(
    request: str, *, book: SkillBook, gateway: ModelGateway
) -> tuple[SkillEntry, str]:
    raise NotImplementedError


class SkillBook:
    def __init__(
        self,
        home: Path,
        builtin: Path,
        database: Path,
        portal: Portal,
        catalog: Catalog,
        bus: Bus,
    ) -> None:
        raise NotImplementedError

    def entries(self) -> list[SkillEntry]:
        raise NotImplementedError

    def active_signals(self) -> tuple[Signal, ...]:
        raise NotImplementedError

    def parse(self, id: str, text: str) -> Skill | list[SkillError]:
        raise NotImplementedError

    def save(self, id: str, text: str) -> SkillEntry:
        raise NotImplementedError

    def approve(self, id: str, hash: str) -> SkillEntry:
        raise NotImplementedError

    def enable(self, id: str) -> SkillEntry:
        raise NotImplementedError

    def disable(self, id: str) -> SkillEntry:
        raise NotImplementedError

    def delete(self, id: str) -> None:
        raise NotImplementedError

    async def react(self, tick: Tick) -> None:
        raise NotImplementedError
