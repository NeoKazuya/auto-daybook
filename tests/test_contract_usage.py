"""Caller examples written before the shared contracts."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from daybook.events import Bus, StatusChanged
from daybook.journal import Observation, WindowInfo
from daybook.web import create_app


def test_mock_user_flow():
    with TestClient(
        create_app(mock=True, token="test", origin="http://testserver")
    ) as client:
        headers = {"X-Daybook-Token": "test", "Origin": "http://testserver"}
        timeline = client.get("/api/timeline").json()
        assert timeline["segments"][0]["observations"][0]["seen"]
        reply = client.post(
            "/api/chat",
            json={"message": "/skill remind me to stand up after meetings"},
            headers=headers,
        ).json()
        entry = reply["entry"]
        assert entry["status"] == "needs_review"
        approved = client.post(
            f"/api/skills/{entry['id']}/approve",
            json={"hash": entry["hash"]},
            headers=headers,
        )
        assert approved.json()["status"] == "active"
        assert client.put(
            "/api/privacy",
            json={"paused": True, "exclude_apps": ["Bitwarden"], "exclude_titles": []},
            headers=headers,
        ).json()["paused"]


async def test_bus_usage():
    bus = Bus()
    with bus.subscribe() as queue:
        event = StatusChanged(component="model", status="ready")
        bus.publish(event)
        assert await queue.get() == event


def test_observation_is_frozen():
    from dataclasses import FrozenInstanceError

    import pytest

    now = datetime.now(timezone.utc)
    note = Observation(
        "o1",
        now,
        now,
        WindowInfo("Code", "main.py"),
        "A Python file is open.",
        "",
        None,
    )
    assert note.active == 0
    with pytest.raises(FrozenInstanceError):
        setattr(note, "seen", "changed")
