"""Recall through real Journal storage; generated replies are controlled, not evidence."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from daybook.chat import Answer, Chat, RecallTurn
from daybook.events import Bus
from daybook.journal import (
    Journal,
    ObservationDraft,
    SegmentAssignment,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)
from daybook.model import ScriptedGateway
from daybook.skills import SkillBook


@pytest.fixture
def journal(tmp_path):
    journal = Journal(tmp_path / "notes.sqlite", Bus())
    yield journal
    journal.close()


def note(journal, at, title, detail):
    observation = journal.add_observation(
        ObservationDraft(at, WindowInfo("Browser", title), title, detail), journal.epoch
    )
    assert observation
    end = at + timedelta(minutes=1)
    journal.extend_observation(observation.id, end)
    assert journal.record_tick(
        Tick(
            end,
            journal.epoch,
            end,
            (observation.id,),
            (
                SegmentAssignment(
                    (observation.id,), Start(title, "coding", "inferred intent")
                ),
            ),
            (),
        )
    )
    segment = journal.portal.now().segment
    assert segment
    return observation.id, segment.id


def chat(journal, gateway):
    return Chat(journal, gateway, cast(SkillBook, object()))


def reply(text="Recorded detail", cites=(), **kwargs):
    return json.dumps({"text": text, "cites": list(cites), **kwargs})


@pytest.mark.asyncio
async def test_older_exact_detail_after_adaptive_search_with_empty_today(journal):
    at = datetime.now(UTC) - timedelta(days=4)
    observation, segment = note(
        journal,
        at,
        "Database debugging",
        "wal_checkpoint(TRUNCATE) removed the stale WAL",
    )
    gateway = ScriptedGateway(
        [
            reply("", queries=["wal_checkpoint"], all_history=True),
            reply(
                "The recorded fix was wal_checkpoint(TRUNCATE).", [segment, "invented"]
            ),
        ]
    )
    answer = await chat(journal, gateway).respond(
        "/ask Which approach finally worked on the database?"
    )
    assert isinstance(answer, Answer)
    assert answer.cites == (segment,)
    assert answer.window.start <= at < answer.window.end
    assert "wal_checkpoint(TRUNCATE)" in json.loads(gateway.prompts[1].user)["notes"]
    assert observation in gateway.prompts[1].user
    assert segment in json.loads(gateway.prompts[0].user)["allowed_cites"]
    assert segment in {s["id"] for s in json.loads(gateway.prompts[0].user)["segments"]}


@pytest.mark.asyncio
async def test_later_query_word_and_raw_details_survive_many_distractors(journal):
    at = datetime.now(UTC) - timedelta(days=2)
    for i in range(20):
        note(
            journal,
            at + timedelta(minutes=2 * i),
            "Debugging options",
            "Generic debugging options and possibilities",
        )
    _, segment = note(
        journal,
        at + timedelta(hours=2),
        "Quasar fix",
        "The exact working parameter was cache_epoch=7",
    )
    gateway = ScriptedGateway([reply("Use cache_epoch=7.", [segment])])
    answer = await chat(journal, gateway).respond(
        "/ask Which debugging options worked for quasar?"
    )
    assert isinstance(answer, Answer)
    assert "cache_epoch=7" in json.loads(gateway.prompts[0].user)["notes"]


@pytest.mark.asyncio
async def test_followup_reads_next_observation_and_keeps_prior_question_only(journal):
    at = datetime.now(UTC) - timedelta(days=2)
    _, first = note(journal, at, "Compiler investigation", "Tried removing the import")
    _, second = note(
        journal,
        at + timedelta(minutes=5),
        "Verification",
        "Rebuilt successfully with the package alias",
    )
    history = (
        RecallTurn(
            "What did I try on the compiler?",
            (first,),
            TimeRange(at, at + timedelta(minutes=1)),
            journal.epoch,
        ),
    )
    gateway = ScriptedGateway(
        [
            reply("", read_ids=[first], follow_up=True, neighbors="next"),
            reply("Then the rebuild succeeded with the alias.", [second]),
        ]
    )
    answer = await chat(journal, gateway).respond("/ask What happened next?", history)
    assert isinstance(answer, Answer)
    assert answer.cites == (second,)
    payload = json.loads(gateway.prompts[1].user)
    assert "Rebuilt successfully" in payload["notes"]
    assert payload["history"][0]["question"] == history[0].question
    assert set(payload["history"][0]) == {"turn", "question", "cites", "window"}


@pytest.mark.asyncio
async def test_explicit_date_overrides_history_and_all_history_plan(journal):
    at = datetime(2026, 9, 1, 10, tzinfo=UTC)
    _, old = note(journal, at, "Old experiment", "FORBIDDEN_OLD_DETAIL")
    _, current = note(
        journal, at + timedelta(days=1), "New experiment", "Allowed detail"
    )
    history = (
        RecallTurn(
            "Old experiment?",
            (old,),
            TimeRange(at, at + timedelta(minutes=1)),
            journal.epoch,
        ),
    )
    gateway = ScriptedGateway(
        [
            reply("", queries=["experiment"], all_history=True, follow_up=True),
            reply("Allowed detail", [current, old]),
        ]
    )
    answer = await chat(journal, gateway).respond(
        "/ask What happened on 2026-09-02?", history
    )
    assert isinstance(answer, Answer)
    assert answer.cites == (current,)
    assert all("FORBIDDEN_OLD_DETAIL" not in p.user for p in gateway.prompts)


@pytest.mark.asyncio
async def test_new_topic_search_does_not_inherit_prior_window(journal):
    at = datetime.now(UTC) - timedelta(days=4)
    _, old = note(journal, at, "Compiler", "Compiler details")
    _, other = note(
        journal,
        at + timedelta(days=1),
        "Botany lecture",
        "Visible transcript: xylem transports water",
    )
    history = (
        RecallTurn(
            "Compiler?", (old,), TimeRange(at, at + timedelta(minutes=1)), journal.epoch
        ),
    )
    gateway = ScriptedGateway(
        [reply("Xylem transports water, according to the visible transcript.", [other])]
    )
    answer = await chat(journal, gateway).respond(
        "/ask What did the botany lecture explain?", history
    )
    assert isinstance(answer, Answer)
    assert answer.cites == (other,)
    assert "xylem" in json.loads(gateway.prompts[0].user)["notes"]


@pytest.mark.asyncio
async def test_stale_question_dropped_before_routing_and_retrieval(journal):
    at = datetime.now(UTC) - timedelta(days=1)
    _, segment = note(journal, at, "Sensitive", "Sensitive detail")
    history = (
        RecallTurn(
            "What about SECRET_OLD_QUESTION?",
            (segment,),
            TimeRange(at, at + timedelta(minutes=1)),
            journal.epoch,
        ),
    )
    journal.forget_all()
    gateway = ScriptedGateway(
        ['{"route":"recall"}', reply("I don't have notes for that.")]
    )
    answer = await chat(journal, gateway).respond("What happened next?", history)
    assert isinstance(answer, Answer)
    assert all("SECRET_OLD_QUESTION" not in p.user for p in gateway.prompts)
    assert answer.epoch == journal.epoch and not answer.cites


@pytest.mark.parametrize("forget_at", [1, 2, 3])
@pytest.mark.asyncio
async def test_forget_at_each_model_stage_restarts_without_evidence_or_history(
    journal, forget_at
):
    at = datetime.now(UTC) - timedelta(hours=1)
    _, segment = note(journal, at, "Research", "ERASED_PAYLOAD")
    history = (
        RecallTurn(
            "ERASED_QUESTION",
            (segment,),
            TimeRange(at, at + timedelta(minutes=1)),
            journal.epoch,
        ),
    )

    class Forgetting(ScriptedGateway):
        async def call(self, priority, prompt):
            result = await super().call(priority, prompt)
            if len(self.prompts) == forget_at:
                journal.forget_all()
            return result

    replies = [
        reply("", queries=["research"]),
        reply("", queries=["payload"]),
        reply("ERASED_MODEL_ANSWER", [segment]),
    ]
    replies = replies[:forget_at] + [reply("I don't have notes for that.")]
    gateway = Forgetting([*replies])
    answer = await chat(journal, gateway).respond(
        "/ask What does the research say?", history
    )
    assert isinstance(answer, Answer)
    assert "ERASED" not in answer.text
    assert not answer.cites
    assert all("ERASED" not in p.user for p in gateway.prompts[forget_at:])


@pytest.mark.asyncio
async def test_retrieval_rounds_and_text_are_bounded(journal):
    at = datetime.now(UTC) - timedelta(hours=1)
    observation, segment = note(journal, at, "Work", "Recorded text")
    journal.edit_observation(observation, seen="x" * 100000)
    gateway = ScriptedGateway(
        [
            reply("", queries=["one"]),
            reply("", queries=["two"]),
            reply("Missing specific detail.", [segment]),
        ]
    )
    answer = await chat(journal, gateway).respond("/ask Explain the work.")
    assert isinstance(answer, Answer)
    assert len(gateway.prompts) == 3
    assert all(len(p.user) < 60000 for p in gateway.prompts)
    assert json.loads(gateway.prompts[0].user)["truncated"]


def test_read_notes_exact_id_neighbors_clipping_and_forget(journal):
    at = datetime.now(UTC) - timedelta(days=1)
    first, segment = note(journal, at, "First", "EXACT")
    second, _ = note(journal, at + timedelta(minutes=2), "Next", "NEIGHBOR")
    result = journal.portal.read_notes([first], neighbors="next")
    assert first in result.text and second in result.text
    clipped = journal.portal.read_notes(
        [first], TimeRange(at + timedelta(seconds=30), at + timedelta(minutes=1))
    )
    assert json.loads(clipped.text)["active_seconds"] == 30
    assert clipped.cites == (segment,)
    journal.forget_observation(first)
    assert journal.portal.read_notes([first]).text == ""


@pytest.mark.asyncio
async def test_answer_after_neighbor_read_does_not_repeat_the_same_read(journal):
    at = datetime.now(UTC) - timedelta(days=1)
    _, first = note(journal, at, "First", "First attempt")
    _, second = note(journal, at + timedelta(minutes=5), "Next", "Then verification")
    turn = RecallTurn(
        "What did I try?",
        (first,),
        TimeRange(at, at + timedelta(minutes=1)),
        journal.epoch,
    )
    gateway = ScriptedGateway(
        [
            reply("", follow_up=True, neighbors="next"),
            reply("Then verification.", [second], follow_up=True, neighbors="next"),
        ]
    )
    answer = await chat(journal, gateway).respond("/ask What happened next?", (turn,))
    assert isinstance(answer, Answer) and answer.cites == (second,)
    assert len(gateway.prompts) == 2


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.asyncio
async def test_forget_while_waiting_for_model_admission_never_transmits_old_notes(
    journal, monkeypatch, routed
):
    import asyncio

    import httpx

    from daybook.model import ModelConfig, ModelGateway, Priority, Prompt

    at = datetime.now(UTC) - timedelta(hours=1)
    _, segment = note(journal, at, "Secret task", "QUEUED_SECRET_NOTE")
    turn = RecallTurn(
        "QUEUED_SECRET_QUESTION",
        (segment,),
        TimeRange(at, at + timedelta(minutes=1)),
        journal.epoch,
    )
    occupied, release = asyncio.Event(), asyncio.Event()
    transmitted = []

    async def transport(request):
        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        if user == "occupy":
            occupied.set()
            await release.wait()
            content = "ready"
        else:
            transmitted.append(user)
            content = (
                '{"route":"recall"}'
                if body["messages"][0]["content"].startswith("Classify")
                else reply("I don't have notes for that.")
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(transport), **kwargs),
    )
    gateway = ModelGateway(ModelConfig(base_url="http://model.test/v1"))
    blocker = asyncio.create_task(
        gateway.call(
            Priority.INTERACTIVE, Prompt(system="hold", user="occupy", output=str)
        )
    )
    try:
        await asyncio.wait_for(occupied.wait(), 2)
        question = "What happened next?" if routed else "/ask What happened next?"
        task = asyncio.create_task(chat(journal, gateway).respond(question, (turn,)))
        for _ in range(20):
            if gateway.status().queue_depth:
                break
            await asyncio.sleep(0)
        assert gateway.status().queue_depth == 1
        journal.forget_all()
        release.set()
        await asyncio.wait_for(blocker, 2)
        answer = await asyncio.wait_for(task, 2)
        assert isinstance(answer, Answer)
        assert transmitted
        assert all("QUEUED_SECRET" not in text for text in transmitted)
        assert not answer.cites
    finally:
        release.set()
        await gateway.close()


@pytest.mark.asyncio
async def test_immediate_followup_answer_is_regenerated_with_prior_window_totals(
    journal,
):
    at = datetime.now(UTC) - timedelta(days=3)
    observation, old = note(journal, at, "Old investigation", "Earlier work")
    journal.extend_observation(observation, at + timedelta(minutes=5))
    note(
        journal,
        datetime.now(UTC) - timedelta(minutes=10),
        "Today's work",
        "Unrelated work",
    )
    prior_window = TimeRange(at, at + timedelta(minutes=10))
    turn = RecallTurn(
        "What did I investigate then?", (old,), prior_window, journal.epoch
    )
    gateway = ScriptedGateway(
        [
            reply("1 min", [old], follow_up=True),
            reply("5 min", [old], follow_up=True),
        ]
    )
    answer = await chat(journal, gateway).respond("/ask How long was that?", (turn,))
    assert isinstance(answer, Answer) and answer.text == "5 min"
    payload = json.loads(gateway.prompts[1].user)
    assert payload["window"] == {
        "from": at.isoformat(),
        "to": prior_window.end.isoformat(),
    }
    assert payload["totals_by_title"] == [
        {"label": "Old investigation", "seconds": 300.0, "duration": "5 min"}
    ]
    assert "Today's work" not in payload["overview"]


@pytest.mark.asyncio
async def test_older_only_citation_window_does_not_include_unrelated_days(journal):
    at = datetime.now(UTC) - timedelta(days=80)
    _, old = note(journal, at, "Distant experiment", "Unique old result")
    note(journal, datetime.now(UTC) - timedelta(minutes=10), "Today", "Unrelated")
    gateway = ScriptedGateway([reply("Unique old result", [old])])
    answer = await chat(journal, gateway).respond(
        "/ask What was the distant experiment result?"
    )
    assert isinstance(answer, Answer)
    assert answer.window == TimeRange(at, at + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_followup_can_return_to_an_earlier_history_turn(journal):
    at = datetime.now(UTC) - timedelta(days=5)
    _, compiler = note(journal, at, "Compiler", "Changed parser")
    _, botany = note(journal, at + timedelta(days=2), "Botany", "Compared leaves")
    compiler_window = TimeRange(at, at + timedelta(minutes=1))
    history = (
        RecallTurn("Compiler progress?", (compiler,), compiler_window, journal.epoch),
        RecallTurn(
            "Botany progress?",
            (botany,),
            TimeRange(at + timedelta(days=2), at + timedelta(days=2, minutes=1)),
            journal.epoch,
        ),
    )
    gateway = ScriptedGateway(
        [
            reply("", follow_up=True, follow_up_turn=0),
            reply("Changed parser.", [compiler], follow_up=True, follow_up_turn=0),
        ]
    )
    answer = await chat(journal, gateway).respond(
        "/ask Going back to the compiler, what changed?", history
    )
    assert isinstance(answer, Answer) and answer.cites == (compiler,)
    payload = json.loads(gateway.prompts[1].user)
    assert payload["window"]["from"] == compiler_window.start.isoformat()
    assert "Changed parser" in payload["notes"]
    assert "Compared leaves" not in payload["notes"]
    assert payload["totals_by_title"][0]["label"] == "Compiler"


@pytest.mark.asyncio
async def test_invalid_calendar_date_does_not_query_todays_notes(journal):
    from daybook.chat import Plain

    note(journal, datetime.now(UTC) - timedelta(minutes=10), "Today", "Unrelated")
    gateway = ScriptedGateway([])
    answer = await chat(journal, gateway).respond("/ask What happened on 2026-02-30?")
    assert isinstance(answer, Plain) and "date is not valid" in answer.text
    assert not gateway.prompts
