from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import stat
import sys
from contextlib import suppress
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from daybook import app


@pytest.fixture(autouse=True)
def isolated_model_credentials(tmp_path_factory, monkeypatch):
    # Real gateway construction reads the configured key immediately. Keep both
    # this process and owned CLI subprocesses away from the developer's HOME.
    home = tmp_path_factory.mktemp("app-model-home")
    key = home / ".config/qwen38/api-key"
    key.parent.mkdir(parents=True)
    key.write_text("isolated-app-test-key")
    monkeypatch.setenv("HOME", str(home))


def test_missing_config_creates_defaults_and_keeps_existing_values(tmp_path: Path):
    from daybook.model import ModelConfig

    config = app.load_config(tmp_path)
    assert config.home == tmp_path
    assert config.port == 8765 and config.tick_interval == 90
    assert config.tick_effort == "none" and not config.keep_frames
    expected = ModelConfig(
        base_url="http://127.0.0.1:18300/v1",
        model="qwen3.8-flash-next",
        api_key_file="~/.config/qwen38/api-key",
        dialect="vllm",
        max_concurrency=2,
    )
    assert config.model == expected
    assert app.Config(home=tmp_path).model == expected
    path = tmp_path / "daybook.toml"
    generated = path.read_text()
    assert "max_concurrency = 2" in generated
    assert app.load_config(tmp_path).model == expected
    assert path.read_text() == generated

    custom = (
        "# Keep this existing deployment.\n"
        'port=8771\ntick_interval="0.5m"\ntick_effort="low"\n'
        '[model]\nbase_url="http://127.0.0.1:11434/v1"\n'
        'model="qwen3.6:35b"\ndialect="ollama"\n'
        'api_key_file="~/private/key"\nmax_concurrency=3\n'
    )
    path.write_text(custom)
    updated = app.load_config(tmp_path)
    assert updated.port == 8771 and updated.tick_interval == 30
    assert updated.tick_effort == "low"
    assert updated.model == ModelConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3.6:35b",
        api_key_file="~/private/key",
        dialect="ollama",
        max_concurrency=3,
    )
    assert path.read_text() == custom
    # The gateway remains generic; the app owns the GX10 deployment profile.
    assert ModelConfig().model == "qwen3.6:35b"
    assert ModelConfig().max_concurrency == 1


@pytest.mark.parametrize("table", ["", "[model]\n", '[model]\nmodel="custom-qwen"\n'])
def test_partial_model_config_uses_app_profile_defaults(tmp_path: Path, table: str):
    path = tmp_path / "daybook.toml"
    path.write_text(table)
    config = app.load_config(tmp_path)
    assert config.model.base_url == "http://127.0.0.1:18300/v1"
    assert config.model.model == (
        "custom-qwen" if "custom-qwen" in table else "qwen3.8-flash-next"
    )
    assert config.model.api_key_file == "~/.config/qwen38/api-key"
    assert config.model.dialect == "vllm"
    assert config.model.max_concurrency == 2
    assert config.tick_interval == 90 and config.tick_effort == "none"
    assert path.read_text() == table


@pytest.mark.parametrize("limit", ["0", "true", "1.5"])
def test_invalid_model_concurrency_fails_clearly(tmp_path: Path, limit: str):
    (tmp_path / "daybook.toml").write_text(f"[model]\nmax_concurrency={limit}\n")
    with pytest.raises(ValueError, match="max_concurrency must be a positive integer"):
        app.load_config(tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        "port=0",
        "port=true",
        "port=8765.5",
        "keep_frames=1",
        'tick_interval="nan"',
        'tick_interval="-1s"',
        'tick_effort="high"',
        'desktop_backend="unknown"',
        "display=3",
        '[model]\ndialect="unknown"',
    ],
)
def test_invalid_config_fails_clearly(tmp_path: Path, text: str):
    (tmp_path / "daybook.toml").write_text(text)
    with pytest.raises(ValueError):
        app.load_config(tmp_path)


def test_tokens_rotate_atomically_and_are_private(tmp_path: Path):
    first = app.launch_token(tmp_path)
    second = app.launch_token(tmp_path)
    assert first != second and len(second) >= 32
    assert (tmp_path / "token").read_text() == second
    assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["token"]


def test_real_screen_lock_fails_fast_and_is_reusable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    with app.capture_lock():
        with pytest.raises(RuntimeError, match="--replay"):
            with app.capture_lock():
                pytest.fail("second owner took the capture lock")
    with app.capture_lock():
        pass
    assert (tmp_path / "capture.lock").exists()


async def test_shell_supervisor_restarts_updates_pid_and_reaps_on_shutdown(
    tmp_path: Path,
):
    # A real child is the external boundary: no Qt or presenter desktop needed.
    pids: asyncio.Queue[int | None] = asyncio.Queue()
    supervisor = app.ShellSupervisor(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        dict(os.environ),
        pids.put_nowait,
        on_quit=lambda: None,
        retry_seconds=0.01,
    )
    task = asyncio.create_task(supervisor.run())
    first = await asyncio.wait_for(pids.get(), 2)
    assert first is not None
    os.kill(first, signal.SIGKILL)
    assert await asyncio.wait_for(pids.get(), 2) is None
    second = await asyncio.wait_for(pids.get(), 2)
    assert second is not None and second != first
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert await asyncio.wait_for(pids.get(), 2) is None
    for pid in (first, second):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_no_capture_screen_cannot_request_pixels():
    screen = app.NoCaptureScreen()
    assert screen.state().locked
    with pytest.raises(RuntimeError, match="disabled"):
        screen.grab(screen.state())
    with pytest.raises(RuntimeError, match="disabled"):
        screen.thumbnail(screen.state())


@pytest.mark.parametrize("mode", ["mock_api", "no_capture", "replay"])
async def test_development_modes_do_not_take_real_screen_lock_or_start_shell(
    tmp_path: Path,
    monkeypatch,
    mode: str,
):
    config = replace(app.load_config(tmp_path), port=8771)
    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    effects: list[bool] = []
    ports: list[int] = []

    class Server:
        def __init__(self, config):
            ports.append(config.port)

        async def serve(self):
            # Even mock mode writes the descriptor and private per-launch token.
            assert json.loads((tmp_path / "runtime.json").read_text())["port"] == 8771
            assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600

    monkeypatch.setattr(app, "LifecycleServer", Server)
    if mode != "mock_api":
        # Concrete owners are covered by the composition tests below; here the
        # daemon setup must be proven independent of real capture locking.
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        import daybook.web

        class Loop:
            async def run(self):
                await asyncio.Event().wait()

        @asynccontextmanager
        async def runtime(config, **kwargs):
            effects.append(kwargs["real_effects"])
            yield SimpleNamespace(
                services=SimpleNamespace(sense=Loop(), mind=Loop()), screen=None
            )

        monkeypatch.setattr(app, "open_runtime", runtime)
        monkeypatch.setattr(daybook.web, "create_app", lambda **_: object())
    args = argparse.Namespace(
        mock_api=mode == "mock_api",
        no_capture=mode == "no_capture",
        replay=tmp_path if mode == "replay" else None,
        real_effects=False,
    )
    with app.capture_lock():
        await app.run_daemon(config, args)
    assert effects == ([] if mode == "mock_api" else [False])
    assert ports == [8771]
    assert not (tmp_path / "runtime.json").exists()


def test_daemon_cli_uses_launch_port_and_authenticated_request(tmp_path: Path):
    requests: list[tuple[str, str, str, object]] = []
    token = app.launch_token(tmp_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            content = self.rfile.read(size)
            requests.append(
                (
                    self.path,
                    self.headers["Origin"],
                    self.headers["X-Daybook-Token"],
                    json.loads(content) if content else None,
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, format: str, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_port
        (tmp_path / "runtime.json").write_text(
            json.dumps({"port": port, "pid": os.getpid()})
        )
        config = app.load_config(tmp_path)
        assert app.daemon_request(config, "/api/music/play", {"mood": "focus"}) == {
            "ok": True
        }
        assert app.daemon_request(config, "/api/overlay/test") == {"ok": True}
        assert requests == [
            ("/api/music/play", f"http://127.0.0.1:{port}", token, {"mood": "focus"}),
            ("/api/overlay/test", f"http://127.0.0.1:{port}", token, None),
        ]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_daemon_commands_fail_clearly_when_no_daemon(tmp_path: Path):
    with pytest.raises(RuntimeError, match="daybook run"):
        app.daemon_request(app.load_config(tmp_path), "/api/overlay/test")


def library_config(tmp_path: Path) -> app.Config:
    root = tmp_path / "music"
    root.mkdir()
    lines = []
    for mood in ("calm", "focus", "break"):
        (root / mood).mkdir()
        (root / mood / "example.wav").write_bytes(b"fixture track")
        lines.extend(
            [
                f'[moods.{mood}]\ndescribe="{mood}"',
                f'[[tracks]]\nfile="{mood}/example.wav"\ntitle="Example"\nartist="Test"\nlicense="CC0"\nsource="https://example.test/track"',
            ]
        )
    (root / "library.toml").write_text("\n".join(lines))
    return replace(app.load_config(tmp_path), music_dir=root)


async def test_concrete_replay_composes_owners_and_never_starts_real_effects(
    tmp_path: Path, monkeypatch
):
    import daybook.model
    import daybook.sense
    from daybook.act import OverlayRequest
    from daybook.model import ScriptedGateway

    async def forbidden_process(*_, **__):
        pytest.fail("a replay without real effects started a child")

    def forbidden_screen(*_, **__):
        pytest.fail("a replay opened the real X11 screen")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_process)
    monkeypatch.setattr(daybook.sense, "X11Screen", forbidden_screen)
    gateway = ScriptedGateway(
        ['{"seen":"Editing a Python function.","visible_text":"def example()"}']
    )
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    config = library_config(tmp_path)
    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    async with app.open_runtime(config, replay=fixture) as runtime:
        services = runtime.services
        assert runtime.screen is None
        assert {entry.id for entry in services.skills.entries()} == {
            "music",
            "break",
            "break-demo",
        }
        await services.sense.poll()
        await services.sense.wait_idle()
        observation = services.journal.portal.now().observation
        assert observation is not None
        assert gateway.priorities == [daybook.model.Priority.OBSERVE]
        gateway.replies.append(
            json.dumps(
                {
                    "segments": [
                        {
                            **{
                                "mode": "start",
                                "title": "Implement example",
                                "kind": "coding",
                                "intent": "Likely implementing a function.",
                            },
                            "observations": [observation.id],
                        }
                    ],
                    "signals": {
                        str(signal.id): None
                        for signal in services.skills.active_signals()
                    },
                }
            )
        )
        tick = await services.mind.tick_now()
        assert tick is not None and tick.id.startswith("tick:")
        assert services.journal.portal.now().segment is not None
        assert all(
            reading.tick == tick.id
            for reading in services.journal.portal.now().readings
        )
        # Fake overlays keep real policy: they own state and block replay capture.
        services.overlay.shell_connected(False)
        result = await services.overlay.show(
            OverlayRequest("Break", "Step away"), "manual"
        )
        assert result.ok
        await services.sense.poll()
        assert services.journal.portal.now().capture == "overlay"
        await services.overlay.hide()
        assert not (tmp_path / "mpv.sock").exists()
    assert services.overlay.state is None
    with pytest.raises(RuntimeError, match="closed"):
        services.journal.portal.now()


async def test_concrete_no_capture_creates_no_pixels_calls_or_children(
    tmp_path: Path, monkeypatch
):
    import daybook.model
    import daybook.sense
    from daybook.model import ScriptedGateway

    async def forbidden_process(*_, **__):
        pytest.fail("no-capture started a real process")

    def forbidden_screen(*_, **__):
        pytest.fail("no-capture opened the real screen")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_process)
    monkeypatch.setattr(daybook.sense, "X11Screen", forbidden_screen)
    gateway = ScriptedGateway([])
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    async with app.open_runtime(library_config(tmp_path), no_capture=True) as runtime:
        await runtime.services.sense.poll()
        assert runtime.services.journal.portal.now().capture == "locked"
        assert await runtime.services.mind.tick_now() is None
        assert gateway.prompts == []
        assert not (tmp_path / "mpv.sock").exists()


def test_cli_checks_and_error_exit_codes(tmp_path: Path, monkeypatch, capsys):
    config = library_config(tmp_path)
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["daybook", "music", "check"])
    app.main()
    assert json.loads(capsys.readouterr().out) == {"ok": True, "errors": []}
    skill = tmp_path / "test.skill.md"
    skill.write_text(
        "---\nid: test\nname: Test\nsignals:\n  focused:\n    ask: Is the user focused?\n    type: bool\nrules:\n  - when: focused becomes true\n    do:\n      - notify.send: {title: Focus, body: Keep going}\nallow: [notify.send]\n---\nNotify me when I focus."
    )
    monkeypatch.setattr(sys, "argv", ["daybook", "skill", "check", str(skill)])
    app.main()
    assert json.loads(capsys.readouterr().out)["ok"]
    skill.write_text("invalid skill")
    with pytest.raises(SystemExit) as error:
        app.main()
    assert error.value.code == 1
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.parametrize("mode", ["real", "effects"])
async def test_run_refuses_competing_real_screen_before_any_startup(
    tmp_path: Path, monkeypatch, mode: str
):
    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    args = argparse.Namespace(
        mock_api=False,
        no_capture=mode == "effects",
        replay=None,
        real_effects=mode == "effects",
    )
    with app.capture_lock():
        with pytest.raises(RuntimeError, match="--replay"):
            await app.run_daemon(app.load_config(tmp_path), args)
    assert not (tmp_path / "token").exists()
    assert not (tmp_path / "journal.db").exists()


async def test_run_rejects_shared_home_without_rotating_existing_token(tmp_path: Path):
    config = app.load_config(tmp_path)
    token = app.launch_token(tmp_path)
    args = argparse.Namespace(
        mock_api=True, no_capture=False, replay=None, real_effects=False
    )
    with app.home_lock(tmp_path):
        with pytest.raises(RuntimeError, match="DAYBOOK_HOME"):
            await app.run_daemon(config, args)
    assert (tmp_path / "token").read_text() == token


async def test_observe_once_printable_result_does_not_save_to_home(
    tmp_path: Path, monkeypatch
):
    import daybook.model
    import daybook.sense
    from daybook.model import ScriptedGateway
    from daybook.sense import ReplayScreen

    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    monkeypatch.setattr(daybook.sense, "X11Screen", lambda _: ReplayScreen(fixture))
    gateway = ScriptedGateway(
        ['{"seen":"Editing a function.","visible_text":"def example()"}']
    )
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    result = await app.observe_once(app.load_config(tmp_path))
    assert isinstance(result, dict) and result["seen"] == "Editing a function."
    assert not (tmp_path / "journal.db").exists()
    assert not (tmp_path / "journal.db-wal").exists()


@pytest.mark.parametrize("state", ["stopped", "paused", "stale"])
def test_tick_once_requires_running_daemon_without_inference_or_history_writes(
    tmp_path: Path, monkeypatch, capsys, state: str
):
    import hashlib
    from datetime import UTC, datetime, timedelta

    import daybook.model
    from daybook.events import Bus
    from daybook.journal import Journal, ObservationDraft, WindowInfo
    from daybook.model import ScriptedGateway

    config = library_config(tmp_path)
    (tmp_path / "daybook.toml").write_text(f'music_dir="{config.music_dir}"\n')
    journal = Journal(config.home / "journal.db", Bus())
    at = datetime.now(UTC) - timedelta(seconds=5)
    journal.set_capture_state("running", at)
    note = journal.add_observation(
        ObservationDraft(at, WindowInfo("Code", "example.py"), "Editing code.", ""),
        epoch=journal.epoch,
    )
    assert note is not None
    journal.extend_observation(note.id, at + timedelta(seconds=1))
    if state != "stale":
        journal.set_capture_state("paused", at + timedelta(seconds=1))
    journal.close()
    if state == "paused":
        (tmp_path / "privacy.json").write_text('{"paused":true}')
    if state == "stale":
        (tmp_path / "runtime.json").write_text(
            json.dumps({"port": 8765, "pid": os.getpid()})
        )
    before = hashlib.sha256((tmp_path / "journal.db").read_bytes()).hexdigest()
    gateway = ScriptedGateway(
        [
            json.dumps(
                {
                    "segments": [
                        {
                            "observations": [note.id],
                            "mode": "start",
                            "title": "Code",
                            "kind": "coding",
                            "intent": "Likely coding.",
                        }
                    ],
                    "signals": {"music.mood": None},
                }
            )
        ]
    )
    constructed: list[bool] = []

    def model(_):
        constructed.append(True)
        return gateway

    monkeypatch.setattr(daybook.model, "ModelGateway", model)
    monkeypatch.setenv("DAYBOOK_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["daybook", "tick-once"])
    with pytest.raises(SystemExit) as error:
        app.main()
    assert error.value.code == 1
    printed = capsys.readouterr()
    assert "running daemon" in printed.err and "daybook run" in printed.err
    assert not printed.out
    assert constructed == [] and gateway.priorities == []
    assert hashlib.sha256((tmp_path / "journal.db").read_bytes()).hexdigest() == before
    assert not (tmp_path / "runtime.json").exists()
    with app.home_lock(tmp_path):
        pass


def test_tick_cli_posts_to_running_daemon_and_prints_current_readings(
    tmp_path: Path, monkeypatch, capsys
):
    config = app.load_config(tmp_path)
    token = app.launch_token(tmp_path)
    requested: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def response(self, value):
            assert self.headers["X-Daybook-Token"] == token
            assert self.headers["Origin"] == origin
            requested.append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.response({"tick": "tick:current", "recorded": True})

        def do_GET(self):
            self.response(
                {
                    "readings": [
                        {"tick": "tick:current", "value": True},
                        {"tick": "old", "value": False},
                    ]
                }
            )

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    (tmp_path / "runtime.json").write_text(
        json.dumps({"port": server.server_port, "pid": os.getpid()})
    )
    monkeypatch.setenv("DAYBOOK_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["daybook", "tick-once"])
    try:
        with app.home_lock(config.home):
            app.main()
        printed = capsys.readouterr()
        assert not printed.err
        assert json.loads(printed.out) == [{"tick": "tick:current", "value": True}]
        assert requested == [("POST", "/api/tick"), ("GET", "/api/now")]
        assert (tmp_path / "runtime.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("sse", [False, True])
async def test_real_cli_sigterm_cleans_up_even_with_connected_sse(
    tmp_path: Path, sse: bool
):
    import socket

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "daybook.app",
        "run",
        "--mock-api",
        "--port",
        str(port),
        env={**os.environ, "DAYBOOK_HOME": str(tmp_path)},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    writer = None
    try:
        for _ in range(100):
            if process.returncode is not None:
                pytest.fail("daemon failed before accepting requests")
            try:
                reader, connection = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.02)
        else:
            pytest.fail("daemon never accepted requests")
        if sse:
            writer = connection
            writer.write(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            assert b"200" in await reader.readline()
        else:
            connection.close()
            await connection.wait_closed()
        process.terminate()
        await asyncio.wait_for(process.wait(), 5)
        assert not (tmp_path / "runtime.json").exists()
        # The home lock is released, without killing an unrelated process.
        with app.home_lock(tmp_path):
            pass
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("repeat_during_cleanup", [False, True])
async def test_real_cli_startup_signal_closes_owned_child_and_runtime(
    tmp_path: Path, monkeypatch, repeat_during_cleanup: bool
):
    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    config = library_config(tmp_path)
    config_path = tmp_path / "daybook.toml"
    config_path.write_text(
        config_path.read_text().replace(
            'music_dir = "~/Music/daybook"',
            f"music_dir = {json.dumps(str(config.music_dir))}",
        )
    )
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "mpv"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "home = Path(os.environ['DAYBOOK_HOME'])\n"
        "def stop(*_):\n"
        "    (home / 'closing-child').write_text('closing')\n"
        "    time.sleep(0.3)\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "(home / 'startup-child.pid').write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    executable.chmod(0o700)
    # The real music owner launches an external child which deliberately never
    # creates its IPC socket. No desktop, audio, model request, or shared lock.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from daybook import app; "
        "app.CAPTURE_LOCK = app.load_config().home / 'capture.lock'; app.main()",
        "run",
        "--no-capture",
        "--real-effects",
        env={
            **os.environ,
            "DAYBOOK_HOME": str(tmp_path),
            "PATH": str(executable_dir) + os.pathsep + os.environ["PATH"],
        },
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    child_pid = None
    try:
        marker = tmp_path / "startup-child.pid"
        for _ in range(200):
            if marker.exists():
                child_pid = int(marker.read_text())
                break
            assert process.returncode is None, "daemon exited before child startup"
            await asyncio.sleep(0.01)
        else:
            pytest.fail("owned child never started")
        assert (tmp_path / "runtime.json").exists()
        process.terminate()
        if repeat_during_cleanup:
            for _ in range(100):
                if (tmp_path / "closing-child").exists():
                    process.terminate()
                    break
                if process.returncode is not None:
                    break
                await asyncio.sleep(0.01)
        await asyncio.wait_for(process.wait(), 5)
        assert not (tmp_path / "runtime.json").exists(), (
            "SIGTERM during startup left the runtime descriptor"
        )
        assert process.returncode == 0
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert not (tmp_path / "mpv.sock").exists()
        with app.home_lock(tmp_path), app.capture_lock():
            pass
        if repeat_during_cleanup:
            assert (tmp_path / "closing-child").exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


async def test_runtime_cleanup_finishes_before_external_cancellation_propagates(
    tmp_path: Path, monkeypatch
):
    from daybook.act import MusicPlayer

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal,time; "
        "signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.3), exit(0))); "
        "print('ready', flush=True); time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdout is not None
    assert await process.stdout.readline() == b"ready\n"
    started, finished = asyncio.Event(), asyncio.Event()
    original_close = MusicPlayer.close

    async def close(player):
        started.set()
        process.terminate()
        await process.wait()
        await original_close(player)
        finished.set()

    monkeypatch.setattr(MusicPlayer, "close", close)
    config = library_config(tmp_path)

    async def run():
        async with app.open_runtime(config, no_capture=True):
            pass

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set(), "cancellation interrupted runtime cleanup"
        assert process.returncode == 0
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_shell_reaping_finishes_after_repeated_cancellation(tmp_path: Path):
    marker = tmp_path / "shell-ready"
    pids: asyncio.Queue[int | None] = asyncio.Queue()
    supervisor = app.ShellSupervisor(
        (
            sys.executable,
            "-c",
            "import signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(marker)!r}).write_text('ready'); time.sleep(60)",
        ),
        dict(os.environ),
        pids.put_nowait,
        on_quit=lambda: None,
    )
    task = asyncio.create_task(supervisor.run())
    pid = None
    try:
        pid = await asyncio.wait_for(pids.get(), 2)
        assert pid is not None
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("owned shell did not install its TERM handler")
        task.cancel()
        assert await asyncio.wait_for(pids.get(), 2) is None
        # TaskGroup cancellation can reach a shell already cancelled by the
        # daemon's serving-finally path. Its two-second kill fallback must run.
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, 4)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if pid is not None:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    ("entry", "available"),
    [
        ("absent", False),
        ("package_without_main", False),
        ("package", True),
        ("module", True),
    ],
)
async def test_effects_enabled_daemon_only_supervises_a_runnable_shell(
    tmp_path: Path, monkeypatch, caplog, entry: str, available: bool
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import daybook
    import daybook.web

    modules = tmp_path / "modules"
    modules.mkdir()
    if entry.startswith("package"):
        package = modules / "shell"
        package.mkdir()
        # Discovery must not initialize Qt or any shell package side effects.
        (package / "__init__.py").write_text("raise AssertionError('shell imported')")
        if entry == "package":
            (package / "__main__.py").write_text(
                "raise AssertionError('shell executed')"
            )
    elif entry == "module":
        (modules / "shell.py").write_text("raise AssertionError('shell imported')")
    monkeypatch.setattr(daybook, "__path__", [str(modules)])
    monkeypatch.delitem(sys.modules, "daybook.shell", raising=False)
    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    monkeypatch.setattr(app, "desktop_environment", lambda _: dict(os.environ))
    config = app.load_config(tmp_path)
    started: list[str] = []
    launches: list[tuple[tuple[str, ...], dict[str, str]]] = []
    pids: list[int | None] = []

    class Loop:
        def __init__(self, name):
            self.name = name

        async def run(self):
            started.append(self.name)
            await asyncio.Event().wait()

    @asynccontextmanager
    async def runtime(_config, **kwargs):
        assert kwargs["real_effects"]
        yield SimpleNamespace(
            services=SimpleNamespace(sense=Loop("sense"), mind=Loop("mind")),
            screen=SimpleNamespace(set_shell_pid=pids.append),
        )

    class Supervisor:
        def __init__(self, command, environment, on_pid, *, on_quit):
            launches.append((command, environment))
            self.on_pid = on_pid

        async def run(self):
            self.on_pid(12345)
            try:
                await asyncio.Event().wait()
            finally:
                self.on_pid(None)

    class Server:
        async def serve(self):
            await asyncio.sleep(0)
            assert sorted(started) == ["mind", "sense"]

    monkeypatch.setattr(app, "open_runtime", runtime)
    monkeypatch.setattr(app, "ShellSupervisor", Supervisor)
    monkeypatch.setattr(app, "http_server", lambda *_: Server())
    monkeypatch.setattr(daybook.web, "create_app", lambda **_: object())
    args = argparse.Namespace(
        mock_api=False, no_capture=False, replay=None, real_effects=False
    )
    await app.run_daemon(config, args)
    launch_count = len(launches)
    assert launch_count == int(available)
    assert pids == ([12345, None] if available else [])
    if available:
        command, environment = launches[0]
        assert command == (
            sys.executable,
            "-m",
            "daybook.shell",
            "--port",
            str(config.port),
        )
        assert environment["DAYBOOK_TOKEN"] == (tmp_path / "token").read_text()
        assert "shell_unavailable" not in caplog.text
    else:
        assert caplog.text.count("shell_unavailable") == 1
    assert not (tmp_path / "runtime.json").exists()


@pytest.mark.parametrize("capture", ["running", "locked"])
async def test_runtime_stops_active_capture_history_before_restart(
    tmp_path: Path, monkeypatch, capture: str
):
    from datetime import UTC, datetime, timedelta

    import daybook.model
    from daybook.events import Bus
    from daybook.journal import Journal, TimeRange
    from daybook.model import ScriptedGateway

    gateway = ScriptedGateway(
        ['{"seen":"Editing code.","visible_text":""}'] if capture == "running" else []
    )
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    config = library_config(tmp_path)
    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    started = datetime.now(UTC)
    async with app.open_runtime(
        config,
        replay=fixture if capture == "running" else None,
        no_capture=capture != "running",
    ) as runtime:
        await runtime.services.sense.poll(started)
        await runtime.services.sense.wait_idle()
        assert runtime.services.journal.portal.now().capture == capture
        before_close = datetime.now(UTC)
    after_close = datetime.now(UTC)
    reopened = Journal(config.home / "journal.db", Bus())
    try:
        assert reopened.portal.now().capture == (
            "paused" if capture == "running" else capture
        )
        restarted = datetime.now(UTC)
        reopened.set_capture_state("running", restarted)
        spans = reopened.portal.capture_spans(
            TimeRange(started, restarted + timedelta(seconds=1))
        )
        if capture == "running":
            assert [span.state for span in spans] == ["running", "paused", "running"]
            assert spans[0].end is not None
            assert before_close <= spans[0].end <= after_close
            assert spans[1].start == spans[0].end and spans[1].end == restarted
        else:
            assert [span.state for span in spans] == ["locked", "running"]
        assert not (tmp_path / "privacy.json").exists()
    finally:
        reopened.close()


@pytest.mark.parametrize("reachable", [False, True])
async def test_tick_once_keeps_live_home_ownership_and_uses_daemon(
    tmp_path: Path, monkeypatch, reachable: bool
):
    config = app.load_config(tmp_path)
    descriptor = json.dumps({"port": config.port, "pid": os.getpid()})
    (tmp_path / "runtime.json").write_text(descriptor)
    requested: list[str] = []

    def request(_config, route, **_):
        requested.append(route)
        if not reachable:
            raise RuntimeError("Cannot reach the daemon for this DAYBOOK_HOME.")
        return (
            {"recorded": True, "tick": "tick:current"}
            if route == "/api/tick"
            else {
                "readings": [
                    {"tick": "tick:current", "value": True},
                    {"tick": "old", "value": False},
                ]
            }
        )

    monkeypatch.setattr(app, "daemon_request", request)
    with app.home_lock(tmp_path):
        if reachable:
            assert await app.tick_once(config) == [
                {"tick": "tick:current", "value": True}
            ]
        else:
            with pytest.raises(RuntimeError, match="Cannot reach"):
                await app.tick_once(config)
        assert (tmp_path / "runtime.json").read_text() == descriptor
    assert requested == (["/api/tick", "/api/now"] if reachable else ["/api/tick"])


def test_debug_frame_retention_is_rejected_explicitly(tmp_path: Path):
    (tmp_path / "daybook.toml").write_text("keep_frames=true\n")
    with pytest.raises(ValueError, match="not supported"):
        app.load_config(tmp_path)


@pytest.mark.parametrize(
    ("layout", "physical_fullscreen"),
    [(None, False), ("fullscreen", False), ("bubble", False), ("bubble", True)],
)
async def test_catalog_overlay_layout_controls_composed_capture(
    tmp_path: Path, monkeypatch, layout: str | None, physical_fullscreen: bool
):
    import daybook.model
    from daybook.model import ScriptedGateway
    from daybook.sense import ReplayScreen

    gateway = ScriptedGateway(['{"seen":"Editing code.","visible_text":"example.py"}'])
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    config = library_config(tmp_path)
    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    async with app.open_runtime(config, replay=fixture) as runtime:
        services = runtime.services
        if physical_fullscreen:
            assert isinstance(services.sense.screen, app.OverlayScreen)
            raw = services.sense.screen.screen
            assert isinstance(raw, ReplayScreen)
            state = raw.state
            monkeypatch.setattr(raw, "state", lambda: replace(state(), overlay=True))
        arguments = {} if layout is None else {"layout": layout}
        result = await services.catalog.allowed(("overlay.show",), "reminder").run(
            "overlay.show", title="Reminder", body="Grab some water.", **arguments
        )
        assert result.ok
        assert services.overlay.state is not None
        assert services.overlay.state.request.layout == (layout or "fullscreen")
        await services.sense.poll()
        await services.sense.wait_idle()
        permits_capture = layout == "bubble" and not physical_fullscreen
        assert services.journal.portal.now().capture == (
            "running" if permits_capture else "overlay"
        )
        observation = services.journal.portal.now().observation
        assert (observation is not None) is permits_capture
        if observation is not None:
            assert observation.window.app == "Code"
        assert len(gateway.prompts) == int(permits_capture)
        assert not list(tmp_path.glob("*.png")) and not list(tmp_path.glob("*.jpg"))


async def test_public_skill_approval_refreshes_still_frame_evidence(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    import httpx

    import daybook.model
    from daybook.model import ScriptedGateway
    from daybook.web import create_app

    gateway = ScriptedGateway(['{"seen":"Editing code.","visible_text":""}'] * 8)
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    config = library_config(tmp_path)
    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    started = datetime.now(UTC)
    question = "Is the current result marked complete?"
    text = (
        "---\nid: result-check\nname: Result check\nsignals:\n"
        f"  complete:\n    ask: {question}\n    type: bool\n"
        "rules:\n  - when: complete becomes true\n    do:\n"
        "      - notify.send: {title: Result, body: Review the result}\n"
        "allow: [notify.send]\n---\nReview completed results.\n"
    )
    async with app.open_runtime(config, replay=fixture) as runtime:
        services = runtime.services
        assert isinstance(services.sense.screen, app.OverlayScreen)
        raw = services.sense.screen.screen
        facts = raw.state()
        monkeypatch.setattr(raw, "state", lambda: facts)

        async def poll_at(seconds):
            await services.sense.poll(started + timedelta(seconds=seconds))
            await services.sense.wait_idle()

        await poll_at(0)
        baseline = tuple(s.ask for s in services.skills.active_signals())
        api = create_app(token="test-token", origin="http://test", services=services)
        headers = {"Origin": "http://test", "X-Daybook-Token": "test-token"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api),
            base_url="http://test",
            headers=headers,
        ) as client:
            parsed = await client.post(
                "/api/skills/parse", json={"id": "result-check", "text": text}
            )
            assert parsed.status_code == 200 and parsed.json()["valid"]
            saved = await client.put("/api/skills/result-check", json={"text": text})
            assert saved.status_code == 200
            await poll_at(10)
            assert len(gateway.prompts) == 1
            assert tuple(s.ask for s in services.skills.active_signals()) == baseline
            approved = await client.post(
                "/api/skills/result-check/approve", json={"hash": saved.json()["hash"]}
            )
            assert approved.status_code == 200
            await poll_at(11)
            assert len(gateway.prompts) == 2
            assert question in gateway.prompts[-1].user
            await client.post("/api/skills/result-check/disable")
            for seconds in (12, 20):
                await poll_at(seconds)
                assert len(gateway.prompts) == 2
            await poll_at(21)
            assert len(gateway.prompts) == 3
            assert question not in gateway.prompts[-1].user
            enabled = await client.post("/api/skills/result-check/enable")
            assert enabled.status_code == 200
            for seconds in (22, 30):
                await poll_at(seconds)
                assert len(gateway.prompts) == 3
            await poll_at(31)
            assert len(gateway.prompts) == 4
            assert question in gateway.prompts[-1].user
            replacement = "Is the current result awaiting review?"
            edited = await client.put(
                "/api/skills/result-check",
                json={"text": text.replace(question, replacement)},
            )
            assert edited.status_code == 200
            await poll_at(32)
            assert len(gateway.prompts) == 4
            await poll_at(41)
            assert len(gateway.prompts) == 5
            assert question not in gateway.prompts[-1].user
            assert replacement not in gateway.prompts[-1].user
            # Fresh approval clears stale readings and changes the journal epoch:
            # retain the existing resume exemption.
            await client.post(
                "/api/skills/result-check/approve", json={"hash": edited.json()["hash"]}
            )
            await poll_at(42)
            assert len(gateway.prompts) == 6
            assert replacement in gateway.prompts[-1].user
            assert question not in gateway.prompts[-1].user


async def test_newly_approved_questions_wait_for_fresh_observation_before_tick(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    import httpx

    import daybook.mind
    import daybook.model
    from daybook.model import Priority, ScriptedGateway
    from daybook.web import create_app

    now = [datetime.now(UTC) + timedelta(seconds=1)]

    class RuntimeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    monkeypatch.setattr(app, "datetime", RuntimeDateTime)
    original_mind = daybook.mind.Mind
    monkeypatch.setattr(
        daybook.mind,
        "Mind",
        lambda *args, **kwargs: original_mind(*args, clock=lambda: now[0], **kwargs),
    )
    question = "Is the visible result marked complete?"

    class EvidenceGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            built = await prompt() if callable(prompt) else prompt
            if built is None:
                return None
            if priority == Priority.OBSERVE:
                # The same frame includes a status detail omitted before the
                # approved question asks the observer to preserve it.
                seen = (
                    "The result is marked complete."
                    if question in built.user
                    else "An editor displays source code."
                )
                self.replies.append(json.dumps({"seen": seen, "visible_text": ""}))
            else:
                current = built.user.split("CURRENT evidence;", 1)[1].split(
                    "Questions about CURRENT evidence;", 1
                )[0]
                self.replies.append(
                    json.dumps(
                        {
                            "segments": [
                                {
                                    **{
                                        "mode": "start",
                                        "title": "Code",
                                        "kind": "coding",
                                        "intent": "Likely working.",
                                    },
                                    "observations": re.findall(
                                        r"^([0-9a-f]{32}) ", built.user, re.M
                                    ),
                                }
                            ],
                            "signals": {
                                str(s.id): "result is marked complete" in current
                                for s in services.skills.active_signals()
                            },
                        }
                    )
                )
            return await super().call(priority, built)

    gateway = EvidenceGateway([])
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    text = (
        "---\nid: result-check\nname: Result check\nsignals:\n"
        f"  complete:\n    ask: {question}\n    type: bool\n"
        "rules:\n  - when: complete becomes true\n    do:\n"
        "      - overlay.show: {title: Result, body: Review the result}\n"
        "allow: [overlay.show]\n---\nReview completed results.\n"
    )
    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    async with app.open_runtime(library_config(tmp_path), replay=fixture) as runtime:
        services = runtime.services
        for entry in services.skills.entries():
            services.skills.disable(entry.id)
        assert isinstance(services.sense.screen, app.OverlayScreen)
        raw = services.sense.screen.screen
        facts = raw.state()
        monkeypatch.setattr(raw, "state", lambda: facts)
        await services.sense.poll(now[0])
        await services.sense.wait_idle()
        assert question not in gateway.prompts[-1].user
        api = create_app(token="test", origin="http://test", services=services)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api),
            base_url="http://test",
            headers={"Origin": "http://test", "X-Daybook-Token": "test"},
        ) as client:
            saved = await client.put("/api/skills/result-check", json={"text": text})
            approved = await client.post(
                "/api/skills/result-check/approve", json={"hash": saved.json()["hash"]}
            )
            assert approved.status_code == 200
            stale = await client.post("/api/tick")
            stale_now = (await client.get("/api/now")).json()
            now[0] += timedelta(seconds=40)
            await services.sense.poll(now[0])
            await services.sense.wait_idle()
            assert question in gateway.prompts[-1].user
            refreshed = await client.post("/api/tick")
            assert refreshed.status_code == 200 and refreshed.json()["recorded"]
            assert stale.json()["recorded"] is False, (
                stale.json(),
                stale_now["readings"],
                "startup overlay fired",
                services.overlay.state is not None,
            )
            assert services.overlay.state is None


@pytest.mark.parametrize("stage", ["queued", "inflight"])
async def test_question_disable_discards_queued_or_inflight_public_tick(
    tmp_path, monkeypatch, stage
):
    from datetime import UTC, datetime, timedelta

    import httpx

    import daybook.mind
    import daybook.model
    from daybook.model import Priority, ScriptedGateway
    from daybook.web import create_app

    now = [datetime.now(UTC) + timedelta(seconds=1)]

    class RuntimeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    monkeypatch.setattr(app, "datetime", RuntimeDateTime)
    original_mind = daybook.mind.Mind
    monkeypatch.setattr(
        daybook.mind,
        "Mind",
        lambda *args, **kwargs: original_mind(*args, clock=lambda: now[0], **kwargs),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldTickGateway(ScriptedGateway):
        async def call(self, priority, prompt):
            if priority != Priority.TICK:
                return await super().call(priority, prompt)
            if stage == "queued":
                entered.set()
                await release.wait()
            built = await prompt() if callable(prompt) else prompt
            if built is None:
                return None
            if stage == "inflight":
                entered.set()
                await release.wait()
            queued = self.replies[0]
            assert isinstance(queued, str)
            answer = json.loads(queued)
            for group in answer["segments"]:
                group["observations"] = re.findall(
                    r"^([0-9a-f]{32}) ", built.user, re.M
                )
            self.replies[0] = json.dumps(answer)
            return await super().call(priority, built)

    answer = json.dumps(
        {
            "segments": [
                {
                    **{
                        "mode": "start",
                        "title": "Code",
                        "kind": "coding",
                        "intent": "Working.",
                    },
                    "observations": ["$batch"],
                }
            ],
            "signals": {"result-check.complete": True},
        }
    )
    gateway = HeldTickGateway(
        ['{"seen":"The visible result is complete.","visible_text":""}', answer, answer]
    )
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    text = (
        "---\nid: result-check\nname: Result check\nsignals:\n"
        "  complete:\n    ask: Is the result complete?\n    type: bool\n"
        "rules:\n  - when: complete becomes true\n    do:\n"
        "      - overlay.show: {title: Result, body: Review the result}\n"
        "allow: [overlay.show]\n---\nReview results.\n"
    )
    fixture = Path(__file__).resolve().parents[2] / "fixtures/replay/example"
    async with app.open_runtime(library_config(tmp_path), replay=fixture) as runtime:
        services = runtime.services
        for entry in services.skills.entries():
            services.skills.disable(entry.id)
        api = create_app(token="test", origin="http://test", services=services)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api),
            base_url="http://test",
            headers={"Origin": "http://test", "X-Daybook-Token": "test"},
        ) as client:
            saved = await client.put("/api/skills/result-check", json={"text": text})
            await client.post(
                "/api/skills/result-check/approve", json={"hash": saved.json()["hash"]}
            )
            await services.sense.poll(now[0])
            await services.sense.wait_idle()
            original_observation = services.journal.portal.now().observation
            ticking = asyncio.create_task(client.post("/api/tick"))
            await asyncio.wait_for(entered.wait(), 1)
            epoch = services.journal.epoch
            await client.post("/api/skills/result-check/disable")
            assert services.journal.epoch == epoch  # No forget barrier masks this race.
            release.set()
            result = await ticking
            assert result.json()["recorded"] is False
            assert gateway.priorities.count(Priority.TICK) == int(stage == "inflight")
            assert not services.journal.portal.now().readings
            # Retained approval with identical questions can reuse the same facts.
            await client.post("/api/skills/result-check/enable")
            now[0] += timedelta(seconds=30)
            result = await client.post("/api/tick")
            assert result.json()["recorded"]
            retained = services.journal.portal.now().observation
            assert retained is not None and original_observation is not None
            assert retained.id == original_observation.id
            assert retained.seen == original_observation.seen
            assert gateway.priorities.count(Priority.OBSERVE) == 1
            assert services.overlay.state is None


async def test_shell_deliberate_quit_stops_daemon_and_owned_runtime(
    tmp_path: Path, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import daybook.web

    monkeypatch.setattr(app, "CAPTURE_LOCK", tmp_path / "capture.lock")
    monkeypatch.setattr(app, "desktop_environment", lambda _: dict(os.environ))
    monkeypatch.setattr(app, "shell_entry_available", lambda: True)
    config = app.load_config(tmp_path)
    events: list[str] = []
    shell_pids: list[int | None] = []
    music_pid: int | None = None
    original_supervisor = app.ShellSupervisor

    class Loop:
        def __init__(self, name):
            self.name = name

        async def run(self):
            events.append(self.name + "-started")
            try:
                await asyncio.Event().wait()
            finally:
                events.append(self.name + "-stopped")

    @asynccontextmanager
    async def runtime(_config, **kwargs):
        nonlocal music_pid
        music = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)"
        )
        music_pid = music.pid
        try:
            yield SimpleNamespace(
                services=SimpleNamespace(sense=Loop("sense"), mind=Loop("mind")),
                screen=SimpleNamespace(set_shell_pid=shell_pids.append),
            )
        finally:
            music.terminate()
            await music.wait()
            events.append("music-stopped")

    def supervisor(command, environment, on_pid, **kwargs):
        return original_supervisor(
            (sys.executable, "-c", "raise SystemExit(20)"),
            environment,
            on_pid,
            retry_seconds=0.01,
            **kwargs,
        )

    class Server:
        should_exit = False

        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0.001)

    monkeypatch.setattr(app, "open_runtime", runtime)
    monkeypatch.setattr(app, "ShellSupervisor", supervisor)
    monkeypatch.setattr(app, "http_server", lambda *_: Server())
    monkeypatch.setattr(daybook.web, "create_app", lambda **_: object())
    args = argparse.Namespace(
        mock_api=False, no_capture=False, replay=None, real_effects=False
    )
    await asyncio.wait_for(app.run_daemon(config, args), 2)
    assert sorted(events) == [
        "mind-started",
        "mind-stopped",
        "music-stopped",
        "sense-started",
        "sense-stopped",
    ]
    assert len(shell_pids) == 2 and shell_pids[0] is not None and shell_pids[1] is None
    assert music_pid is not None
    for pid in (shell_pids[0], music_pid):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not (tmp_path / "runtime.json").exists()
    with app.home_lock(tmp_path), app.capture_lock():
        pass


@pytest.mark.parametrize("exit_code", [0, 1, -signal.SIGTERM])
async def test_unexpected_shell_exits_restart_without_quitting_daemon(
    tmp_path: Path, exit_code: int
):
    from daybook.web.wire import SHELL_QUIT_EXIT_CODE

    marker = tmp_path / "launch-count"
    # The first child exits unexpectedly; the replacement stays alive for reap.
    script = (
        "import os,signal,time; from pathlib import Path; "
        f"p=Path({str(marker)!r}); n=int(p.read_text()) if p.exists() else 0; "
        "p.write_text(str(n+1)); "
        f"code={exit_code}; "
        "\nif n == 0:\n"
        "    if code < 0: os.kill(os.getpid(), -code)\n"
        "    raise SystemExit(code)\n"
        "time.sleep(60)"
    )
    pids: asyncio.Queue[int | None] = asyncio.Queue()
    quits: list[bool] = []
    supervisor = app.ShellSupervisor(
        (sys.executable, "-c", script),
        dict(os.environ),
        pids.put_nowait,
        on_quit=lambda: quits.append(True),
        retry_seconds=0.01,
    )
    task = asyncio.create_task(supervisor.run())
    first = second = None
    try:
        first = await asyncio.wait_for(pids.get(), 2)
        assert first is not None
        assert await asyncio.wait_for(pids.get(), 2) is None
        second = await asyncio.wait_for(pids.get(), 2)
        assert second is not None and second != first
        assert not quits
        assert exit_code != SHELL_QUIT_EXIT_CODE
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert await asyncio.wait_for(pids.get(), 2) is None
    for pid in (first, second):
        if pid is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


async def test_deliberate_shell_quit_callback_runs_once_without_restart():
    from daybook.web.wire import SHELL_QUIT_EXIT_CODE

    pids: list[int | None] = []
    quits: list[bool] = []
    supervisor = app.ShellSupervisor(
        (sys.executable, "-c", f"raise SystemExit({SHELL_QUIT_EXIT_CODE})"),
        dict(os.environ),
        pids.append,
        on_quit=lambda: quits.append(True),
        retry_seconds=0.01,
    )
    await asyncio.wait_for(supervisor.run(), 2)
    assert quits == [True]
    assert len(pids) == 2 and pids[0] is not None and pids[1] is None
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)
