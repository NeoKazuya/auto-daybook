"""Local library and one managed mpv child; fake mode never starts a process."""

from __future__ import annotations

import asyncio
import json
import random
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from daybook.events import Bus, MusicChanged
from daybook.journal import Outcome

from . import MusicLibrary, MusicStatus, MusicTrack, Overlay

if TYPE_CHECKING:
    from asyncio.subprocess import Process


def load(root: Path) -> MusicLibrary:
    data = tomllib.loads((root / "library.toml").read_text())
    moods = {
        str(name): str(value.get("describe", ""))
        for name, value in data.get("moods", {}).items()
    }
    tracks = tuple(
        MusicTrack(
            str(t.get("file", "")),
            str(t.get("title", "")),
            str(t.get("artist", "")),
            str(t.get("license", "")),
            str(t.get("source", "")),
        )
        for t in data.get("tracks", [])
    )
    return MusicLibrary(root, moods, tracks)


def check(library: MusicLibrary) -> tuple[str, ...]:
    errors = []
    root = library.root.resolve()
    folders = (
        {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")}
        if root.exists()
        else set()
    )
    if folders != set(library.moods):
        errors.append("mood folders must match library.toml moods")
    listed = set()
    for track in library.tracks:
        file = root / track.file
        listed.add(file.resolve())
        if (
            not file.resolve().is_relative_to(root)
            or len(Path(track.file).parts) != 2
            or Path(track.file).parts[0] not in library.moods
        ):
            errors.append(f"invalid track folder: {track.file}")
        if not file.is_file():
            errors.append(f"missing track: {track.file}")
        if not track.license or not track.source:
            errors.append(f"license and source required: {track.file}")
    for mood in library.moods:
        if not any(
            Path(t.file).parts and Path(t.file).parts[0] == mood for t in library.tracks
        ):
            errors.append(f"no tracks for mood: {mood}")
        if (root / mood).is_dir():
            for file in (root / mood).iterdir():
                if (
                    file.is_file()
                    and file.suffix.lower() in (".mp3", ".ogg", ".flac", ".wav", ".m4a")
                    and file.resolve() not in listed
                ):
                    errors.append(f"track not in manifest: {mood}/{file.name}")
    return tuple(errors)


class Mpv:
    def __init__(self, home: Path) -> None:
        self.socket = home / "mpv.sock"
        self.process: Process | None = None
        self.sequence = 0

    async def start(self) -> None:
        if (
            self.process is not None
            and self.process.returncode is None
            and self.socket.exists()
        ):
            return
        await self.close()
        self.socket.parent.mkdir(parents=True, exist_ok=True)
        self.socket.unlink(missing_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            "mpv",
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            f"--input-ipc-server={self.socket}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(40):
            if self.socket.exists():
                return
            if self.process.returncode is not None:
                break
            await asyncio.sleep(0.05)
        raise OSError("mpv unavailable")

    async def command(self, *args: object) -> object:
        await self.start()
        reader, writer = await asyncio.open_unix_connection(self.socket)
        self.sequence += 1
        id = self.sequence
        try:
            writer.write(
                (json.dumps({"command": args, "request_id": id}) + "\n").encode()
            )
            await writer.drain()
            while True:
                line = await asyncio.wait_for(reader.readline(), 3)
                if not line:
                    raise OSError("mpv disconnected")
                reply = json.loads(line)
                if reply.get("request_id") == id:
                    if reply.get("error") != "success":
                        raise OSError("mpv command failed")
                    return reply.get("data")
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()
        self.socket.unlink(missing_ok=True)


class Player:
    def __init__(
        self,
        library: MusicLibrary,
        home: Path,
        bus: Bus,
        overlay: Overlay,
        real_effects: bool,
    ) -> None:
        self.library, self.bus, self.overlay = library, bus, overlay
        self.real_effects = real_effects
        self.mpv = Mpv(home)
        self.current = MusicStatus()
        self.last_changes: dict[str, float] = {}
        self.transcript: list[tuple[object, ...]] = []
        self.lock = asyncio.Lock()
        self.playlist: tuple[MusicTrack, ...] = ()
        self.monitor: asyncio.Task[None] | None = None

    async def command(self, *args: object) -> None:
        self.transcript.append(args)
        if self.real_effects:
            await self.mpv.command(*args)

    async def fade(self, start: float, end: float) -> None:
        for step in range(1, 13):
            await self.command(
                "set_property", "volume", start + (end - start) * step / 12
            )
            if self.real_effects:
                await asyncio.sleep(1.5 / 12)

    def permission(self, skill: str, min_dwell: float) -> str | None:
        overlay = self.overlay.state
        if overlay is not None and overlay.owner != skill:
            return "overlay_owned"
        if time.monotonic() - self.last_changes.get(skill, float("-inf")) < min_dwell:
            return "dwell"
        return None

    async def play(self, mood: str, skill: str, min_dwell: float) -> Outcome:
        async with self.lock:
            if mood not in self.library.moods or min_dwell < 0:
                return Outcome("music.play_mood", False, "invalid_mood_or_dwell")
            refusal = self.permission(skill, min_dwell)
            if refusal:
                return Outcome("music.play_mood", True, refusal)
            if self.current.playing and self.current.mood == mood:
                # Same mood remains a no-op unless the child/socket died.
                if not self.real_effects or (
                    self.mpv.process is not None
                    and self.mpv.process.returncode is None
                    and self.mpv.socket.exists()
                ):
                    return Outcome("music.play_mood", True, "unchanged")
            if self.library.check():
                return Outcome("music.play_mood", False, "invalid_library")
            tracks = [t for t in self.library.tracks if Path(t.file).parts[0] == mood]
            random.shuffle(tracks)
            try:
                if self.current.playing:
                    await self.fade(100, 0)
                # An overlay may have appeared while fading; ownership is checked again.
                refusal = self.permission(skill, min_dwell)
                if refusal:
                    await self.command("set_property", "volume", 100)
                    return Outcome("music.play_mood", True, refusal)
                await self.command("set_property", "volume", 0)
                await self.command("set_property", "loop-playlist", "inf")
                for index, track in enumerate(tracks):
                    await self.command(
                        "loadfile",
                        str(self.library.root / track.file),
                        "replace" if index == 0 else "append",
                    )
                await self.command("set_property", "pause", False)
                await self.fade(0, 100)
            except (OSError, ValueError, TimeoutError):
                if self.real_effects:
                    await self.mpv.close()
                self.current = MusicStatus()
                self.bus.publish(MusicChanged(None, None))
                return Outcome("music.play_mood", False, "player_unavailable")
            self.last_changes[skill] = time.monotonic()
            self.playlist = tuple(tracks)
            self.current = MusicStatus(mood, tracks[0].title, True)
            if self.real_effects and self.monitor is None:
                self.monitor = asyncio.create_task(self.watch())
            self.bus.publish(MusicChanged(mood, tracks[0].title))
            return Outcome("music.play_mood", True)

    async def stop(self, skill: str | None = None) -> Outcome:
        async with self.lock:
            overlay = self.overlay.state
            if skill is not None and overlay is not None and overlay.owner != skill:
                return Outcome("music.stop", True, "overlay_owned")
            self.playlist = ()
            try:
                if self.current.playing:
                    await self.command("stop")
            except (OSError, ValueError, TimeoutError):
                return Outcome("music.stop", False, "player_unavailable")
            self.current = MusicStatus()
            self.bus.publish(MusicChanged(None, None))
            return Outcome("music.stop", True)

    async def watch(self) -> None:
        while True:
            await asyncio.sleep(1)
            async with self.lock:
                if not self.playlist:
                    continue
                try:
                    dead = (
                        self.mpv.process is None
                        or self.mpv.process.returncode is not None
                        or not self.mpv.socket.exists()
                    )
                    if dead:
                        await self.mpv.close()
                        await self.command("set_property", "loop-playlist", "inf")
                        for index, track in enumerate(self.playlist):
                            await self.command(
                                "loadfile",
                                str(self.library.root / track.file),
                                "replace" if index == 0 else "append",
                            )
                        await self.command("set_property", "volume", 100)
                        await self.command("set_property", "pause", False)
                    path = await self.mpv.command("get_property", "path")
                    title = next(
                        (
                            t.title
                            for t in self.playlist
                            if str(self.library.root / t.file) == path
                        ),
                        self.current.title,
                    )
                    updated = MusicStatus(self.current.mood, title, True)
                    if updated != self.current:
                        self.current = updated
                        self.bus.publish(MusicChanged(updated.mood, updated.title))
                except (OSError, ValueError, TimeoutError):
                    await self.mpv.close()
                    self.current = MusicStatus(
                        self.current.mood, self.current.title, False
                    )
                    self.bus.publish(
                        MusicChanged(self.current.mood, self.current.title)
                    )

    async def close(self) -> None:
        self.playlist = ()
        task, self.monitor = self.monitor, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.real_effects:
            await self.mpv.close()
        self.current = MusicStatus()
