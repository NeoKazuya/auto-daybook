"""Files and approval state share one owner; effects are claimed before awaits."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, cast

from daybook.act import Catalog
from daybook.events import Bus, SkillFired, SkillsChanged
from daybook.journal import Outcome, Portal, SignalReading, Tick, TimeRange

from . import (
    HookContext,
    NeedsReview,
    Occurrence,
    Signal,
    SignalLimit,
    Skill,
    SkillEntry,
    SkillSource,
    StaleApproval,
    parse_skill,
)
from ._evaluate import match, streak
from ._ledger import Ledger


def valid_id(id: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]{1,40}", id):
        raise ValueError("invalid skill id")


class Book:
    def __init__(
        self,
        home: Path,
        builtin: Path,
        database: Path,
        portal: Portal,
        catalog: Catalog,
        bus: Bus,
        clear_readings: Callable[[str], None] | None,
    ) -> None:
        self.home, self.builtin = home / "skills", builtin
        self.home.mkdir(parents=True, exist_ok=True)
        self.portal, self.catalog, self.bus = portal, catalog, bus
        self.ledger = Ledger(database)
        self.clear_readings = clear_readings

    def sources(self) -> dict[str, SkillSource]:
        sources = {}
        for directory, origin in ((self.builtin, "builtin"), (self.home, "user")):
            for path in sorted(directory.glob("*.skill.md")):
                id = path.name.removesuffix(".skill.md")
                if re.fullmatch(r"[a-z0-9-]{1,40}", id):
                    sources[id] = SkillSource(path, origin)  # type: ignore[arg-type]
        return sources

    def hook_text(self, source: SkillSource) -> str | None:
        if source.origin != "builtin":
            return None
        path = source.path.with_name(source.path.name.replace(".skill.md", ".hook.py"))
        return path.read_text() if path.exists() else None

    def entry(self, id: str) -> SkillEntry:
        valid_id(id)
        source = self.sources().get(id)
        if source is None:
            raise KeyError(id)
        text = source.path.read_text()
        hook = self.hook_text(source)
        hash = hashlib.sha256((text + (hook or "")).encode()).hexdigest()
        parsed = parse_skill(text, source=source, catalog=self.catalog, hook_text=hook)
        state = self.ledger.state(id)
        if state is None:
            self.ledger.set_state(
                id,
                enabled=source.origin == "builtin" and id == "music",
                approved_hash=hash if source.origin == "builtin" else None,
                approved_at=datetime.now(timezone.utc).isoformat()
                if source.origin == "builtin"
                else None,
            )
            state = self.ledger.state(id)
        assert state is not None
        if source.origin == "builtin" and state["approved_hash"] != hash:
            self.replace_approval(id, hash, enabled=bool(state["enabled"]))
            state = self.ledger.state(id)
            assert state is not None
        approved = state["approved_hash"] == hash
        skill = parsed if isinstance(parsed, Skill) else None
        errors = () if isinstance(parsed, Skill) else tuple(parsed)
        status = (
            "invalid"
            if errors
            else "needs_review"
            if not approved
            else "active"
            if state["enabled"]
            else "disabled"
        )
        return SkillEntry(
            id,
            text,
            hash,
            source,
            skill,
            errors,
            bool(state["enabled"]),
            approved,
            status,
        )

    def entries(self) -> list[SkillEntry]:
        return [self.entry(id) for id in sorted(self.sources())]

    def active_signals(self) -> tuple[Signal, ...]:
        return tuple(
            s
            for e in self.entries()
            if e.status == "active" and e.skill
            for s in e.skill.signals
        )

    def save(self, id: str, text: str) -> SkillEntry:
        valid_id(id)
        path = self.home / f"{id}.skill.md"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text)
        temporary.replace(path)
        entry = self.entry(id)
        self.bus.publish(SkillsChanged(id))
        return entry

    def limit(self, entry: SkillEntry) -> None:
        if entry.skill is None:
            raise ValueError("invalid skill cannot be enabled")
        count = sum(
            len(e.skill.signals)
            for e in self.entries()
            if e.id != entry.id and e.status == "active" and e.skill
        )
        if count + len(entry.skill.signals) > 12:
            raise SignalLimit("at most 12 active signals")

    def replace_approval(self, id: str, hash: str, *, enabled: bool) -> None:
        self.ledger.set_state(id, enabled=False, approved_hash=None, approved_at=None)
        if self.clear_readings is not None:
            self.clear_readings(id)
        self.ledger.clear(id)
        self.ledger.set_state(
            id,
            enabled=enabled,
            approved_hash=hash,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    def approve(self, id: str, hash: str) -> SkillEntry:
        entry = self.entry(id)
        if hash != entry.hash:
            raise StaleApproval(id)
        self.limit(entry)
        if not entry.approved:
            self.replace_approval(id, hash, enabled=True)
        else:
            self.toggle(id, True)
        self.bus.publish(SkillsChanged(id))
        return self.entry(id)

    def toggle(self, id: str, enabled: bool) -> None:
        state = self.ledger.state(id)
        assert state is not None
        self.ledger.set_state(
            id,
            enabled=enabled,
            approved_hash=cast(str | None, state["approved_hash"]),
            approved_at=cast(str | None, state["approved_at"]),
        )  # type: ignore[arg-type]

    def enable(self, id: str) -> SkillEntry:
        entry = self.entry(id)
        if not entry.approved:
            raise NeedsReview(id)
        self.limit(entry)
        self.toggle(id, True)
        self.bus.publish(SkillsChanged(id))
        return self.entry(id)

    def disable(self, id: str) -> SkillEntry:
        self.entry(id)
        self.toggle(id, False)
        self.bus.publish(SkillsChanged(id))
        return self.entry(id)

    def delete(self, id: str) -> None:
        entry = self.entry(id)
        if entry.source.origin == "builtin":
            self.disable(id)
            return
        self.toggle(id, False)
        if self.clear_readings:
            self.clear_readings(id)
        entry.source.path.unlink()
        self.ledger.clear(id, state=True)
        self.bus.publish(SkillsChanged(id))

    def eligible(self, entry: SkillEntry, tick: Tick) -> bool:
        if self.portal.now().capture not in ("running", "overlay"):
            return False
        try:
            latest = self.entry(entry.id)
        except KeyError:
            return False
        state = self.ledger.state(entry.id)
        return (
            latest.status == "active"
            and latest.hash == entry.hash
            and state is not None
            and (
                state["approved_at"] is None
                or tick.at >= datetime.fromisoformat(str(state["approved_at"]))
            )
        )

    async def react(self, tick: Tick) -> None:
        for entry in self.entries():
            if entry.skill is None or not self.eligible(entry, tick):
                continue
            skill = entry.skill
            # Entire history is needed to keep one key across a long continuous streak.
            history = [
                r
                for signal in skill.signals
                for r in self.portal.readings(
                    signal.id,
                    TimeRange(
                        datetime.min.replace(tzinfo=timezone.utc),
                        tick.at + timedelta(microseconds=1),
                    ),
                )
            ]
            actions = self.catalog.allowed(skill.allow, skill.id)
            for index, rule in enumerate(skill.rules):
                occurrence = match(rule, history, max_gap=300, now=tick.at)
                if (
                    occurrence is None
                    or not self.eligible(entry, tick)
                    or not self.ledger.claim(
                        skill.id, index, occurrence.key, tick.at, rule.cooldown
                    )
                ):
                    continue
                self.bus.publish(SkillFired(skill.id, index, occurrence.key))
                outcomes = []
                for step in rule.steps:
                    if not self.eligible(entry, tick):
                        outcomes.append(Outcome(step.action, False, "skill_inactive"))
                        break
                    try:
                        if step.action == "hook":
                            path = entry.source.path.with_name(f"{skill.id}.hook.py")
                            spec = importlib.util.spec_from_file_location(
                                f"daybook_builtin_{skill.id}", path
                            )
                            if (
                                entry.source.origin != "builtin"
                                or spec is None
                                or spec.loader is None
                            ):
                                raise ValueError("invalid hook source")
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                            function = getattr(module, str(step.arguments["function"]))
                            await asyncio.wait_for(
                                function(HookContext(self.portal, actions, occurrence)),
                                timeout=10,
                            )
                            outcomes.append(Outcome("hook", True))
                        else:
                            args = {
                                key: render(value, occurrence, history)
                                for key, value in step.arguments.items()
                            }
                            outcomes.append(await actions.run(step.action, **args))
                    except TimeoutError:
                        outcomes.append(Outcome(step.action, False, "hook_timeout"))
                    except Exception:
                        outcomes.append(Outcome(step.action, False, "step_failed"))
                self.ledger.finish(skill.id, index, occurrence.key, outcomes)


def render(
    value: object, occurrence: Occurrence, history: list[SignalReading]
) -> object:
    if not isinstance(value, str):
        return value
    if re.fullmatch(r"\$[a-zA-Z_][a-zA-Z0-9_]*", value):
        return occurrence.values.get(value[1:])

    def replace(match: re.Match[str]) -> str:
        name = match[1]
        if match[2]:
            held = streak(
                sorted(
                    (
                        r
                        for r in history
                        if r.signal.name == name and r.at <= occurrence.at
                    ),
                    key=lambda r: r.at,
                )
            )
            seconds = held[1] if held else 0
            return (
                f"{int(seconds // 60)} min" if seconds >= 60 else f"{int(seconds)} sec"
            )
        return str(occurrence.values.get(name, "unknown"))

    return re.sub(r"\$([a-zA-Z_][a-zA-Z0-9_]*)(\.for)?", replace, value)
