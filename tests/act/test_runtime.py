import asyncio
from dataclasses import replace

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


async def test_real_mpv_ipc_transcript_and_restart(tmp_path):
    from daybook.act._music import Mpv

    home = tmp_path / "home"
    home.mkdir()
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
