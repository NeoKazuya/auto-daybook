from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from daybook.chat import Answer, Chat, Plain, resolve_window
from daybook.journal import (
    CaptureSpan,
    Journal,
    Observation,
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
        )

    def context(self, window, budget, detail):
        self.details.append((budget, detail))
        return "Segment s1: Fix imports. Editing the import test."

    def search(self, term, window):
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
    assert notes.details == [(8000, "observations")]
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
