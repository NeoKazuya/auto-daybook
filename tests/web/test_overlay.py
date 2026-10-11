import pytest
from fastapi.testclient import TestClient

from daybook.events import Bus
from daybook.web import create_app
from daybook.web import wire as w
from daybook.web._mock import MockAPI


def test_mock_overlay_test_contract_and_event():
    bus = Bus()
    mock = MockAPI(bus)
    mock.shells = 1
    with bus.subscribe() as queue:
        state = mock.overlay_test()
        name, data = w.serialize_event(queue.get_nowait())
    assert name == "OverlayShown"
    event = w.OverlayShownPayload.model_validate_json(data)
    assert event.id == state.id
    assert event.request.title == state.request.title
    assert state.owner == "manual"
    assert mock.dismiss(state.id).ok
    assert mock.status.overlay is None


def test_mock_overlay_route_rejects_without_shell_and_without_auth():
    app = create_app(mock=True, token="test-token", origin="http://testserver")
    with TestClient(app) as client:
        assert client.post("/api/overlay/test").status_code == 403
        response = client.post(
            "/api/overlay/test",
            headers={"Origin": "http://testserver", "X-Daybook-Token": "test-token"},
        )
        assert response.status_code == 409
        assert response.json() == {"detail": "shell_disconnected"}


def test_mock_music_play_contract_and_event():
    bus = Bus()
    mock = MockAPI(bus)
    with bus.subscribe() as queue:
        status = mock.music_play("calm")
        name, data = w.serialize_event(queue.get_nowait())
    assert name == "MusicChanged"
    event = w.MusicChangedPayload.model_validate_json(data)
    assert event.mood == status.mood == "calm"
    assert event.title == status.title


def test_mock_music_route_validates_request():
    app = create_app(mock=True, token="test-token", origin="http://testserver")
    headers = {"Origin": "http://testserver", "X-Daybook-Token": "test-token"}
    with TestClient(app) as client:
        result = client.post("/api/music/play", headers=headers, json={"mood": "calm"})
        assert result.status_code == 200
        assert w.MusicStatus.model_validate(result.json()).playing
        assert (
            client.post(
                "/api/music/play", headers=headers, json={"mood": "missing"}
            ).status_code
            == 409
        )


@pytest.mark.parametrize("layout", ["fullscreen", "bubble"])
async def test_real_overlay_layout_status_and_sse_roundtrip(layout, tmp_path):
    from types import SimpleNamespace
    from typing import cast

    from daybook.act import MusicStatus, Overlay, OverlayRequest
    from daybook.model import ScriptedGateway
    from daybook.sense import CaptureStatus
    from daybook.web import Services
    from daybook.web._live import LiveAPI

    bus = Bus()
    overlay = Overlay(bus, home=tmp_path)
    overlay.shell_connected(True)
    backend = LiveAPI(
        cast(
            Services,
            SimpleNamespace(
                overlay=overlay,
                sense=SimpleNamespace(status=CaptureStatus("running")),
                gateway=ScriptedGateway([]),
                music=SimpleNamespace(status=lambda: MusicStatus()),
                journal=SimpleNamespace(
                    portal=SimpleNamespace(
                        now=lambda: SimpleNamespace(capture="running")
                    )
                ),
            ),
        )
    )
    try:
        request = w.OverlayRequest(
            title="Break", body="Grab some water.", layout=layout
        )
        with bus.subscribe() as queue:
            assert (
                await overlay.show(OverlayRequest(**request.model_dump()), "reminder")
            ).ok
            name, data = w.serialize_event(queue.get_nowait())
        assert name == "OverlayShown"
        event = w.OverlayShownPayload.model_validate_json(data)
        status = w.StatusResponse.model_validate_json(backend.status.model_dump_json())
        assert status.overlay is not None
        assert status.overlay.request.layout == event.request.layout == layout
        assert status.overlay.request.body == "Grab some water."
        assert status.overlay.id == event.id
    finally:
        await overlay.hide()
        await overlay.close()


def test_wire_overlay_layout_default_and_invalid_value():
    from pydantic import ValidationError

    assert w.OverlayRequest(title="Break", body="Rest.").layout == "fullscreen"
    with pytest.raises(ValidationError):
        w.OverlayRequest.model_validate(
            {"title": "Break", "body": "Rest.", "layout": "popup"}
        )
    assert w.example(w.OverlayRequest).layout == "fullscreen"
