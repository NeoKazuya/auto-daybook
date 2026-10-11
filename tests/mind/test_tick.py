import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from daybook.events import Bus
from daybook.journal import (
    Continue,
    Journal,
    ObservationDraft,
    SegmentAssignment,
    SignalId,
    SignalReading,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)
from daybook.mind import Mind as _Mind
from daybook.mind import parse_tick
from daybook.model import (
    ModelConfig,
    ModelGateway,
    ModelUnavailable,
    Priority,
    Prompt,
    ScriptedGateway,
)
from daybook.skills import Signal, SkillBook


class Mind(_Mind):
    """Exercise history scheduling in isolation from the separate skill loop."""

    async def tick_now(self):
        return await self._runner.tick_now()

    async def run(self):
        await self._runner.run()


class RecordingJournal(Journal):
    def __init__(self, *args):
        super().__init__(*args)
        self.recorded = []

    def record_tick(self, tick):
        accepted = super().record_tick(tick)
        if accepted:
            self.recorded.append(tick)
        return accepted


class BatchGateway(ScriptedGateway):
    async def call(self, priority, prompt):
        built = await prompt() if callable(prompt) else prompt
        if built is None:
            return None
        if self.replies and isinstance(self.replies[0], str):
            answer = json.loads(self.replies[0])
            answer["signals"] = {}
            for group in answer.get("segments", []):
                if group["observations"] == ["$batch"]:
                    group["observations"] = re.findall(
                        r"^([0-9a-f]{32}) ", built.user, re.M
                    )
            self.replies[0] = json.dumps(answer)
        return await super().call(priority, built)


NOW = datetime.now(timezone.utc) + timedelta(seconds=1)
SIGNALS = (
    Signal(SignalId("test", "focused"), "Focused now?", "bool"),
    Signal(
        SignalId("test", "mood"),
        "Music?",
        "music.mood",
        ("focus", "calm"),
        {"focus": "coding", "calm": "reading"},
    ),
    Signal(SignalId("test", "level"), "Level?", "number", minimum=0, maximum=10),
)
SEGMENT = {
    "mode": "continue",
    "title": "Editing code",
    "kind": "coding",
    "intent": "Probably implementing a feature",
}


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            {"test.focused": True, "test.mood": "focus", "test.level": 4},
            (True, "focus", 4.0),
        ),
        (
            {"test.focused": "false", "test.mood": "unknown", "test.level": "5"},
            (False, None, 5.0),
        ),
        (
            {"test.focused": 1, "test.level": True, "extra": "ignore"},
            (None, None, None),
        ),
        ({"test.level": float("nan")}, (None, None, None)),
        ({"test.level": 11}, (None, None, None)),
        ({}, (None, None, None)),
    ],
)
def test_parse_signal_coercion_missing_extra_and_bounds(raw, expected):
    tick = parse_tick(
        {"segments": [], "signals": raw},
        signals=SIGNALS,
        at=NOW,
        epoch=3,
        evidence_until=NOW,
        observations=(),
        has_open_segment=True,
    )
    assert tuple(r.value for r in tick.readings) == expected
    assert all(r.tick == tick.id for r in tick.readings)
    assert tick.segments == ()


@pytest.mark.parametrize(
    "open,mode,ids,type_",
    [
        (False, "continue", ("o",), Start),
        (True, "continue", ("o",), Continue),
        (True, "start", ("o",), Start),
        (True, "start", (), type(None)),
    ],
)
def test_segment_decisions(open, mode, ids, type_):
    tick = parse_tick(
        {
            "segments": [{**SEGMENT, "mode": mode, "observations": list(ids)}]
            if ids
            else []
        },
        signals=(),
        at=NOW,
        epoch=0,
        evidence_until=NOW,
        observations=ids,
        has_open_segment=open,
    )
    assert isinstance(tick.segments[0].decision if tick.segments else None, type_)


class SpyBook(SkillBook):
    def __init__(self):
        self.fired: list[Tick] = []

    def active_signals(self):
        return SIGNALS

    async def react(self, tick):
        self.fired.append(tick)


class Clock:
    def __init__(self):
        self.at = NOW

    def __call__(self):
        return self.at

    def advance(self, seconds):
        self.at += timedelta(seconds=seconds)


@pytest.fixture
def runtime(tmp_path):
    journal = RecordingJournal(tmp_path / "journal.db", Bus())
    clock = Clock()
    journal.set_capture_state("running", clock())
    observation = journal.add_observation(
        ObservationDraft(
            clock(), WindowInfo("Code", "editor.py"), "Python code is open."
        ),
        journal.epoch,
    )
    assert observation
    yield journal, clock, observation, SpyBook()
    journal.close()


def reply(segment=SEGMENT, ids=("$batch",)):
    return json.dumps(
        {
            "segments": [{**segment, "observations": list(ids)}] if segment else [],
            "signals": {"test.focused": True, "test.mood": "focus", "test.level": 3.0},
        }
    )


async def test_scripted_full_tick_then_still_screen_readings(runtime):
    journal, clock, observation, skills = runtime
    gateway = BatchGateway([reply(), reply(None)])
    mind = Mind(journal, gateway, skills, clock=clock)
    first = await mind.tick_now()
    assert first and isinstance(first.segments[0].decision, Start)
    assert journal.portal.now().segment
    assert journal.recorded == [first]
    assert gateway.priorities == [Priority.TICK]
    assert not gateway.prompts[0].images
    assert await mind.tick_now() is None
    clock.advance(30)
    journal.extend_observation(observation.id, clock())
    second = await mind.tick_now()
    assert second and not second.observations and not second.segments
    assert (
        len(
            journal.portal.readings(
                SIGNALS[0].id, TimeRange(NOW, clock() + timedelta(seconds=1))
            )
        )
        == 0
    )
    assert "screen has stayed the same" in gateway.prompts[1].user


async def test_schedule_debounce_spacing_and_evidence(runtime):
    journal, clock, observation, skills = runtime
    gateway = BatchGateway([reply(), reply(None), reply(None)])

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)
        if clock().second != NOW.second:
            journal.extend_observation(observation.id, clock())
        if len(journal.recorded) >= 2:
            raise asyncio.CancelledError

    mind = Mind(journal, gateway, skills, tick_interval=90, clock=clock, sleep=sleep)
    mind.nudge(app_switched=True)
    with pytest.raises(asyncio.CancelledError):
        await mind.run()
    assert journal.recorded[0].at == NOW + timedelta(seconds=20)
    assert journal.recorded[1].at == NOW + timedelta(seconds=110)
    assert (journal.recorded[1].at - journal.recorded[0].at).total_seconds() >= 30
    assert len(gateway.prompts) == 2


async def test_no_evidence_or_paused_means_no_model_call(runtime):
    journal, clock, observation, skills = runtime
    gateway = BatchGateway([reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now()
    clock.advance(90)
    assert await mind.tick_now() is None
    journal.extend_observation(observation.id, clock())
    journal.set_capture_state("paused", clock())
    assert await mind.tick_now() is None
    assert len(gateway.prompts) == 1


async def test_stale_epoch_drops_tick_and_no_reaction(runtime):
    journal, clock, observation, skills = runtime

    class ForgetGateway(BatchGateway):
        async def call(self, priority, prompt):
            journal.forget_all()
            return await super().call(priority, prompt)

    gateway = ForgetGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    assert not journal.recorded
    assert not journal.portal.now().readings


async def test_model_failure_does_not_retry_until_spacing(runtime):
    journal, clock, observation, skills = runtime
    gateway = BatchGateway([ModelUnavailable("offline"), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    clock.advance(1)
    assert await mind.tick_now() is None
    assert len(gateway.prompts) == 1
    clock.advance(29)
    assert await mind.tick_now()


def test_dynamic_schema_rejects_wrong_enum_and_constrains_each_signal():
    from daybook.mind._tick import output_model

    model = output_model(SIGNALS)
    values = {"test.focused": None, "test.mood": "focus", "test.level": None}
    assert model.model_validate({"segments": [], "signals": values})
    with pytest.raises(ValidationError):
        model.model_validate(
            {"segments": [], "signals": {**values, "test.mood": "unlisted"}}
        )
    with pytest.raises(ValidationError):
        model.model_validate({"segments": [], "signals": {**values, "test.level": 11}})
    assert (
        "test.focused"
        in model.model_json_schema()["$defs"]["TickSignals"]["properties"]
    )


async def test_app_switch_nudge_during_model_call_is_preserved(runtime):
    journal, clock, observation, skills = runtime
    mind = None

    class SwitchGateway(BatchGateway):
        async def call(self, priority, prompt):
            clock.advance(10)
            journal.extend_observation(observation.id, clock())
            assert mind
            mind.nudge(app_switched=True)
            return await super().call(priority, prompt)

    gateway = SwitchGateway([reply(), reply(None)])

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)
        journal.extend_observation(observation.id, clock())
        if len(journal.recorded) >= 2:
            raise asyncio.CancelledError

    mind = Mind(journal, gateway, skills, clock=clock, sleep=sleep)
    mind.nudge(app_switched=True)
    with pytest.raises(asyncio.CancelledError):
        await mind.run()
    assert [tick.at for tick in journal.recorded] == [
        NOW + timedelta(seconds=20),
        NOW + timedelta(seconds=50),
    ]


async def test_gap_over_five_minutes_closes_segment_without_model(runtime):
    journal, clock, observation, skills = runtime
    gateway = BatchGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now()
    journal.set_capture_state("paused", clock())
    clock.advance(301)
    assert await mind.tick_now() is None
    assert journal.portal.now().segment is None
    assert len(gateway.prompts) == 1


async def test_bounded_prompt_assigns_only_oldest_included_batch(runtime):
    journal, clock, first, skills = runtime
    all_ids = [first.id]
    for index in range(12):
        clock.advance(10)
        observation = journal.add_observation(
            ObservationDraft(
                clock(),
                WindowInfo("Code", f"task-{index}"),
                f"Task {index}: " + "x" * 1000,
            ),
            journal.epoch,
        )
        assert observation
        all_ids.append(observation.id)
    gateway = BatchGateway([reply(), reply(), reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    first_tick = await mind.tick_now()
    assert first_tick and 0 < len(first_tick.observations) < len(all_ids)
    assert list(first_tick.observations) == all_ids[: len(first_tick.observations)]
    assert all(id in gateway.prompts[0].user for id in first_tick.observations)
    assigned = list(first_tick.observations)
    while len(assigned) < len(all_ids):
        clock.advance(30)
        tick = await mind.tick_now()
        assert tick and tick.observations
        assigned.extend(tick.observations)
    assert assigned == all_ids


async def test_contiguous_mixed_capture_states_close_five_minute_gap(runtime):
    journal, clock, observation, skills = runtime
    journal.record_tick(
        Tick(
            clock(),
            journal.epoch,
            clock(),
            (observation.id,),
            (
                SegmentAssignment(
                    (observation.id,), Start("Work", "coding", "Implement")
                ),
            ),
            (),
        )
    )
    clock.advance(30)
    journal.set_capture_state("paused", clock())
    clock.advance(240)
    journal.set_capture_state("locked", clock())
    clock.advance(241)
    mind = Mind(journal, BatchGateway([]), skills, clock=clock)
    assert await mind.tick_now() is None
    assert journal.portal.now().segment is None


async def test_restart_does_not_call_model_for_persisted_evidence_without_signals(
    runtime,
):
    journal, clock, observation, skills = runtime
    assert journal.record_tick(
        Tick(
            clock(),
            journal.epoch,
            clock(),
            (observation.id,),
            (
                SegmentAssignment(
                    (observation.id,), Start("Work", "coding", "Implement")
                ),
            ),
            (),
        )
    )
    clock.advance(30)
    gateway = BatchGateway([reply(None)])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    clock.advance(30)
    assert await mind.tick_now() is None
    assert not gateway.prompts
    journal.extend_observation(observation.id, clock())
    assert await mind.tick_now()
    assert len(gateway.prompts) == 1


async def test_rejected_assignment_does_not_consume_evidence(runtime):
    journal, clock, observation, skills = runtime
    assert not journal.record_tick(
        Tick(clock(), journal.epoch, clock(), (observation.id,), (), ())
    )
    assert journal.portal.latest_tick_evidence_until() is None
    gateway = BatchGateway([reply()])
    tick = await Mind(journal, gateway, skills, clock=clock).tick_now()
    assert tick and tick.observations == (observation.id,)
    assert journal.portal.now().segment


def test_output_requires_every_signal_but_allows_explicit_unknown():
    from daybook.mind._tick import output_model

    model = output_model(SIGNALS)
    with pytest.raises(ValidationError):
        model.model_validate({"segments": [], "signals": {"test.focused": None}})
    assert model.model_validate(
        {"segments": [], "signals": {str(s.id): None for s in SIGNALS}}
    )


async def test_periodic_cadence_counts_from_call_start_not_finish(runtime):
    journal, clock, observation, skills = runtime

    class DelayedGateway(BatchGateway):
        async def call(self, priority, prompt):
            clock.advance(20)
            journal.extend_observation(observation.id, clock())
            return await super().call(priority, prompt)

    gateway = DelayedGateway([reply(), reply(None)])

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)
        journal.extend_observation(observation.id, clock())
        if len(journal.recorded) == 2:
            raise asyncio.CancelledError

    mind = Mind(journal, gateway, skills, 30, clock=clock, sleep=sleep)
    with pytest.raises(asyncio.CancelledError):
        await mind.run()
    assert [tick.at for tick in journal.recorded] == [
        NOW + timedelta(seconds=30),
        NOW + timedelta(seconds=60),
    ]


def test_segment_output_depends_on_new_evidence():
    from daybook.mind._tick import output_model

    values = {str(signal.id): None for signal in SIGNALS}
    new = output_model(SIGNALS, observations=("o",))
    assert new.model_validate(
        {"segments": [{**SEGMENT, "observations": ["o"]}], "signals": values}
    )
    with pytest.raises(ValidationError):
        new.model_validate({"segments": [], "signals": values})
    still = output_model(SIGNALS, observations=())
    assert still.model_validate({"segments": [], "signals": values})
    with pytest.raises(ValidationError):
        still.model_validate(
            {"segments": [{**SEGMENT, "observations": ["o"]}], "signals": values}
        )


async def test_backlog_batches_do_not_span_long_capture_gap(runtime):
    journal, clock, before, skills = runtime
    clock.advance(10)
    journal.set_capture_state("paused", clock())
    clock.advance(301)
    journal.set_capture_state("running", clock())
    after = journal.add_observation(
        ObservationDraft(clock(), WindowInfo("Code", "resumed.py"), "Resumed work."),
        journal.epoch,
    )
    assert after
    gateway = BatchGateway([reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    first = await mind.tick_now()
    assert first and first.observations == (before.id,)
    assert journal.portal.now().segment is None
    clock.advance(30)
    second = await mind.tick_now()
    assert second and second.observations == (after.id,)
    assert isinstance(second.segments[0].decision, Start)
    segments = journal.portal.segments(TimeRange(NOW, clock() + timedelta(seconds=1)))
    assert len(segments) == 2
    assert [tuple(o.id for o in segment.observations) for segment in segments] == [
        (before.id,),
        (after.id,),
    ]


async def test_resume_waits_for_fresh_observation_before_readings_and_actions(runtime):
    journal, clock, before, skills = runtime
    clock.advance(10)
    journal.set_capture_state("paused", clock())
    clock.advance(10)
    journal.set_capture_state("running", clock())
    gateway = BatchGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    # Sense publishes running before its asynchronous observation finishes.
    assert await mind.tick_now() is None
    assert not gateway.prompts and not journal.recorded
    assert not journal.portal.now().readings
    after = journal.add_observation(
        ObservationDraft(
            clock(), WindowInfo("Code", "resumed.py"), "Fresh resumed screen."
        ),
        journal.epoch,
    )
    assert after
    assert await mind.tick_now()
    assert len(gateway.prompts) == 1


async def test_in_flight_tick_is_discarded_after_pause_and_resume(runtime):
    journal, clock, observation, skills = runtime

    class ResumeGateway(BatchGateway):
        async def call(self, priority, prompt):
            clock.advance(1)
            journal.set_capture_state("paused", clock())
            clock.advance(1)
            journal.set_capture_state("running", clock())
            return await super().call(priority, prompt)

    gateway = ResumeGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    assert not journal.recorded and not journal.portal.now().readings
    assert journal.portal.latest_tick_evidence_until() is None


@pytest.mark.parametrize(
    "next_kind,expected_totals,decision_type",
    [
        ("coding", {"reading": 60, "coding": 30}, Start),
        ("reading", {"reading": 90}, Continue),
    ],
)
async def test_kind_transition_preserves_prior_durations_and_same_kind_continues(
    tmp_path, next_kind, expected_totals, decision_type
):
    journal = Journal(tmp_path / "db", Bus())
    clock, skills = Clock(), SpyBook()
    journal.set_capture_state("running", clock())
    first = journal.add_observation(
        ObservationDraft(
            clock(), WindowInfo("Evince", "paper.pdf"), "Research document."
        ),
        journal.epoch,
    )
    assert first
    reading = {**SEGMENT, "kind": "reading", "title": "Reading research"}
    changed = {**SEGMENT, "kind": next_kind}
    gateway = BatchGateway([reply(reading), reply(changed), reply(None)])
    mind = Mind(journal, gateway, skills, clock=clock)
    try:
        clock.advance(30)
        journal.extend_observation(first.id, clock())
        assert await mind.tick_now()
        original = journal.portal.now().segment
        assert original
        clock.advance(30)
        journal.extend_observation(first.id, clock())
        second = journal.add_observation(
            ObservationDraft(
                clock(),
                WindowInfo("Code", "task.py")
                if next_kind == "coding"
                else WindowInfo("Evince", "next.pdf"),
                "New visible activity.",
            ),
            journal.epoch,
        )
        assert second
        clock.advance(30)
        journal.extend_observation(second.id, clock())
        tick = await mind.tick_now()
        assert tick
        totals = journal.portal.totals(TimeRange(NOW, clock() + timedelta(seconds=1)))
        assert {total.label: total.active for total in totals} == expected_totals
        assert isinstance(tick.segments[0].decision, Continue)
        current = journal.portal.now().segment
        assert current
        assert (current.id == original.id) is (next_kind == "reading")
        clock.advance(30)
        journal.extend_observation(second.id, clock())
        still = await mind.tick_now()
        assert still and not still.observations and not still.segments
        after = journal.portal.now().segment
        assert after and after.id == current.id
    finally:
        journal.close()


@pytest.mark.parametrize("changed_id", [True, False])
async def test_queued_tick_rejects_replaced_current_but_keeps_extensions(
    runtime, changed_id
):
    journal, clock, observation, skills = runtime
    clock.advance(30)
    journal.extend_observation(observation.id, clock())
    observe_started = asyncio.Event()
    release_observe = asyncio.Event()
    tick_started = asyncio.Event()
    release_tick = asyncio.Event()

    async def transport(request):
        body = json.loads(request.content)
        if body["messages"][-1]["content"] == "observe":
            observe_started.set()
            await release_observe.wait()
            content = "New visible screen"
        else:
            tick_started.set()
            await release_tick.wait()
            content = json.dumps(
                {**json.loads(reply(ids=(observation.id,))), "signals": {}}
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    gateway = ModelGateway(ModelConfig())
    await gateway._client.aclose()
    gateway._client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://mock/v1/"
    )

    async def observe():
        seen = await gateway.call(Priority.OBSERVE, Prompt("Describe", "observe", str))
        clock.advance(1)
        if changed_id:
            assert seen
            journal.add_observation(
                ObservationDraft(clock(), WindowInfo("Meeting", "Call"), seen),
                journal.epoch,
            )
        else:
            journal.extend_observation(observation.id, clock())

    observing = asyncio.create_task(observe())
    try:
        await asyncio.wait_for(observe_started.wait(), 1)
        mind = Mind(journal, gateway, skills, clock=clock)
        ticking = asyncio.create_task(mind.tick_now())
        while not gateway.status().queue_depth:
            await asyncio.sleep(0)
        assert gateway.status().in_flight == "observe"
        release_observe.set()
        await observing
        if not changed_id:
            await asyncio.wait_for(tick_started.wait(), 1)
        release_tick.set()
        tick = await asyncio.wait_for(ticking, 1)
        if changed_id:
            assert not tick_started.is_set()  # Rejected before HTTP admission.
            assert tick is None
            assert not journal.recorded
            assert not journal.portal.now().readings
            assert journal.portal.now().segment is None
        else:
            assert tick and journal.recorded == [tick]
            assert journal.portal.now().segment
    finally:
        release_observe.set()
        release_tick.set()
        await gateway.close()


@pytest.mark.parametrize("owner", ["book", "callback"])
async def test_evidence_readiness_failure_recovers_without_consuming_attempt(
    runtime, monkeypatch, owner
):
    journal, clock, observation, skills = runtime
    unavailable = [True]
    original = skills.active_signals

    def active_signals():
        if unavailable[0] and owner == "book":
            raise OSError("source temporarily unavailable")
        return original()

    def ready(observation_id, questions):
        assert observation_id == observation.id
        assert questions == tuple(signal.ask for signal in SIGNALS)
        if unavailable[0] and owner == "callback":
            raise OSError("source temporarily unavailable")
        return True

    monkeypatch.setattr(skills, "active_signals", active_signals)
    gateway = BatchGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock, evidence_ready=ready)
    assert await mind.tick_now() is None
    assert not gateway.prompts and not journal.recorded
    unavailable[0] = False
    assert await mind.tick_now() is not None  # Same clock; no wasted 30 s cooldown.
    assert len(gateway.prompts) == 1


@pytest.mark.parametrize("reconcile_call", [2, 3])
async def test_evidence_callback_epoch_change_is_checked_after_callback(
    runtime, reconcile_call
):
    journal, clock, _, skills = runtime
    reads = [0]

    def ready(_observation_id, _questions):
        reads[0] += 1
        if reads[0] == reconcile_call:
            journal.clear_skill_readings("test")
        return True

    gateway = BatchGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock, evidence_ready=ready)
    assert await mind.tick_now() is None
    assert len(gateway.prompts) == (0 if reconcile_call == 2 else 1)
    assert not journal.recorded and not journal.portal.now().readings
    assert journal.portal.latest_tick_evidence_until() is None


async def test_many_small_observations_bound_response_groups_and_drain_backlog(runtime):
    journal, clock, first, skills = runtime
    ids = [first.id]
    for i in range(17):
        clock.advance(10)
        note = journal.add_observation(
            ObservationDraft(clock(), WindowInfo("App", str(i)), "Task"), journal.epoch
        )
        assert note
        ids.append(note.id)
    gateway = BatchGateway([reply(), reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assigned = []
    while len(assigned) < len(ids):
        tick = await mind.tick_now()
        assert tick and 1 <= len(tick.observations) <= 8
        assigned.extend(tick.observations)
        clock.advance(30)
    assert assigned == ids
    assert len(gateway.prompts) == 3
    assert all(prompt.max_tokens == 4096 for prompt in gateway.prompts)
    # Historical bounded groups do not substitute for the newest signal facts.
    assert "CURRENT evidence;" in gateway.prompts[0].user
    assert "App / 16: Task" in gateway.prompts[0].user


@pytest.mark.parametrize("drive", ["tick", "run"])
async def test_bounded_pre_gap_backlog_keeps_one_segment_until_drained(runtime, drive):
    journal, clock, first, skills = runtime
    before_ids = [first.id]
    for index in range(8):
        clock.advance(10)
        journal.extend_observation(before_ids[-1], clock())
        note = journal.add_observation(
            ObservationDraft(
                clock(), WindowInfo("Code", "same-task.py"), f"Step {index}"
            ),
            journal.epoch,
        )
        assert note
        before_ids.append(note.id)
    clock.advance(10)
    journal.extend_observation(before_ids[-1], clock())
    journal.set_capture_state("paused", clock())
    clock.advance(301)
    journal.set_capture_state("running", clock())
    after = journal.add_observation(
        ObservationDraft(clock(), WindowInfo("Code", "same-task.py"), "Resumed task"),
        journal.epoch,
    )
    assert after
    clock.advance(10)
    journal.extend_observation(after.id, clock())
    gateway = BatchGateway([reply(), reply(), reply()])

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)
        if len(journal.recorded) >= 3:
            raise asyncio.CancelledError

    mind = Mind(journal, gateway, skills, tick_interval=30, clock=clock, sleep=sleep)
    if drive == "run":
        with pytest.raises(asyncio.CancelledError):
            await mind.run()
    else:
        for _ in range(3):
            assert await mind.tick_now()
            clock.advance(30)

    assert [len(tick.observations) for tick in journal.recorded] == [8, 1, 1]
    assert [
        id for tick in journal.recorded for id in tick.observations
    ] == before_ids + [after.id]
    segments = journal.portal.segments(TimeRange(NOW, clock() + timedelta(seconds=1)))
    assert [
        tuple(note.id for note in segment.observations) for segment in segments
    ] == [
        tuple(before_ids),
        (after.id,),
    ]
    assert [segment.active for segment in segments] == [90, 10]
    assert [segment.open for segment in segments] == [False, True]
    assert not journal.portal.observations(
        TimeRange(NOW, clock() + timedelta(seconds=1)), unassigned_only=True
    )


async def test_explicit_same_kind_groups_remain_distinct_even_if_both_continue(runtime):
    journal, clock, first, skills = runtime
    clock.advance(30)
    journal.extend_observation(first.id, clock())
    second = journal.add_observation(
        ObservationDraft(
            clock(),
            WindowInfo("Code", "another-project.py"),
            "Debugging a different project",
        ),
        journal.epoch,
    )
    assert second
    clock.advance(30)
    journal.extend_observation(second.id, clock())
    response = {
        "segments": [
            {**SEGMENT, "observations": [first.id], "title": "First task"},
            {**SEGMENT, "observations": [second.id], "title": "Second task"},
        ],
        "signals": {"test.focused": True, "test.mood": "focus", "test.level": 3},
    }
    gateway = BatchGateway([json.dumps(response)])
    tick = await Mind(journal, gateway, skills, clock=clock).tick_now()
    assert tick
    segments = journal.portal.segments(TimeRange(NOW, clock() + timedelta(seconds=1)))
    assert [(s.title, s.active) for s in segments] == [
        ("First task", 30),
        ("Second task", 30),
    ]
    assert all(isinstance(group.decision, Start) for group in tick.segments)


async def test_legacy_consumed_unassigned_frontier_does_not_block_new_batch(runtime):
    journal, clock, first, skills = runtime
    notes = [first]
    for index in range(8):
        clock.advance(30)
        journal.extend_observation(notes[-1].id, clock())
        note = journal.add_observation(
            ObservationDraft(clock(), WindowInfo("Code", str(index)), "Code"),
            journal.epoch,
        )
        assert note
        notes.append(note)
    # Exactly the persisted shape produced by old null-segment ticks: a consumed
    # evidence watermark and readings, while those observations remain unassigned.
    old_tick = Tick(
        clock(),
        journal.epoch,
        clock(),
        (),
        (),
        (SignalReading(SIGNALS[0].id, False, clock(), ""),),
    )
    assert journal.record_tick(old_tick)
    before = journal.portal.readings(
        SIGNALS[0].id, TimeRange(NOW, clock() + timedelta(seconds=1))
    )
    clock.advance(30)
    journal.extend_observation(notes[-1].id, clock())
    gateway = BatchGateway([reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    current = await mind.tick_now()
    assert current and current.observations == (notes[-1].id,)
    assert journal.recorded == [old_tick, current]
    assert (
        journal.portal.readings(
            SIGNALS[0].id, TimeRange(NOW, clock() + timedelta(seconds=1))
        )[0]
        == before[0]
    )
    clock.advance(30)
    assert await mind.tick_now() is None
    assert len(gateway.prompts) == 1


async def test_legacy_unassigned_still_screen_retries_only_after_extension(runtime):
    journal, clock, note, skills = runtime
    clock.advance(30)
    journal.extend_observation(note.id, clock())
    assert journal.record_tick(Tick(clock(), journal.epoch, clock(), (), (), ()))
    gateway = BatchGateway([reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    assert not gateway.prompts
    clock.advance(30)
    journal.extend_observation(note.id, clock())
    current = await mind.tick_now()
    assert current and current.observations == (note.id,)


async def test_legacy_skipped_observations_do_not_hold_segment_across_gap(runtime):
    journal, clock, first, skills = runtime
    clock.advance(30)
    journal.extend_observation(first.id, clock())
    assert journal.record_tick(
        Tick(
            clock(),
            journal.epoch,
            clock(),
            (first.id,),
            (SegmentAssignment((first.id,), Start("Before", "coding", "Task")),),
            (),
        )
    )
    legacy = journal.add_observation(
        ObservationDraft(
            clock(), WindowInfo("Code", "legacy.py"), "Legacy pending code"
        ),
        journal.epoch,
    )
    assert legacy
    clock.advance(30)
    journal.extend_observation(legacy.id, clock())
    assert journal.record_tick(Tick(clock(), journal.epoch, clock(), (), (), ()))
    journal.set_capture_state("paused", clock())
    clock.advance(301)
    journal.set_capture_state("running", clock())
    after = journal.add_observation(
        ObservationDraft(clock(), WindowInfo("Code", "after.py"), "Resumed code"),
        journal.epoch,
    )
    assert after
    clock.advance(30)
    journal.extend_observation(after.id, clock())
    gateway = BatchGateway([reply()])
    current = await Mind(journal, gateway, skills, clock=clock).tick_now()
    assert current and current.observations == (after.id,)
    segments = journal.portal.segments(TimeRange(NOW, clock() + timedelta(seconds=1)))
    assert [tuple(n.id for n in s.observations) for s in segments] == [
        (first.id,),
        (after.id,),
    ]
