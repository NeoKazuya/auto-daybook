"""Thin adapters from the existing HTTP contract to each state owner."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from daybook.act import OverlayRequest
from daybook.chat import Answer, DraftReady
from daybook.journal import TimeRange
from daybook.sense import PrivacySettings
from daybook.skills import Skill, SkillEntry, SkillSource

from . import wire as w

if TYPE_CHECKING:
    from . import Services


def _local_now() -> datetime:
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        try:
            return datetime.now(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        with Path("/etc/localtime").open("rb") as source:
            zone = ZoneInfo.from_file(source)
        return datetime.now(zone)
    except (OSError, ValueError):
        return datetime.now(UTC)


class LiveAPI:
    def __init__(self, services: Services):
        self.services = services
        self._shells = 0

    @property
    def shells(self) -> int:
        return self._shells

    @shells.setter
    def shells(self, count: int) -> None:
        self._shells = max(0, count)
        self.services.overlay.shell_connected(self._shells > 0)

    def get_timeline(
        self, start: datetime | None, end: datetime | None
    ) -> w.TimelineResponse:
        end = end or _local_now()
        start = start or end.replace(hour=0, minute=0, second=0, microsecond=0)
        if start >= end:
            raise ValueError("invalid_time_range")
        window = TimeRange(start.astimezone(UTC), end.astimezone(UTC))
        portal = self.services.journal.portal
        return w.TimelineResponse(
            segments=[w.Segment.model_validate(s) for s in portal.segments(window)],
            capture_spans=[
                w.CaptureSpan.model_validate(s) for s in portal.capture_spans(window)
            ],
        )

    def now(self) -> w.NowResponse:
        situation = self.services.journal.portal.now()
        return w.NowResponse(
            capture=situation.capture,
            segment=w.Segment.model_validate(situation.segment)
            if situation.segment
            else None,
            observation=w.Observation.model_validate(situation.observation)
            if situation.observation
            else None,
            readings=[
                w.SignalReading(
                    signal=str(r.signal), value=r.value, at=r.at, tick=r.tick
                )
                for r in situation.readings
            ],
        )

    def edit_segment(self, id: str, body: w.SegmentPatch) -> w.Segment:
        return w.Segment.model_validate(
            self.services.journal.edit_segment(
                id, title=body.title, kind=body.kind, intent=body.intent
            )
        )

    def edit_observation(self, id: str, body: w.ObservationPatch) -> w.Observation:
        return w.Observation.model_validate(
            self.services.journal.edit_observation(
                id, seen=body.seen, visible_text=body.visible_text
            )
        )

    def _mutation(self) -> w.MutationResponse:
        return w.MutationResponse(
            deletion_pending=self.services.journal.deletion_pending
        )

    def forget_segment(self, id: str) -> w.MutationResponse:
        self.services.journal.forget_segment(id)
        return self._mutation()

    def forget_observation(self, id: str) -> w.MutationResponse:
        self.services.journal.forget_observation(id)
        return self._mutation()

    def forget(self, window: w.TimeWindow | None) -> w.MutationResponse:
        if window:
            self.services.journal.forget_range(TimeRange(window.start, window.end))
        else:
            self.services.journal.forget_all()
        return self._mutation()

    async def chat(
        self, message: str
    ) -> w.AnswerResponse | w.DraftResponse | w.PlainResponse:
        reply = await self.services.chat.respond(message)
        if isinstance(reply, Answer):
            return w.AnswerResponse(
                text=reply.text,
                cites=list(reply.cites),
                window=w.TimeWindow(start=reply.window.start, end=reply.window.end),
            )
        if isinstance(reply, DraftReady):
            return w.DraftResponse(
                entry=w.skill_from_domain(reply.entry), summary=reply.summary
            )
        return w.PlainResponse(text=reply.text)

    def skills(self) -> w.SkillsResponse:
        return w.SkillsResponse(
            entries=[w.skill_from_domain(e) for e in self.services.skills.entries()],
            active_signals=len(self.services.skills.active_signals()),
            catalog=[
                w.ActionSpec.model_validate(s)
                for s in self.services.catalog.specs.values()
            ],
            moods=self.services.catalog.moods,
        )

    def parse(self, id: str, text: str) -> w.SkillParseResponse:
        book = self.services.skills
        parsed = book.parse(id, text)
        original = next((e for e in book.entries() if e.id == id), None)
        source = (
            original.source if original else SkillSource(Path(f"{id}.skill.md"), "user")
        )
        digest = hashlib.sha256(text.encode()).hexdigest()
        valid = isinstance(parsed, Skill)
        approved = bool(
            original and original.hash == digest and original.approved and valid
        )
        enabled = bool(approved and original and original.enabled)
        entry = SkillEntry(
            id,
            text,
            digest,
            source,
            parsed if valid else None,
            () if valid else tuple(parsed),
            enabled,
            approved,
            "invalid"
            if not valid
            else "active"
            if enabled
            else "disabled"
            if approved
            else "needs_review",
        )
        result = w.skill_from_domain(entry)
        return w.SkillParseResponse(valid=valid, errors=result.errors, entry=result)

    def save(self, id: str, text: str) -> w.SkillResponse:
        return w.skill_from_domain(self.services.skills.save(id, text))

    def approve(self, id: str, hash: str) -> w.SkillResponse:
        return w.skill_from_domain(self.services.skills.approve(id, hash))

    def enable(self, id: str, enabled: bool) -> w.SkillResponse:
        method = (
            self.services.skills.enable if enabled else self.services.skills.disable
        )
        return w.skill_from_domain(method(id))

    def delete_skill(self, id: str) -> w.MutationResponse:
        self.services.skills.delete(id)
        return self._mutation()

    @property
    def privacy(self) -> w.PrivacyResponse:
        privacy = self.services.sense.privacy
        return w.PrivacyResponse(
            paused=privacy.paused,
            exclude_apps=list(privacy.exclude_apps),
            exclude_titles=list(privacy.exclude_titles),
            keep_frames=self.services.keep_frames,
            screenshots_stored="debug" if self.services.keep_frames else "none",
            deletion_pending=self.services.journal.deletion_pending,
        )

    async def set_privacy(self, body: w.PrivacyPut) -> w.PrivacyResponse:
        self.services.sense.set_privacy(
            PrivacySettings(
                body.paused, tuple(body.exclude_apps), tuple(body.exclude_titles)
            )
        )
        if body.paused:
            await self.services.overlay.hide()
        return self.privacy

    async def overlay_test(self) -> w.OverlayState:
        result = await self.services.overlay.show(
            OverlayRequest(
                "Take a moment", "This is a Daybook overlay test.", minutes=1
            ),
            owner="manual",
        )
        if not result.ok:
            raise HTTPException(409, result.detail or "overlay_unavailable")
        state = self.services.overlay.state
        if state is None:
            raise HTTPException(409, "overlay_unavailable")
        return w.OverlayState.model_validate(state)

    async def dismiss(self, id: str) -> w.MutationResponse:
        result = await self.services.overlay.hide(id)
        return w.MutationResponse(ok=result.ok)

    @property
    def status(self) -> w.StatusResponse:
        overlay = self.services.overlay.state
        return w.StatusResponse(
            gateway=w.GatewayStatus.model_validate(self.services.gateway.status()),
            music=w.MusicStatus.model_validate(self.services.music.status()),
            overlay=w.OverlayState.model_validate(overlay) if overlay else None,
            capture=self.services.journal.portal.now().capture,
            shell_connected=self.shells > 0,
            mock=False,
        )

    async def tick(self) -> w.TickResponse:
        tick = await self.services.mind.tick_now()
        return w.TickResponse(recorded=tick is not None, tick=tick.id if tick else None)
