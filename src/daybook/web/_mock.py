"""In-memory API backend for Lane B. No screen, model, or disk access."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from daybook import events
from daybook.act import OverlayRequest, build_catalog
from daybook.skills import Skill, SkillEntry, SkillSource, parse_skill

from . import wire as w


class MockAPI:
    def __init__(self, bus: events.Bus) -> None:
        self.bus = bus
        self.timeline = w.example(w.TimelineResponse)
        now = datetime.now(timezone.utc)
        segment = self.timeline.segments[0]
        segment.started_at = now - timedelta(minutes=4)
        segment.ended_at = now
        segment.observations[0].started_at = segment.started_at
        segment.observations[0].last_seen_at = now
        self.timeline.capture_spans[0].start = now - timedelta(minutes=9)
        self.timeline.capture_spans[0].end = segment.started_at
        self.privacy = w.example(w.PrivacyResponse)
        self.status = w.example(w.StatusResponse)
        self.catalog = build_catalog(w.MOCK_MOODS)
        self.entries: dict[str, w.SkillResponse] = {
            "stand-after-meeting": w.example(w.SkillResponse)
        }
        self.shells = 0
        self.tick_count = 0

    def get_timeline(
        self, start: datetime | None, end: datetime | None
    ) -> w.TimelineResponse:
        def overlaps(a: datetime | None, b: datetime | None) -> bool:
            return (
                a is not None
                and (end is None or a <= end)
                and (start is None or b is None or b >= start)
            )

        return w.TimelineResponse(
            segments=[
                s for s in self.timeline.segments if overlaps(s.started_at, s.ended_at)
            ],
            capture_spans=[
                s for s in self.timeline.capture_spans if overlaps(s.start, s.end)
            ],
        )

    def now(self) -> w.NowResponse:
        segment = next((s for s in reversed(self.timeline.segments) if s.open), None)
        return w.NowResponse(
            capture=self.status.capture,
            segment=segment,
            observation=segment.observations[-1]
            if segment and segment.observations
            else None,
            readings=[],
        )

    def segment(self, id: str) -> w.Segment:
        for segment in self.timeline.segments:
            if segment.id == id:
                return segment
        raise HTTPException(404, "segment_not_found")

    def observation(self, id: str) -> w.Observation:
        for segment in self.timeline.segments:
            for observation in segment.observations:
                if observation.id == id:
                    return observation
        raise HTTPException(404, "observation_not_found")

    def edit_segment(self, id: str, patch: w.SegmentPatch) -> w.Segment:
        segment = self.segment(id)
        for field, value in patch.model_dump(exclude_none=True).items():
            setattr(segment, field, value)
        segment.authored = "user"
        self.bus.publish(events.SegmentChanged(id))
        return segment

    def edit_observation(self, id: str, patch: w.ObservationPatch) -> w.Observation:
        observation = self.observation(id)
        for field, value in patch.model_dump(exclude_none=True).items():
            setattr(observation, field, value)
        self.bus.publish(events.SegmentChanged(observation.segment or ""))
        return observation

    def forget_segment(self, id: str) -> w.MutationResponse:
        self.segment(id)
        self.timeline.segments = [s for s in self.timeline.segments if s.id != id]
        self.bus.publish(events.SegmentChanged(id))
        return w.MutationResponse()

    def forget_observation(self, id: str) -> w.MutationResponse:
        observation = self.observation(id)
        if observation.segment:
            segment = self.segment(observation.segment)
            segment.observations = [o for o in segment.observations if o.id != id]
            self._shrink(segment)
        return w.MutationResponse()

    def _shrink(self, segment: w.Segment) -> None:
        if not segment.observations:
            self.timeline.segments.remove(segment)
        else:
            segment.started_at = min(o.started_at for o in segment.observations)
            segment.ended_at = max(o.last_seen_at for o in segment.observations)
            segment.active = sum(o.active for o in segment.observations)
            if segment.authored == "model":
                segment.title = "(summary removed after forget)"
                segment.intent = ""
                segment.kind = "other"
        self.bus.publish(events.SegmentChanged(segment.id))

    def forget(self, window: w.TimeWindow | None) -> w.MutationResponse:
        for segment in list(self.timeline.segments):
            before = len(segment.observations)
            segment.observations = [
                o
                for o in segment.observations
                if window is not None
                and not (o.started_at < window.end and o.last_seen_at >= window.start)
            ]
            if before != len(segment.observations):
                self._shrink(segment)
        return w.MutationResponse()

    def skills(self) -> w.SkillsResponse:
        return w.SkillsResponse(
            entries=list(self.entries.values()),
            active_signals=sum(
                len(e.signals) for e in self.entries.values() if e.status == "active"
            ),
            catalog=[
                w.ActionSpec.model_validate(s) for s in self.catalog.specs.values()
            ],
            moods=self.catalog.moods,
        )

    def parse(self, id: str, text: str) -> w.SkillParseResponse:
        if not re.fullmatch(r"[a-z0-9-]{1,40}", id):
            raise HTTPException(422, "invalid_skill_id")
        source = SkillSource(Path(f"{id}.skill.md"), "user")
        parsed = parse_skill(text, source=source, catalog=self.catalog)
        valid = isinstance(parsed, Skill)
        entry = SkillEntry(
            id,
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            source,
            parsed if valid else None,
            () if valid else tuple(parsed),
            False,
            False,
            "needs_review" if valid else "invalid",
        )
        result = w.skill_from_domain(entry)
        return w.SkillParseResponse(valid=valid, errors=result.errors, entry=result)

    def save(self, id: str, text: str) -> w.SkillResponse:
        entry = self.parse(id, text).entry
        old = self.entries.get(id)
        if old and old.hash == entry.hash:
            return old
        self.entries[id] = entry
        self.bus.publish(events.SkillsChanged(id))
        return entry

    def entry(self, id: str) -> w.SkillResponse:
        if id not in self.entries:
            raise HTTPException(404, "skill_not_found")
        return self.entries[id]

    def approve(self, id: str, hash: str) -> w.SkillResponse:
        entry = self.entry(id)
        if entry.hash != hash:
            raise HTTPException(409, "stale_approval")
        if entry.errors:
            raise HTTPException(409, "invalid_skill")
        self._limit(entry)
        entry.approved = entry.enabled = True
        entry.status = "active"
        self.bus.publish(events.SkillsChanged(id))
        return entry

    def _limit(self, entry: w.SkillResponse) -> None:
        count = len(entry.signals) + sum(
            len(e.signals)
            for e in self.entries.values()
            if e.id != entry.id and e.status == "active"
        )
        if count > 12:
            raise HTTPException(409, "active_signal_limit")

    def enable(self, id: str, enabled: bool) -> w.SkillResponse:
        entry = self.entry(id)
        if enabled and not entry.approved:
            raise HTTPException(409, "needs_review")
        if enabled:
            self._limit(entry)
        entry.enabled = enabled
        entry.status = (
            "invalid"
            if entry.errors
            else "active"
            if enabled
            else "disabled"
            if entry.approved
            else "needs_review"
        )
        self.bus.publish(events.SkillsChanged(id))
        return entry

    def delete_skill(self, id: str) -> w.MutationResponse:
        self.entry(id)
        del self.entries[id]
        self.bus.publish(events.SkillsChanged(id))
        return w.MutationResponse()

    def chat(
        self, message: str
    ) -> w.AnswerResponse | w.DraftResponse | w.PlainResponse:
        if message.startswith("/skill") or any(
            word in message.lower()
            for word in ("remind", "create a skill", "make a skill")
        ):
            entry = self.save("stand-after-meeting", w.DRAFT_TEXT)
            return w.DraftResponse(
                entry=entry, summary="Show a reminder when a meeting ends."
            )
        if message.startswith("/ask") or any(
            word in message.lower() for word in ("what", "today", "hour", "minutes")
        ):
            now = datetime.now(timezone.utc)
            text = " ".join(
                f"You spent {s.active / 60:g} minutes on {s.title}."
                for s in self.timeline.segments
            )
            return w.AnswerResponse(
                text=(text or "I don't have notes for that.")
                + " There are no notes for paused intervals.",
                cites=[s.id for s in self.timeline.segments],
                window=w.TimeWindow(start=now - timedelta(hours=1), end=now),
            )
        return w.example(w.PlainResponse)

    def set_privacy(self, body: w.PrivacyPut) -> w.PrivacyResponse:
        self.privacy = w.PrivacyResponse(**body.model_dump())
        next_state = "paused" if body.paused else "running"
        if next_state != self.status.capture:
            now = datetime.now(timezone.utc)
            if (
                self.timeline.capture_spans
                and self.timeline.capture_spans[-1].end is None
            ):
                self.timeline.capture_spans[-1].end = now
            self.timeline.capture_spans.append(
                w.CaptureSpan(state=next_state, start=now, end=None)
            )
        self.status.capture = next_state
        if body.paused and self.status.overlay:
            self.dismiss(self.status.overlay.id)
        self.bus.publish(events.CaptureStateChanged(self.status.capture))
        return self.privacy

    def overlay_test(self) -> w.OverlayState:
        if self.shells == 0:
            raise HTTPException(409, "shell_disconnected")
        now = datetime.now(timezone.utc)
        request = w.OverlayRequest(
            title="Take a moment", body="This is a Daybook overlay test.", minutes=1
        )
        self.status.overlay = w.OverlayState(
            id=f"test-{now.timestamp()}",
            request=request,
            owner="manual",
            expires_at=now + timedelta(minutes=1),
        )
        self.bus.publish(
            events.OverlayShown(
                self.status.overlay.id, OverlayRequest(**request.model_dump())
            )
        )
        return self.status.overlay

    def dismiss(self, id: str) -> w.MutationResponse:
        if self.status.overlay and self.status.overlay.id == id:
            self.status.overlay = None
            self.bus.publish(events.OverlayHidden(id))
        return w.MutationResponse()

    def tick(self) -> w.TickResponse:
        if self.privacy.paused:
            return w.TickResponse(recorded=False, tick=None)
        self.tick_count += 1
        id = f"tick-{self.tick_count}"
        self.bus.publish(events.TickRecorded(id))
        return w.TickResponse(recorded=True, tick=id)

    async def script(self) -> None:
        phase = 0
        while True:
            await asyncio.sleep(3)
            self.status.shell_connected = self.shells > 0
            if not self.privacy.paused:
                if phase % 6 == 0:
                    for s in self.timeline.segments:
                        self.bus.publish(events.SegmentChanged(s.id))
                elif phase % 6 == 1:
                    self.tick()
                elif phase % 6 == 2:
                    self.status.music = w.MusicStatus(
                        mood="calm", title="Evening reading", playing=True
                    )
                    self.bus.publish(events.MusicChanged("calm", "Evening reading"))
                elif phase % 6 == 3 and self.shells:
                    request = w.example(w.OverlayRequest)
                    self.status.overlay = w.OverlayState(
                        id="overlay-demo",
                        request=request,
                        owner="break-demo",
                        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3),
                    )
                    self.bus.publish(
                        events.OverlayShown(
                            "overlay-demo", OverlayRequest(**request.model_dump())
                        )
                    )
                elif phase % 6 == 4:
                    self.dismiss("overlay-demo")
                else:
                    self.bus.publish(events.StatusChanged("model", "ready"))
            phase += 1
