import json
from datetime import datetime, timedelta, timezone

import pytest

from daybook.act import build_catalog
from daybook.events import Bus
from daybook.journal import Journal, ObservationDraft, Outcome, TimeRange, WindowInfo
from daybook.mind import Mind
from daybook.model import ModelUnavailable, ScriptedGateway
from daybook.skills import SkillBook

TEXT = """---
id: nudge
name: Nudge
signals:
  present:
    ask: Is TikTok the active browser page?
    type: bool
rules:
- when: present is true for 0s
  do:
  - notify.send: {title: Back to work, body: Close TikTok}
allow: [notify.send]
---
Nudge on first detection.
"""


@pytest.fixture
def setup(tmp_path):
    now = [datetime.now(timezone.utc) + timedelta(seconds=1)]
    bus = Bus()
    journal = Journal(tmp_path / "db", bus)
    calls = []

    async def notify(ctx, args):
        calls.append(ctx.skill)
        return Outcome("notify.send", True)

    skills = SkillBook(
        tmp_path,
        tmp_path / "builtin",
        journal.path,
        journal.portal,
        build_catalog({}, {"notify.send": notify}),
        bus,
        clear_readings=journal.clear_skill_readings,
    )
    entry = skills.save("nudge", TEXT)
    skills.approve(entry.id, entry.hash)
    journal.set_capture_state("running", now[0])
    note = journal.add_observation(
        ObservationDraft(now[0], WindowInfo("Firefox", "TikTok"), "TikTok feed"),
        journal.epoch,
    )
    assert note
    yield journal, skills, now, note, calls
    journal.close()


async def test_first_skill_check_does_not_wait_for_or_consume_history(setup):
    journal, skills, now, note, calls = setup
    gateway = ScriptedGateway(['{"nudge.present":true}', '{"nudge.present":true}'])
    mind = Mind(journal, gateway, skills, clock=lambda: now[0])
    first = await mind._signals.check_now()
    assert first and calls == ["nudge"]
    assert gateway.prompts[0].max_tokens == 512
    assert gateway.prompts[0].effort == "none"
    assert "Historical notes" not in gateway.prompts[0].user
    assert not first.segments and not first.observations
    assert journal.portal.latest_tick_evidence_until() is None
    assert journal.portal.now().segment is None
    assert not journal.record_readings(first)  # Persistence rejects a replay.
    assert await mind._signals.check_now() is None
    now[0] += timedelta(seconds=9)
    journal.extend_observation(note.id, now[0])
    assert await mind._signals.check_now() is None
    now[0] += timedelta(seconds=1)
    journal.extend_observation(note.id, now[0])
    assert await mind._signals.check_now()
    assert calls == ["nudge"]  # Same held streak, not a second nudge.
    assert len(gateway.prompts) == 2

    # History can still consume the exact evidence already used by skills.
    gateway.replies.append(
        json.dumps(
            {
                "segments": [
                    {
                        "observations": [note.id],
                        "mode": "start",
                        "title": "TikTok",
                        "kind": "other",
                        "intent": "Browsing",
                    }
                ],
                "signals": {},
            }
        )
    )
    history = await mind._runner.tick_now()
    assert history and not history.readings
    assert journal.portal.now().segment is not None
    assert calls == ["nudge"]
    assert (
        len(
            journal.portal.readings(
                first.readings[0].signal,
                TimeRange(first.at, now[0] + timedelta(seconds=1)),
            )
        )
        == 2
    )


@pytest.mark.parametrize("phase", ["queued", "inflight"])
@pytest.mark.parametrize(
    "change", ["pause", "resume", "forget", "tab", "disable", "edit", "delete"]
)
async def test_stale_signal_answers_never_act(setup, phase, change):
    journal, skills, now, note, calls = setup

    def invalidate():
        now[0] += timedelta(seconds=1)
        if change in ("pause", "resume"):
            journal.set_capture_state("paused", now[0])
            if change == "resume":
                now[0] += timedelta(seconds=1)
                journal.set_capture_state("running", now[0])
        elif change == "forget":
            journal.forget_all()
        elif change == "tab":
            journal.add_observation(
                ObservationDraft(
                    now[0], WindowInfo("Firefox", "Work"), "Work document"
                ),
                journal.epoch,
            )
        elif change == "disable":
            skills.disable("nudge")
        elif change == "edit":
            skills.save("nudge", TEXT.replace("TikTok", "YouTube"))
        else:
            skills.delete("nudge")

    class ChangingGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            if phase == "queued":
                invalidate()
            built = await prompt() if callable(prompt) else prompt
            if built is None:
                return None
            answer = await super().call(priority, built)
            if phase == "inflight":
                invalidate()
            return answer

    gateway = ChangingGateway(['{"nudge.present":true}'])
    mind = Mind(journal, gateway, skills, clock=lambda: now[0])
    assert await mind._signals.check_now() is None
    assert not calls and not journal.portal.now().readings
    assert len(gateway.prompts) == int(phase == "inflight")


async def test_failure_is_bounded_and_fresh_evidence_can_retry(setup):
    journal, skills, now, note, calls = setup
    gateway = ScriptedGateway([ModelUnavailable("offline"), '{"nudge.present":true}'])
    mind = Mind(journal, gateway, skills, clock=lambda: now[0])
    assert await mind._signals.check_now() is None
    assert await mind._signals.check_now() is None
    now[0] += timedelta(seconds=10)
    journal.extend_observation(note.id, now[0])
    assert await mind._signals.check_now()
    assert calls == ["nudge"]


async def test_unobserved_questions_wait_without_consuming_attempt(setup):
    _, skills, now, _, calls = setup
    journal = setup[0]
    ready = [False]
    gateway = ScriptedGateway(['{"nudge.present":true}'])
    mind = Mind(
        journal,
        gateway,
        skills,
        clock=lambda: now[0],
        evidence_ready=lambda *_: ready[0],
    )
    assert await mind._signals.check_now() is None
    assert not gateway.prompts
    ready[0] = True
    assert await mind._signals.check_now()  # Same instant, no wasted interval.
    assert calls == ["nudge"]


async def test_run_checks_skills_before_the_history_interval_and_cancels_children(
    setup,
):
    import asyncio

    journal, skills, now, _, calls = setup
    sleeping = asyncio.Event()

    async def sleep(_):
        sleeping.set()
        await asyncio.Event().wait()

    gateway = ScriptedGateway(['{"nudge.present":true}'])
    mind = Mind(journal, gateway, skills, clock=lambda: now[0], sleep=sleep)
    running = asyncio.create_task(mind.run())
    try:
        await asyncio.wait_for(sleeping.wait(), 1)
        assert calls == ["nudge"]
        assert len(gateway.prompts) == 1
        assert journal.portal.now().segment is None
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
