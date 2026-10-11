from __future__ import annotations

import hashlib
from dataclasses import replace
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
    SegmentAssignment,
    SignalId,
    SignalReading,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)
from daybook.mind import Mind
from daybook.model import ScriptedGateway
from daybook.sense import PrivacySettings, Screen, Sense
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
        (
            SegmentAssignment(
                (observation.id,), Start("Fix imports", "coding", "Repair tests")
            ),
        ),
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
    music = SimpleNamespace(current=MusicStatus("focus", "Cipher", True), calls=[])

    async def play_mood(mood, *, skill, min_dwell):
        music.calls.append((mood, skill, min_dwell))
        if mood not in catalog.moods:
            return Outcome("music.play_mood", False, "unknown_mood")
        music.current = MusicStatus(mood, "A reading track", True)
        return Outcome("music.play_mood", True)

    music.play_mood = play_mood
    music.status = lambda: music.current
    services = Services(
        journal,
        cast(Sense, sense),
        cast(Mind, mind),
        cast(SkillBook, book),
        cast(Chat, chat),
        ScriptedGateway([]),
        cast(MusicPlayer, music),
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
            music=music,
            observation=observation,
            time_range={
                "from": at.isoformat(),
                "to": (at + timedelta(minutes=5)).isoformat(),
            },
            tick=tick,
        )
    journal.close()


def test_real_timeline_now_and_note_edits_validate(live):
    client = live.client
    timeline = w.TimelineResponse.model_validate(
        client.get("/api/timeline", params=live.time_range).json()
    )
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
    timeline = client.get("/api/timeline", params=live.time_range).json()
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
    assert not client.get("/api/timeline", params=live.time_range).json()["segments"]
    assert not journal.portal.search("imports")


def test_skills_parse_save_review_and_delete_through_owner(live):
    client = live.client
    skills = w.SkillsResponse.model_validate(client.get("/api/skills").json())
    assert len(skills.catalog) == 10
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


def test_music_cli_route_uses_existing_player(live):
    client = live.client
    assert client.post("/api/music/play", json={"mood": "calm"}).status_code == 403
    assert not live.music.calls
    response = client.post("/api/music/play", headers=HEADERS, json={"mood": "calm"})
    assert response.status_code == 200
    status = w.MusicStatus.model_validate(response.json())
    assert status.mood == "calm" and status.playing
    assert live.music.calls == [("calm", "manual", 0)]
    assert client.get("/api/status").json()["music"] == response.json()
    assert (
        client.post("/api/music/play", headers=HEADERS, json={"mood": ""}).status_code
        == 422
    )
    refused = client.post("/api/music/play", headers=HEADERS, json={"mood": "missing"})
    assert refused.status_code == 409
    assert refused.json() == {"detail": "unknown_mood"}


def test_parse_of_builtin_override_does_not_inherit_trust(live):
    from dataclasses import replace

    entry = live.book.entries()[0]
    live.book.rows[entry.id] = replace(
        entry,
        source=SkillSource(Path("builtin") / f"{entry.id}.skill.md", "builtin"),
        approved=True,
        enabled=True,
        status="active",
    )
    response = live.client.post(
        "/api/skills/parse",
        headers=HEADERS,
        json={"id": entry.id, "text": entry.text},
    )
    preview = w.SkillParseResponse.model_validate(response.json())
    assert preview.valid and preview.entry.status == "needs_review"
    assert not preview.entry.approved
    assert live.book.rows[entry.id].approved


def test_persisted_skill_approval_and_external_edit_through_http(live, tmp_path):
    from dataclasses import replace

    def make_book():
        return SkillBook(
            tmp_path,
            Path("skills/builtin"),
            tmp_path / "skills.db",
            live.services.journal.portal,
            live.services.catalog,
            live.services.bus,
            clear_readings=live.services.journal.clear_skill_readings,
        )

    book = make_book()
    app = create_app(
        token="test-token",
        origin="http://testserver",
        services=replace(live.services, skills=book),
    )
    with TestClient(app) as client:
        initial = w.SkillsResponse.model_validate(client.get("/api/skills").json())
        assert {"music", "break", "break-demo"} <= {e.id for e in initial.entries}
        saved = client.put(
            "/api/skills/stand-after-meeting",
            headers=HEADERS,
            json={"text": w.DRAFT_TEXT},
        )
        entry = w.SkillResponse.model_validate(saved.json())
        assert entry.status == "needs_review" and entry.origin == "user"
        approved = client.post(
            "/api/skills/stand-after-meeting/approve",
            headers=HEADERS,
            json={"hash": entry.hash},
        )
        assert w.SkillResponse.model_validate(approved.json()).status == "active"
        reloaded = next(e for e in make_book().entries() if e.id == entry.id)
        assert reloaded.approved and reloaded.enabled
        path = tmp_path / "skills" / f"{entry.id}.skill.md"
        path.write_text(path.read_text() + "\nExternal edit.\n")
        current = next(
            e
            for e in w.SkillsResponse.model_validate(
                client.get("/api/skills").json()
            ).entries
            if e.id == entry.id
        )
        assert current.status == "needs_review" and not current.approved
        assert all(signal.id.skill != entry.id for signal in book.active_signals())
        assert (
            client.post(
                "/api/skills/stand-after-meeting/enable", headers=HEADERS
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/skills/stand-after-meeting/approve",
                headers=HEADERS,
                json={"hash": entry.hash},
            ).status_code
            == 409
        )
        assert (
            client.delete(
                "/api/skills/stand-after-meeting", headers=HEADERS
            ).status_code
            == 200
        )
        assert not path.exists()

        invalid = client.put(
            "/api/skills/invalid", headers=HEADERS, json={"text": "Invalid skill"}
        ).json()
        rejected = client.post(
            "/api/skills/invalid/approve",
            headers=HEADERS,
            json={"hash": invalid["hash"]},
        )
        assert rejected.status_code == 409
        assert rejected.json() == {"detail": "invalid_skill"}


@pytest.mark.parametrize("kind", ["segments", "observations"])
def test_repeated_note_delete_is_404_without_invalidating_work(live, kind):
    id = (
        live.client.get("/api/timeline", params=live.time_range).json()["segments"][0][
            "id"
        ]
        if kind == "segments"
        else live.observation.id
    )
    path = f"/api/{kind}/{id}"
    assert live.client.delete(path, headers=HEADERS).status_code == 200
    epoch = live.services.journal.epoch
    assert live.client.delete(path, headers=HEADERS).status_code == 404
    assert live.services.journal.epoch == epoch


@pytest.mark.parametrize("mock", [False, True])
def test_conditional_privacy_preserves_intervening_exclusion_without_writes(
    live, tmp_path, mock
):
    sense = Sense(
        cast(Screen, SimpleNamespace()),
        live.services.journal,
        live.services.gateway,
        live.services.bus,
        tmp_path / "privacy-home",
        lambda _: None,
    )
    services = replace(live.services, sense=sense)
    app = create_app(
        mock=mock, token="test-token", origin="http://testserver", services=services
    )
    fields = ("paused", "exclude_apps", "exclude_titles")
    with TestClient(app) as client:
        initial = {k: client.get("/api/privacy").json()[k] for k in fields}
        edited = {
            **initial,
            "exclude_titles": [*initial["exclude_titles"], "*private*"],
        }
        first = client.put(
            "/api/privacy", headers=HEADERS, json={**edited, "expected": initial}
        )
        assert first.status_code == 200
        assert "expected" not in first.json()
        generation = sense.generation
        path = sense.home / "privacy.json"
        persisted = (path.read_bytes(), path.stat().st_mtime_ns) if not mock else None
        status = client.get("/api/status").json()
        with live.services.bus.subscribe() as queue:
            conflict = client.put(
                "/api/privacy",
                headers=HEADERS,
                json={**initial, "paused": True, "expected": initial},
            )
            assert conflict.status_code == 409
            assert conflict.json() == {"detail": "privacy_conflict"}
            assert queue.empty()
        assert {k: client.get("/api/privacy").json()[k] for k in fields} == edited
        assert client.get("/api/status").json()["capture"] == status["capture"]
        assert sense.generation == generation
        assert live.overlay.hidden == []
        if persisted is not None:
            assert (path.read_bytes(), path.stat().st_mtime_ns) == persisted
        # Reapply the original pause operation to the new snapshot.
        retried = client.put(
            "/api/privacy",
            headers=HEADERS,
            json={**edited, "paused": True, "expected": edited},
        )
        assert retried.status_code == 200
        assert retried.json()["paused"] is True
        assert retried.json()["exclude_titles"] == edited["exclude_titles"]
        assert "expected" not in retried.json()
