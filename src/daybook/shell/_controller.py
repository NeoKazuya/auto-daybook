"""What the overlay window should show, from the backend's status and events.

Pure Python: no Qt and no HTTP, so it can be tested directly. The backend owns the
overlay (daybook.act.Overlay); this only mirrors it (see README.md).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

# How long past its expiry an overlay may stay up while waiting for OverlayHidden.
GRACE = timedelta(seconds=2)


@dataclass(frozen=True)
class Overlay:
    id: str
    title: str
    body: str
    dismiss: str
    style: Literal["calm", "alert"]
    expires_at: datetime


@dataclass(frozen=True)
class View:
    overlay: Overlay | None
    remaining: float  # whole seconds left, never negative

    @property
    def visible(self) -> bool:
        return self.overlay is not None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _request(id: object, request: object, expires_at: datetime) -> Overlay | None:
    if not isinstance(id, str) or not isinstance(request, Mapping):
        return None
    style = request.get("style", "calm")
    return Overlay(
        id=id,
        title=str(request.get("title", "")),
        body=str(request.get("body", "")),
        dismiss=str(request.get("dismiss") or "OK"),
        style="alert" if style == "alert" else "calm",
        expires_at=expires_at,
    )


class OverlayController:
    def __init__(self, now: Callable[[], datetime] = _now) -> None:
        self._now = now
        self._overlay: Overlay | None = None

    def resync(self, status: Mapping[str, Any]) -> View:
        """Apply GET /api/status, which is the truth after any (re)connect."""
        state = status.get("overlay")
        if not isinstance(state, Mapping):
            self._overlay = None
            return self.view()
        try:
            expires = datetime.fromisoformat(str(state.get("expires_at")))
        except ValueError:
            return self.view()
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        overlay = _request(state.get("id"), state.get("request"), expires)
        if overlay is not None:
            self._overlay = overlay
        return self.view()

    def handle(self, event: str, data: Mapping[str, Any]) -> View:
        """Apply one SSE event; events other than the overlay's are ignored."""
        if event == "OverlayShown":
            request = data.get("request")
            minutes = request.get("minutes", 1) if isinstance(request, Mapping) else 1
            try:
                seconds = max(0.0, float(minutes) * 60)
            except (TypeError, ValueError):
                seconds = 60.0
            overlay = _request(
                data.get("id"), request, self._now() + timedelta(seconds=seconds)
            )
            if overlay is not None:
                self._overlay = overlay
        elif event == "OverlayHidden":
            if self._overlay is not None and data.get("id") == self._overlay.id:
                self._overlay = None
        return self.view()

    def dismiss(self) -> str | None:
        """Hide at once and return the id to report to the backend, if any."""
        overlay, self._overlay = self._overlay, None
        return overlay.id if overlay else None

    def view(self) -> View:
        overlay = self._overlay
        if overlay is None:
            return View(None, 0)
        left = (overlay.expires_at - self._now()).total_seconds()
        if left < -GRACE.total_seconds():
            # The backend's timer should have hidden it; don't leave it stuck.
            self._overlay = None
            return View(None, 0)
        return View(overlay, max(0, int(left + 0.999)))


def countdown(seconds: float) -> str:
    """4:52, 0:07, 61:00."""
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"
