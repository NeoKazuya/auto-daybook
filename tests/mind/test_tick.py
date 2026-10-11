import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from daybook.events import Bus
from daybook.journal import (
    Continue,
    Journal,
    ObservationDraft,
    SignalId,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)
from daybook.mind import Mind, parse_tick
from daybook.model import ModelUnavailable, Priority, ScriptedGateway
from daybook.skills import Signal, SkillBook

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
        {"signals": raw},
        signals=SIGNALS,
        at=NOW,
        epoch=3,
        evidence_until=NOW,
        observations=(),
        has_open_segment=True,
    )
    assert tuple(r.value for r in tick.readings) == expected
    assert all(r.tick == tick.id for r in tick.readings)
    assert tick.segment is None


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
        {"segment": {**SEGMENT, "mode": mode}},
        signals=(),
        at=NOW,
        epoch=0,
        evidence_until=NOW,
        observations=ids,
        has_open_segment=open,
    )
    assert isinstance(tick.segment, type_)


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
    journal = Journal(tmp_path / "journal.db", Bus())
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


def reply(segment=SEGMENT):
    return json.dumps(
        {
            "segment": segment,
            "signals": {"test.focused": True, "test.mood": "focus", "test.level": 3.0},
        }
    )


async def test_scripted_full_tick_then_still_screen_readings(runtime):
    journal, clock, observation, skills = runtime
    gateway = ScriptedGateway([reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    first = await mind.tick_now()
    assert first and isinstance(first.segment, Start)
    assert journal.portal.now().segment
    assert skills.fired == [first]
    assert gateway.priorities == [Priority.TICK]
    assert not gateway.prompts[0].images
    assert await mind.tick_now() is None
    clock.advance(30)
    journal.extend_observation(observation.id, clock())
    second = await mind.tick_now()
    assert second and not second.observations and second.segment is None
    assert (
        len(
            journal.portal.readings(
                SIGNALS[0].id, TimeRange(NOW, clock() + timedelta(seconds=1))
            )
        )
        == 2
    )
    assert "screen has stayed the same" in gateway.prompts[1].user


async def test_schedule_debounce_spacing_and_evidence(runtime):
    journal, clock, observation, skills = runtime
    gateway = ScriptedGateway([reply(), reply(), reply()])

    async def sleep(seconds):
        clock.advance(seconds)
        if clock().second != NOW.second:
            journal.extend_observation(observation.id, clock())
        if len(skills.fired) >= 2:
            raise asyncio.CancelledError

    mind = Mind(journal, gateway, skills, tick_interval=90, clock=clock, sleep=sleep)
    mind.nudge(app_switched=True)
    with pytest.raises(asyncio.CancelledError):
        await mind.run()
    assert skills.fired[0].at == NOW + timedelta(seconds=20)
    assert skills.fired[1].at == NOW + timedelta(seconds=110)
    assert (skills.fired[1].at - skills.fired[0].at).total_seconds() >= 30
    assert len(gateway.prompts) == 2


async def test_no_evidence_or_paused_means_no_model_call(runtime):
    journal, clock, observation, skills = runtime
    gateway = ScriptedGateway([reply(), reply()])
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

    class ForgetGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            journal.forget_all()
            return await super().call(priority, prompt)

    gateway = ForgetGateway([reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    assert not skills.fired
    assert not journal.portal.now().readings


async def test_model_failure_does_not_retry_until_spacing(runtime):
    journal, clock, observation, skills = runtime
    gateway = ScriptedGateway([ModelUnavailable("offline"), reply()])
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
    assert model.model_validate({"signals": {"test.mood": "focus"}})
    with pytest.raises(ValidationError):
        model.model_validate({"signals": {"test.mood": "unlisted"}})
    with pytest.raises(ValidationError):
        model.model_validate({"signals": {"test.level": 11}})
    assert (
        "test.focused"
        in model.model_json_schema()["$defs"]["TickSignals"]["properties"]
    )


async def test_app_switch_nudge_during_model_call_is_preserved(runtime):
    journal, clock, observation, skills = runtime
    mind = None

    class SwitchGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            clock.advance(10)
            journal.extend_observation(observation.id, clock())
            assert mind
            mind.nudge(app_switched=True)
            return await super().call(priority, prompt)

    gateway = SwitchGateway([reply(), reply()])

    async def sleep(seconds):
        clock.advance(seconds)
        journal.extend_observation(observation.id, clock())
        if len(skills.fired) >= 2:
            raise asyncio.CancelledError

    mind = Mind(journal, gateway, skills, clock=clock, sleep=sleep)
    mind.nudge(app_switched=True)
    with pytest.raises(asyncio.CancelledError):
        await mind.run()
    assert [tick.at for tick in skills.fired] == [
        NOW + timedelta(seconds=20),
        NOW + timedelta(seconds=50),
    ]


async def test_gap_over_five_minutes_closes_segment_without_model(runtime):
    journal, clock, observation, skills = runtime
    gateway = ScriptedGateway([reply()])
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
    gateway = ScriptedGateway([reply(), reply(), reply(), reply()])
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
            Start("Work", "coding", "Implement"),
            (),
        )
    )
    clock.advance(30)
    journal.set_capture_state("paused", clock())
    clock.advance(240)
    journal.set_capture_state("locked", clock())
    clock.advance(241)
    mind = Mind(journal, ScriptedGateway([]), skills, clock=clock)
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
            Start("Work", "coding", "Implement"),
            (),
        )
    )
    clock.advance(30)
    gateway = ScriptedGateway([reply(), reply()])
    mind = Mind(journal, gateway, skills, clock=clock)
    assert await mind.tick_now() is None
    clock.advance(30)
    assert await mind.tick_now() is None
    assert not gateway.prompts
    journal.extend_observation(observation.id, clock())
    assert await mind.tick_now()
    assert len(gateway.prompts) == 1
