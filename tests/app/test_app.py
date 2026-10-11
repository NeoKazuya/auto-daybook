from __future__ import annotations

import argparse
import asyncio
import json
import os
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


def test_missing_config_creates_defaults_and_keeps_existing_values(tmp_path: Path):
    config = app.load_config(tmp_path)
    assert config.home == tmp_path
    assert config.port == 8765 and config.tick_interval == 90
    assert config.model.model == "qwen3.6:35b"
    assert config.model.dialect == "ollama"
    assert config.tick_effort == "none" and not config.keep_frames
    path = tmp_path / "daybook.toml"
    path.write_text(
        'port=8771\ntick_interval="0.5m"\ntick_effort="low"\n[model]\ndialect="vllm"\napi_key_file="~/private/key"\n'
    )
    updated = app.load_config(tmp_path)
    assert updated.port == 8771 and updated.tick_interval == 30
    assert updated.tick_effort == "low"
    assert updated.model.api_key_file == "~/private/key"
    assert path.read_text().startswith("port=8771")


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
        screen.grab()
    with pytest.raises(RuntimeError, match="disabled"):
        screen.thumbnail()


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

    monkeypatch.setattr(app.uvicorn, "Server", Server)
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
        assert services.journal.portal.now().observation is not None
        assert gateway.priorities == [daybook.model.Priority.OBSERVE]
        gateway.replies.append(
            json.dumps(
                {
                    "segment": {
                        "mode": "start",
                        "title": "Implement example",
                        "kind": "coding",
                        "intent": "Likely implementing a function.",
                    },
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
        "---\nid: test\nname: Test\nsignals: {}\nrules: []\nallow: []\n---\nDo nothing."
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


async def test_tick_once_records_current_unassigned_notes_without_a_daemon(
    tmp_path: Path, monkeypatch
):
    from datetime import UTC, datetime

    import daybook.model
    from daybook.events import Bus
    from daybook.journal import Journal, ObservationDraft, WindowInfo
    from daybook.model import ScriptedGateway

    config = library_config(tmp_path)
    journal = Journal(config.home / "journal.db", Bus())
    now = datetime.now(UTC)
    journal.add_observation(
        ObservationDraft(now, WindowInfo("Code", "example.py"), "Editing code.", ""),
        epoch=journal.epoch,
    )
    journal.close()
    gateway = ScriptedGateway(
        [
            json.dumps(
                {
                    "segment": {
                        "mode": "start",
                        "title": "Implement example",
                        "kind": "coding",
                        "intent": "Likely writing code.",
                    },
                    "signals": {},
                }
            )
        ]
    )
    monkeypatch.setattr(daybook.model, "ModelGateway", lambda _: gateway)
    result = await app.tick_once(config)
    assert isinstance(result, list) and result
    assert all(reading["tick"].startswith("tick:") for reading in result)
    reopened = Journal(config.home / "journal.db", Bus())
    try:
        segment = reopened.portal.now().segment
        assert segment is not None and segment.title == "Implement example"
    finally:
        reopened.close()
