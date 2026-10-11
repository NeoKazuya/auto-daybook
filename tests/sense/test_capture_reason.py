"""Current explanations do not change capture policy or retain window details."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from daybook.events import Bus, CaptureStateChanged, StatusChanged
from daybook.journal import Journal, TimeRange, WindowInfo
from daybook.model import ScriptedGateway
from daybook.sense import (
    CaptureStatus,
    PrivacySettings,
    Rect,
    ScreenState,
    Sense,
    VisibleWindow,
    gate,
)

BASE = datetime.now(UTC)
RECT = Rect(0, 0, 100, 100)
CODE = WindowInfo("Code", "example.py")
DAYBOOK = WindowInfo("Firefox", "Daybook · private journal sentinel")
PRIVATE = WindowInfo("KeePassXC", "private account sentinel")


def facts(window=CODE, **updates):
    return replace(
        ScreenState(window, (VisibleWindow("1", window, RECT),), False, 0),
        **updates,
    )


@pytest.mark.parametrize(
    "screen,privacy,expected",
    [
        (
            facts(DAYBOOK),
            PrivacySettings(),
            CaptureStatus("withheld", "daybook_visible"),
        ),
        (
            facts(PRIVATE),
            PrivacySettings(),
            CaptureStatus("withheld", "excluded_window"),
        ),
        (facts(DAYBOOK), PrivacySettings(exclude_titles=()), CaptureStatus("running")),
        (
            facts(DAYBOOK),
            PrivacySettings(exclude_apps=("Firefox",)),
            CaptureStatus("withheld", "excluded_window"),
        ),
        (
            facts(DAYBOOK),
            PrivacySettings(exclude_titles=("*private journal*",)),
            CaptureStatus("withheld", "excluded_window"),
        ),
        (
            facts(
                windows=(
                    VisibleWindow("1", DAYBOOK, RECT),
                    VisibleWindow("2", CODE, RECT),
                )
            ),
            PrivacySettings(),
            CaptureStatus("running"),
        ),
        (
            facts(
                windows=(
                    VisibleWindow("1", DAYBOOK, RECT),
                    VisibleWindow("2", CODE, Rect(0, 0, 50, 100)),
                )
            ),
            PrivacySettings(),
            CaptureStatus("withheld", "daybook_visible"),
        ),
        (
            facts(
                windows=(
                    VisibleWindow("1", DAYBOOK, RECT),
                    VisibleWindow("2", PRIVATE, Rect(0, 0, 50, 100)),
                )
            ),
            PrivacySettings(),
            CaptureStatus("withheld", "excluded_window"),
        ),
        (facts(locked=True), PrivacySettings(), CaptureStatus("locked")),
        (
            facts(locked=None),
            PrivacySettings(paused=True),
            CaptureStatus("locked", "screen_unavailable"),
        ),
        (facts(), PrivacySettings(paused=True), CaptureStatus("paused")),
        (
            facts(active=None),
            PrivacySettings(),
            CaptureStatus("locked", "screen_unavailable"),
        ),
    ],
)
def test_gate_preserves_decisions_with_safe_reason(screen, privacy, expected):
    assert gate(screen, privacy) == expected


class Screen:
    def __init__(self):
        self.facts = facts(DAYBOOK)
        self.pixels = bytes([100]) * 576
        self.fail_thumbnail = False

    def state(self):
        return self.facts

    def thumbnail(self, expected):
        assert expected == self.facts
        if self.fail_thumbnail:
            raise RuntimeError("private error sentinel")
        return self.pixels

    def grab(self, expected):
        assert expected == self.facts
        return b"fake boundary pixels"


@pytest.fixture
def real_sense(tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    screen = Screen()
    gateway = ScriptedGateway(['{"seen":"An editor","visible_text":""}'] * 4)
    sense = Sense(screen, journal, gateway, bus, tmp_path, lambda _: None)
    yield sense, screen, journal, bus
    journal.close()


async def test_reason_only_change_refreshes_without_new_history(real_sense):
    sense, screen, journal, bus = real_sense
    with bus.subscribe() as queue:
        await sense.poll(BASE)
        assert sense.status == CaptureStatus("withheld", "daybook_visible")
        first = []
        while not queue.empty():
            first.append(queue.get_nowait())
        assert any(isinstance(event, CaptureStateChanged) for event in first)

        screen.facts = facts(PRIVATE)
        await sense.poll(BASE + timedelta(seconds=5))
        assert sense.status == CaptureStatus("withheld", "excluded_window")
        assert queue.get_nowait() == StatusChanged("sense", "capture_reason_changed")
        assert queue.empty()
        await sense.poll(BASE + timedelta(seconds=10))
        assert queue.empty()
        # The reason is transient; changing it did not split the capture span.
        assert journal.portal.now().capture == "withheld"
        assert (
            len(
                journal.portal.capture_spans(
                    TimeRange(BASE, BASE + timedelta(seconds=15))
                )
            )
            == 1
        )

        screen.facts = facts()
        await sense.poll(BASE + timedelta(seconds=15))
        await sense.wait_idle()
        assert sense.status == CaptureStatus("running")
        assert "private" not in repr(sense.status)


@pytest.mark.parametrize("failure", ["thumbnail", "black", "questions", "observe"])
async def test_unavailable_explanation_clears_on_recovery(real_sense, failure):
    sense, screen, _, _ = real_sense
    screen.facts = facts()
    if failure == "thumbnail":
        screen.fail_thumbnail = True
    elif failure == "black":
        screen.pixels = bytes(576)
    elif failure == "questions":

        def unavailable():
            raise RuntimeError("private error sentinel")

        sense._question_provider = unavailable
    else:
        sense.gateway = ScriptedGateway([RuntimeError("private error sentinel")])
    await sense.poll(BASE)
    await sense.wait_idle()
    expected = (
        CaptureStatus("locked", "screen_unavailable")
        if failure in ("thumbnail", "black")
        else CaptureStatus("withheld", "observation_unavailable")
    )
    assert sense.status == expected
    screen.fail_thumbnail = False
    screen.pixels = bytes([100]) * 576
    sense._question_provider = lambda: ()
    sense.gateway = ScriptedGateway(['{"seen":"An editor","visible_text":""}'])
    await sense.poll(BASE + timedelta(seconds=5))
    await sense.wait_idle()
    assert sense.status == CaptureStatus("running")


async def test_stale_failure_preserves_newer_pause_and_status_fallback(real_sense):
    sense, _, journal, _ = real_sense
    await sense.poll(BASE)
    sense.set_privacy(replace(sense.privacy, paused=True))
    sense._failed(BASE, 0, "observe_failed", captured=True)
    assert sense.status == CaptureStatus("paused")
    # Journal can also be changed by app lifecycle; never attach an old reason.
    journal.set_capture_state("locked", BASE + timedelta(minutes=1))
    assert sense.status == CaptureStatus("locked")


async def test_post_inference_daybook_gate_keeps_specific_reason(real_sense):
    sense, screen, journal, _ = real_sense
    screen.facts = facts()

    class SwitchingGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            result = await super().call(priority, prompt)
            screen.facts = facts(DAYBOOK)
            return result

    sense.gateway = SwitchingGateway(['{"seen":"An editor","visible_text":""}'])
    await sense.poll(BASE)
    await sense.wait_idle()
    assert sense.status == CaptureStatus("withheld", "daybook_visible")
    assert journal.portal.now().observation is None


@pytest.mark.parametrize(
    "window,reason", [(DAYBOOK, "daybook_visible"), (PRIVATE, "excluded_window")]
)
async def test_post_grab_exclusion_keeps_specific_reason(real_sense, window, reason):
    sense, screen, journal, _ = real_sense
    screen.facts = facts()

    def grab(expected):
        assert expected == screen.facts
        screen.facts = facts(window)
        return b"discarded boundary pixels"

    screen.grab = grab
    await sense.poll(BASE)
    await sense.wait_idle()
    assert sense.status == CaptureStatus("withheld", reason)
    assert not sense.gateway.prompts
    assert journal.portal.now().observation is None


@pytest.mark.parametrize(
    "blocked,reason",
    [
        (facts(DAYBOOK), "daybook_visible"),
        (facts(PRIVATE), "excluded_window"),
        (facts(locked=None), "screen_unavailable"),
    ],
)
async def test_queued_admission_gate_keeps_specific_reason(real_sense, blocked, reason):
    sense, screen, journal, _ = real_sense
    screen.facts = facts()

    class SwitchingGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            screen.facts = blocked
            return await super().call(priority, prompt)

    def unexpected_grab(expected):
        raise AssertionError("An excluded queued request must not capture")

    screen.grab = unexpected_grab
    sense.gateway = SwitchingGateway([])
    await sense.poll(BASE)
    await sense.wait_idle()
    # Preserve the existing discard state; only the explanation changes.
    assert sense.status == CaptureStatus("withheld", reason)
    assert not sense.gateway.prompts
    assert journal.portal.now().observation is None
