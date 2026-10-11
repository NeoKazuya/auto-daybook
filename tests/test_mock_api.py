import pytest
from fastapi.testclient import TestClient

from daybook.web import create_app
from daybook.web import wire as w

HEADERS = {"X-Daybook-Token": "test", "Origin": "http://testserver"}


@pytest.fixture
def client():
    with TestClient(
        create_app(mock=True, token="test", origin="http://testserver")
    ) as client:
        yield client


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("patch", "/api/segments/s-code", {"title": "Edited"}),
        ("delete", "/api/segments/s-code", None),
        ("patch", "/api/observations/o-code", {"seen": "Edited"}),
        ("delete", "/api/observations/o-code", None),
        ("post", "/api/forget", w.EXAMPLES[w.TimeWindow]),
        ("post", "/api/forget-all", None),
        ("post", "/api/chat", {"message": "hello"}),
        ("post", "/api/skills/parse", w.EXAMPLES[w.SkillParseRequest]),
        ("put", "/api/skills/stand-after-meeting", w.EXAMPLES[w.SkillPut]),
        ("post", "/api/skills/stand-after-meeting/approve", {"hash": "stale"}),
        ("post", "/api/skills/stand-after-meeting/enable", None),
        ("post", "/api/skills/stand-after-meeting/disable", None),
        ("delete", "/api/skills/stand-after-meeting", None),
        ("put", "/api/privacy", w.EXAMPLES[w.PrivacyPut]),
        ("post", "/api/overlay/overlay-demo/dismiss", None),
        ("post", "/api/tick", None),
    ],
)
def test_mutations_require_token_and_origin(client, method, path, body):
    for headers in (
        {},
        {"Origin": "http://testserver"},
        {"X-Daybook-Token": "test"},
        {"Origin": "https://foreign.example", "X-Daybook-Token": "test"},
    ):
        assert (
            client.request(method, path, json=body, headers=headers).status_code == 403
        )
    assert client.request(method, path, json=body, headers=HEADERS).status_code != 403


def test_skill_edit_review_and_enablement(client):
    entry = client.post(
        "/api/chat", json={"message": "/skill meeting reminder"}, headers=HEADERS
    ).json()["entry"]
    base = f"/api/skills/{entry['id']}"
    assert client.post(base + "/enable", headers=HEADERS).status_code == 409
    assert (
        client.post(
            base + "/approve", json={"hash": "stale"}, headers=HEADERS
        ).status_code
        == 409
    )
    approved = client.post(
        base + "/approve", json={"hash": entry["hash"]}, headers=HEADERS
    )
    assert approved.json()["status"] == "active"
    disabled = client.post(base + "/disable", headers=HEADERS).json()
    assert disabled["approved"] and not disabled["enabled"]
    assert client.post(base + "/enable", headers=HEADERS).json()["status"] == "active"
    edited = client.put(
        base, json={"text": entry["text"] + "Edited goal.\n"}, headers=HEADERS
    ).json()
    assert edited["status"] == "needs_review" and edited["hash"] != entry["hash"]
    invalid = client.put(base, json={"text": "invalid"}, headers=HEADERS).json()
    assert invalid["status"] == "invalid" and invalid["errors"]
    assert (
        client.post(
            base + "/approve", json={"hash": invalid["hash"]}, headers=HEADERS
        ).status_code
        == 409
    )
    assert client.delete(base, headers=HEADERS).json()["ok"]
    assert not client.get("/api/skills").json()["entries"]


def test_note_edits_forget_and_recall(client):
    assert (
        client.patch(
            "/api/segments/s-code", json={"title": "My edit"}, headers=HEADERS
        ).json()["authored"]
        == "user"
    )
    assert (
        client.patch(
            "/api/observations/o-code",
            json={"seen": "Visible test failure"},
            headers=HEADERS,
        ).json()["seen"]
        == "Visible test failure"
    )
    assert client.get("/api/timeline").json()["segments"][0]["title"] == "My edit"
    assert client.delete("/api/observations/o-code", headers=HEADERS).json()["ok"]
    assert client.get("/api/timeline").json()["segments"] == []
    assert client.get("/api/now").json()["observation"] is None
    answer = client.post(
        "/api/chat", json={"message": "/ask last hour"}, headers=HEADERS
    ).json()
    assert answer["cites"] == [] and "don't have notes" in answer["text"]


def test_responses_and_openapi(client):
    for route, model in (
        ("timeline", w.TimelineResponse),
        ("now", w.NowResponse),
        ("skills", w.SkillsResponse),
        ("privacy", w.PrivacyResponse),
        ("status", w.StatusResponse),
    ):
        assert model.model_validate(client.get("/api/" + route).json())
    schema = client.get("/openapi.json").json()
    assert set(schema["x-sse-events"]) == set(w.EVENT_MODELS)
    assert 'name="daybook-token" content="test"' in client.get("/").text
    assert client.post("/api/tick", headers=HEADERS).json()["recorded"]
    body = w.EXAMPLES[w.PrivacyPut] | {"paused": True}
    assert client.put("/api/privacy", json=body, headers=HEADERS).json()["paused"]
    assert client.get("/api/status").json()["capture"] == "paused"
    assert not client.post("/api/tick", headers=HEADERS).json()["recorded"]
    assert client.get("/api/timeline?from=2026-10-10T09:00:00").status_code == 422


@pytest.mark.parametrize(
    "description", ["bad", ["bad"], 42, {"work": 42}, {"unknown": "label"}]
)
def test_malformed_enum_descriptions_return_parse_errors(description):
    import yaml

    data = {
        "id": "sample",
        "name": "Sample",
        "signals": {
            "activity": {
                "ask": "What is on screen?",
                "type": {"enum": ["work", "rest"]},
                "describe": description,
            }
        },
        "rules": [
            {
                "when": "activity updates",
                "do": [{"overlay.show": {"title": "Break", "body": "Rest"}}],
            }
        ],
        "allow": ["overlay.show"],
    }
    text = "---\n" + yaml.safe_dump(data) + "---\nA goal.\n"
    with TestClient(
        create_app(mock=True, token="test", origin="http://testserver"),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/api/skills/parse", json={"id": "sample", "text": text}, headers=HEADERS
        )
    assert response.status_code == 200
    assert not response.json()["valid"]
    assert any(
        e["location"] == "signals.activity.describe" for e in response.json()["errors"]
    )
