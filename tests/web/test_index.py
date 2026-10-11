from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from daybook.events import Bus
from daybook.journal import (
    Journal,
    ObservationDraft,
    Start,
    Tick,
    TimeRange,
    WindowInfo,
)
from daybook.web import Services, create_app
from daybook.web import wire as w

HEADERS = {"Origin": "http://testserver", "X-Daybook-Token": "test-token"}


@pytest.fixture
def index(tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    at = datetime(2026, 10, 11, 12, tzinfo=UTC)
    observation = journal.add_observation(
        ObservationDraft(at, WindowInfo("Code", "tests.py"), "Repair imports"),
        journal.epoch,
    )
    assert observation is not None
    journal.extend_observation(observation.id, at + timedelta(minutes=2))
    assert journal.record_tick(
        Tick(
            at + timedelta(minutes=2),
            journal.epoch,
            at + timedelta(minutes=2),
            (observation.id,),
            Start("Repair imports", "coding", "Fix the import path"),
            (),
        )
    )
    segment = journal.portal.now().segment
    assert segment is not None
    services = cast(Services, SimpleNamespace(journal=journal, bus=bus))
    app = create_app(services=services, token="test-token", origin="http://testserver")
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                journal=journal,
                bus=bus,
                client=client,
                at=at,
                observation=observation,
                segment=segment,
                window={
                    "from": at.isoformat(),
                    "to": (at + timedelta(minutes=3)).isoformat(),
                },
            )
    finally:
        journal.close()


def test_search_exposes_ranked_journal_hits_for_index(index):
    response = index.client.get("/api/search", params={"q": "imports", **index.window})
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert {hit["segment_id"] for hit in hits} == {index.segment.id}
    assert {hit["title"] for hit in hits} == {"Repair imports"}
    assert {hit["kind"] for hit in hits} == {"coding"}
    assert index.observation.id in {hit["observation_id"] for hit in hits}
    assert any(hit["observation_id"] is None for hit in hits)


def test_totals_exposes_journal_activity_seconds_for_index(index):
    response = index.client.get("/api/totals", params={"by": "kind", **index.window})
    assert response.status_code == 200
    assert response.json() == {"totals": [{"label": "coding", "active": 120.0}]}


def add_note(
    journal,
    at,
    *,
    app="Firefox",
    title="Reference page",
    seen="Import reference",
    seconds=60,
):
    note = journal.add_observation(
        ObservationDraft(at, WindowInfo(app, title), seen, "import reference"),
        journal.epoch,
    )
    assert note is not None
    journal.extend_observation(note.id, at + timedelta(seconds=seconds))
    return note


def test_unassigned_hit_preserves_nullable_classification_and_link(index):
    note = add_note(index.journal, index.at + timedelta(minutes=3))
    window = {**index.window, "to": (index.at + timedelta(minutes=5)).isoformat()}
    response = index.client.get("/api/search", params={"q": "reference", **window})
    assert response.status_code == 200
    assert response.json()["hits"] == [
        {
            "segment_id": None,
            "observation_id": note.id,
            "at": note.started_at.isoformat().replace("+00:00", "Z"),
            "kind": None,
            "title": "Reference page",
            "snippet": "Reference page\nImport reference\nimport reference",
        }
    ]


@pytest.mark.parametrize("query", ["", "  ", '"; --', "unknown"])
def test_empty_and_unmatched_searches_are_read_only(index, query):
    before = index.journal.portal.observations(
        TimeRange(index.at, index.at + timedelta(minutes=10))
    )
    with index.bus.subscribe() as events:
        response = index.client.get(
            "/api/search",
            params={"q": query, **index.window},
            headers={"X-Daybook-Token": "wrong", "Origin": "https://other.test"},
        )
        assert response.status_code == 200
        assert response.json() == {"hits": []}
        assert events.empty()
    assert (
        index.journal.portal.observations(
            TimeRange(index.at, index.at + timedelta(minutes=10))
        )
        == before
    )


def test_search_preserves_portal_ranking_and_caps_results(index):
    for offset in range(60):
        add_note(
            index.journal,
            index.at + timedelta(minutes=3, seconds=offset),
            seen="imports imports" if offset % 3 == 0 else "imports",
            seconds=1,
        )
    window = {**index.window, "to": (index.at + timedelta(minutes=5)).isoformat()}
    expected = index.journal.portal.search(
        "imports", TimeRange(index.at, index.at + timedelta(minutes=5))
    )
    for params, count in [({}, 50), ({"limit": 3}, 3), ({"limit": 200}, len(expected))]:
        response = index.client.get(
            "/api/search", params={"q": "imports", **window, **params}
        )
        hits = response.json()["hits"]
        assert len(hits) == count
        assert [hit["observation_id"] or hit["segment_id"] for hit in hits] == [
            hit.id for hit in expected[:count]
        ]


@pytest.mark.parametrize(
    ("by", "assigned_label", "unassigned_label"),
    [
        ("kind", "coding", "other"),
        ("app", "Code", "Firefox"),
        ("title", "Repair imports", "Unassigned"),
    ],
)
def test_totals_clip_activity_and_include_unassigned_notes(
    index, by, assigned_label, unassigned_label
):
    add_note(index.journal, index.at + timedelta(minutes=3), seconds=90)
    window = {
        "from": (index.at + timedelta(seconds=30)).isoformat(),
        "to": (index.at + timedelta(minutes=4)).isoformat(),
    }
    response = index.client.get("/api/totals", params={"by": by, **window})
    assert response.status_code == 200
    assert response.json() == {
        "totals": [
            {"label": assigned_label, "active": 90.0},
            {"label": unassigned_label, "active": 60.0},
        ]
    }


def test_index_reflects_note_edits_and_forget_without_stale_results(index):
    assert (
        index.client.patch(
            f"/api/segments/{index.segment.id}",
            headers=HEADERS,
            json={"title": "Documentation review", "kind": "reading"},
        ).status_code
        == 200
    )
    response = index.client.get("/api/search", params={"q": "imports", **index.window})
    assert {hit["title"] for hit in response.json()["hits"]} == {"Documentation review"}
    assert {hit["kind"] for hit in response.json()["hits"]} == {"reading"}
    assert index.client.get("/api/totals", params=index.window).json() == {
        "totals": [{"label": "reading", "active": 120.0}]
    }
    assert (
        index.client.delete(
            f"/api/observations/{index.observation.id}", headers=HEADERS
        ).status_code
        == 200
    )
    assert index.client.get(
        "/api/search", params={"q": "imports", **index.window}
    ).json() == {"hits": []}
    assert index.client.get("/api/totals", params=index.window).json() == {"totals": []}


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-11T12:00:00"},
        {"to": "2026-10-11T12:00:00"},
        {"from": "2026-10-12T12:00:00Z", "to": "2026-10-11T12:00:00Z"},
        {"from": "2026-10-11T12:00:00Z", "to": "2026-10-11T12:00:00Z"},
        {"from": "not-a-date"},
    ],
)
@pytest.mark.parametrize("route", ["/api/search", "/api/totals", "/api/timeline"])
def test_index_and_timeline_share_range_validation(index, route, params):
    response = index.client.get(route, params=params)
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"limit": "1.5"}, {"q": "x" * 501}]
)
def test_search_rejects_invalid_query_limits(index, params):
    response = index.client.get("/api/search", params={**index.window, **params})
    assert response.status_code == 422
    assert response.json()["detail"].startswith("invalid_request:")


def test_totals_reject_invalid_group(index):
    response = index.client.get("/api/totals", params={**index.window, "by": "window"})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_request: query.by"


def test_local_today_defaults_and_explicit_windows_share_routes(tmp_path, monkeypatch):
    from zoneinfo import ZoneInfo

    from daybook.web import _window

    now = datetime(2026, 10, 12, 2, tzinfo=ZoneInfo("Pacific/Kiritimati"))
    monkeypatch.setattr(_window, "local_now", lambda: now)
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    previous = add_note(journal, now - timedelta(hours=3), seen="midnight reference")
    current = add_note(journal, now - timedelta(hours=1), seen="midnight reference")
    services = cast(Services, SimpleNamespace(journal=journal, bus=bus))
    application = create_app(
        services=services, token="test-token", origin="http://testserver"
    )
    try:
        with TestClient(application) as client:
            # Local midnight is 10:00Z on the previous UTC calendar day.
            assert (
                client.get("/api/search", params={"q": "midnight"}).json()["hits"][0][
                    "observation_id"
                ]
                == current.id
            )
            assert client.get("/api/totals").json() == {
                "totals": [{"label": "other", "active": 60.0}]
            }
            assert (
                client.get(
                    "/api/search",
                    params={
                        "q": "midnight",
                        "from": (now - timedelta(hours=4)).isoformat(),
                        "to": (now - timedelta(hours=2)).isoformat(),
                    },
                ).json()["hits"][0]["observation_id"]
                == previous.id
            )
            assert client.get(
                "/api/totals", params={"from": (now - timedelta(hours=4)).isoformat()}
            ).json() == {"totals": [{"label": "other", "active": 120.0}]}
            assert client.get("/api/totals", params={"to": now.isoformat()}).json() == {
                "totals": [{"label": "other", "active": 60.0}]
            }
            assert client.get(
                "/api/totals", params={"from": (now + timedelta(hours=1)).isoformat()}
            ).json() == {"detail": "invalid_time_range"}
    finally:
        journal.close()


def test_mock_index_contract_follows_edits_forget_and_time_windows():
    application = create_app(mock=True, token="test-token", origin="http://testserver")
    with TestClient(application) as client:
        timeline = client.get("/api/timeline").json()
        segment = timeline["segments"][0]
        observation = segment["observations"][0]
        window = {"from": observation["started_at"], "to": observation["last_seen_at"]}
        search = client.get("/api/search", params={"q": "journal", **window})
        assert search.status_code == 200
        assert w.SearchResponse.model_validate(search.json()).hits
        assert client.get("/api/search", params={"q": "  ", **window}).json() == {
            "hits": []
        }
        assert client.get("/api/totals", params={"by": "kind", **window}).json() == {
            "totals": [{"label": "coding", "active": 240.0}]
        }
        assert client.get("/api/totals", params={"by": "app", **window}).json() == {
            "totals": [{"label": "Code", "active": 240.0}]
        }
        start = datetime.fromisoformat(observation["started_at"].replace("Z", "+00:00"))
        clipped = {**window, "from": (start + timedelta(seconds=60)).isoformat()}
        assert client.get("/api/totals", params=clipped).json() == {
            "totals": [{"label": "coding", "active": 180.0}]
        }
        assert (
            client.patch(
                f"/api/segments/{segment['id']}",
                headers=HEADERS,
                json={"title": "New searchable title"},
            ).status_code
            == 200
        )
        assert (
            client.get("/api/search", params={"q": "searchable", **window}).json()[
                "hits"
            ][0]["title"]
            == "New searchable title"
        )
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/search" in paths and "/api/totals" in paths
        assert (
            client.delete(f"/api/segments/{segment['id']}", headers=HEADERS).status_code
            == 200
        )
        assert client.get(
            "/api/search", params={"q": "searchable", **window}
        ).json() == {"hits": []}
        assert client.get("/api/totals", params=window).json() == {"totals": []}
