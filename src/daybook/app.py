"""Configuration, lifecycle, and the Daybook command line."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import math
import os
import secrets
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, cast

import uvicorn

from daybook.act import Overlay
from daybook.model import ModelConfig
from daybook.sense import Screen, ScreenState

if TYPE_CHECKING:
    from daybook.sense import X11Screen
    from daybook.web import Services

LOG = logging.getLogger(__name__)
CAPTURE_LOCK = Path("/tmp/daybook-capture.lock")
BUILTIN_SKILLS = Path(__file__).resolve().parents[2] / "skills/builtin"
DEFAULT_CONFIG = """port = 8765
display = ":1"
xauthority = "/run/user/1000/gdm/Xauthority"
music_dir = "~/Music/daybook"
desktop_backend = "xdo"
keep_frames = false
tick_interval = "90s"
# Set to "low" only if the replay signal-quality gate needs it.
tick_effort = "none"

[model]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3.6:35b"
api_key_file = ""
dialect = "ollama"
"""


@dataclass(frozen=True)
class Config:
    home: Path
    port: int = 8765
    display: str = ":1"
    xauthority: str = "/run/user/1000/gdm/Xauthority"
    music_dir: Path = Path("~/Music/daybook")
    desktop_backend: Literal["xdo", "cua"] = "xdo"
    keep_frames: bool = False
    tick_interval: float = 90
    tick_effort: Literal["none", "low"] = "none"
    model: ModelConfig = ModelConfig()


def duration(value: object) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("tick_interval must be a duration such as 90s")
    text = str(value).strip()
    scale = 60 if text.endswith("m") else 1
    seconds = float(text[:-1] if text.endswith(("s", "m")) else text) * scale
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("tick_interval must be positive")
    return seconds


def load_config(home: Path | None = None) -> Config:
    home = (
        (home or Path(os.environ.get("DAYBOOK_HOME", "~/.daybook")))
        .expanduser()
        .resolve()
    )
    home.mkdir(parents=True, exist_ok=True)
    path = home / "daybook.toml"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(DEFAULT_CONFIG)
    except FileExistsError:
        pass
    data = tomllib.loads(path.read_text())
    port = data.get("port", 8765)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    backend = data.get("desktop_backend", "xdo")
    if backend not in ("xdo", "cua"):
        raise ValueError("desktop_backend must be xdo or cua")
    effort = data.get("tick_effort", "none")
    if effort not in ("none", "low"):
        raise ValueError("tick_effort must be none or low")
    keep_frames = data.get("keep_frames", False)
    if type(keep_frames) is not bool:
        raise ValueError("keep_frames must be a boolean")
    model = data.get("model", {})
    if not isinstance(model, dict):
        raise ValueError("model must be a TOML table")
    dialect = model.get("dialect", "ollama")
    if dialect not in ("ollama", "vllm", "openai"):
        raise ValueError("model.dialect must be ollama, vllm or openai")

    def string(source: dict, name: str, default: str) -> str:
        value = source.get(name, default)
        if not isinstance(value, str) or not value.strip() and name != "api_key_file":
            raise ValueError(f"{name} must be a nonempty string")
        return value

    return Config(
        home=home,
        port=port,
        display=string(data, "display", ":1"),
        xauthority=string(data, "xauthority", "/run/user/1000/gdm/Xauthority"),
        music_dir=Path(string(data, "music_dir", "~/Music/daybook")).expanduser(),
        desktop_backend=backend,
        keep_frames=keep_frames,
        tick_interval=duration(data.get("tick_interval", "90s")),
        tick_effort=effort,
        model=ModelConfig(
            base_url=string(model, "base_url", "http://127.0.0.1:11434/v1"),
            model=string(model, "model", "qwen3.6:35b"),
            api_key_file=string(model, "api_key_file", ""),
            dialect=dialect,
        ),
    )


class FileLock:
    """Keep the inode in place so every process locks the same object."""

    def __init__(self, path: Path, message: str) -> None:
        self.path, self.message = path, message
        self.fd: int | None = None

    def __enter__(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError(self.message) from None
        self.fd = fd
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def capture_lock() -> FileLock:
    return FileLock(
        CAPTURE_LOCK,
        "The real screen is already in use. Use --replay DIR or --no-capture with a separate DAYBOOK_HOME.",
    )


def home_lock(home: Path) -> FileLock:
    return FileLock(
        home / "daemon.lock", "This DAYBOOK_HOME is already in use by a daemon."
    )


def atomic_private(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def launch_token(home: Path) -> str:
    token = secrets.token_urlsafe(32)
    atomic_private(home / "token", token)
    return token


def desktop_environment(config: Config) -> dict[str, str]:
    return {
        **os.environ,
        "DISPLAY": config.display,
        "XAUTHORITY": str(Path(config.xauthority).expanduser()),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "DAYBOOK_HOME": str(config.home),
    }


class ShellSupervisor:
    def __init__(
        self,
        command: tuple[str, ...],
        environment: dict[str, str],
        on_pid: Callable[[int | None], None],
        *,
        retry_seconds: float = 1,
    ) -> None:
        self.command, self.environment, self.on_pid = command, environment, on_pid
        self.retry_seconds = retry_seconds

    async def run(self) -> None:
        while True:
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *self.command,
                    env=self.environment,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                self.on_pid(process.pid)
                code = await process.wait()
                LOG.warning("shell_exited code=%s", code)
            except OSError:
                LOG.warning("shell_unavailable")
            finally:
                self.on_pid(None)
                if process is not None and process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
            await asyncio.sleep(self.retry_seconds)


class NoCaptureScreen:
    """Fail closed; even an accidental pixel request cannot capture the desktop."""

    def state(self) -> ScreenState:
        return ScreenState(None, (), True, 0)

    def thumbnail(self) -> bytes:
        raise RuntimeError("screen capture is disabled")

    def grab(self) -> bytes:
        raise RuntimeError("screen capture is disabled")


class DevelopmentOverlay(Overlay):
    """Simulate a connected view without ever starting Qt."""

    def shell_connected(self, connected: bool) -> None:
        super().shell_connected(True)


class OverlayScreen:
    def __init__(self, screen: Screen, overlay: Overlay) -> None:
        self.screen, self.overlay = screen, overlay

    def state(self) -> ScreenState:
        facts = self.screen.state()
        return replace(facts, overlay=facts.overlay or self.overlay.state is not None)

    def thumbnail(self) -> bytes:
        return self.screen.thumbnail()

    def grab(self) -> bytes:
        return self.screen.grab()


@dataclass(frozen=True)
class Runtime:
    services: Services
    screen: X11Screen | None


@asynccontextmanager
async def open_runtime(
    config: Config,
    *,
    replay: Path | None = None,
    no_capture: bool = False,
    real_effects: bool = False,
):
    from daybook.act import (
        FakeDesktop,
        MusicLibrary,
        MusicPlayer,
        XdoDesktop,
        bind_handlers,
        build_catalog,
    )
    from daybook.chat import Chat
    from daybook.events import Bus
    from daybook.journal import Journal
    from daybook.mind import Mind
    from daybook.model import ModelGateway
    from daybook.sense import ReplayScreen, Sense, X11Screen
    from daybook.skills import SkillBook
    from daybook.web import Services

    if real_effects and config.desktop_backend == "cua":
        raise ValueError("cua trial was rejected on GX10; use desktop_backend = 'xdo'")
    async with AsyncExitStack() as stack:
        bus = Bus()
        journal = Journal(config.home / "journal.db", bus)
        stack.callback(journal.close)
        gateway = ModelGateway(config.model)
        stack.push_async_callback(gateway.close)
        library = MusicLibrary.load(config.music_dir)
        overlay = (
            Overlay(bus, home=config.home) if real_effects else DevelopmentOverlay(bus)
        )
        if not real_effects:
            overlay.shell_connected(True)
        stack.push_async_callback(overlay.close)
        music = MusicPlayer(
            library, config.home, bus, overlay, real_effects=real_effects
        )
        stack.push_async_callback(music.close)
        await music.start()
        desktop = XdoDesktop() if real_effects else FakeDesktop()
        catalog = build_catalog(
            library.moods,
            bind_handlers(music, overlay, desktop, real_effects=real_effects),
        )
        skills = SkillBook(
            config.home,
            BUILTIN_SKILLS,
            journal.path,
            journal.portal,
            catalog,
            bus,
            clear_readings=journal.clear_skill_readings,
        )
        mind = Mind(
            journal, gateway, skills, config.tick_interval, effort=config.tick_effort
        )
        x11 = None
        raw: Screen
        if replay is not None:
            raw = ReplayScreen(replay)
        elif no_capture:
            raw = NoCaptureScreen()
        else:
            x11 = X11Screen(config.display)
            raw = x11
        sense = Sense(
            OverlayScreen(raw, overlay), journal, gateway, bus, config.home, mind.nudge
        )
        stack.push_async_callback(sense.close)
        yield Runtime(
            Services(
                journal=journal,
                sense=sense,
                mind=mind,
                skills=skills,
                chat=Chat(journal, gateway, skills),
                gateway=gateway,
                music=music,
                overlay=overlay,
                bus=bus,
                catalog=catalog,
                keep_frames=config.keep_frames,
            ),
            x11,
        )


async def run_daemon(config: Config, args: argparse.Namespace) -> None:
    from daybook.web import create_app

    effects = (
        not (args.replay is not None or args.no_capture or args.mock_api)
        or args.real_effects
    )
    real_screen = not (args.replay is not None or args.no_capture or args.mock_api)
    with (
        home_lock(config.home),
        capture_lock() if real_screen or effects else nullcontext(),
    ):
        environment = desktop_environment(config)
        if real_screen or effects:
            os.environ.update(environment)
        token = launch_token(config.home)
        atomic_private(
            config.home / "runtime.json",
            json.dumps({"port": config.port, "pid": os.getpid()}),
        )
        try:
            origin = f"http://127.0.0.1:{config.port}"
            if args.mock_api:
                app = create_app(mock=True, token=token, origin=origin)
                await uvicorn.Server(
                    uvicorn.Config(
                        app, host="127.0.0.1", port=config.port, access_log=False
                    )
                ).serve()
                return
            async with open_runtime(
                config,
                replay=args.replay,
                no_capture=args.no_capture,
                real_effects=effects,
            ) as runtime:
                services = runtime.services
                app = create_app(token=token, origin=origin, services=services)
                server = uvicorn.Server(
                    uvicorn.Config(
                        app, host="127.0.0.1", port=config.port, access_log=False
                    )
                )
                async with asyncio.TaskGroup() as group:
                    tasks = [
                        group.create_task(services.sense.run()),
                        group.create_task(services.mind.run()),
                    ]
                    if effects:
                        shell = ShellSupervisor(
                            (
                                sys.executable,
                                "-m",
                                "daybook.shell",
                                "--port",
                                str(config.port),
                            ),
                            {**environment, "DAYBOOK_TOKEN": token},
                            runtime.screen.set_shell_pid
                            if runtime.screen
                            else lambda _: None,
                        )
                        tasks.append(group.create_task(shell.run()))
                    try:
                        await server.serve()
                    finally:
                        for task in tasks:
                            task.cancel()
        finally:
            (config.home / "runtime.json").unlink(missing_ok=True)


def daemon_request(
    config: Config, route: str, body: object | None = None, *, method: str = "POST"
) -> object:
    path = config.home / "runtime.json"
    if not path.exists() or not (config.home / "token").exists():
        raise RuntimeError(
            "No daemon is running for this DAYBOOK_HOME. Start daybook run first."
        )
    descriptor = json.loads(path.read_text())
    port = descriptor.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid daemon port")
    origin = f"http://127.0.0.1:{port}"
    request = urllib.request.Request(
        origin + route,
        data=json.dumps(body).encode()
        if body is not None
        else b""
        if method == "POST"
        else None,
        headers={
            "X-Daybook-Token": (config.home / "token").read_text().strip(),
            "Origin": origin,
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=70) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            reason = json.load(error).get("detail", f"HTTP {error.code}")
        except (ValueError, AttributeError):
            reason = f"HTTP {error.code}"
        raise RuntimeError(str(reason)) from None
    except urllib.error.URLError:
        raise RuntimeError("Cannot reach the daemon for this DAYBOOK_HOME.") from None


async def observe_once(config: Config) -> object:
    from daybook.events import Bus
    from daybook.journal import Journal, TimeRange
    from daybook.model import ModelGateway
    from daybook.sense import Sense, X11Screen

    with (
        capture_lock(),
        tempfile.TemporaryDirectory(prefix="daybook-observe-") as folder,
    ):
        os.environ.update(desktop_environment(config))
        async with AsyncExitStack() as stack:
            bus = Bus()
            journal = Journal(Path(folder) / "journal.db", bus)
            stack.callback(journal.close)
            gateway = ModelGateway(config.model)
            stack.push_async_callback(gateway.close)
            sense = Sense(
                X11Screen(config.display),
                journal,
                gateway,
                bus,
                config.home,
                lambda _: None,
            )
            stack.push_async_callback(sense.close)
            at = datetime.now(UTC)
            await sense.poll(at)
            await sense.wait_idle()
            notes = journal.portal.observations(
                TimeRange(
                    at - timedelta(seconds=1), datetime.now(UTC) + timedelta(seconds=1)
                )
            )
            return (
                asdict(notes[-1])
                if notes
                else {"capture": journal.portal.now().capture, "observation": None}
            )


async def tick_once(config: Config) -> object:
    if (config.home / "runtime.json").exists():
        result = cast(
            dict, await asyncio.to_thread(daemon_request, config, "/api/tick")
        )
        now = cast(
            dict,
            await asyncio.to_thread(daemon_request, config, "/api/now", method="GET"),
        )
        return (
            [
                reading
                for reading in now["readings"]
                if reading["tick"] == result["tick"]
            ]
            if result["recorded"]
            else []
        )
    with home_lock(config.home):
        async with open_runtime(config, no_capture=True) as runtime:
            tick = await runtime.services.mind.tick_now()
            return [asdict(reading) for reading in tick.readings] if tick else []


def main() -> None:
    parser = argparse.ArgumentParser(prog="daybook")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    modes = run.add_mutually_exclusive_group()
    modes.add_argument("--mock-api", action="store_true")
    modes.add_argument("--replay", type=Path)
    modes.add_argument("--no-capture", action="store_true")
    run.add_argument("--real-effects", action="store_true")
    run.add_argument("--port", type=int)
    sub.add_parser("observe-once")
    sub.add_parser("tick-once")
    skill = sub.add_parser("skill").add_subparsers(dest="operation", required=True)
    skill.add_parser("check").add_argument("file", type=Path)
    music = sub.add_parser("music").add_subparsers(dest="operation", required=True)
    music.add_parser("check")
    music.add_parser("play").add_argument("mood")
    sub.add_parser("overlay").add_subparsers(
        dest="operation", required=True
    ).add_parser("test")
    args = parser.parse_args()
    try:
        config = load_config()
        if args.command == "run":
            if args.mock_api and args.real_effects:
                raise ValueError("--mock-api cannot use --real-effects")
            if args.port is not None:
                if not 1 <= args.port <= 65535:
                    raise ValueError("port must be between 1 and 65535")
                config = replace(config, port=args.port)
            asyncio.run(run_daemon(config, args))
            return
        if args.command == "observe-once":
            result = asyncio.run(observe_once(config))
        elif args.command == "tick-once":
            result = asyncio.run(tick_once(config))
        elif args.command == "overlay":
            result = daemon_request(config, "/api/overlay/test")
        elif args.command == "music" and args.operation == "play":
            result = daemon_request(config, "/api/music/play", {"mood": args.mood})
        else:
            from daybook.act import MusicLibrary, build_catalog
            from daybook.skills import Skill, SkillSource, parse_skill

            library = MusicLibrary.load(config.music_dir)
            if args.command == "music":
                errors = library.check()
                result = {"ok": not errors, "errors": list(errors)}
            else:
                path = args.file.resolve()
                origin = (
                    "builtin"
                    if path.is_relative_to(BUILTIN_SKILLS.resolve())
                    else "user"
                )
                hook = path.with_name(path.name.replace(".skill.md", ".hook.py"))
                checked = parse_skill(
                    path.read_text(),
                    source=SkillSource(path, origin),
                    catalog=build_catalog(library.moods),
                    hook_text=hook.read_text()
                    if origin == "builtin" and hook.exists()
                    else None,
                )
                errors = [] if isinstance(checked, Skill) else checked
                result = {
                    "ok": not errors,
                    "errors": [asdict(error) for error in errors],
                }
            print(json.dumps(result, default=str))
            if errors:
                parser.exit(1)
            return
        print(json.dumps(result, default=str))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"daybook: {error}\n")


if __name__ == "__main__":
    main()
