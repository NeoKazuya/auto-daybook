import asyncio
from types import SimpleNamespace

import pytest

from daybook.act import (
    FakeDesktop,
    FakeOverlay,
    MusicLibrary,
    MusicPlayer,
    MusicTrack,
    bind_handlers,
    build_catalog,
)
from daybook.events import Bus


@pytest.fixture
def library(tmp_path):
    tracks = []
    for mood in ("focus", "calm"):
        (tmp_path / mood).mkdir()
        (tmp_path / mood / "track.mp3").write_bytes(b"fixture")
        tracks.append(
            MusicTrack(
                f"{mood}/track.mp3", mood, "Artist", "CC0", "https://example.org"
            )
        )
    return MusicLibrary(tmp_path, {"focus": "work", "calm": "read"}, tuple(tracks))


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("test must not start real music or desktop processes")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)


@pytest.mark.parametrize(
    "action,args",
    [("music.play_mood", {"mood": "calm", "min_dwell": "0s"}), ("music.stop", {})],
)
@pytest.mark.parametrize("reason", ["pause", "disable"])
async def test_queued_music_request_is_not_admitted_after_revocation(
    library, tmp_path, action, args, reason
):
    state = SimpleNamespace(capture="running", enabled=True)
    bus = Bus()
    overlay = FakeOverlay(bus)
    player = MusicPlayer(library, tmp_path / "home", bus, overlay)
    assert (await player.play_mood("focus", skill="manual")).ok
    baseline = player.transcript
    actions = build_catalog(
        library.moods, bind_handlers(player, overlay, FakeDesktop())
    ).allowed(
        (action,), "demo", can_act=lambda: state.capture == "running" and state.enabled
    )
    await player._player.lock.acquire()
    task = asyncio.create_task(actions.run(action, **args))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        if reason == "pause":
            state.capture = "paused"
        else:
            state.enabled = False
    finally:
        player._player.lock.release()
    result = await task
    assert (
        player.transcript == baseline
        and player.status().mood == "focus"
        and player.status().playing
    )
    assert result.detail == "skill_inactive" and not result.ok
    await player.close()
    await overlay.close()


async def test_revocation_during_idle_mpv_startup_prevents_first_ipc_effect(
    library, tmp_path, monkeypatch
):
    state = SimpleNamespace(enabled=True, connections=0)
    entered, release = asyncio.Event(), asyncio.Event()
    bus = Bus()
    overlay = FakeOverlay(bus)
    player = MusicPlayer(library, tmp_path / "home", bus, overlay, real_effects=True)
    mpv = player._player.mpv

    class Process:
        returncode = None

        def terminate(self):
            raise AssertionError("revocation must not terminate a started child")

    async def spawn(*args, **kwargs):
        assert args[0] == "mpv"
        entered.set()
        await release.wait()
        mpv.socket.touch()
        return Process()

    async def connect(*args, **kwargs):
        state.connections += 1
        raise AssertionError("revoked request must not send a first IPC command")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "open_unix_connection", connect)
    actions = build_catalog(
        library.moods, bind_handlers(player, overlay, FakeDesktop())
    ).allowed(("music.play_mood",), "demo", can_act=lambda: state.enabled)
    task = asyncio.create_task(actions.run("music.play_mood", mood="focus"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        state.enabled = False
    finally:
        release.set()
    result = await asyncio.wait_for(task, 1)
    assert result.detail == "skill_inactive" and not result.ok
    assert state.connections == 0 and not player.status().playing
    assert mpv.process is not None  # Started idle helper may finish; no cleanup kill.
    mpv.process = None
    await player.close()
    await overlay.close()
