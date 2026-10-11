import json
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


def test_set_paused_changes_only_paused_with_token_and_origin():
    seen = []
    saved = {
        "paused": False,
        "exclude_apps": ["keepassxc"],
        "exclude_titles": ["*Bank*"],
        "keep_frames": False,
        "deletion_pending": False,
        "screenshots_stored": "none",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={**saved, **json.loads(request.content)})
        return httpx.Response(200, json=saved)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    result = Backend(8765, "t0k", http).set_paused(True)
    assert result["paused"] is True
    get, put = seen
    assert (get.method, get.url.path) == ("GET", "/api/privacy")
    assert (put.method, put.url.path) == ("PUT", "/api/privacy")
    assert json.loads(put.content) == {
        "paused": True,
        "exclude_apps": ["keepassxc"],
        "exclude_titles": ["*Bank*"],
        "expected": {
            "paused": False,
            "exclude_apps": ["keepassxc"],
            "exclude_titles": ["*Bank*"],
        },
    }
    assert put.headers["X-Daybook-Token"] == "t0k"
    assert put.headers["Origin"] == "http://127.0.0.1:8765"


def test_run_reports_a_lost_stream():
    stop = threading.Event()
    statuses, events = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    def on_event(name, data):
        events.append(name)
        stop.set()

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    Backend(8765, "t", http).run(statuses.append, on_event, stop)
    assert statuses == []
    assert events == ["Disconnected"]


@pytest.mark.parametrize("hide_at", ["before_subscription", "during_snapshot"])
def test_reconnect_does_not_leave_overlay_hidden_in_subscription_gap(hide_at):
    """HTTP headers can arrive before the server's event generator subscribes."""
    stop = threading.Event()
    current: dict[str, dict[str, str] | None] = {"overlay": {"id": "old-overlay"}}
    displayed = []
    queued = []
    subscribed = False

    def hide():
        current["overlay"] = None
        if subscribed:
            queued.append(b'event: OverlayHidden\ndata: {"id": "old-overlay"}\n\n')

    class DelayedSubscription(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal subscribed
            if hide_at == "before_subscription":
                hide()
            subscribed = True
            yield b": connected\n\n"
            while queued:
                yield queued.pop(0)

    def handler(request):
        if request.url.path == "/api/events":
            return httpx.Response(200, stream=DelayedSubscription())
        snapshot = dict(current)
        if hide_at == "during_snapshot":
            hide()
        return httpx.Response(200, json=snapshot)

    def on_status(status):
        displayed.append(status["overlay"])

    def on_event(name, data):
        if name == "OverlayHidden":
            displayed.append(None)
        elif name == "Disconnected":
            stop.set()

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://x"
    ) as http:
        Backend(8765, "t", http).run(on_status, on_event, stop)

    assert displayed
    assert displayed[-1] is None


def test_stream_ending_before_ready_never_applies_unsynchronized_snapshot():
    stop = threading.Event()
    statuses = []

    def handler(request):
        if request.url.path == "/api/events":
            return httpx.Response(200, content=b": heartbeat\n\n")
        return httpx.Response(200, json={"overlay": {"id": "stale"}})

    def on_event(name, data):
        assert name == "Disconnected"
        stop.set()

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://x"
    ) as http:
        Backend(8765, "t", http).run(statuses.append, on_event, stop)
    assert statuses == []


def test_pause_retries_conflict_and_preserves_new_exclusion():
    saved = {"paused": False, "exclude_apps": [], "exclude_titles": []}
    puts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=saved)
        body = json.loads(request.content)
        puts.append(body)
        if len(puts) == 1:
            saved["exclude_titles"] = ["*private*"]
        if body.get("expected") != saved:
            return httpx.Response(409, json={"detail": "privacy_conflict"})
        saved.update({k: body[k] for k in saved})
        return httpx.Response(200, json=saved)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://x"
    ) as http:
        result = Backend(8765, "t", http).set_paused(True)
    assert result == {
        "paused": True,
        "exclude_apps": [],
        "exclude_titles": ["*private*"],
    }
    assert len(puts) == 2
    assert puts[-1]["expected"]["exclude_titles"] == ["*private*"]


def test_pause_conflicts_stop_after_three_attempts():
    puts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"paused": False, "exclude_apps": [], "exclude_titles": []}
            )
        puts.append(request)
        return httpx.Response(409, json={"detail": "privacy_conflict"})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://x"
    ) as http:
        with pytest.raises(httpx.HTTPStatusError):
            Backend(8765, "t", http).set_paused(True)
    assert len(puts) == 3
