import asyncio
import io
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, cast

import pytest
from PIL import Image

from daybook.act import Catalog
from daybook.events import Bus, TickRecorded
from daybook.journal import Journal, Observation, TimeRange, WindowInfo
from daybook.mind import Mind
from daybook.model import (
    ModelUnavailable,
    ObserveReplaced,
    Priority,
    Prompt,
    ScriptedGateway,
)
from daybook.sense import (
    PrivacySettings,
    Rect,
    ReplayScreen,
    ScreenState,
    Sense,
    VisibleWindow,
    gate,
)
from daybook.sense._gate import black, changed, excluded_visible
from daybook.skills import SkillBook

BASE = datetime.now(timezone.utc)
CODE = WindowInfo("Code", "example.py")
PASSWORD = WindowInfo("KeePassXC", "Fixture")
RECT = Rect(0, 0, 100, 100)


def state(**updates):
    return replace(
        ScreenState(CODE, (VisibleWindow("1", CODE, RECT),), False, 0), **updates
    )


@pytest.mark.parametrize(
    "facts,privacy,expected",
    [
        (state(locked=True), PrivacySettings(), "locked"),
        (state(locked=None), PrivacySettings(paused=True), "locked"),
        (state(), PrivacySettings(paused=True), "paused"),
        (state(active=None), PrivacySettings(), "locked"),
        (state(overlay=True, idle_ms=300_000), PrivacySettings(), "overlay"),
        (state(idle_ms=300_000), PrivacySettings(), "away"),
        (state(idle_ms=300_000, microphone_active=True), PrivacySettings(), "running"),
        (
            state(windows=(VisibleWindow("2", PASSWORD, RECT),)),
            PrivacySettings(),
            "withheld",
        ),
        (
            state(
                windows=(
                    VisibleWindow(
                        "2", WindowInfo("Firefox", "site — Private Browsing"), RECT
                    ),
                )
            ),
            PrivacySettings(),
            "withheld",
        ),
        (
            state(
                windows=(
                    VisibleWindow(
                        "2", WindowInfo("Firefox", "Daybook · Timeline"), RECT
                    ),
                )
            ),
            PrivacySettings(),
            "withheld",
        ),
        (state(), PrivacySettings(), "running"),
    ],
)
def test_gate(facts, privacy, expected):
    assert gate(facts, privacy) == expected


@pytest.mark.parametrize(
    "cover,expected",
    [
        ([], True),
        ([VisibleWindow("3", CODE, RECT)], False),
        ([VisibleWindow("3", CODE, Rect(0, 0, 50, 100))], True),
        ([VisibleWindow("3", CODE, RECT, 0.5)], True),
        (
            [
                VisibleWindow("3", CODE, Rect(0, 0, 50, 100)),
                VisibleWindow("4", CODE, Rect(50, 0, 50, 100)),
            ],
            False,
        ),
        (
            [
                VisibleWindow("3", CODE, Rect(0, 0, 60, 100)),
                VisibleWindow("4", CODE, Rect(40, 0, 50, 100)),
            ],
            True,
        ),
        ([VisibleWindow("3", CODE, Rect(-10, -10, 120, 120))], False),
    ],
)
def test_visible_union_and_transparency(cover, expected):
    facts = state(windows=(VisibleWindow("2", PASSWORD, RECT), *cover))
    assert excluded_visible(facts, PrivacySettings()) is expected


def test_black_and_change():
    assert black(bytes(576))
    assert black(bytes([5]) * 576)
    assert not black(bytes([6]) * 576)
    assert not changed(bytes([100]) * 576, bytes([107]) * 576)
    assert changed(bytes([100]) * 576, bytes([108]) * 576)


class FakeScreen:
    def __init__(self):
        self.facts = state()
        self.pixels = bytes([100]) * 576
        self.grabs = 0
        self.thumbnails = 0
        self.after_grab = None

    def state(self):
        return self.facts

    def thumbnail(self, expected):
        assert expected == self.facts
        self.thumbnails += 1
        return self.pixels

    def grab(self, expected):
        assert expected == self.facts
        self.grabs += 1
        if self.after_grab:
            self.facts = self.after_grab
        return b"jpeg-in-memory"


class FakeJournal(Journal):
    def __init__(self):
        self._test_epoch = 0
        self.rows = []
        self.states = []
        self.trims = []

    @property
    def epoch(self):
        return self._test_epoch

    @epoch.setter
    def epoch(self, value):
        self._test_epoch = value

    def add_observation(self, draft, epoch):
        if epoch != self.epoch:
            return None
        if self.rows:
            self.rows[-1] = replace(
                self.rows[-1],
                last_seen_at=min(self.rows[-1].last_seen_at, draft.started_at),
            )
        row = Observation(
            str(len(self.rows)),
            draft.started_at,
            draft.started_at,
            draft.window,
            draft.seen,
            draft.visible_text,
            None,
        )
        self.rows.append(row)
        return row

    def extend_observation(self, id, until):
        for i, row in enumerate(self.rows):
            if row.id == id:
                self.rows[i] = replace(row, last_seen_at=until)

    def trim_after(self, at):
        self.trims.append(at)
        self.epoch += 1
        self.rows = [
            replace(row, last_seen_at=min(at, row.last_seen_at))
            for row in self.rows
            if row.started_at <= at
        ]

    def set_capture_state(self, state, at):
        if not self.states or self.states[-1][0] != state:
            self.states.append((state, at))


def sense(tmp_path, replies=None):
    screen = FakeScreen()
    journal = FakeJournal()
    gw = ScriptedGateway(
        replies
        or ['{"seen":"An editor displays source code.","visible_text":"example.py"}']
        * 10
    )
    nudges = []
    obj = Sense(screen, journal, gw, Bus(), tmp_path, nudges.append)
    return obj, screen, journal, gw, nudges


async def poll(obj, seconds):
    await obj.poll(BASE + timedelta(seconds=seconds))
    if obj._tasks:
        await asyncio.gather(*tuple(obj._tasks))


async def test_unchanged_cooldown_and_switch(tmp_path):
    obj, screen, journal, gw, nudges = sense(tmp_path)
    await poll(obj, 0)
    await poll(obj, 5)
    assert len(gw.prompts) == 1
    screen.pixels = bytes([200]) * 576
    await poll(obj, 9)
    assert len(gw.prompts) == 1
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=9)
    await poll(obj, 10)
    assert len(gw.prompts) == 2
    assert journal.rows[0].last_seen_at == journal.rows[1].started_at
    screen.facts = state(active=WindowInfo("Firefox", "PDF"))
    await poll(obj, 11)
    assert len(gw.prompts) == 3
    assert nudges == [False, False, True]


@pytest.mark.parametrize(
    "gap_state", ["paused", "locked", "away", "overlay", "withheld"]
)
async def test_resume_breaks_continuity(tmp_path, gap_state):
    obj, screen, journal, gw, _ = sense(tmp_path)
    await poll(obj, 0)
    await poll(obj, 5)
    if gap_state == "paused":
        obj.set_privacy(PrivacySettings(paused=True))
    elif gap_state == "locked":
        screen.facts = state(locked=True)
    elif gap_state == "away":
        screen.facts = state(idle_ms=300_000)
    elif gap_state == "overlay":
        screen.facts = state(overlay=True)
    else:
        screen.facts = state(windows=(VisibleWindow("private", PASSWORD, RECT),))
    await poll(obj, 305)
    obj.set_privacy(PrivacySettings())
    screen.facts = state()
    await poll(obj, 306)
    assert journal.rows[-1].started_at == BASE + timedelta(seconds=306)
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=5)
    assert len(gw.prompts) == 2


async def test_away_removes_multiple_changed_observations(tmp_path):
    obj, screen, journal, gw, _ = sense(tmp_path)
    await poll(obj, 0)
    await poll(obj, 10)
    screen.pixels = bytes([200]) * 576
    await poll(obj, 100)
    screen.pixels = bytes([150]) * 576
    await poll(obj, 200)
    screen.facts = state(idle_ms=300_000)
    await poll(obj, 305)
    assert len(journal.rows) == 1
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=5)
    assert journal.trims == [BASE + timedelta(seconds=5)]
    assert journal.states[-1] == ("away", BASE + timedelta(seconds=5))


async def test_failed_observe_is_a_gap(tmp_path):
    obj, screen, journal, gw, _ = sense(
        tmp_path,
        [
            '{"seen":"code","visible_text":""}',
            ModelUnavailable("timeout"),
            '{"seen":"reading","visible_text":""}',
        ],
    )
    await poll(obj, 0)
    await poll(obj, 5)
    screen.pixels = bytes([200]) * 576
    await poll(obj, 10)
    await poll(obj, 25)
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=10)
    assert journal.rows[1].started_at == BASE + timedelta(seconds=25)
    assert journal.states[-2][0] == "withheld"


@pytest.mark.parametrize(
    "post",
    [
        state(locked=True),
        state(idle_ms=300_000),
        state(overlay=True),
        state(windows=(VisibleWindow("private", PASSWORD, RECT),)),
        state(windows=(VisibleWindow("changed", CODE, RECT),)),
        state(windows=(VisibleWindow("1", CODE, Rect(1, 0, 100, 100)),)),
    ],
)
async def test_post_grab_gate_and_window_change_discard(tmp_path, post):
    obj, screen, journal, gw, _ = sense(tmp_path)
    screen.after_grab = post
    await poll(obj, 0)
    assert not journal.rows
    assert not gw.prompts
    assert screen.grabs == 1


async def test_gate_prevents_all_pixels_and_black_prevents_model(tmp_path):
    obj, screen, journal, gw, _ = sense(tmp_path)
    screen.facts = state(locked=None)
    await poll(obj, 0)
    assert screen.thumbnails == screen.grabs == 0
    screen.facts = state()
    screen.pixels = bytes(576)
    await poll(obj, 5)
    assert screen.grabs == 0
    assert not gw.prompts
    assert journal.states[-1][0] == "locked"


async def test_privacy_generation_roundtrip_no_frame_files(tmp_path):
    obj, screen, journal, gw, _ = sense(tmp_path)
    settings = PrivacySettings(True, ("Keep*",), ("*private*",))
    obj.set_privacy(settings)
    assert obj.generation == 1
    assert journal.states[-1][0] == "paused"
    obj.set_privacy(settings)
    assert obj.generation == 1
    other, *_ = sense(tmp_path)
    assert other.privacy == settings
    obj.set_privacy(PrivacySettings())
    await poll(obj, 0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["privacy.json"]
    assert (tmp_path / "privacy.json").stat().st_mode & 0o777 == 0o600


class HeldGateway(ScriptedGateway):
    def __init__(self):
        super().__init__(['{"seen":"code","visible_text":""}'])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, priority, prompt):
        built = (
            await cast(Callable[[], Awaitable[Prompt | None]], prompt)()
            if callable(prompt)
            else prompt
        )
        if built is None:
            return None
        self.entered.set()
        await self.release.wait()
        return await super().call(priority, built)


@pytest.mark.parametrize("mutation", ["pause", "exclusions", "forget", "lock"])
async def test_result_consent_and_forget_barriers(tmp_path, mutation):
    screen, journal, gw = FakeScreen(), FakeJournal(), HeldGateway()
    obj = Sense(screen, journal, gw, Bus(), tmp_path, lambda _: None)
    await obj.poll(BASE)
    await gw.entered.wait()
    if mutation == "pause":
        obj.set_privacy(PrivacySettings(paused=True))
    elif mutation == "exclusions":
        obj.set_privacy(PrivacySettings(exclude_apps=("Code",)))
    elif mutation == "forget":
        journal.epoch += 1
    else:
        screen.facts = state(locked=True)
        await obj.poll(BASE + timedelta(seconds=5))
    gw.release.set()
    await asyncio.gather(*obj._tasks)
    assert journal.rows == []
    if mutation in ("pause", "lock"):
        assert journal.states[-1][0] == ("paused" if mutation == "pause" else "locked")


async def test_pending_result_caps_extended_prior_observation(tmp_path):
    obj, screen, journal, gw, _ = sense(tmp_path)
    await poll(obj, 0)
    held = HeldGateway()
    obj.gateway = held
    screen.pixels = bytes([200]) * 576
    await obj.poll(BASE + timedelta(seconds=10))
    await held.entered.wait()
    await obj.poll(BASE + timedelta(seconds=15))
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=15)
    held.release.set()
    await asyncio.gather(*obj._tasks)
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=10)
    assert journal.rows[1].last_seen_at == BASE + timedelta(seconds=15)


def test_replay_speed_step_and_image(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("daybook.sense._screen.time.monotonic", lambda: now[0])
    folder = Path(__file__).parents[2] / "fixtures/replay/example"
    screen = ReplayScreen(folder, speed=2)
    assert screen.state().active == CODE
    assert len(screen.thumbnail(screen.state())) == 576
    image = Image.open(io.BytesIO(screen.grab(screen.state())))
    assert image.format == "JPEG" and image.width <= 1280
    steps = json.loads((folder / "steps.json").read_text())
    now[0] = steps[1]["at"] / 2
    active = screen.state().active
    assert active is not None and active.app == "Evince"
    now[0] = 100000
    assert gate(screen.state(), PrivacySettings()) == "withheld"
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError):
        ReplayScreen(folder, speed=0)


async def test_wait_idle_and_close_only_own_tasks(tmp_path):
    screen, journal, gw = FakeScreen(), FakeJournal(), HeldGateway()
    obj = Sense(screen, journal, gw, Bus(), tmp_path, lambda _: None)
    await obj.poll(BASE)
    await gw.entered.wait()
    waiting = asyncio.create_task(obj.wait_idle())
    await asyncio.sleep(0)
    assert not waiting.done()
    gw.release.set()
    await waiting
    assert len(journal.rows) == 1
    await obj.close()
    await obj.poll(BASE + timedelta(seconds=20))
    assert len(journal.rows) == 1

    gw = HeldGateway()
    obj = Sense(screen, journal, gw, Bus(), tmp_path, lambda _: None)
    await obj.poll(BASE + timedelta(seconds=30))
    await gw.entered.wait()
    await obj.close()
    assert not obj._tasks
    assert len(journal.rows) == 1


def test_x11_server_title_bar_is_part_of_visibility(monkeypatch):
    from types import SimpleNamespace

    from Xlib import X

    from daybook.sense import X11Screen

    class Window:
        def get_attributes(self):
            return SimpleNamespace(map_state=X.IsViewable)

        def get_geometry(self):
            return SimpleNamespace(width=100, height=100)

        def get_wm_class(self):
            return ("keepassxc", "KeePassXC")

        def get_wm_name(self):
            return "private"

    root = SimpleNamespace(
        get_geometry=lambda: SimpleNamespace(width=200, height=200),
        translate_coords=lambda *_: SimpleNamespace(x=0, y=30),
        screensaver_query_info=lambda: SimpleNamespace(idle=0),
    )
    screen = X11Screen.__new__(X11Screen)
    screen._root = root
    monkeypatch.setattr(
        screen,
        "_display",
        SimpleNamespace(create_resource_object=lambda *_: Window()),
        raising=False,
    )
    screen._shell_pid = None

    def prop(window, name):
        return {
            "_NET_ACTIVE_WINDOW": [1],
            "_NET_CLIENT_LIST_STACKING": [1],
            "_NET_FRAME_EXTENTS": [0, 0, 30, 0],
        }.get(name)

    monkeypatch.setattr(screen, "_property", prop)
    monkeypatch.setattr(screen, "_locked", lambda: False)
    monkeypatch.setattr("daybook.sense._screen._command", lambda *_: "")
    facts = screen.state()
    assert facts.windows[0].rect == Rect(0, 0, 100, 130)
    covered_client = replace(
        facts,
        windows=(*facts.windows, VisibleWindow("cover", CODE, Rect(0, 30, 100, 100))),
    )
    assert gate(covered_client, PrivacySettings()) == "withheld"


async def test_older_active_observe_failure_trims_original_change(tmp_path):
    class FailingGateway(HeldGateway):
        async def call(self, priority, prompt):
            built = (
                await cast(Callable[[], Awaitable[Prompt | None]], prompt)()
                if callable(prompt)
                else prompt
            )
            self.entered.set()
            await self.release.wait()
            if built:
                raise ModelUnavailable("timeout")
            return None

    obj, screen, journal, _, _ = sense(tmp_path)
    await poll(obj, 0)
    failing = FailingGateway()
    obj.gateway = failing
    screen.pixels = bytes([150]) * 576
    await obj.poll(BASE + timedelta(seconds=10))
    await failing.entered.wait()
    screen.pixels = bytes([200]) * 576
    await obj.poll(BASE + timedelta(seconds=20))
    await asyncio.sleep(0)
    failing.release.set()
    await obj.wait_idle()
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=10)
    assert journal.trims[0] == BASE + timedelta(seconds=10)


@pytest.mark.parametrize(
    "failure", [None, ObserveReplaced("replaced"), ModelUnavailable("timeout")]
)
async def test_queued_observe_cannot_return_to_forgotten_time(tmp_path, failure):
    class AdmissionGateway(ScriptedGateway):
        def __init__(self):
            super().__init__(['{"seen":"code","visible_text":""}'])
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def call(self, priority, prompt):
            self.entered.set()
            await self.release.wait()
            if failure:
                raise failure
            return await super().call(priority, prompt)

    screen, journal, gw = FakeScreen(), FakeJournal(), AdmissionGateway()
    obj = Sense(screen, journal, gw, Bus(), tmp_path, lambda _: None)
    await obj.poll(BASE)
    await gw.entered.wait()
    journal.epoch += 1
    gw.release.set()
    await obj.wait_idle()
    assert not journal.rows and not gw.prompts and not journal.trims


async def test_forget_between_poll_and_task_admission(tmp_path):
    obj, _, journal, gw, _ = sense(tmp_path)
    await obj.poll(BASE)
    journal.epoch += 1
    await obj.wait_idle()
    assert not journal.rows and not gw.prompts


async def test_app_switch_nudge_survives_failed_observe(tmp_path):
    obj, screen, journal, gw, nudges = sense(
        tmp_path,
        [
            '{"seen":"code","visible_text":""}',
            ModelUnavailable("timeout"),
            '{"seen":"reading","visible_text":""}',
        ],
    )
    await poll(obj, 0)
    pdf = WindowInfo("Evince", "research.pdf")
    screen.facts = state(active=pdf, windows=(VisibleWindow("pdf", pdf, RECT),))
    await poll(obj, 1)
    await poll(obj, 5)
    assert nudges == [False, True]


async def test_same_app_document_change_survives_cooldown(tmp_path):
    obj, screen, journal, _, _ = sense(tmp_path)
    await poll(obj, 0)
    document = WindowInfo("Code", "other.py")
    screen.facts = state(active=document, windows=(VisibleWindow("2", document, RECT),))
    await poll(obj, 1)
    await poll(obj, 10)
    assert len(journal.rows) == 2 and journal.rows[-1].window == document


@pytest.mark.parametrize(
    "gap_state,gap_start,resume_at,expected_tick,initial_tick",
    [
        ("overlay", 85, 100, 120, False),
        ("overlay", 3, 5, 30, True),
        ("paused", 85, 100, 180, False),
        ("locked", 85, 100, 180, False),
    ],
)
async def test_sense_resume_mind_cadence(
    tmp_path, monkeypatch, gap_state, gap_start, resume_at, expected_tick, initial_tick
):
    # Drive the public Sense -> Mind callback with real journal/skill owners.
    now = [BASE]

    class SenseDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    monkeypatch.setattr("daybook.sense._sense.datetime", SenseDateTime)
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    book = SkillBook(
        tmp_path,
        tmp_path / "empty-builtins",
        tmp_path / "journal.db",
        journal.portal,
        Catalog((), {}),
        bus,
        clear_readings=journal.clear_skill_readings,
    )
    screen = FakeScreen()
    expected_observations = (
        [0, 5]
        if initial_tick
        else [0, 60, 100]
        if gap_state == "overlay"
        else [0, 60, 100, 160]
    )
    observe_gateway = ScriptedGateway(
        ['{"seen":"code","visible_text":""}'] * len(expected_observations)
    )
    tick_gateway = ScriptedGateway(
        [
            json.dumps(
                {
                    "segment": {
                        "mode": "continue",
                        "title": "Work",
                        "kind": "coding",
                        "intent": "Implementing",
                    },
                    "signals": {},
                }
            )
        ]
        * (2 if initial_tick else 1)
    )
    ticks = []

    async def sleep(seconds: float) -> None:
        while not events.empty():
            event = events.get_nowait()
            if isinstance(event, TickRecorded):
                ticks.append((now[0] - BASE).total_seconds())
        if now[0] >= BASE + timedelta(seconds=expected_tick + 1):
            raise asyncio.CancelledError
        now[0] += timedelta(seconds=seconds)
        elapsed = (now[0] - BASE).total_seconds()
        if elapsed == gap_start:
            if gap_state == "paused":
                obj.set_privacy(PrivacySettings(paused=True))
            else:
                screen.facts = state(**{gap_state: True})
        if elapsed == resume_at:
            obj.set_privacy(PrivacySettings())
            screen.facts = state()
        await obj.poll(now[0])
        await obj.wait_idle()
        if gap_start <= elapsed < resume_at:
            assert tick_gateway.priorities == ([Priority.TICK] if initial_tick else [])
            assert len(observe_gateway.prompts) == (1 if initial_tick else 2)

    mind = Mind(journal, tick_gateway, book, clock=lambda: now[0], sleep=sleep)
    obj = Sense(screen, journal, observe_gateway, bus, tmp_path, mind.nudge)
    try:
        with bus.subscribe() as events:
            await obj.poll(now[0])
            await obj.wait_idle()
            if initial_tick:
                assert await mind.tick_now()
            with pytest.raises(asyncio.CancelledError):
                await mind.run()
        assert ticks == ([0, expected_tick] if initial_tick else [expected_tick])
        assert len(observe_gateway.prompts) == len(expected_observations)
        assert [
            row.started_at
            for row in journal.portal.observations(
                TimeRange(BASE, BASE + timedelta(seconds=expected_tick + 2))
            )
        ] == [BASE + timedelta(seconds=second) for second in expected_observations]
        assert tick_gateway.priorities == [Priority.TICK] * len(ticks)
    finally:
        await obj.close()
        journal.close()


async def test_overlay_resume_nudge_survives_failed_observe(tmp_path):
    obj, screen, _, _, nudges = sense(
        tmp_path,
        [
            '{"seen":"code","visible_text":""}',
            ModelUnavailable("timeout"),
            '{"seen":"code","visible_text":""}',
        ],
    )
    await poll(obj, 0)
    screen.facts = state(overlay=True)
    await poll(obj, 1)
    screen.facts = state()
    await poll(obj, 2)
    assert nudges == [False]
    await poll(obj, 5)
    assert nudges == [False, True]
    await poll(obj, 15)
    assert nudges == [False, True]


@pytest.mark.parametrize("interruption", ["paused", "locked"])
async def test_overlay_resume_nudge_does_not_cross_privacy_gap(tmp_path, interruption):
    obj, screen, _, _, nudges = sense(tmp_path)
    await poll(obj, 0)
    screen.facts = state(overlay=True)
    await poll(obj, 1)
    if interruption == "paused":
        obj.set_privacy(PrivacySettings(paused=True))
    else:
        screen.facts = state(locked=True)
    await poll(obj, 2)
    obj.set_privacy(PrivacySettings())
    screen.facts = state()
    await poll(obj, 3)
    assert nudges == [False, False]


def questions_in(prompt):
    return json.loads(prompt.user.split("Evidence questions (context data):\n", 1)[1])


def question_sense(tmp_path, provider, gateway=None, journal=None):
    screen = FakeScreen()
    journal = journal or FakeJournal()
    gateway = gateway or ScriptedGateway(
        ['{"seen":"Visible task evidence.","visible_text":""}'] * 12
    )
    obj = Sense(
        screen,
        journal,
        gateway,
        Bus(),
        tmp_path,
        lambda _: None,
        question_provider=provider,
    )
    return obj, screen, journal, gateway


async def test_question_change_refreshes_still_frame_at_existing_cap(tmp_path):
    questions: list[tuple[str, ...]] = [("Is the result visible?",)]
    obj, screen, journal, gw = question_sense(tmp_path, lambda: questions[0])
    await poll(obj, 0)
    questions[0] = ("Is the status complete?",)
    for second in (1, 9):
        await poll(obj, second)
        assert screen.grabs == 1
    await poll(obj, 10)
    assert [questions_in(p) for p in gw.prompts] == [
        ["Is the result visible?"],
        ["Is the status complete?"],
    ]
    assert len(journal.rows) == 2
    await poll(obj, 20)
    assert screen.grabs == 2
    questions[0] = ()
    await poll(obj, 21)
    assert questions_in(gw.prompts[-1]) == []
    assert screen.grabs == 3


class QuestionAdmissionGateway(ScriptedGateway):
    def __init__(self):
        super().__init__(['{"seen":"Visible evidence.","visible_text":""}'] * 4)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, priority, prompt):
        self.entered.set()
        await self.release.wait()
        return await super().call(priority, prompt)


async def test_queued_questions_are_read_at_admission_before_pixels(tmp_path):
    questions = [("Old question?",)]
    gw = QuestionAdmissionGateway()
    obj, screen, journal, _ = question_sense(tmp_path, lambda: questions[0], gw)
    await obj.poll(BASE)
    await gw.entered.wait()
    assert screen.grabs == 0
    questions[0] = ("New question?",)
    await obj.poll(BASE + timedelta(seconds=10))
    assert len(obj._tasks) == 1 and screen.grabs == 0
    gw.release.set()
    await obj.wait_idle()
    assert questions_in(gw.prompts[0]) == ["New question?"]
    await poll(obj, 20)
    assert len(journal.rows) == screen.grabs == 1


async def test_inflight_question_change_refreshes_after_success_without_duplicates(
    tmp_path,
):
    questions = [("Initial question?",)]
    gw = HeldGateway()
    obj, screen, journal, _ = question_sense(tmp_path, lambda: questions[0], gw)
    await obj.poll(BASE)
    await gw.entered.wait()
    questions[0] = ("Later question?",)
    await obj.poll(BASE + timedelta(seconds=10))
    await obj.poll(BASE + timedelta(seconds=20))
    assert len(obj._tasks) == screen.grabs == 1
    gw.release.set()
    await obj.wait_idle()
    assert questions_in(gw.prompts[0]) == ["Initial question?"]
    gw.replies.append('{"seen":"Later evidence.","visible_text":""}')
    await poll(obj, 21)
    assert questions_in(gw.prompts[-1]) == ["Later question?"]
    assert len(journal.rows) == screen.grabs == 2
    await poll(obj, 31)
    assert screen.grabs == 2


@pytest.mark.parametrize("stage", ["poll", "admission"])
async def test_question_provider_failure_retries_without_pixels_or_empty_success(
    tmp_path, stage
):
    questions = [("Initial question?",)]
    failed = [False]

    def provider():
        if failed[0]:
            raise OSError("source temporarily unavailable")
        return questions[0]

    obj, screen, journal, gw = question_sense(tmp_path, provider)
    await poll(obj, 0)
    questions[0] = ("Updated question?",)
    failed[0] = stage == "poll"
    if stage == "admission":
        held = QuestionAdmissionGateway()
        obj.gateway = held
        await obj.poll(BASE + timedelta(seconds=10))
        await held.entered.wait()
        failed[0] = True
        held.release.set()
        await obj.wait_idle()
        assert not held.prompts
        obj.gateway = gw
    else:
        await poll(obj, 10)
    assert screen.grabs == 1 and len(journal.rows) == 1
    assert journal.states[-1][0] == "withheld"
    failed[0] = False
    await poll(obj, 20)
    assert questions_in(gw.prompts[-1]) == ["Updated question?"]
    assert screen.grabs == len(journal.rows) == 2


@pytest.mark.parametrize("blocked", ["paused", "locked", "away", "withheld", "black"])
async def test_question_provider_is_behind_privacy_and_black_frame_gate(
    tmp_path, blocked
):
    def forbidden():
        pytest.fail("privacy-blocked poll read skill questions")

    obj, screen, _, gw = question_sense(tmp_path, forbidden)
    if blocked == "paused":
        obj.set_privacy(PrivacySettings(paused=True))
    elif blocked == "locked":
        screen.facts = state(locked=True)
    elif blocked == "away":
        screen.facts = state(idle_ms=300_000)
    elif blocked == "withheld":
        screen.facts = state(windows=(VisibleWindow("private", PASSWORD, RECT),))
    else:
        screen.pixels = bytes(576)
    await poll(obj, 0)
    assert screen.grabs == 0 and not gw.prompts


async def test_question_refresh_keeps_pixel_change_scheduling_while_inflight(tmp_path):
    questions = [("Initial?",)]
    gw = HeldGateway()
    obj, screen, _, _ = question_sense(tmp_path, lambda: questions[0], gw)
    await obj.poll(BASE)
    await gw.entered.wait()
    questions[0] = ("Changed?",)
    screen.pixels = bytes([200]) * 576
    await obj.poll(BASE + timedelta(seconds=10))
    await asyncio.sleep(0)
    assert len(obj._tasks) == 2
    gw.replies.append('{"seen":"Changed frame.","visible_text":""}')
    gw.release.set()
    await obj.wait_idle()


async def test_questions_are_not_marked_fresh_after_rejected_write(tmp_path):
    class RejectOnceJournal(FakeJournal):
        reject = False

        def add_observation(self, draft, epoch):
            if self.reject:
                self.reject = False
                return None
            return super().add_observation(draft, epoch)

    questions = [("Initial?",)]
    journal = RejectOnceJournal()
    obj, screen, _, gw = question_sense(tmp_path, lambda: questions[0], journal=journal)
    await poll(obj, 0)
    questions[0] = ("Changed?",)
    journal.reject = True
    await poll(obj, 10)
    assert len(journal.rows) == 1
    await poll(obj, 20)
    assert [questions_in(p) for p in gw.prompts] == [
        ["Initial?"],
        ["Changed?"],
        ["Changed?"],
    ]
    assert len(journal.rows) == 2 and screen.grabs == 3


async def test_admission_question_reconciliation_cannot_capture_old_epoch(tmp_path):
    gw = QuestionAdmissionGateway()
    journal = FakeJournal()
    reconcile = [False]

    def provider():
        if reconcile[0]:
            journal.epoch += 1
        return ("Current question?",)

    obj, screen, _, _ = question_sense(tmp_path, provider, gw, journal)
    await obj.poll(BASE)
    await gw.entered.wait()
    reconcile[0] = True
    gw.release.set()
    await obj.wait_idle()
    assert not gw.prompts and not journal.rows and screen.grabs == 0


@pytest.mark.parametrize("tiny_change", [False, True])
async def test_still_frame_refreshes_small_visible_changes_within_one_minute(
    tmp_path, tiny_change
):
    # A small control/text change may vanish in the 32x18 change detector.
    # The real journal must still receive fresh facts without losing continuity.
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    screen = FakeScreen()
    gw = ScriptedGateway(
        [
            '{"seen":"Status: waiting.","visible_text":"waiting"}',
            '{"seen":"Status: complete.","visible_text":"complete"}',
        ]
    )
    nudges = []
    obj = Sense(screen, journal, gw, bus, tmp_path, nudges.append)
    try:
        await poll(obj, 0)
        if tiny_change:
            screen.pixels = screen.pixels[:-1] + bytes([150])
        for second in range(5, 60, 5):
            await poll(obj, second)
        assert len(gw.prompts) == 1
        await poll(obj, 60)
        rows = journal.portal.observations(
            TimeRange(BASE, BASE + timedelta(seconds=61))
        )
        assert [row.seen for row in rows] == [
            "Status: waiting.",
            "Status: complete.",
        ]
        assert rows[0].last_seen_at == rows[1].started_at
        assert rows[1].started_at == BASE + timedelta(seconds=60)
        assert gw.priorities == [Priority.OBSERVE, Priority.OBSERVE]
        assert screen.grabs == 2 and nudges == [False, False]
    finally:
        await obj.close()
        journal.close()


@pytest.mark.parametrize("phase", ["queued", "inflight"])
async def test_periodic_refresh_does_not_duplicate_pending_observe(tmp_path, phase):
    obj, screen, journal, gw, _ = sense(tmp_path)
    await poll(obj, 0)
    held = QuestionAdmissionGateway() if phase == "queued" else HeldGateway()
    obj.gateway = held
    screen.pixels = bytes([200]) * 576
    await obj.poll(BASE + timedelta(seconds=55))
    await held.entered.wait()
    for second in range(60, 101, 5):
        await obj.poll(BASE + timedelta(seconds=second))
        assert len(obj._tasks) == 1
        assert screen.grabs == (1 if phase == "queued" else 2)
    held.release.set()
    await obj.wait_idle()
    assert len(journal.rows) == screen.grabs == 2
    obj.gateway = gw
    # Admission at 100 is fresh; pixels captured at 55 have aged during inference.
    next_refresh = 160 if phase == "queued" else 115
    await poll(obj, next_refresh - 5)
    assert screen.grabs == 2
    await poll(obj, next_refresh)
    assert len(journal.rows) == screen.grabs == 3


@pytest.mark.parametrize(
    "blocked", ["paused", "locked", "away", "overlay", "withheld", "black"]
)
async def test_periodic_refresh_stays_behind_privacy_gate(tmp_path, blocked):
    obj, screen, journal, gw, _ = sense(tmp_path)
    await poll(obj, 0)
    await poll(obj, 55)
    if blocked == "paused":
        obj.set_privacy(PrivacySettings(paused=True))
    elif blocked == "locked":
        screen.facts = state(locked=True)
    elif blocked == "away":
        screen.facts = state(idle_ms=300_000)
    elif blocked == "overlay":
        screen.facts = state(overlay=True)
    elif blocked == "withheld":
        screen.facts = state(windows=(VisibleWindow("private", PASSWORD, RECT),))
    else:
        screen.pixels = bytes(576)
    await poll(obj, 60)
    assert len(gw.prompts) == screen.grabs == 1
    assert not obj._tasks
    obj.set_privacy(PrivacySettings())
    screen.facts = state()
    screen.pixels = bytes([100]) * 576
    await poll(obj, 65)
    assert len(gw.prompts) == screen.grabs == 2
    assert journal.rows[-1].started_at == BASE + timedelta(seconds=65)


@pytest.mark.parametrize("mutation", ["pause", "exclusions", "forget", "lock"])
async def test_periodic_refresh_discards_result_after_consent_or_forget(
    tmp_path, mutation
):
    obj, screen, journal, _, _ = sense(tmp_path)
    await poll(obj, 0)
    held = HeldGateway()
    obj.gateway = held
    await obj.poll(BASE + timedelta(seconds=60))
    await held.entered.wait()
    if mutation == "pause":
        obj.set_privacy(PrivacySettings(paused=True))
    elif mutation == "exclusions":
        obj.set_privacy(PrivacySettings(exclude_apps=("Code",)))
    elif mutation == "forget":
        journal.epoch += 1
    else:
        screen.facts = state(locked=True)
        await obj.poll(BASE + timedelta(seconds=65))
    held.release.set()
    await obj.wait_idle()
    assert len(journal.rows) == 1
    assert journal.rows[0].started_at == BASE
    assert screen.grabs == 2
    assert not obj._tasks


async def test_periodic_refresh_failure_leaves_gap_and_recovers(tmp_path):
    obj, _, journal, _, _ = sense(
        tmp_path,
        [
            '{"seen":"code","visible_text":""}',
            ModelUnavailable("timeout"),
            '{"seen":"recovered","visible_text":""}',
        ],
    )
    await poll(obj, 0)
    await poll(obj, 55)
    await poll(obj, 60)
    assert journal.states[-1] == ("withheld", BASE + timedelta(seconds=60))
    assert journal.trims == [BASE + timedelta(seconds=60)]
    await poll(obj, 65)
    assert journal.rows[0].last_seen_at == BASE + timedelta(seconds=60)
    assert journal.rows[1].started_at == BASE + timedelta(seconds=65)


async def test_periodic_refresh_keeps_normal_request_spacing_and_load(tmp_path):
    obj, screen, journal, gw, _ = sense(tmp_path)
    for second in range(0, 301, 5):
        await poll(obj, second)
    assert len(gw.prompts) == screen.grabs == 6
    assert [row.started_at for row in journal.rows] == [
        BASE + timedelta(seconds=second) for second in range(0, 301, 60)
    ]

    obj, screen, journal, gw, _ = sense(tmp_path)
    for second in range(61):
        screen.pixels = bytes([100 if (second // 10) % 2 == 0 else 200]) * 576
        await poll(obj, second)
    assert len(gw.prompts) == screen.grabs == 7
    assert [row.started_at for row in journal.rows] == [
        BASE + timedelta(seconds=second) for second in range(0, 61, 10)
    ]
