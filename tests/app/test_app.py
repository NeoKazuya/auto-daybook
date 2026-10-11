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

        def log_message(self, *_):
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
