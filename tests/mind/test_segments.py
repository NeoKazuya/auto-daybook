"""Multiple tasks in one tick retain their own durations."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from daybook.events import Bus
from daybook.journal import Journal, ObservationDraft, TimeRange, WindowInfo
from daybook.mind import Mind
from daybook.model import ScriptedGateway
from daybook.skills import SkillBook


async def test_reading_then_coding_pending_batch_preserves_both_durations(tmp_path):
    at = datetime(2026, 10, 11, 12, tzinfo=UTC)
    journal = Journal(tmp_path / "journal.db", Bus())

    class Book(SkillBook):
        def __init__(self):
            self.fired = []

        def active_signals(self):
            return ()

        async def react(self, tick):
            self.fired.append(tick)

    skills = Book()
    pdf = journal.add_observation(
        ObservationDraft(
            at, WindowInfo("Reader", "Research"), "Reading research findings."
        ),
        journal.epoch,
    )
    assert pdf
    journal.extend_observation(pdf.id, at + timedelta(seconds=80))
    code = journal.add_observation(
        ObservationDraft(
            at + timedelta(seconds=80),
            WindowInfo("Editor", "Program"),
            "Editing source code.",
        ),
        journal.epoch,
    )
    assert code
    journal.extend_observation(code.id, at + timedelta(seconds=110))
    gateway = ScriptedGateway(
        [
            json.dumps(
                {
                    "segments": [
                        {
                            "observations": [pdf.id],
                            "mode": "start",
                            "title": "Reading",
                            "kind": "reading",
                            "intent": "Reviewing research",
                        },
                        {
                            "observations": [code.id],
                            "mode": "start",
                            "title": "Coding",
                            "kind": "coding",
                            "intent": "Editing source",
                        },
                    ],
                    "signals": {},
                }
            )
        ]
    )
    try:
        tick = await Mind(
            journal, gateway, skills, clock=lambda: at + timedelta(seconds=110)
        ).tick_now()
        assert tick
        totals = journal.portal.totals(TimeRange(at, at + timedelta(seconds=110)))
        assert {total.label: total.active for total in totals} == {
            "reading": 80,
            "coding": 30,
        }
    finally:
        journal.close()


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [["a"]],
        [["a"], ["a"]],
        [["a"], ["foreign"]],
        [["b"], ["a"]],
        [[], ["a", "b"]],
    ],
)
def test_response_schema_rejects_incomplete_or_invalid_partition(groups):
    from daybook.mind._tick import output_model

    model = output_model((), observations=("a", "b"))
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "segments": [
                    {
                        "observations": ids,
                        "mode": "start",
                        "title": "Task",
                        "kind": "other",
                        "intent": "",
                    }
                    for ids in groups
                ],
                "signals": {},
            }
        )


async def test_invalid_partition_uses_gateway_repair_before_commit(tmp_path):
    from daybook.model import ModelConfig, ModelGateway

    at = datetime(2026, 10, 11, 12, tzinfo=UTC)
    journal = Journal(tmp_path / "db", Bus())
    note = journal.add_observation(
        ObservationDraft(at, WindowInfo("App", "Task"), "Visible task"), journal.epoch
    )
    assert note

    class Book(SkillBook):
        def __init__(self):
            self.fired = []

        def active_signals(self):
            return ()

        async def react(self, tick):
            self.fired.append(tick)

    book = Book()
    requests = []

    async def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        assert journal.portal.latest_tick_evidence_until() is None
        ids = ["foreign"] if len(requests) == 1 else [note.id]
        result = {
            "segments": [
                {
                    "observations": ids,
                    "mode": "start",
                    "title": "Task",
                    "kind": "other",
                    "intent": "",
                }
            ],
            "signals": {},
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(result)}}]}
        )

    gateway = ModelGateway(ModelConfig())
    await gateway._client.aclose()
    gateway._client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://model/v1/"
    )
    try:
        tick = await Mind(journal, gateway, book, clock=lambda: at).tick_now()
        assert tick and len(requests) == 2 and book.fired == []
        assert "Validation errors" in requests[1]["messages"][0]["content"]
        observation = journal.portal.now().observation
        assert observation and observation.segment is not None
    finally:
        await gateway.close()
        journal.close()
