"""The Upcoming list: journal storage, extraction, tick facts, skills, chat, API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from daybook.act import build_catalog
from daybook.chat import Chat, DraftReady, Plain
from daybook.events import Bus
from daybook.journal import (
    Journal,
    ObservationDraft,
    SegmentAssignment,
    SignalId,
    Start,
    Tick,
    TimeRange,
    UpcomingDraft,
    WindowInfo,
    due_phrase,
)
from daybook.mind import Mind
from daybook.mind._upcoming import dated, extract
from daybook.model import Priority, ScriptedGateway
from daybook.skills import (
    Occurrence,
    Signal,
    Skill,
    SkillBook,
    SkillSource,
    parse_skill,
)
from daybook.skills._book import render
from daybook.web import create_app
from daybook.web import wire as w

NOW = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
CATALOG = build_catalog({"focus": "coding", "break": "not working"})
BUILTIN = Path(__file__).resolve().parents[1] / "skills/builtin"


@pytest.fixture
def journal(tmp_path: Path):
    value = Journal(tmp_path / "journal.db", Bus())
    yield value
    value.close()


def note(journal: Journal, text: str, at: datetime = NOW, title: str = "Calendar"):
    added = journal.add_observation(
        ObservationDraft(at, WindowInfo("firefox", title), text), journal.epoch
    )
    assert added is not None
    return added


def parse(text: str) -> Skill | list:
    return parse_skill(
        text, source=SkillSource(Path("nudge.skill.md"), "user"), catalog=CATALOG
    )


SKILL = """---
id: nudge
name: Nudge
reads: [upcoming]
signals:
  drifting:
    ask: Is the user off task while something on the Upcoming list is due within 3 hours?
    type: bool
rules:
  - when: drifting is true for 1m
    do:
      - overlay.show: {title: Due soon, body: "$upcoming.next. Back to it?", layout: bubble}
allow: [overlay.show]
---
Nudge me back before a deadline.
"""


# Journal


def test_items_dedupe_by_title_and_due_and_sort_soonest_first(journal):
    due = NOW + timedelta(hours=3)
    first = journal.add_upcoming(
        [UpcomingDraft("CSE 130  Assignment 3", due)], journal.epoch, at=NOW
    )
    again = journal.add_upcoming(
        [
            UpcomingDraft("cse 130 assignment 3!", due),
            UpcomingDraft("Exam", NOW + timedelta(hours=1)),
        ],
        journal.epoch,
        at=NOW,
    )
    assert [i.title for i in first] == ["CSE 130 Assignment 3"]
    assert [i.title for i in again] == ["Exam"]
    assert [i.title for i in journal.portal.upcoming(NOW)] == [
        "Exam",
        "CSE 130 Assignment 3",
    ]
    # Past items leave the default list but remain within an overdue window.
    later = NOW + timedelta(hours=2)
    assert [i.title for i in journal.portal.upcoming(later)] == ["CSE 130 Assignment 3"]
    assert len(journal.portal.upcoming(later, overdue=3600 * 2)) == 2


def test_dismissed_item_is_not_re_added_from_the_same_screen(journal):
    source = note(journal, "Standup tomorrow 9:00")
    due = NOW + timedelta(hours=3)
    screen = UpcomingDraft("Standup", due, source.id)
    [item] = journal.add_upcoming([screen], journal.epoch, at=NOW)
    journal.dismiss_upcoming(item.id)
    assert journal.portal.upcoming(NOW) == []
    assert journal.add_upcoming([screen], journal.epoch, at=NOW) == []
    with pytest.raises(KeyError):
        journal.dismiss_upcoming(item.id)
    # The user asking for it again overrides their dismissal and owns it now.
    [again] = journal.add_upcoming(
        [UpcomingDraft("standup", due, kind="event")], journal.epoch, at=NOW
    )
    assert (again.id, again.source, again.kind) == (item.id, None, "event")
    assert [i.id for i in journal.portal.upcoming(NOW)] == [item.id]


def test_forgetting_notes_removes_their_items_and_stale_results(journal):
    source = note(journal, "Event: Standup tomorrow 9:00")
    due = NOW + timedelta(days=1)
    epoch = journal.epoch
    journal.add_upcoming([UpcomingDraft("Standup", due, source.id)], epoch, at=NOW)
    journal.add_upcoming([UpcomingDraft("Exam", due)], epoch, at=NOW)
    journal.forget_observation(source.id)
    assert [i.title for i in journal.portal.upcoming(NOW)] == ["Exam"]
    # An extraction started before the forget can't write back.
    assert journal.add_upcoming([UpcomingDraft("Quiz", due)], epoch, at=NOW) == []
    # A source note that no longer exists is skipped.
    assert (
        journal.add_upcoming(
            [UpcomingDraft("Quiz", due, source.id)], journal.epoch, at=NOW
        )
        == []
    )
    journal.forget_range(
        TimeRange(NOW - timedelta(seconds=1), NOW + timedelta(seconds=1))
    )
    assert journal.portal.upcoming(NOW) == []
    journal.add_upcoming([UpcomingDraft("Exam", due)], journal.epoch, at=NOW)
    journal.forget_all()
    assert journal.portal.upcoming(NOW) == []


@pytest.mark.parametrize(
    ("minutes", "phrase"),
    [
        (130, "due in 2 h 10 min"),
        (120, "due in 2 h"),
        (5, "due in 5 min"),
        (0, "due now"),
        (60 * 26, "due in 1 d 2 h"),
        (-15, "was due 15 min ago"),
    ],
)
def test_due_phrase_is_computed_in_code(minutes, phrase):
    assert due_phrase(NOW + timedelta(minutes=minutes), NOW) == phrase


def test_events_start_rather_than_fall_due():
    later, earlier = NOW + timedelta(minutes=90), NOW - timedelta(minutes=5)
    assert due_phrase(later, NOW, "event") == "starts in 1 h 30 min"
    assert due_phrase(earlier, NOW, "event") == "started 5 min ago"


# Extraction


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CSE 130 Assignment 3 · Sunday, October 11 · 11:59pm", True),
        ("Assignment due tomorrow", True),
        ("Oct 12 exam", True),
        ("Meeting 10/14", True),
        ("2026-10-12 release", True),
        ("[10:32 AM] Alex: sounds good", False),
        ("12:30:45 INFO server started", False),
        ("def main(): pass", False),
        ("No calendar or specific deadlines are visible.", False),
        ("Assignment due in 3 days", True),
        ("Quiz 4 · 2 days left", True),
        ("Project report due next week", True),
        ("Uploaded 3 days ago", False),
        ("Quiz closes in 30 minutes", True),
        ("15 min remaining", True),
        ("Project due in 1 month", True),
        ("due in 2 h", True),
        ("3 hrs remaining", True),
        ("1 wk left", True),
        ("Closes in 45min", True),
        ("Found in 3 places", False),
    ],
)
def test_dated_gate_needs_a_date_not_just_a_clock_time(text, expected):
    assert dated(text) is expected


async def test_extract_keeps_future_items_within_the_horizon():
    local = NOW.astimezone()
    soon = (local + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
    past = (local - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
    far = (local + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M")
    gateway = ScriptedGateway(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "title": "CSE 130 A3",
                            "kind": "deadline",
                            "due": soon,
                            "note": "o1",
                        },
                        {"title": "Old", "kind": "event", "due": past, "note": "o1"},
                        {"title": "Far", "kind": "event", "due": far, "note": "o1"},
                    ]
                }
            )
        ]
    )
    found = await extract(gateway, notes={"o1": "Calendar: CSE 130 A3"}, now=NOW)
    assert [(i.title, i.source) for i in found] == [("CSE 130 A3", "o1")]
    assert found[0].due_at.tzinfo is not None
    assert abs((found[0].due_at - NOW).total_seconds() - 5 * 3600) < 60
    assert gateway.priorities == [Priority.TICK]
    assert "[o1] Calendar: CSE 130 A3" in gateway.prompts[0].user


async def test_extract_rejects_note_ids_that_were_not_supplied():
    gateway = ScriptedGateway(
        [
            '{"items": [{"title": "X", "kind": "event", "due": "2030-01-01T09:00", "note": "other"}]}'
        ]
    )
    with pytest.raises(Exception):
        await extract(gateway, notes={"o1": "text"}, now=NOW)


# Tick and scan


class Book:
    def __init__(self, signals=()):
        self.signals = signals
        self.reacted = 0

    def active_signals(self):
        return self.signals

    async def react(self, tick):
        self.reacted += 1


def tick_reply(signals: dict) -> str:
    return json.dumps(
        {
            "segments": [
                {
                    "observations": ["$id"],
                    "mode": "start",
                    "title": "Calendar",
                    "kind": "browsing",
                    "intent": "Checking deadlines",
                }
            ],
            "signals": signals,
        }
    )


class IdGateway(ScriptedGateway):
    """Fill the one new observation ID into a scripted tick reply."""

    def __init__(self, replies, id):
        super().__init__([r.replace("$id", id) for r in replies])


DRIFT = Signal(SignalId("nudge", "drifting"), "Off task?", "bool", reads=("upcoming",))
PLAIN = Signal(SignalId("music", "focused"), "Focused?", "bool")


async def test_dated_notes_are_scanned_once_after_the_tick(journal):
    journal.set_capture_state("running", NOW)
    source = note(journal, "Event details: CSE 130 Assignment 3, Sunday 11:59 PM")
    due = (NOW.astimezone() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M")
    gateway = IdGateway(
        [
            tick_reply({}),
            json.dumps(
                {
                    "items": [
                        {
                            "title": "CSE 130 Assignment 3",
                            "kind": "deadline",
                            "due": due,
                            "note": "$id",
                        }
                    ]
                }
            ),
        ],
        source.id,
    )
    mind = Mind(
        journal, gateway, cast(SkillBook, Book()), clock=lambda: NOW, upcoming=True
    )
    assert await mind.tick_now()
    assert journal.portal.upcoming(NOW) == []  # the scan is outside the tick
    await mind.scan_upcoming()
    [item] = journal.portal.upcoming(NOW)
    assert (item.title, item.source) == ("CSE 130 Assignment 3", source.id)
    assert gateway.priorities == [Priority.TICK, Priority.TICK]
    # Nothing new: no further call.
    await mind.scan_upcoming()
    assert len(gateway.prompts) == 2


async def test_same_relative_wording_is_rescanned_on_a_later_day(journal):
    journal.set_capture_state("running", NOW)
    clock = [NOW]
    text = "Standup tomorrow at 9:00"
    gateway = ScriptedGateway(['{"items": []}', '{"items": []}'])
    mind = Mind(
        journal,
        gateway,
        cast(SkillBook, Book()),
        clock=lambda: clock[0],
        upcoming=True,
    )
    runner = mind._runner
    for day in (0, 0, 1):
        clock[0] = NOW + timedelta(days=day)
        runner.unscanned.append(
            (journal.epoch, note(journal, text, at=clock[0] + timedelta(seconds=day)))
        )
        await mind.scan_upcoming()
    assert len(gateway.prompts) == 2


async def test_undated_notes_and_disabled_upcoming_cost_no_call(journal):
    journal.set_capture_state("running", NOW)
    source = note(journal, "Python code is open.", title="editor.py")
    gateway = IdGateway([tick_reply({})], source.id)
    mind = Mind(
        journal, gateway, cast(SkillBook, Book()), clock=lambda: NOW, upcoming=True
    )
    assert await mind.tick_now()
    await mind.scan_upcoming()
    assert len(gateway.prompts) == 1


async def test_tick_shows_upcoming_facts_only_to_skills_that_read_them(journal):
    journal.set_capture_state("running", NOW)
    source = note(journal, "YouTube video playing", title="YouTube")
    journal.add_upcoming(
        [UpcomingDraft("CSE 130 Assignment 3", NOW + timedelta(minutes=130))],
        journal.epoch,
        at=NOW,
    )
    plain = IdGateway([tick_reply({"music.focused": False})], source.id)
    mind = Mind(journal, plain, cast(SkillBook, Book((PLAIN,))), clock=lambda: NOW)
    assert await mind.tick_now()
    assert "Upcoming" not in plain.prompts[0].user

    # The screen is unchanged since the first tick, so no segments.
    reading = ScriptedGateway(
        [
            json.dumps(
                {
                    "segments": [],
                    "signals": {"music.focused": False, "nudge.drifting": True},
                }
            )
        ]
    )
    clock = NOW + timedelta(seconds=40)
    journal.extend_observation(source.id, clock)
    mind = Mind(
        journal,
        reading,
        cast(SkillBook, Book((PLAIN, DRIFT))),
        clock=lambda: clock,
    )
    await mind.tick_now()
    user = reading.prompts[0].user
    assert "- CSE 130 Assignment 3 (due in 2 h 9 min)" in user
    assert "nudge.drifting (bool) [reads Upcoming]: Off task?" in user
    assert "music.focused (bool): Focused?" in user
    assert reading.prompts[0].system == plain.prompts[0].system


# Skills


def test_reads_and_upcoming_next_parse_and_render():
    skill = parse(SKILL)
    assert isinstance(skill, Skill)
    assert skill.reads == ("upcoming",)
    assert skill.signals[0].reads == ("upcoming",)
    body = skill.rules[0].steps[0].arguments["body"]
    occurrence = Occurrence("k", NOW, {"drifting": True})
    assert (
        render(body, occurrence, [], textual=True, upcoming="Exam (due in 1 h)")
        == "Exam (due in 1 h). Back to it?"
    )


@pytest.mark.parametrize(
    ("changes", "location"),
    [
        ([("reads: [upcoming]\n", "")], "rules[0].do[0].body"),
        ([("reads: [upcoming]", "reads: [calendar]")], "reads"),
        ([("reads: [upcoming]", "reads: upcoming")], "reads"),
        (
            [("  drifting:", "  upcoming:"), ("drifting is", "upcoming is")],
            "signals.upcoming",
        ),
        (
            [
                ("title: Due soon", "title: $upcoming.next"),
                ("layout: bubble", "layout: $upcoming.next"),
            ],
            "rules[0].do[0].layout",
        ),
    ],
)
def test_reads_errors(changes, location):
    text = SKILL
    for old, new in changes:
        assert old in text
        text = text.replace(old, new)
    errors = parse(text)
    assert isinstance(errors, list)
    assert location in {e.location for e in errors}


def test_existing_skills_parse_unchanged_with_no_reads():
    for path in BUILTIN.glob("*.skill.md"):
        skill = parse_skill(
            path.read_text(),
            source=SkillSource(path, "builtin"),
            catalog=CATALOG,
        )
        assert isinstance(skill, Skill), path
        assert skill.reads == (
            ("upcoming",) if path.stem == "deadline-nudge.skill" else ()
        )


# Chat


class ChatBook:
    def __init__(self, tmp_path: Path):
        self.saved = {}
        self._book = SimpleNamespace(catalog=CATALOG)
        self.home = tmp_path

    def entries(self):
        return []

    def parse(self, id, text):
        return parse_skill(
            text, source=SkillSource(Path(f"{id}.skill.md"), "user"), catalog=CATALOG
        )

    def save(self, id, text):
        self.saved[id] = text
        return SimpleNamespace(id=id, text=text)


async def test_remember_adds_a_user_item(journal):
    due = (datetime.now(UTC).astimezone() + timedelta(hours=4)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    gateway = ScriptedGateway(
        [
            json.dumps(
                {"items": [{"title": "CSE 130 A3", "kind": "deadline", "due": due}]}
            )
        ]
    )
    chat = Chat(journal, gateway, cast(SkillBook, None))
    reply = await chat.respond("/remember CSE 130 A3 is due at " + due[-5:])
    assert isinstance(reply, Plain) and reply.text.startswith(
        "Added to Upcoming: CSE 130 A3"
    )
    [item] = journal.portal.upcoming(datetime.now(UTC))
    assert item.source is None
    assert gateway.priorities == [Priority.INTERACTIVE]
    again = await Chat(
        journal,
        ScriptedGateway([json.dumps({"items": []})]),
        cast(SkillBook, None),
    ).respond("/remember nothing here")
    assert isinstance(again, Plain) and "couldn't find" in again.text


async def test_drafter_sees_recent_notes_and_the_upcoming_list(journal, tmp_path):
    now = datetime.now(UTC)
    seen = note(
        journal, "Event details: CSE 130 Assignment 3", at=now - timedelta(minutes=2)
    )
    assert journal.record_tick(
        Tick(
            now - timedelta(minutes=1),
            journal.epoch,
            seen.last_seen_at,
            (seen.id,),
            (
                SegmentAssignment(
                    (seen.id,),
                    Start("Checking the CSE 130 deadline", "browsing", "Planning"),
                ),
            ),
            (),
        )
    )
    journal.add_upcoming(
        [UpcomingDraft("CSE 130 Assignment 3", now + timedelta(hours=6))],
        journal.epoch,
        at=now,
    )
    plan = {
        "draft": {
            "id": "cse130-nudge",
            "name": "Back to CSE 130",
            "goal": "Pull me back to CSE 130 Assignment 3.",
            "signals": [
                {
                    "name": "drifting",
                    "ask": "Is the user off task while CSE 130 Assignment 3 on the Upcoming list is due within 12 hours?",
                    "type": "bool",
                }
            ],
            "rules": [
                {
                    "when": "drifting is true for 1m",
                    "steps": [
                        {
                            "action": "overlay.show",
                            "params": [
                                {"name": "title", "value": "CSE 130"},
                                {
                                    "name": "body",
                                    "value": "$upcoming.next. Back to it?",
                                },
                            ],
                        }
                    ],
                }
            ],
            "allow": ["overlay.show"],
            "reads": ["upcoming"],
            "summary": "Nudge back to CSE 130",
        },
        "explanation": None,
    }
    note(journal, "Quiz 2 opens Monday", at=now - timedelta(seconds=30), title="Canvas")
    gateway = ScriptedGateway([json.dumps(plan)])
    book = ChatBook(tmp_path)
    reply = await Chat(journal, gateway, cast(SkillBook, book)).respond(
        "/skill pull me back to this assignment if I slack off"
    )
    assert isinstance(reply, DraftReady)
    user = gateway.prompts[0].user
    assert "Recent notes (last 30 minutes)" in user
    assert "Checking the CSE 130 deadline (browsing" in user
    assert "Not yet summarized: firefox · Canvas: Quiz 2 opens Monday" in user
    assert "- CSE 130 Assignment 3 (due in 5 h 59 min)" in user or (
        "- CSE 130 Assignment 3 (due in 6 h)" in user
    )
    text = book.saved["cse130-nudge"]
    assert "reads:\n- upcoming" in text
    assert isinstance(book.parse("cse130-nudge", text), Skill)


# HTTP


def test_live_and_mock_upcoming_routes(journal):
    headers = {"Origin": "http://testserver", "X-Daybook-Token": "t"}
    services = SimpleNamespace(journal=journal, bus=Bus())
    live = create_app(token="t", origin="http://testserver", services=services)  # type: ignore[arg-type]
    mock = create_app(mock=True, token="t", origin="http://testserver")
    due = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    for app in (live, mock):
        with TestClient(app) as client:
            before = client.get("/api/upcoming").json()["items"]
            added = client.post(
                "/api/upcoming",
                json={"title": "Quiz 2", "due_at": due},
                headers=headers,
            )
            assert added.status_code == 200, added.text
            item = w.UpcomingItem.model_validate(added.json())
            assert item.source is None
            duplicate = client.post(
                "/api/upcoming",
                json={"title": "quiz  2", "due_at": due},
                headers=headers,
            )
            assert duplicate.status_code == 409
            stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            assert (
                client.post(
                    "/api/upcoming",
                    json={"title": "Old quiz", "due_at": stale},
                    headers=headers,
                ).status_code
                == 422
            )
            listed = client.get("/api/upcoming").json()["items"]
            assert len(listed) == len(before) + 1
            assert (
                client.post(
                    "/api/upcoming",
                    json={"title": "x", "due_at": "2030-01-01T09:00"},
                    headers=headers,
                ).status_code
                == 422
            )
            assert client.delete(f"/api/upcoming/{item.id}", headers=headers).json()[
                "ok"
            ]
            assert (
                client.delete(f"/api/upcoming/{item.id}", headers=headers).status_code
                == 404
            )
            assert client.delete("/api/upcoming/x").status_code == 403


def test_mock_range_forget_removes_items_like_the_journal():
    headers = {"Origin": "http://testserver", "X-Daybook-Token": "t"}
    with TestClient(
        create_app(mock=True, token="t", origin="http://testserver")
    ) as client:
        [seeded] = client.get("/api/upcoming").json()["items"]
        assert seeded["source"]
        due = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        client.post(
            "/api/upcoming", json={"title": "Quiz 2", "due_at": due}, headers=headers
        )
        now = datetime.now(UTC)
        window = {
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(minutes=1)).isoformat(),
        }
        assert (
            client.post("/api/forget", json=window, headers=headers).status_code == 200
        )
        assert client.get("/api/upcoming").json()["items"] == []


class DismissingGateway(ScriptedGateway):
    """Dismiss an Upcoming item while the tick's model call is in flight."""

    def __init__(self, replies, journal, item):
        super().__init__(replies)
        self.journal, self.item = journal, item

    async def call(self, priority, prompt):
        built = await prompt() if callable(prompt) else prompt
        if built is None:
            return None
        self.journal.dismiss_upcoming(self.item)
        return await super().call(priority, built)


async def test_dismissal_during_a_tick_discards_its_upcoming_readings(journal):
    journal.set_capture_state("running", NOW)
    source = note(journal, "YouTube video playing", title="YouTube")
    [item] = journal.add_upcoming(
        [UpcomingDraft("CSE 130 Assignment 3", NOW + timedelta(hours=2))],
        journal.epoch,
        at=NOW,
    )
    reply = tick_reply({"nudge.drifting": True}).replace("$id", source.id)
    book = Book((DRIFT,))
    gateway = DismissingGateway([reply], journal, item.id)
    mind = Mind(journal, gateway, cast(SkillBook, book), clock=lambda: NOW)
    assert await mind.tick_now() is None
    assert book.reacted == 0


class RevivingGateway(ScriptedGateway):
    """Dismiss and re-add the same item, now an event, during the model call."""

    def __init__(self, replies, journal, item):
        super().__init__(replies)
        self.journal, self.item = journal, item

    async def call(self, priority, prompt):
        built = await prompt() if callable(prompt) else prompt
        if built is None:
            return None
        self.journal.dismiss_upcoming(self.item)
        [revived] = self.journal.add_upcoming(
            [
                UpcomingDraft(
                    "CSE 130 Assignment 3", NOW + timedelta(hours=2), kind="event"
                )
            ],
            self.journal.epoch,
            at=NOW,
        )
        assert revived.id == self.item
        return await super().call(priority, built)


async def test_revived_item_with_new_details_also_discards_the_tick(journal):
    journal.set_capture_state("running", NOW)
    source = note(journal, "YouTube video playing", title="YouTube")
    [item] = journal.add_upcoming(
        [UpcomingDraft("CSE 130 Assignment 3", NOW + timedelta(hours=2))],
        journal.epoch,
        at=NOW,
    )
    reply = tick_reply({"nudge.drifting": True}).replace("$id", source.id)
    book = Book((DRIFT,))
    gateway = RevivingGateway([reply], journal, item.id)
    mind = Mind(journal, gateway, cast(SkillBook, book), clock=lambda: NOW)
    assert await mind.tick_now() is None
    assert book.reacted == 0
