import threading

import httpx
import pytest

from daybook.shell._client import Backend, read_token, sse_events


def test_sse_events_dispatch_named_events_and_skip_comments():
    lines = [
        ": connected",
        "",
        "event: OverlayShown",
        'data: {"id": "o1",',
        'data: "request": {}}',
        "",
        ": heartbeat",
        "",
        "event: OverlayHidden",
        'data: {"id": "o1"}',
        "",
        "event: Empty",
        "",
    ]
    assert list(sse_events(lines)) == [
        ("OverlayShown", '{"id": "o1",\n"request": {}}'),
        ("OverlayHidden", '{"id": "o1"}'),
    ]


def test_read_token_prefers_env_then_home_file(tmp_path):
    assert read_token({"DAYBOOK_TOKEN": "abc"}) == "abc"
    (tmp_path / "token").write_text("from-file\n")
    assert read_token({"DAYBOOK_HOME": str(tmp_path)}) == "from-file"
    with pytest.raises(FileNotFoundError):
        read_token({"DAYBOOK_HOME": str(tmp_path / "missing")})


def test_dismiss_sends_token_and_origin_and_retries_once():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    backend = Backend(8765, "t0k", http)
    assert backend.dismiss("o1")
    assert len(seen) == 2
    request = seen[-1]
    assert request.method == "POST"
    assert request.url.path == "/api/overlay/o1/dismiss"
    assert request.headers["X-Daybook-Token"] == "t0k"
    assert request.headers["Origin"] == "http://127.0.0.1:8765"


def test_dismiss_gives_up_on_client_errors():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"detail": "invalid_token_or_origin"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    assert not Backend(8765, "bad", http).dismiss("o1")
    assert len(calls) == 1


def test_run_resyncs_from_status_then_delivers_events():
    stop = threading.Event()
    statuses, events = [], []
    stream = (
        b": connected\n\n"
        b'event: OverlayShown\ndata: {"id": "o1", "request": {"title": "Hi"}}\n\n'
        b'event: OverlayHidden\ndata: {"id": "o1"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/events":
            assert request.url.params["client"] == "shell"
            return httpx.Response(200, content=stream)
        return httpx.Response(200, json={"overlay": None, "capture": "running"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")

    def on_event(name, data):
        events.append(name)
        if name == "OverlayHidden":
            stop.set()

    Backend(8765, "t", http).run(statuses.append, on_event, stop)
    assert statuses == [{"overlay": None, "capture": "running"}]
    assert events == ["OverlayShown", "OverlayHidden"]
