from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from daybook.act import build_catalog
from daybook.chat import Answer, Chat, Plain, resolve_window
from daybook.events import Bus
from daybook.journal import (
    CaptureSpan,
    Journal,
    Observation,
    RecallNotes,
    Segment,
    TimeRange,
    Total,
    WindowInfo,
)
from daybook.model import ModelUnavailable, Priority, ScriptedGateway
from daybook.skills import SkillBook


@pytest.mark.parametrize(
    ("message", "start", "end"),
    [
        (
            "what did I do in the last hour?",
            "2026-10-10T13:30:00+00:00",
            "2026-10-10T14:30:00+00:00",
        ),
        (
            "what did I do in the last 10 minutes?",
            "2026-10-10T14:20:00+00:00",
            "2026-10-10T14:30:00+00:00",
        ),
        (
            "what did I do today?",
            "2026-10-10T00:00:00+00:00",
            "2026-10-10T14:30:00+00:00",
        ),
        ("this morning", "2026-10-10T00:00:00+00:00", "2026-10-10T12:00:00+00:00"),
        ("yesterday", "2026-10-09T00:00:00+00:00", "2026-10-10T00:00:00+00:00"),
        (
            "how long did I code?",
            "2026-10-10T00:00:00+00:00",
            "2026-10-10T14:30:00+00:00",
        ),
    ],
)
def test_demo_windows(message, start, end):
    assert resolve_window(
        message, datetime(2026, 10, 10, 14, 30, tzinfo=UTC)
    ) == TimeRange(datetime.fromisoformat(start), datetime.fromisoformat(end))


def test_calendar_window_respects_dst_and_morning_has_no_future():
    now = datetime(2026, 11, 2, 7, tzinfo=ZoneInfo("America/Los_Angeles"))
    window = resolve_window("yesterday", now)
    assert (window.end - window.start).total_seconds() == 25 * 3600
    assert resolve_window("this morning", now).end == now.astimezone(UTC)
    assert (
        resolve_window("last 2 hours", now).end
        - resolve_window("last 2 hours", now).start
    ).total_seconds() == 7200
    with pytest.raises(ValueError, match="timezone"):
        resolve_window("today", datetime(2026, 1, 1))


class Notes:
    def __init__(self):
        self.epoch = 0
        at = datetime.now(UTC) - timedelta(minutes=20)
        observation = Observation(
            "o1",
            at,
            at + timedelta(minutes=10),
            WindowInfo("code", "tests.py"),
            "Editing the import test",
            "",
            "s1",
        )
        self.rows = [
            Segment(
                "s1",
                "Fix imports",
                "coding",
                "Repair import test",
                "model",
                True,
                (observation,),
            )
        ]
        self.details = []
        self.searches = []
        self.portal = SimpleNamespace(
            segments=lambda window: self.rows,
            observations=lambda window: [o for s in self.rows for o in s.observations],
            capture_spans=lambda window: [
                CaptureSpan(
                    "paused", at + timedelta(minutes=10), at + timedelta(minutes=15)
                )
            ],
            totals=lambda window, by: (
                [Total("Fix imports" if by == "title" else "coding", 600)]
                if self.rows
                else []
            ),
            context=self.context,
            search=self.search,
            read_notes=lambda ids, window=None, **kwargs: RecallNotes(
                self.context(window, 4000, "observations"),
                tuple(s.id for s in self.rows),
                window,
                False,
            ),
        )

    def context(self, window, budget, detail):
        self.details.append((budget, detail))
        return "Segment s1: Fix imports. Editing the import test."

    def search(self, term, window, *, limit=None):
        self.searches.append(term)
        return []

    def chat(self, gateway):
        return Chat(cast(Journal, self), gateway, cast(SkillBook, object()))


@pytest.mark.asyncio
async def test_recall_uses_code_totals_bounded_context_and_valid_cites():
    gateway = ScriptedGateway(
        ['{"text":"You fixed imports for 10 min.","cites":["s1","invented","s1"]}']
    )
    notes = Notes()
    reply = await notes.chat(gateway).respond(
        '/ask what did I do with "imports" in the last hour?'
    )
    assert isinstance(reply, Answer)
    assert reply.cites == ("s1",)
    assert notes.details == [(4000, "observations"), (2000, "segments")]
    assert notes.searches == ["imports"]
    assert gateway.priorities == [Priority.INTERACTIVE]
    payload = json.loads(gateway.prompts[0].user)
    assert payload["totals_by_title"] == [
        {"label": "Fix imports", "seconds": 600, "duration": "10 min"}
    ]
    assert payload["capture_gaps"][0]["state"] == "paused"


@pytest.mark.asyncio
async def test_route_then_recall_and_other():
    gateway = ScriptedGateway(
        [
            '{"route":"recall"}',
            '{"text":"You fixed imports.","cites":["s1"]}',
            '{"route":"other"}',
        ]
    )
    notes = Notes()
    assert isinstance(await notes.chat(gateway).respond("what did I do today?"), Answer)
    assert isinstance(await notes.chat(gateway).respond("hello"), Plain)
    assert len(gateway.prompts) == 3


@pytest.mark.asyncio
async def test_forget_while_answering_discards_old_answer():
    notes = Notes()

    class ForgetDuringCall(ScriptedGateway):
        async def call(self, priority, prompt):
            reply = await super().call(priority, prompt)
            notes.epoch += 1
            notes.rows.clear()
            return reply

    gateway = ForgetDuringCall(['{"text":"The deleted private text.","cites":["s1"]}'])
    reply = await notes.chat(gateway).respond("/ask what did I do today?")
    assert isinstance(reply, Answer)
    assert reply.text == "I don't have notes for that."
    assert not reply.cites
    assert len(gateway.prompts) == 1


@pytest.mark.asyncio
async def test_edit_while_answering_retries_and_continual_edits_stop():
    notes = Notes()

    class EditDuringCall(ScriptedGateway):
        async def call(self, priority, prompt):
            reply = await super().call(priority, prompt)
            notes.epoch += 1
            return reply

    gateway = EditDuringCall(['{"text":"stale","cites":["s1"]}'] * 3)
    reply = await notes.chat(gateway).respond("/ask last hour")
    assert isinstance(reply, Plain)
    assert "changed" in reply.text
    assert len(gateway.prompts) == 3


@pytest.mark.asyncio
async def test_paused_question_never_invents_activity_or_calls_model():
    gateway = ScriptedGateway([])
    reply = (
        await Notes()
        .chat(gateway)
        .respond("/ask what did I do during the paused stretch?")
    )
    assert isinstance(reply, Answer)
    assert "don't have notes" in reply.text
    assert not reply.cites
    assert not gateway.prompts


@pytest.mark.asyncio
async def test_empty_prefix_and_model_failure_are_readable():
    notes = Notes()
    gateway = ScriptedGateway([ModelUnavailable()])
    for message in ("", "/ask", "/skill"):
        assert isinstance(await notes.chat(gateway).respond(message), Plain)
    reply = await notes.chat(gateway).respond("hello")
    assert isinstance(reply, Plain)
    assert "unavailable" in reply.text


@pytest.mark.asyncio
async def test_mixed_gap_question_keeps_recorded_activity():
    gateway = ScriptedGateway(
        [
            '{"text":"Before the pause you fixed imports for 10 min; there are no notes during the pause.","cites":["s1"]}'
        ]
    )
    reply = (
        await Notes()
        .chat(gateway)
        .respond("/ask what did I do before and after the paused period?")
    )
    assert isinstance(reply, Answer)
    assert reply.cites == ("s1",)
    assert "10 min" in reply.text


@pytest.mark.asyncio
async def test_chat_production_clock_retains_dst_rules(monkeypatch):
    import daybook.chat as module

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 11, 2, 20, tzinfo=UTC)
            return (
                value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)
            )

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    monkeypatch.setattr(module, "datetime", Clock)
    notes = Notes()
    notes.rows.clear()
    reply = await notes.chat(ScriptedGateway([])).respond("/ask yesterday")
    assert isinstance(reply, Answer)
    assert (reply.window.end - reply.window.start).total_seconds() == 25 * 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "what did I do during the paused period and how long was it?",
        "what did I do while locked and why?",
        "tell me about the private period and its duration",
    ],
)
async def test_gap_only_conjunction_stays_no_notes_when_model_unavailable(message):
    gateway = ScriptedGateway([ModelUnavailable()])
    reply = await Notes().chat(gateway).respond("/ask " + message)
    assert isinstance(reply, Answer)
    assert "don't have notes" in reply.text
    assert not reply.cites
    assert not gateway.prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["except", "besides"])
async def test_gap_exclusions_keep_recorded_notes(relation):
    gateway = ScriptedGateway(
        ['{"text":"You fixed imports for 10 min.","cites":["s1"]}']
    )
    reply = (
        await Notes()
        .chat(gateway)
        .respond(f"/ask what did I do {relation} during the paused period?")
    )
    assert isinstance(reply, Answer)
    assert reply.cites == ("s1",)
    assert len(gateway.prompts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("modifier", ["earlier", "later", "preceding", "following"])
@pytest.mark.parametrize("gap", ["paused period", "private stretch"])
async def test_gap_modifiers_do_not_request_outside_notes(modifier, gap):
    gateway = ScriptedGateway([ModelUnavailable()])
    reply = (
        await Notes()
        .chat(gateway)
        .respond(f"/ask what did I do during the {modifier} {gap}?")
    )
    assert isinstance(reply, Answer)
    assert "don't have notes" in reply.text
    assert not reply.cites
    assert not gateway.prompts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "what did I do during the paused period and then?",
        "what did I do earlier and during the paused period?",
        "what did I do following the earlier paused period?",
    ],
)
async def test_gap_sequence_keeps_recorded_notes(question):
    gateway = ScriptedGateway(
        ['{"text":"You fixed imports for 10 min.","cites":["s1"]}']
    )
    reply = await Notes().chat(gateway).respond("/ask " + question)
    assert isinstance(reply, Answer)
    assert reply.cites == ("s1",)


@pytest.mark.parametrize("explicit_command", [False, True])
async def test_unsupported_skill_explains_capability_without_saving(
    tmp_path, explicit_command
):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    catalog = build_catalog({"focus": "Work", "calm": "Reading", "break": "Rest"})
    effects = []

    async def handler(skill, parameters):
        effects.append((skill, parameters))
        raise AssertionError("A chat request must not execute an action")

    catalog.handlers.update({action: handler for action in catalog.specs})
    skills = SkillBook(
        tmp_path,
        Path(__file__).parents[2] / "skills/builtin",
        tmp_path / "journal.db",
        journal.portal,
        catalog,
        bus,
        clear_readings=journal.clear_skill_readings,
    )
    before = skills.entries()
    explanation = "Daybook reads visible screen notes and cannot hear calls or detect voice stress."
    responses = [] if explicit_command else ['{"route":"make_skill"}']
    responses.append(json.dumps({"draft": None, "explanation": explanation}))
    gateway = ScriptedGateway([*responses])
    message = "When I sound stressed on a call, remind me to breathe"
    try:
        reply = await Chat(journal, gateway, skills).respond(
            ("/skill " if explicit_command else "") + message
        )
        assert isinstance(reply, Plain) and reply.text == explanation
        assert skills.entries() == before
        assert not list((tmp_path / "skills").glob("*.skill.md"))
        assert not effects
        assert len(gateway.prompts) == (1 if explicit_command else 2)
        assert [prompt.effort for prompt in gateway.prompts] == (
            [] if explicit_command else ["none"]
        ) + ["low"]
    finally:
        journal.close()
