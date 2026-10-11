import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from daybook.act import (
    FakeOverlay,
    MusicLibrary,
    MusicPlayer,
    XdoDesktop,
    bind_handlers,
    build_catalog,
)
from daybook.events import Bus
from daybook.journal import (
    CaptureState,
    Journal,
    Portal,
    SignalId,
    SignalReading,
    Situation,
    Tick,
)
from daybook.skills import SkillBook


class MemoryPortal(Portal):
    def __init__(self, journal):
        self.journal = journal
        self.capture: CaptureState = "running"
        self.history = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.effects, self.hook_results, self.later_hooks = [], [], []
        self.boundary = None

    def now(self):
        return Situation(self.capture, None, None, ())

    def capture_spans(self, range):
        return self.journal.portal.capture_spans(range)

    def readings(self, signal, range):
        return [
            r
            for r in self.history
            if r.signal == signal and range.start <= r.at < range.end
        ]


@pytest.fixture
async def runtime(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "capture.sqlite", Bus())
    portal = MemoryPortal(journal)

    class Process:
        returncode = 0

        def __init__(self, value="", effect=None):
            self.value, self.effect = value, effect

        async def communicate(self):
            return self.value.encode(), b""

        async def wait(self):
            if self.effect == portal.boundary:
                portal.entered.set()
                await portal.release.wait()
            return 0

        def kill(self):
            raise AssertionError("eligibility must not kill an already-started helper")

    async def spawn(*args, **kwargs):
        queries = {
            ("xdotool", "getactivewindow"): "42",
            (
                "xprop",
                "-id",
                "42",
                "WM_CLASS",
            ): 'WM_CLASS(STRING) = "Navigator", "firefox"',
        }
        if args in queries:
            return Process(queries[args])
        if args[0] == "xdg-open":
            effect = "open_url"
        elif args[:3] == ("xdotool", "windowactivate", "--sync"):
            effect = (
                "new_tab"
                if args[-1] == "ctrl+t"
                else "type_url"
                if "type" in args
                else "navigate"
            )
        else:
            raise AssertionError(args)
        portal.effects.append(effect)
        return Process(effect=effect)

    # Only the external process boundary is replaced. No desktop command runs.
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    bus = Bus()
    overlay = FakeOverlay(bus)
    player = MusicPlayer(MusicLibrary(tmp_path, {}, ()), tmp_path, bus, overlay)
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    book = SkillBook(
        tmp_path,
        builtin,
        tmp_path / "db",
        portal,
        build_catalog({}, bind_handlers(player, overlay, XdoDesktop())),
        bus,
        clear_readings=lambda skill: None,
    )

    def install(steps):
        (builtin / "music.skill.md").write_text(
            "---\nid: music\nname: Navigation\nsignals:\n  ready: {ask: 'Is the page ready?', type: bool}\nrules:\n  - when: ready updates\n    do:\n"
            + steps
            + "allow: [desktop.open_url, desktop.open_tab]\n---\nNavigate.\n"
        )
        (builtin / "music.hook.py").write_text(
            "async def delayed(ctx):\n    ctx.portal.entered.set()\n    await ctx.portal.release.wait()\n    ctx.portal.hook_results.append(await ctx.actions.run('desktop.open_url', url='https://example.com'))\n\nasync def later(ctx):\n    ctx.portal.later_hooks.append('ran')\n"
        )
        assert book.entries()[0].status == "active", book.entries()[0].errors
        at = datetime.now(timezone.utc)
        reading = SignalReading(SignalId("music", "ready"), True, at, "one")
        portal.history = [reading]
        return Tick(at, 0, at, (), (), (reading,))

    yield book, portal, install, tmp_path / "db"
    await player.close()
    await overlay.close()
    journal.close()


def revoke(book, portal, reason):
    if reason == "pause":
        portal.capture = "paused"
    else:
        book.disable("music")


def outcomes(database):
    with sqlite3.connect(database) as db:
        return json.loads(db.execute("SELECT outcomes FROM firings").fetchone()[0])


@pytest.mark.parametrize("reason", ["pause", "disable"])
async def test_delayed_hook_cannot_act_or_run_later_steps_after_revocation(
    runtime, reason
):
    book, portal, install, database = runtime
    tick = install(
        "      - hook: delayed\n      - desktop.open_url: {url: 'https://later.example'}\n      - hook: later\n"
    )
    task = asyncio.create_task(book.react(tick))
    try:
        await asyncio.wait_for(portal.entered.wait(), 1)
        revoke(book, portal, reason)
    finally:
        portal.release.set()
    await asyncio.wait_for(task, 1)
    assert portal.effects == []
    assert portal.hook_results[0].detail == "skill_inactive"
    assert portal.later_hooks == []
    assert outcomes(database) == [
        {"action": "hook", "ok": False, "detail": "skill_inactive"}
    ]
    # The occurrence is consumed; repeating it never retries its partial work.
    portal.capture = "running"
    book.enable("music")
    await book.react(tick)
    assert portal.effects == [] and len(portal.hook_results) == 1


@pytest.mark.parametrize("reason", ["pause", "disable"])
async def test_new_tab_paused_mid_action_suppresses_typing_and_later_hook(
    runtime, reason
):
    book, portal, install, database = runtime
    tick = install(
        "      - desktop.open_tab: {url: 'https://example.com'}\n      - hook: later\n      - desktop.open_url: {url: 'https://later.example'}\n"
    )
    portal.boundary = "new_tab"
    task = asyncio.create_task(book.react(tick))
    try:
        await asyncio.wait_for(portal.entered.wait(), 1)
        revoke(book, portal, reason)
    finally:
        portal.release.set()
    await asyncio.wait_for(task, 1)
    assert portal.effects == ["new_tab"]  # Started helper finishes; no rollback.
    assert portal.later_hooks == []
    assert outcomes(database) == [
        {"action": "desktop.open_tab", "ok": False, "detail": "skill_inactive"}
    ]


@pytest.mark.parametrize("reason", ["pause_resume", "disable_enable"])
async def test_complete_revocation_pulse_still_cancels_pending_rule(runtime, reason):
    book, portal, install, database = runtime
    tick = install(
        "      - hook: delayed\n      - desktop.open_url: {url: 'https://later.example'}\n      - hook: later\n"
    )
    task = asyncio.create_task(book.react(tick))
    try:
        await asyncio.wait_for(portal.entered.wait(), 1)
        if reason == "pause_resume":
            portal.journal.set_capture_state("paused", datetime.now(timezone.utc))
            portal.journal.set_capture_state("running", datetime.now(timezone.utc))
            assert portal.journal.portal.now().capture == "running"
        else:
            book.disable("music")
            assert book.enable("music").status == "active"
        # Both changes complete before the blocked hook resumes or checks.
    finally:
        portal.release.set()
    await asyncio.wait_for(task, 1)
    assert portal.effects == [] and portal.later_hooks == []
    assert portal.hook_results[0].detail == "skill_inactive"
    assert outcomes(database) == [
        {"action": "hook", "ok": False, "detail": "skill_inactive"}
    ]
    at = datetime.now(timezone.utc)
    reading = SignalReading(SignalId("music", "ready"), True, at, "two")
    portal.history.append(reading)
    await book.react(Tick(at, 0, at, (), (), (reading,)))
    assert portal.effects == ["open_url", "open_url"]
    assert portal.later_hooks == ["ran"]
