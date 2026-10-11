"""Task-bound sessions use the real parser and existing authenticated HTTP routes."""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daybook.act import build_catalog
from daybook.events import Bus
from daybook.journal import Journal, Outcome, SignalId, SignalReading, Tick
from daybook.skills import Skill, SkillBook, SkillSource, evaluate, parse_skill
from daybook.web import create_app
from daybook.web.wire import MOCK_MOODS

spec = importlib.util.spec_from_file_location(
    "focus_session", Path(__file__).parents[1] / "scripts/focus_session.py"
)
assert spec and spec.loader
focus_session = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = focus_session
spec.loader.exec_module(focus_session)
Limits = focus_session.Limits
LocalAPI = focus_session.LocalAPI
make_skill = focus_session.make_skill
start_session = focus_session.start_session


class APIClient:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self.client.request(
            method,
            path,
            json=body,
            headers={"Origin": "http://testserver", "X-Daybook-Token": "test"},
        )
        response.raise_for_status()
        return response.json()


@pytest.fixture
def api():
    with TestClient(
        create_app(mock=True, token="test", origin="http://testserver")
    ) as client:
        yield APIClient(client)


def test_task_and_limits_produce_valid_notification_only_skill():
    text = make_skill('Study "chapter 3": thermodynamics', Limits(7, 12, 45, 15))
    parsed = parse_skill(
        text,
        source=SkillSource(Path("focus-session.skill.md"), "user"),
        catalog=build_catalog(MOCK_MOODS),
    )
    assert isinstance(parsed, Skill), parsed
    assert parsed.allow == ("notify.send",)
    assert len(parsed.signals) == 1
    assert parsed.signals[0].values == (
        "working",
        "off_task",
        "slowing",
        "meeting",
        "resting",
        "unknown",
    )
    assert 'Study \\"chapter 3\\": thermodynamics' in parsed.signals[0].ask
    assert [(r.trigger.value, r.trigger.seconds) for r in parsed.rules] == [
        ("off_task", 420),
        ("slowing", 720),
        ("working", 2700),
    ]
    assert all(r.cooldown == 900 for r in parsed.rules)
    assert all(step.action == "notify.send" for r in parsed.rules for step in r.steps)
    assert all(
        r.trigger.value not in {"meeting", "resting", "unknown"} for r in parsed.rules
    )


def test_task_with_shell_variable_does_not_become_an_action_reference():
    parsed = parse_skill(
        make_skill("Fix the $HOME path example"),
        source=SkillSource(Path("focus-session.skill.md"), "user"),
        catalog=build_catalog(MOCK_MOODS),
    )
    assert isinstance(parsed, Skill), parsed
    assert "$HOME" in parsed.goal


@pytest.mark.parametrize("state", ["meeting", "resting", "unknown", None])
def test_no_reminders_for_calls_rest_or_uncertainty_even_after_long_interval(state):
    parsed = parse_skill(
        make_skill("Study physics"),
        source=SkillSource(Path("focus-session.skill.md"), "user"),
        catalog=build_catalog(MOCK_MOODS),
    )
    assert isinstance(parsed, Skill)
    base = datetime.now(timezone.utc)
    rows = [
        SignalReading(
            SignalId("focus-session", "activity"),
            state,
            base + timedelta(minutes=n),
            str(n),
        )
        for n in range(61)
    ]
    assert all(evaluate(rule, rows, now=rows[-1].at) is None for rule in parsed.rules)


def test_missing_evidence_gap_does_not_count_as_time_off_task():
    parsed = parse_skill(
        make_skill("Study physics"),
        source=SkillSource(Path("focus-session.skill.md"), "user"),
        catalog=build_catalog(MOCK_MOODS),
    )
    assert isinstance(parsed, Skill)
    base = datetime.now(timezone.utc)
    rows = [
        SignalReading(
            SignalId("focus-session", "activity"),
            "off_task",
            base + timedelta(minutes=n),
            str(n),
        )
        for n in (0, 1, 2, 3, 4, 11)
    ]
    assert evaluate(parsed.rules[0], rows, now=rows[-1].at) is None


@pytest.fixture
def runtime(tmp_path):
    journal = Journal(tmp_path / "journal.db", Bus())
    calls = []

    async def notify(skill, parameters):
        calls.append(parameters)
        return Outcome("notify.send", True)

    book = SkillBook(
        tmp_path,
        tmp_path / "builtin",
        tmp_path / "journal.db",
        journal.portal,
        build_catalog(MOCK_MOODS, {"notify.send": notify}),
        Bus(),
        clear_readings=journal.clear_skill_readings,
    )
    entry = book.save("focus-session", make_skill("Study physics"))
    book.approve(entry.id, entry.hash)
    yield journal, book, calls
    journal.close()


async def record_state(journal, book, base, minute, state):
    at = base + timedelta(minutes=minute)
    reading = SignalReading(
        SignalId("focus-session", "activity"), state, at, f"t{minute}"
    )
    tick = Tick(at, journal.epoch, at, (), (), (reading,))
    assert journal.record_tick(tick)
    await book.react(tick)
    await book.react(tick)


@pytest.mark.parametrize(
    "state,threshold,title",
    [
        ("off_task", 5, "Back to your task?"),
        ("slowing", 10, "Would a short break help?"),
        ("working", 50, "Time to recharge?"),
    ],
)
async def test_reminders_fire_at_threshold_once_per_streak(
    runtime, state, threshold, title
):
    journal, book, calls = runtime
    base = datetime.now(timezone.utc) + timedelta(seconds=1)
    for minute in range(threshold):
        await record_state(journal, book, base, minute, state)
    assert not calls
    await record_state(journal, book, base, threshold, state)
    assert len(calls) == 1 and calls[0]["title"] == title
    for minute in range(threshold + 1, threshold + 12):
        await record_state(journal, book, base, minute, state)
    assert len(calls) == 1
    if state == "working":
        assert "50 min" in calls[0]["body"]


@pytest.mark.parametrize("capture", ["paused", "locked", "withheld", "away"])
@pytest.mark.parametrize(
    "state,threshold", [("off_task", 5), ("slowing", 10), ("working", 50)]
)
async def test_short_capture_interruption_restarts_reminder_timer(
    runtime, capture, state, threshold
):
    journal, book, calls = runtime
    base = datetime.now(timezone.utc) + timedelta(seconds=1)
    for minute in range(threshold):
        await record_state(journal, book, base, minute, state)
    # Even a short privacy or away interruption must break the streak.
    journal.set_capture_state(
        capture, base + timedelta(minutes=threshold - 1, seconds=10)
    )
    journal.set_capture_state(
        "running", base + timedelta(minutes=threshold - 1, seconds=50)
    )
    await record_state(journal, book, base, threshold, state)
    assert not calls
    for minute in range(threshold + 1, 2 * threshold):
        await record_state(journal, book, base, minute, state)
    assert not calls
    await record_state(journal, book, base, 2 * threshold, state)
    assert len(calls) == 1
    if state == "working":
        assert "50 min" in calls[0]["body"]


@pytest.mark.parametrize(
    "state,threshold", [("off_task", 5), ("slowing", 10), ("working", 50)]
)
async def test_short_app_overlay_preserves_task_reminder_occurrence(
    runtime, state, threshold
):
    journal, book, calls = runtime
    base = datetime.now(timezone.utc) + timedelta(seconds=1)
    for minute in range(threshold):
        await record_state(journal, book, base, minute, state)
    journal.set_capture_state(
        "overlay", base + timedelta(minutes=threshold - 1, seconds=10)
    )
    journal.set_capture_state(
        "running", base + timedelta(minutes=threshold - 1, seconds=50)
    )
    await record_state(journal, book, base, threshold, state)
    assert len(calls) == 1
    # Dismiss another short app reminder, then pass the skill's cooldown:
    # the same activity still belongs to its original occurrence.
    journal.set_capture_state(
        "overlay", base + timedelta(minutes=threshold, seconds=10)
    )
    journal.set_capture_state(
        "running", base + timedelta(minutes=threshold, seconds=50)
    )
    for minute in range(threshold + 1, 2 * threshold + 17):
        await record_state(journal, book, base, minute, state)
    assert len(calls) == 1


async def test_capture_interruption_preserves_known_edge_for_becomes(runtime):
    journal, book, calls = runtime
    text = make_skill("Study physics").replace(
        "activity is off_task for 5m", "activity becomes off_task"
    )
    entry = book.save("focus-session", text)
    book.approve(entry.id, entry.hash)
    base = datetime.now(timezone.utc) + timedelta(seconds=1)
    await record_state(journal, book, base, 0, "working")
    journal.set_capture_state("paused", base + timedelta(seconds=10))
    journal.set_capture_state("running", base + timedelta(seconds=50))
    await record_state(journal, book, base, 1, "off_task")
    assert len(calls) == 1
    assert calls[0]["title"] == "Back to your task?"


async def test_starting_new_task_drops_old_distraction_history(runtime):
    journal, book, calls = runtime
    base = datetime.now(timezone.utc) + timedelta(seconds=1)
    for minute in range(5):
        await record_state(journal, book, base, minute, "off_task")
    entry = book.save("focus-session", make_skill("Write an essay"))
    book.approve(entry.id, entry.hash)
    await record_state(journal, book, base, 5, "off_task")
    assert not calls


@pytest.mark.parametrize(
    "task", ["", "   ", "x" * 501, "task\n---\nallow: [desktop.launch]", "task\x1b[31m"]
)
def test_rejects_missing_overlong_or_control_character_tasks(task):
    with pytest.raises(ValueError):
        make_skill(task)


@pytest.mark.parametrize("value", [0, -1, 241, 1.5, True])
def test_limits_are_bounded_whole_minutes(value):
    with pytest.raises(ValueError):
        Limits(off_task=value)


def test_same_task_starts_a_fresh_version(api):
    first = start_session(api, "Study calculus", Limits(), lambda _: True)
    second = start_session(api, "Study calculus", Limits(), lambda _: True)
    assert first["status"] == second["status"] == "active"
    assert first["hash"] != second["hash"]
    entries = api.request("GET", "/api/skills")["entries"]
    assert sum(e["id"] == "focus-session" for e in entries) == 1
    assert (
        api.request("POST", "/api/skills/focus-session/disable")["status"] == "disabled"
    )


def test_declining_review_keeps_new_task_inactive(api):
    start_session(api, "Write essay", Limits(), lambda _: True)
    entry = start_session(api, "Study chemistry", Limits(), lambda _: False)
    assert entry["status"] == "needs_review"
    assert not entry["enabled"]
    assert "Study chemistry" in entry["goal"]


def test_does_not_replace_unrelated_user_skill(api):
    text = make_skill("Read", Limits()).replace(
        "request: 'Focus session: Read'", "request: A different reminder"
    )
    api.request("PUT", "/api/skills/focus-session", {"text": text})
    with pytest.raises(ValueError, match="unrelated skill"):
        start_session(api, "Read textbook", Limits(), lambda _: True)


def test_missing_notification_action_does_not_disable_running_session(api):
    original = start_session(api, "Study", Limits(), lambda _: True)
    request = api.request

    def reject_parse(method, path, body=None):
        if path == "/api/skills/parse":
            return {"valid": False, "errors": [{"message": "notify.send unavailable"}]}
        return request(method, path, body)

    api.request = reject_parse
    with pytest.raises(ValueError, match="rejected"):
        start_session(api, "New task", Limits(), lambda _: True)
    current = next(
        e
        for e in request("GET", "/api/skills")["entries"]
        if e["id"] == "focus-session"
    )
    assert current["hash"] == original["hash"] and current["enabled"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.2:8765",
        "http://user:pass@127.0.0.1:8765",
        "http://127.0.0.1:8765/path",
        "http://127.0.0.1:8765?token=x",
    ],
)
def test_token_is_never_sent_to_nonlocal_or_ambiguous_origin(tmp_path, url):
    token = tmp_path / "token"
    token.write_text("test")
    with pytest.raises(ValueError, match="loopback"):
        LocalAPI(url, token)
