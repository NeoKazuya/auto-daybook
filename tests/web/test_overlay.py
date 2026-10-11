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
