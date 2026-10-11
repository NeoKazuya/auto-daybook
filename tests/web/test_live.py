from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from daybook.act import MusicPlayer, MusicStatus, Overlay, OverlayState, build_catalog
from daybook.chat import Answer, Chat, DraftReady, Plain
from daybook.events import Bus
from daybook.journal import (
    Journal,
    ObservationDraft,
    Outcome,
    SignalId,
    SignalReading,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)
from daybook.mind import Mind
from daybook.model import ScriptedGateway
from daybook.sense import PrivacySettings, Sense
from daybook.skills import (
    NeedsReview,
    Skill,
    SkillBook,
    SkillEntry,
    SkillSource,
    StaleApproval,
    parse_skill,
)
from daybook.web import Services, create_app
from daybook.web import wire as w

HEADERS = {"Origin": "http://testserver", "X-Daybook-Token": "test-token"}


class Book:
    def __init__(self, catalog):
        self.catalog = catalog
        self.rows = {}

    def entries(self):
        return list(self.rows.values())

    def active_signals(self):
        return tuple(
            signal
            for entry in self.rows.values()
            if entry.enabled and entry.skill
            for signal in entry.skill.signals
        )

    def parse(self, id, text):
        return parse_skill(
            text,
            source=SkillSource(Path(f"{id}.skill.md"), "user"),
            catalog=self.catalog,
        )

    def save(self, id, text):
        parsed = self.parse(id, text)
        valid = isinstance(parsed, Skill)
        self.rows[id] = SkillEntry(
            id,
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            SkillSource(Path(f"{id}.skill.md"), "user"),
            parsed if valid else None,
            () if valid else tuple(parsed),
            False,
            False,
            "needs_review" if valid else "invalid",
        )
        return self.rows[id]

    def approve(self, id, hash):
        from dataclasses import replace

        entry = self.rows[id]
        if hash != entry.hash:
            raise StaleApproval
        self.rows[id] = replace(entry, approved=True, enabled=True, status="active")
        return self.rows[id]

    def enable(self, id):
        from dataclasses import replace

        entry = self.rows[id]
        if not entry.approved:
            raise NeedsReview
        self.rows[id] = replace(entry, enabled=True, status="active")
        return self.rows[id]

    def disable(self, id):
        from dataclasses import replace

        self.rows[id] = replace(self.rows[id], enabled=False, status="disabled")
        return self.rows[id]

    def delete(self, id):
        del self.rows[id]


@pytest.fixture
def live(tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    at = datetime.now(UTC) - timedelta(minutes=10)
    journal.set_capture_state("running", at)
    observation = journal.add_observation(
        ObservationDraft(at, WindowInfo("code", "tests.py"), "Fixing imports"),
        journal.epoch,
    )
    assert observation is not None
    journal.extend_observation(observation.id, at + timedelta(minutes=5))
    tick = Tick(
        at + timedelta(minutes=5),
        journal.epoch,
        at + timedelta(minutes=5),
        (observation.id,),
        Start("Fix imports", "coding", "Repair tests"),
        (
            SignalReading(
                SignalId("music", "mood"), "focus", at + timedelta(minutes=5), ""
            ),
        ),
    )
    assert journal.record_tick(tick)
    catalog = build_catalog(
        {
            "focus": "Focused work",
            "calm": "Reading",
            "break": "Rest",
            "energetic": "Activity",
        }
    )
    book = Book(catalog)
    book.save("stand-after-meeting", w.DRAFT_TEXT)
    sense = SimpleNamespace(privacy=PrivacySettings())

    def set_privacy(settings):
        sense.privacy = settings
        journal.set_capture_state(
            "paused" if settings.paused else "running", datetime.now(UTC)
        )
        return settings

    sense.set_privacy = set_privacy
    overlay = SimpleNamespace(state=None, connected=False, hidden=[])

    def shell_connected(value):
        overlay.connected = value

    async def hide(id=None):
        overlay.hidden.append(id)
        overlay.state = None
        return Outcome("overlay.hide", True)

    async def show(request, owner):
        if not overlay.connected:
            return Outcome("overlay.show", False, "shell_disconnected")
        overlay.state = OverlayState(
            "test-overlay",
            request,
            owner,
            (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        )
        return Outcome("overlay.show", True)

    overlay.show = show
    overlay.hide = hide
    overlay.shell_connected = shell_connected
    chat = SimpleNamespace(reply=Plain("Hello"), messages=[])

    async def respond(message):
        chat.messages.append(message)
        return chat.reply

    chat.respond = respond
    mind = SimpleNamespace(calls=0)

    async def tick_now():
        mind.calls += 1
        return tick

    mind.tick_now = tick_now
    services = Services(
        journal,
        cast(Sense, sense),
        cast(Mind, mind),
        cast(SkillBook, book),
        cast(Chat, chat),
        ScriptedGateway([]),
        cast(
            MusicPlayer,
            SimpleNamespace(status=lambda: MusicStatus("focus", "Cipher", True)),
        ),
        cast(Overlay, overlay),
        bus,
        catalog,
    )
    app = create_app(token="test-token", origin="http://testserver", services=services)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            services=services,
            book=book,
            sense=sense,
            overlay=overlay,
            chat=chat,
            mind=mind,
            observation=observation,
            tick=tick,
        )
    journal.close()


def test_real_timeline_now_and_note_edits_validate(live):
    client = live.client
    timeline = w.TimelineResponse.model_validate(client.get("/api/timeline").json())
    assert len(timeline.segments) == 1
    segment = timeline.segments[0]
    assert segment.active == 300
    now = w.NowResponse.model_validate(client.get("/api/now").json())
    assert now.readings[0].signal == "music.mood"
    changed = client.patch(
        f"/api/segments/{segment.id}",
        headers=HEADERS,
        json={"title": "My corrected summary"},
    )
    assert changed.status_code == 200
    assert w.Segment.model_validate(changed.json()).authored == "user"
    changed = client.patch(
        f"/api/observations/{live.observation.id}",
        headers=HEADERS,
        json={"seen": "Corrected evidence"},
    )
    assert w.Observation.model_validate(changed.json()).seen == "Corrected evidence"
    assert (
        client.patch(
            "/api/segments/missing", headers=HEADERS, json={"title": "x"}
        ).status_code
        == 404
    )
    assert client.get("/api/timeline?from=2026-01-01&to=2025-01-01").status_code == 422


@pytest.mark.parametrize("operation", ["observation", "segment", "range", "all"])
def test_real_forget_removes_database_notes_and_search(live, operation):
    journal = live.services.journal
    client = live.client
    timeline = client.get("/api/timeline").json()
    if operation == "observation":
        response = client.delete(
            f"/api/observations/{live.observation.id}", headers=HEADERS
        )
    elif operation == "segment":
        response = client.delete(
            f"/api/segments/{timeline['segments'][0]['id']}", headers=HEADERS
        )
    elif operation == "range":
        response = client.post(
            "/api/forget",
            headers=HEADERS,
            json={
                "from": (datetime.now(UTC) - timedelta(minutes=15)).isoformat(),
                "to": datetime.now(UTC).isoformat(),
            },
        )
    else:
        response = client.post("/api/forget-all", headers=HEADERS)
    assert response.status_code == 200
    assert w.MutationResponse.model_validate(response.json()).ok
    assert not client.get("/api/timeline").json()["segments"]
    assert not journal.portal.search("imports")


def test_skills_parse_save_review_and_delete_through_owner(live):
    client = live.client
    skills = w.SkillsResponse.model_validate(client.get("/api/skills").json())
    assert len(skills.catalog) == 8
    parse = client.post(
        "/api/skills/parse",
        headers=HEADERS,
        json={"id": "stand-after-meeting", "text": w.DRAFT_TEXT},
    )
    assert w.SkillParseResponse.model_validate(parse.json()).valid
    assert (
        client.post(
            "/api/skills/stand-after-meeting/enable", headers=HEADERS
        ).status_code
        == 409
    )
    saved = client.put(
        "/api/skills/stand-after-meeting", headers=HEADERS, json={"text": w.DRAFT_TEXT}
    )
    entry = w.SkillResponse.model_validate(saved.json())
    assert (
        client.post(
            "/api/skills/stand-after-meeting/approve",
            headers=HEADERS,
            json={"hash": "stale"},
        ).status_code
        == 409
    )
    approved = client.post(
        "/api/skills/stand-after-meeting/approve",
        headers=HEADERS,
        json={"hash": entry.hash},
    )
    assert w.SkillResponse.model_validate(approved.json()).status == "active"
    assert (
        client.post("/api/skills/stand-after-meeting/disable", headers=HEADERS).json()[
            "status"
        ]
        == "disabled"
    )
    assert (
        client.post("/api/skills/stand-after-meeting/enable", headers=HEADERS).json()[
            "status"
        ]
        == "active"
    )
    assert (
        client.delete("/api/skills/stand-after-meeting", headers=HEADERS).status_code
        == 200
    )
    assert not client.get("/api/skills").json()["entries"]


def test_chat_all_reply_shapes_and_private_mutations(live):
    client = live.client
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 403
    assert not live.chat.messages
    assert client.post(
        "/api/chat", headers=HEADERS, json={"message": "hello"}
    ).json() == {"type": "plain", "text": "Hello"}
    live.chat.reply = Answer(
        "Your notes",
        ("s1",),
        TimeRange(datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC)),
    )
    assert w.AnswerResponse.model_validate(
        client.post("/api/chat", headers=HEADERS, json={"message": "last hour"}).json()
    ).cites == ["s1"]
    live.chat.reply = DraftReady(live.book.entries()[0], "A reminder")
    assert (
        w.DraftResponse.model_validate(
            client.post(
                "/api/chat", headers=HEADERS, json={"message": "remind me"}
            ).json()
        ).entry.status
        == "needs_review"
    )
    body = {"paused": True, "exclude_apps": ["Secrets"], "exclude_titles": []}
    privacy = client.put("/api/privacy", headers=HEADERS, json=body)
    assert w.PrivacyResponse.model_validate(privacy.json()).paused
    assert live.overlay.hidden == [None]
    assert client.get("/api/privacy").json()["screenshots_stored"] == "none"
    status = w.StatusResponse.model_validate(client.get("/api/status").json())
    assert status.capture == "paused" and not status.mock
    assert client.post("/api/overlay/stale/dismiss", headers=HEADERS).json()["ok"]
    assert client.post("/api/tick", headers=HEADERS).json() == {
        "recorded": True,
        "tick": live.tick.id,
    }
    assert live.mind.calls == 1


def test_overlay_test_requires_auth_and_connected_shell(live):
    from daybook.web._live import LiveAPI

    client = live.client
    assert client.post("/api/overlay/test").status_code == 403
    missing = client.post("/api/overlay/test", headers=HEADERS)
    assert missing.status_code == 409
    assert missing.json() == {"detail": "shell_disconnected"}
    backend = LiveAPI(live.services)
    backend.shells += 1
    assert live.overlay.connected
    response = client.post("/api/overlay/test", headers=HEADERS)
    assert response.status_code == 200
    state = w.OverlayState.model_validate(response.json())
    assert state.owner == "manual"
    assert state.request.minutes == 1
    assert client.get("/api/status").json()["overlay"]["id"] == state.id
    backend.shells -= 1
    assert not live.overlay.connected
    assert "/api/overlay/test" in client.get("/openapi.json").json()["paths"]
