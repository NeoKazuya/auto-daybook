"""Observation continuity and consent: no queued image, no stale write."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from daybook.events import Bus, StatusChanged
from daybook.journal import CaptureState, Journal, Observation, ObservationDraft
from daybook.model import (
    ModelGateway,
    ObserveReplaced,
    Priority,
    Prompt,
)

from ._gate import black, changed, gate
from ._types import PrivacySettings, Screen, ScreenState


class _Observed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seen: str = Field(min_length=1, max_length=1000)
    visible_text: str = Field(max_length=300)


class Sense:
    def __init__(
        self,
        screen: Screen,
        journal: Journal,
        gateway: ModelGateway,
        bus: Bus,
        home: Path,
        nudge: Callable[[bool], None],
    ) -> None:
        self.screen, self.journal, self.gateway, self.bus = (
            screen,
            journal,
            gateway,
            bus,
        )
        self.home, self.nudge = home, nudge
        path = home / "privacy.json"
        if path.exists():
            data = json.loads(path.read_text())
            self._privacy = PrivacySettings(
                bool(data.get("paused", False)),
                tuple(data.get("exclude_apps", PrivacySettings().exclude_apps)),
                tuple(data.get("exclude_titles", PrivacySettings().exclude_titles)),
            )
        else:
            self._privacy = PrivacySettings()
        self._generation = 0
        self._epoch = journal.epoch
        self._last_state: CaptureState | None = None
        self._observation: Observation | None = None
        self._last_thumb: bytes | None = None
        self._last_window = None
        self._pending_window = False
        self._switch = 0
        self._notified_switch = 0
        self._last_request: datetime | None = None
        self._last_poll: datetime | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._continuity = 0
        self._sequence = 0
        self._closed = False

    @property
    def privacy(self) -> PrivacySettings:
        return self._privacy

    @property
    def generation(self) -> int:
        return self._generation

    def set_privacy(self, settings: PrivacySettings) -> PrivacySettings:
        if settings == self._privacy:
            return settings
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / "privacy.json"
        temporary = self.home / ".privacy.json.tmp"
        with open(temporary, "w", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(asdict(settings), output)
        temporary.replace(path)
        self._privacy = settings
        self._generation += 1
        self.gateway.drop_observe()
        self._continuity += 1
        self._observation = None
        self._last_thumb = None
        if settings.paused:
            self._state("paused", datetime.now(timezone.utc))
        self.bus.publish(StatusChanged("sense", "privacy_changed"))
        return settings

    def _state(self, state: CaptureState, at: datetime) -> None:
        self.journal.set_capture_state(state, at)
        self._last_state = state

    def _gap(
        self, state: CaptureState, at: datetime, facts: ScreenState | None = None
    ) -> None:
        if state == "away" and self._last_state != "away" and facts:
            at -= timedelta(milliseconds=facts.idle_ms)
            self.journal.trim_after(at)
            self._epoch = self.journal.epoch
        if self._last_state == "running" or self._observation is not None:
            self._continuity += 1
        self._observation = None
        self._last_thumb = None
        self._last_window = None
        self._state(state, at)

    def _failed(
        self, at: datetime, sequence: int, status: str, *, captured: bool = False
    ) -> None:
        self.bus.publish(StatusChanged("sense", status))
        # A replaced builder had no pixels and a newer trigger supersedes it.
        if not captured and (
            sequence != self._sequence or self._last_state != "running"
        ):
            return
        self.journal.trim_after(at)
        self._epoch = self.journal.epoch
        self._continuity += 1
        self._observation = None
        self._last_thumb = None
        if self._last_state == "running":
            self._state("withheld", at)

    async def poll(self, at: datetime | None = None) -> None:
        if self._closed:
            return
        at = at or datetime.now(timezone.utc)
        self._last_poll = at
        facts = self.screen.state()
        state = gate(facts, self._privacy)
        if state != "running":
            self._gap(state, at, facts)
            return
        try:
            thumbnail = self.screen.thumbnail()
        except Exception:
            self._gap("locked", at)
            self.bus.publish(StatusChanged("sense", "screen_unavailable"))
            return
        if black(thumbnail):
            self._gap("locked", at)
            return
        epoch_changed = self._epoch != self.journal.epoch
        resumed = self._last_state != "running" or epoch_changed
        if epoch_changed:
            self._epoch = self.journal.epoch
            self._continuity += 1
            self._observation = None
            self._last_thumb = None
        if not resumed and self._observation:
            self.journal.extend_observation(self._observation.id, at)
        self._state("running", at)
        window_changed = self._last_window != facts.active
        app_switched = (
            self._last_window is not None
            and facts.active is not None
            and self._last_window.app != facts.active.app
        )
        self._last_window = facts.active
        self._pending_window |= window_changed
        if app_switched:
            self._switch += 1
        pending_switch = self._switch > self._notified_switch
        new = (
            resumed
            or self._pending_window
            or self._last_thumb is None
            or changed(self._last_thumb, thumbnail)
        )
        due = (
            self._last_request is None
            or (at - self._last_request).total_seconds() >= 10
        )
        if new and (due or app_switched or resumed):
            self._pending_window = False
            self._last_request = at
            self._last_thumb = thumbnail
            self._sequence += 1
            task = asyncio.create_task(
                self._observe(
                    at,
                    facts,
                    pending_switch,
                    self._switch,
                    self.journal.epoch,
                    self._sequence,
                    self._continuity,
                    self._generation,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _observe(
        self,
        at: datetime,
        trigger: ScreenState,
        app_switched: bool,
        switch: int,
        epoch: int,
        sequence: int,
        continuity: int,
        consent: int,
    ) -> None:
        window = trigger.active
        built = False
        admitted = False

        async def builder() -> Prompt[_Observed] | None:
            nonlocal window, built, admitted
            before = self.screen.state()
            if (
                epoch != self.journal.epoch
                or consent != self._generation
                or continuity != self._continuity
                or gate(before, self._privacy) != "running"
            ):
                return None
            admitted = True
            window = before.active
            image = self.screen.grab()
            after = self.screen.state()
            if (
                epoch != self.journal.epoch
                or consent != self._generation
                or gate(after, self._privacy) != "running"
                or before.active != after.active
                or before.windows != after.windows
            ):
                return None
            built = True
            return Prompt(
                "Describe only what is visible in one or two sentences. Do not infer goals, intentions, or feelings. visible_text is at most 300 characters of salient legible text. Window facts are authoritative.",
                f"Active application: {window.app if window else ''}\nWindow title: {window.title if window else ''}",
                _Observed,
                (image,),
                max_tokens=400,
            )

        try:
            result = await self.gateway.call(Priority.OBSERVE, builder)
        except ObserveReplaced:
            if epoch != self.journal.epoch:
                self.bus.publish(StatusChanged("sense", "discarded_after_forget"))
                return
            self._failed(at, sequence, "observe_replaced")
            return
        except Exception:
            if epoch != self.journal.epoch:
                self.bus.publish(StatusChanged("sense", "discarded_after_forget"))
                return
            self._failed(at, sequence, "observe_failed", captured=admitted)
            return
        if epoch != self.journal.epoch:
            self.bus.publish(StatusChanged("sense", "discarded_after_forget"))
            return
        if consent != self._generation or continuity != self._continuity:
            self._failed(at, sequence, "discarded_after_consent", captured=admitted)
            return
        if result is None or not built or window is None:
            self._failed(at, sequence, "frame_discarded", captured=admitted)
            return
        current = self.screen.state()
        state = gate(current, self._privacy)
        if state != "running":
            self._gap(state, self._last_poll or at, current)
            self.bus.publish(StatusChanged("sense", "frame_discarded"))
            return
        observation = self.journal.add_observation(
            ObservationDraft(at, window, result.seen, result.visible_text), epoch
        )
        if observation is None:
            self._failed(at, sequence, "discarded_after_forget", captured=admitted)
            return
        self._observation = observation
        self._epoch = self.journal.epoch
        if self._last_poll and self._last_poll > at and self._last_state == "running":
            self.journal.extend_observation(observation.id, self._last_poll)
        if app_switched:
            self._notified_switch = max(self._notified_switch, switch)
        self.nudge(app_switched)

    async def wait_idle(self) -> None:
        """Wait for scheduled observations; useful after a single explicit poll."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def close(self) -> None:
        """Cancel only our tasks; the app owns the shared gateway and journal."""
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def run(self) -> None:
        try:
            while not self._closed:
                await self.poll()
                await asyncio.sleep(5)
        finally:
            await self.close()
