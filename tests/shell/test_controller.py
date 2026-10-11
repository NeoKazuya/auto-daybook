from datetime import datetime, timedelta, timezone

from daybook.shell._controller import OverlayController, countdown

T0 = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


REQUEST = {
    "title": "Time for a break",
    "body": "Stand up.",
    "dismiss": "I'm back",
    "minutes": 5,
    "style": "calm",
}


def shown(id="o1", **request):
    return {"id": id, "request": {**REQUEST, **request}}


def test_shown_event_shows_with_local_expiry():
    clock = Clock()
    c = OverlayController(clock)
    view = c.handle("OverlayShown", shown())
    assert view.visible and view.overlay
    assert view.overlay.title == "Time for a break"
    assert view.overlay.dismiss == "I'm back"
    assert view.remaining == 300
    clock.advance(8)
    assert c.view().remaining == 292
    assert countdown(c.view().remaining) == "4:52"


def test_status_resync_uses_backend_expiry_and_clears():
    clock = Clock()
    c = OverlayController(clock)
    status = {
        "overlay": {
            "id": "o2",
            "request": {**REQUEST, "style": "alert"},
            "owner": "break",
            "expires_at": (T0 + timedelta(seconds=47)).isoformat(),
        }
    }
    view = c.resync(status)
    assert view.overlay and view.overlay.style == "alert"
    assert view.remaining == 47
    assert not c.resync({"overlay": None}).visible


def test_hidden_event_only_hides_matching_overlay():
    c = OverlayController(Clock())
    c.handle("OverlayShown", shown("o1"))
    assert c.handle("OverlayHidden", {"id": "other"}).visible
    assert not c.handle("OverlayHidden", {"id": "o1"}).visible


def test_newer_overlay_replaces_older():
    c = OverlayController(Clock())
    c.handle("OverlayShown", shown("o1"))
    view = c.handle("OverlayShown", shown("o2", title="Meeting's over"))
    assert view.overlay and view.overlay.id == "o2"
    assert view.overlay.title == "Meeting's over"


def test_dismiss_hides_and_reports_id_once():
    c = OverlayController(Clock())
    c.handle("OverlayShown", shown("o1"))
    assert c.dismiss() == "o1"
    assert not c.view().visible
    assert c.dismiss() is None


def test_expired_overlay_waits_for_backend_then_hides_itself():
    clock = Clock()
    c = OverlayController(clock)
    c.handle("OverlayShown", shown(minutes=1))
    clock.advance(61)
    assert c.view().visible and c.view().remaining == 0
    clock.advance(2)
    assert not c.view().visible


def test_bad_payloads_are_ignored():
    c = OverlayController(Clock())
    assert not c.handle("OverlayShown", {"id": 7, "request": REQUEST}).visible
    assert not c.resync({"overlay": {"id": "o1", "expires_at": "soon"}}).visible
    assert not c.handle("MusicChanged", {"mood": "calm"}).visible
    view = c.handle("OverlayShown", shown(style="neon", dismiss=""))
    assert (
        view.overlay and view.overlay.style == "calm" and view.overlay.dismiss == "OK"
    )


def test_countdown_format():
    assert countdown(0) == "0:00"
    assert countdown(7) == "0:07"
    assert countdown(3660) == "61:00"


def test_layout_comes_from_the_request_and_defaults_to_fullscreen():
    c = OverlayController(Clock())
    view = c.handle("OverlayShown", shown("o1", layout="bubble"))
    assert view.overlay and view.overlay.layout == "bubble"
    view = c.handle("OverlayShown", shown("o2"))
    assert view.overlay and view.overlay.layout == "fullscreen"
    view = c.handle("OverlayShown", shown("o3", layout="sideways"))
    assert view.overlay and view.overlay.layout == "fullscreen"
