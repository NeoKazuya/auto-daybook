import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from daybook.act import (
    FakeDesktop,
    MusicLibrary,
    MusicPlayer,
    MusicTrack,
    Overlay,
    OverlayRequest,
    XdoDesktop,
    bind_handlers,
    build_catalog,
)
from daybook.events import Bus, OverlayHidden, OverlayShown


@pytest.fixture
def library(tmp_path):
    tracks = []
    for mood in ("focus", "calm", "break"):
        (tmp_path / mood).mkdir()
        (tmp_path / mood / "track.mp3").write_bytes(b"fixture")
        tracks.append(
            MusicTrack(
                f"{mood}/track.mp3", mood, "artist", "CC BY 4.0", "https://example.com"
            )
        )
    return MusicLibrary(
        tmp_path, {"focus": "work", "calm": "read", "break": "rest"}, tuple(tracks)
    )


def test_manifest_moods_licenses_and_unlisted_tracks(library):
    assert not library.check()
    assert replace(library, tracks=library.tracks[:-1]).check()
    assert replace(library, moods={"focus": "work"}).check()
    assert replace(
        library, tracks=(replace(library.tracks[0], license=""), *library.tracks[1:])
    ).check()
    (library.root / "focus/unlisted.mp3").write_bytes(b"bad")
    assert any("not in manifest" in e for e in library.check())


async def test_overlay_roundtrip_replacement_stale_dismiss_and_disconnect():
    bus = Bus()
    overlay = Overlay(bus)
    request = OverlayRequest("Rest", "Stand up")
    assert not (await overlay.show(request, "break")).ok
    overlay.shell_connected(True)
    with bus.subscribe() as queue:
        assert (await overlay.show(request, "break")).ok
        event = queue.get_nowait()
        assert isinstance(event, OverlayShown)
        first = event.id
        await overlay.show(OverlayRequest("Second", "Rest"), "other")
        event = queue.get_nowait()
        assert isinstance(event, OverlayShown) and event.id != first
        await overlay.hide(first)
        assert overlay.state and overlay.state.owner == "other"
        overlay.shell_connected(False)
        assert overlay.state and overlay.state.owner == "other"
        overlay.shell_connected(True)
        await overlay.hide()
        assert isinstance(queue.get_nowait(), OverlayHidden)
        assert overlay.state is None
    await overlay.close()


async def test_overlay_auto_expires():
    overlay = Overlay(Bus())
    overlay.shell_connected(True)
    await overlay.show(OverlayRequest("Rest", "Stand", minutes=0.0001), "break")
    await asyncio.sleep(0.02)
    assert overlay.state is None


@pytest.mark.parametrize("order", [("music", "break"), ("break", "music")])
async def test_same_tick_break_music_wins_in_either_order(library, tmp_path, order):
    bus = Bus()
    overlay = Overlay(bus)
    overlay.shell_connected(True)
    player = MusicPlayer(library, tmp_path / "home", bus, overlay)
    for skill in order:
        if skill == "break":
            await overlay.show(OverlayRequest("Rest", "Stand up"), "break")
            assert (await player.play_mood("break", skill="break", min_dwell=0)).ok
        else:
            assert (await player.play_mood("focus", skill="music")).ok
    assert player.status().mood == "break"
    assert (await player.play_mood("calm", skill="music")).detail == "overlay_owned"
    await overlay.close()
    await player.close()


async def test_dwell_is_per_skill_and_fake_never_starts_child(
    library, tmp_path, monkeypatch
):
    from daybook.act import _music

    now = [0.0]
    monkeypatch.setattr(_music.time, "monotonic", lambda: now[0])

    async def forbidden(*args, **kwargs):
        raise AssertionError("fake must not start a child")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    bus = Bus()
    overlay = Overlay(bus)
    player = MusicPlayer(library, tmp_path / "home", bus, overlay)
    assert (await player.play_mood("focus", skill="music")).ok
    now[0] = 30
    assert (await player.play_mood("calm", skill="music")).detail == "dwell"
    now[0] = 61
    await player.play_mood("break", skill="break", min_dwell=0)
    assert (await player.play_mood("focus", skill="music")).detail == ""
    assert player.status().mood == "focus"
    assert not (tmp_path / "home/mpv.sock").exists()
    assert sum(1 for c in player.transcript if c[0] == "loadfile") == 3
    assert (await player.stop()).ok and not player.status().playing
    await player.close()


async def test_capability_parameters_defaults_and_desktop_fakes(library, tmp_path):
    bus = Bus()
    overlay = Overlay(bus)
    desktop = FakeDesktop()
    player = MusicPlayer(library, tmp_path / "home", bus, overlay)
    catalog = build_catalog(library.moods, bind_handlers(player, overlay, desktop))
    actions = catalog.allowed(tuple(catalog.specs), "demo")
    assert not (
        await actions.run(
            "overlay.show", title="Bad", body="Rest", minutes=float("nan")
        )
    ).ok
    assert not (await actions.run("music.play_mood", mood="unlisted")).ok
    assert not (await actions.run("desktop.launch", app="Editor", unknown=True)).ok
    assert (await actions.run("desktop.open_url", url="https://example.com")).ok
    assert (await actions.run("desktop.launch", app="Editor")).ok
    assert (await actions.run("desktop.focus", app="*Editor*")).ok
    assert len(desktop.calls) == 3
    assert (await actions.run("music.play_mood", mood="focus")).ok
    assert (
        await actions.run("notify.send", title="Rest", body="Stand up")
    ).detail == "fake"
    await player.close()


async def test_xdo_invalid_requests_do_not_spawn(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid request spawned")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    desktop = XdoDesktop()
    assert not (await desktop.open_url("-bad")).ok
    assert not (await desktop.launch("bad;command")).ok


@pytest.fixture
def short_home():
    # macOS caps AF_UNIX socket paths at 104 bytes, and pytest's tmp_path there is
    # longer than that, so a home holding a socket lives under a short /tmp path.
    with tempfile.TemporaryDirectory(dir="/tmp") as short:
        home = Path(short) / "home"
        home.mkdir()
        yield home


async def test_real_mpv_ipc_transcript_and_restart(short_home):
    from daybook.act._music import Mpv

    home = short_home
    mpv = Mpv(home)

    # Existing external IPC peer simulates an mpv child without speakers.
    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return 0

    mpv.process = Process()  # type: ignore[assignment]
    commands = []

    async def peer(reader, writer):
        import json

        message = json.loads(await reader.readline())
        commands.append(message["command"])
        writer.write(
            (
                json.dumps({"request_id": message["request_id"], "error": "success"})
                + "\n"
            ).encode()
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(peer, path=mpv.socket)
    async with server:
        await mpv.command("set_property", "volume", 0)
        await mpv.command("loadfile", "focus/track.mp3", "replace")
    assert commands == [
        ["set_property", "volume", 0],
        ["loadfile", "focus/track.mp3", "replace"],
    ]
    await mpv.close()


async def test_overlay_survives_disconnect_and_reconnect():
    overlay = Overlay(Bus())
    overlay.shell_connected(True)
    await overlay.show(OverlayRequest("Rest", "Stand"), "break")
    before = overlay.state
    overlay.shell_connected(False)
    assert overlay.state == before
    overlay.shell_connected(True)
    assert overlay.state == before
    await overlay.close()


async def test_stop_action_cannot_silence_another_overlay_owner(library, tmp_path):
    bus = Bus()
    overlay = Overlay(bus)
    overlay.shell_connected(True)
    player = MusicPlayer(library, tmp_path / "home", bus, overlay)
    await overlay.show(OverlayRequest("Rest", "Stand"), "break")
    await player.play_mood("break", skill="break", min_dwell=0)
    catalog = build_catalog(
        library.moods, bind_handlers(player, overlay, FakeDesktop())
    )
    outcome = await catalog.allowed(("music.stop",), "other").run("music.stop")
    assert outcome.detail == "overlay_owned" and player.status().playing
    assert (await player.stop()).ok  # direct manual stop remains available to the user
    await player.close()
    await overlay.close()


async def test_player_observes_track_changes_and_recovers_dead_child(
    library, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from daybook.act import _music

    second = library.root / "focus/second.mp3"
    second.write_bytes(b"fixture")
    library = replace(
        library,
        tracks=(
            *library.tracks,
            MusicTrack(
                "focus/second.mp3",
                "Second track",
                "artist",
                "CC BY 4.0",
                "https://example.com",
            ),
        ),
    )
    peers = []

    class FakeMpv:
        def __init__(self, home):
            self.socket = tmp_path / "mpv.sock"
            self.socket.touch()
            self.process = SimpleNamespace(returncode=None)
            self.commands = []
            self.path = None
            peers.append(self)

        async def command(self, *args):
            if self.process is None or self.process.returncode is not None:
                self.process = SimpleNamespace(returncode=None)
                self.socket.touch()
            self.commands.append(args)
            if args[0] == "loadfile" and args[2] == "replace":
                self.path = args[1]
            return self.path if args[0] == "get_property" else None

        async def close(self):
            self.process = None
            self.socket.unlink(missing_ok=True)

    monkeypatch.setattr(_music, "Mpv", FakeMpv)
    original_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await original_sleep(0 if seconds == 0.125 else 0.001)

    monkeypatch.setattr(_music.asyncio, "sleep", fast_sleep)
    bus = Bus()
    player = MusicPlayer(
        library, tmp_path / "home", bus, Overlay(bus), real_effects=True
    )
    assert (await player.play_mood("focus", skill="music")).ok
    peer = peers[0]
    next_track = next(
        t
        for t in library.tracks
        if t.file.startswith("focus/") and t.title != player.status().title
    )
    peer.path = str(library.root / next_track.file)
    await original_sleep(0.015)
    assert player.status().title == next_track.title
    count = sum(c[0] == "loadfile" for c in peer.commands)
    peer.process.returncode = 1
    await original_sleep(0.015)
    assert sum(c[0] == "loadfile" for c in peer.commands) > count
    assert player.status().playing
    await player.close()
    assert peer.process is None


@pytest.mark.parametrize("operation", ["stop", "switch"])
async def test_ipc_failure_clears_status_and_recovery_target(
    library, tmp_path, monkeypatch, operation
):
    from daybook.act import _music

    player = MusicPlayer(library, tmp_path / "home", Bus(), Overlay(Bus()))
    assert (await player.play_mood("focus", skill="music")).ok

    async def failed(self, *args):
        raise OSError("IPC unavailable")

    monkeypatch.setattr(_music.Player, "command", failed)
    result = (
        await player.stop()
        if operation == "stop"
        else await player.play_mood("calm", skill="manual", min_dwell=0)
    )
    assert not result.ok
    assert not player.status().playing
    assert player.status().mood is None
    assert not player._player.playlist
    await player.close()


async def test_overlay_restart_restores_original_id_owner_and_expiry(tmp_path):
    first = Overlay(Bus(), home=tmp_path)
    first.shell_connected(True)
    assert (
        await first.show(OverlayRequest("Rest", "Stand", minutes=0.001), "break")
    ).ok
    shown = first.state
    assert shown is not None
    await first.close()
    restored = Overlay(Bus(), home=tmp_path)
    assert restored.state == shown
    restored.shell_connected(True)
    assert (await restored.hide("stale")).ok
    assert restored.state == shown
    await asyncio.sleep(0.08)
    assert restored.state is None
    assert Overlay(Bus(), home=tmp_path).state is None
    await restored.close()


async def test_overlay_dismiss_is_not_restored_and_expired_snapshot_is_dropped(
    tmp_path,
):
    overlay = Overlay(Bus(), home=tmp_path)
    overlay.shell_connected(True)
    await overlay.show(OverlayRequest("Rest", "Stand"), "break")
    await overlay.hide()
    assert Overlay(Bus(), home=tmp_path).state is None
    await overlay.show(OverlayRequest("Rest", "Stand", minutes=0.0001), "break")
    await overlay.close()
    await asyncio.sleep(0.02)
    assert Overlay(Bus(), home=tmp_path).state is None


async def test_mpv_start_fake_is_safe_and_idle_child_is_supervised(
    library, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from daybook.act import _music

    starts = []

    class Peer:
        def __init__(self, home):
            self.socket = home / "mpv.sock"
            self.process = None

        async def start(self):
            self.socket.parent.mkdir(parents=True, exist_ok=True)
            self.socket.touch()
            if self.process is None or self.process.returncode is not None:
                self.process = SimpleNamespace(returncode=None)
                starts.append(self.process)

        async def close(self):
            self.process = None
            self.socket.unlink(missing_ok=True)

    monkeypatch.setattr(_music, "Mpv", Peer)
    original_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await original_sleep(0.001)

    monkeypatch.setattr(_music.asyncio, "sleep", fast_sleep)
    bus = Bus()
    fake = MusicPlayer(library, tmp_path / "fake", bus, Overlay(bus))
    await fake.start()
    assert not starts and not (tmp_path / "fake/mpv.sock").exists()
    await fake.close()
    real = MusicPlayer(library, tmp_path / "real", bus, Overlay(bus), real_effects=True)
    await real.start()
    assert len(starts) == 1 and not real.status().playing
    starts[0].returncode = 1
    await original_sleep(0.015)
    assert len(starts) == 2 and not real.status().playing
    await real.close()


async def test_fake_overlay_records_without_shell_or_processes(monkeypatch):
    from daybook.act import FakeOverlay

    async def forbidden(*args, **kwargs):
        raise AssertionError("fake overlay must not create a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    bus = Bus()
    overlay = FakeOverlay(bus)
    overlay.shell_connected(False)
    with bus.subscribe() as queue:
        result = await overlay.show(OverlayRequest("Rest", "Stand"), "break")
        assert result.ok and result.detail == "fake"
        assert isinstance(queue.get_nowait(), OverlayShown)
        assert overlay.state and overlay.state.owner == "break"
        await overlay.hide()
        assert isinstance(queue.get_nowait(), OverlayHidden)
    await overlay.close()


@pytest.mark.parametrize("layout", [None, "fullscreen", "bubble"])
async def test_overlay_layout_catalog_event_and_restart(library, tmp_path, layout):
    import json

    bus = Bus()
    overlay = Overlay(bus, home=tmp_path)
    overlay.shell_connected(True)
    player = MusicPlayer(library, tmp_path / "music-home", bus, overlay)
    catalog = build_catalog(
        library.moods, bind_handlers(player, overlay, FakeDesktop())
    )
    actions = catalog.allowed(("overlay.show",), "reminder")
    expected = layout or "fullscreen"
    arguments = {} if layout is None else {"layout": layout}
    restored = None
    try:
        with bus.subscribe() as queue:
            result = await actions.run(
                "overlay.show", title="Break", body="Grab some water.", **arguments
            )
            assert result.ok
            event = queue.get_nowait()
            assert isinstance(event, OverlayShown)
            assert event.request.layout == expected
            assert overlay.state is not None and overlay.state.request == event.request
            saved = json.loads((tmp_path / "overlay.json").read_text())
            assert saved["request"]["layout"] == expected
            await overlay.close()
            restored = Overlay(bus, home=tmp_path)
            assert restored.state is not None
            assert restored.state.request.layout == expected
            assert restored.state.id == event.id
    finally:
        await overlay.close()
        if restored is not None:
            await restored.hide()
            await restored.close()
        await player.close()


async def test_legacy_overlay_snapshot_defaults_to_fullscreen(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    (tmp_path / "overlay.json").write_text(
        json.dumps(
            {
                "id": "legacy",
                "owner": "reminder",
                "request": {"title": "Break", "body": "Stand up."},
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            }
        )
    )
    overlay = Overlay(Bus(), home=tmp_path)
    try:
        assert overlay.state is not None
        assert overlay.state.request.layout == "fullscreen"
    finally:
        await overlay.hide()
        await overlay.close()


async def test_overlay_layout_rejects_invalid_catalog_value(library, tmp_path):
    bus = Bus()
    overlay = Overlay(bus)
    overlay.shell_connected(True)
    player = MusicPlayer(library, tmp_path / "home", bus, overlay)
    catalog = build_catalog(
        library.moods, bind_handlers(player, overlay, FakeDesktop())
    )
    try:
        result = await catalog.allowed(("overlay.show",)).run(
            "overlay.show", title="Break", body="Rest.", layout="popup"
        )
        assert not result.ok and result.detail == "invalid_parameters"
        assert overlay.state is None
    finally:
        await overlay.close()
        await player.close()


async def test_wait_step_sleeps_and_close_tab_goes_through_the_desktop(
    library, tmp_path, monkeypatch
):
    import daybook.act as act

    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(act.asyncio, "sleep", fake_sleep)
    desktop = FakeDesktop()
    overlay = Overlay(Bus())
    player = MusicPlayer(library, tmp_path / "home", Bus(), overlay)
    catalog = build_catalog(library.moods, bind_handlers(player, overlay, desktop))
    actions = catalog.allowed(
        ("overlay.show", "wait", "desktop.close_tab"), "instagram-nudge"
    )

    # Never closes unannounced: no warning from this skill yet.
    unwarned = await actions.run("desktop.close_tab", title="*Instagram*")
    assert not unwarned.ok and unwarned.detail == "no_warning_shown"
    overlay.shell_connected(True)
    shown = await actions.run(
        "overlay.show", title="Instagram?", body="Bro.", minutes=0.0834, layout="bubble"
    )
    assert shown.ok
    assert (await actions.run("wait", **{"for": "5s"})).ok and waits == [5]
    assert (await actions.run("desktop.close_tab", title="*Instagram*")).ok
    assert desktop.calls == [("close_tab", "*Instagram*")]
    # Another skill's warning doesn't count.
    other = catalog.allowed(("desktop.close_tab",), "other-skill")
    assert (await other.run("desktop.close_tab", title="*Instagram*")).detail == (
        "no_warning_shown"
    )
    await overlay.close()
    # Waits are 1s to 10s; anything else is refused before it can run.
    assert not (await actions.run("wait", **{"for": "11s"})).ok
    assert not (await actions.run("wait", **{"for": "0s"})).ok
    assert waits == [5]
    assert "wait" in catalog.describe_for_prompt()


async def test_pausing_during_the_wait_stops_the_close(tmp_path):
    from datetime import datetime, timedelta, timezone

    from daybook.journal import (
        Outcome,
        Portal,
        SignalId,
        SignalReading,
        Situation,
        Tick,
    )
    from daybook.skills import SkillBook

    class Portal_(Portal):
        def __init__(self):
            self.capture = "running"
            self.history = []

        def now(self):
            return Situation(self.capture, None, None, ())  # type: ignore[arg-type]

        def capture_spans(self, range):
            return []

        def readings(self, signal, range):
            return [r for r in self.history if r.signal == signal]

    portal = Portal_()
    closed = []

    async def wait(skill, args):
        portal.capture = "paused"  # the user pauses Daybook mid-wait
        return Outcome("wait", True)

    async def close(skill, args):
        closed.append(args)
        return Outcome("desktop.close_tab", True)

    book = SkillBook(
        tmp_path,
        tmp_path / "builtin",
        tmp_path / "journal.db",
        portal,
        build_catalog({"focus": "work"}, {"wait": wait, "desktop.close_tab": close}),
        Bus(),
        clear_readings=lambda skill: None,
    )
    saved = book.save(
        "nudge",
        """---
id: nudge
name: Nudge
signals:
  on_instagram:
    ask: Is Instagram the active tab?
    type: bool
rules:
  - when: on_instagram updates
    do:
      - wait: { for: 5s }
      - desktop.close_tab: { title: "*Instagram*" }
allow: [wait, desktop.close_tab]
---
Close Instagram.
""",
    )
    assert saved.status == "needs_review", saved.errors
    book.approve("nudge", saved.hash)
    when = datetime.now(timezone.utc) + timedelta(seconds=1)
    reading = SignalReading(SignalId("nudge", "on_instagram"), True, when, "t1")
    portal.history.append(reading)
    await book.react(Tick(when, 0, when, (), None, (reading,)))
    assert portal.capture == "paused", "the rule fired and its wait step ran"
    assert closed == []


async def test_xdo_close_tab_targets_only_the_checked_window(monkeypatch):
    from daybook.act import _desktop

    ran = []
    screen = {
        "window": "4242",
        "title": "(3) Instagram • Reels — Mozilla Firefox",
        "class": 'WM_CLASS(STRING) = "Navigator", "firefox"',
    }

    async def output(*args):
        if args == ("xdotool", "getactivewindow"):
            return screen["window"]
        if args == ("xdotool", "getwindowname", screen["window"]):
            return screen["title"]
        if args == ("xprop", "-id", screen["window"], "WM_CLASS"):
            return screen["class"]
        return None

    async def command(action, *args):
        ran.append(args)
        return _desktop.Outcome(action, True)

    monkeypatch.setattr(_desktop, "output", output)
    monkeypatch.setattr(_desktop, "command", command)
    desktop = XdoDesktop()

    assert (await desktop.close_tab("*instagram*")).ok
    assert ran == [
        (
            "xdotool",
            "windowactivate",
            "--sync",
            "4242",
            "key",
            "--clearmodifiers",
            "ctrl+w",
        )
    ]

    screen["title"] = "auto-daybook — Visual Studio Code"
    moved_on = await desktop.close_tab("*Instagram*")
    assert not moved_on.ok and moved_on.detail == "tab_not_active"

    # A matching title in a non-browser (an editor document) is never closed.
    screen["title"] = "Instagram campaign.md — Visual Studio Code"
    screen["class"] = 'WM_CLASS(STRING) = "code", "Code"'
    assert (await desktop.close_tab("*Instagram*")).detail == "not_a_browser"
    screen["class"] = 'WM_CLASS(STRING) = "google-chrome", "Google-chrome"'
    screen["title"] = "Instagram - Google Chrome"
    assert (await desktop.close_tab("*Instagram*")).ok
    assert len(ran) == 2
    ran.pop()

    screen["window"] = ""
    assert (await desktop.close_tab("*Instagram*")).detail == "window_unreadable"

    for broad in ("*", "*?*", "[a]*", " * ", "[a-z]*", "[a-z][a-z][a-z]*", "*[!x]*"):
        assert (await desktop.close_tab(broad)).detail == "pattern_too_broad"
    assert len(ran) == 1


def test_instagram_nudge_builtin_parses_against_the_catalog(library):
    from pathlib import Path

    from daybook.skills import SkillSource, parse_skill

    path = Path(__file__).parents[2] / "skills/builtin/instagram-nudge.skill.md"
    catalog = build_catalog(library.moods)
    skill = parse_skill(
        path.read_text(),
        source=SkillSource(path, "builtin"),
        catalog=catalog,
        hook_text=None,
    )
    assert not isinstance(skill, list), skill
    assert [s.action for s in skill.rules[0].steps] == [
        "overlay.show",
        "wait",
        "desktop.close_tab",
    ]
