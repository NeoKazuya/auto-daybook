import asyncio
import io
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, cast

import pytest
from PIL import Image

from daybook.events import Bus
from daybook.journal import Journal, Observation, WindowInfo
from daybook.model import ModelUnavailable, Prompt, ScriptedGateway
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

    def thumbnail(self):
        self.thumbnails += 1
        return self.pixels

    def grab(self):
        self.grabs += 1
        if self.after_grab:
            self.facts = self.after_grab
        return b"jpeg-in-memory"


class FakeJournal(Journal):
    def __init__(self):
        self._epoch = 0
        self.rows = []
        self.states = []
        self.trims = []

    @property
    def epoch(self):
        return self._epoch

    @epoch.setter
    def epoch(self, value):
        self._epoch = value

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
    assert len(screen.thumbnail()) == 576
    image = Image.open(io.BytesIO(screen.grab()))
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


async def test_queued_observe_cannot_return_to_forgotten_time(tmp_path):
    class AdmissionGateway(ScriptedGateway):
        def __init__(self):
            super().__init__(['{"seen":"code","visible_text":""}'])
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def call(self, priority, prompt):
            self.entered.set()
            await self.release.wait()
            return await super().call(priority, prompt)

    screen, journal, gw = FakeScreen(), FakeJournal(), AdmissionGateway()
    obj = Sense(screen, journal, gw, Bus(), tmp_path, lambda _: None)
    await obj.poll(BASE)
    await gw.entered.wait()
    journal.epoch += 1
    gw.release.set()
    await obj.wait_idle()
    assert not journal.rows and not gw.prompts


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
