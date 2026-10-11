"""The shell's only link to daybookd: status, the SSE stream, dismiss, and pause.

Runs in a background thread; callbacks are handed to Qt by the caller.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx


def sse_events(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield (event name, data) for each dispatched Server-Sent Event."""
    name, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield name, "\n".join(data)
            name, data = "message", []
        elif line.startswith(":"):
            continue  # comment: "connected", heartbeats
        else:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)


def read_token(env: Mapping[str, str] = os.environ) -> str:
    """DAYBOOK_TOKEN when spawned by daybookd, else $DAYBOOK_HOME/token."""
    if env.get("DAYBOOK_TOKEN"):
        return env["DAYBOOK_TOKEN"]
    home = Path(env.get("DAYBOOK_HOME", "~/.daybook")).expanduser()
    return (home / "token").read_text().strip()


class Backend:
    def __init__(self, port: int, token: str, http: httpx.Client | None = None) -> None:
        self.origin = f"http://127.0.0.1:{port}"
        self.token = token
        self.http = http or httpx.Client(base_url=self.origin, timeout=5)

    def _headers(self) -> dict[str, str]:
        return {"X-Daybook-Token": self.token, "Origin": self.origin}

    def status(self) -> dict[str, Any]:
        response = self.http.get("/api/status")
        response.raise_for_status()
        return response.json()

    def privacy(self) -> dict[str, Any]:
        response = self.http.get("/api/privacy")
        response.raise_for_status()
        return response.json()

    def set_paused(self, paused: bool) -> dict[str, Any]:
        """Change only `paused` (PUT /api/privacy); returns the saved settings."""
        current = self.privacy()
        # PrivacyPut forbids the response-only fields, so send exactly its three.
        body = {
            "paused": paused,
            "exclude_apps": current["exclude_apps"],
            "exclude_titles": current["exclude_titles"],
        }
        response = self.http.put("/api/privacy", json=body, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def dismiss(self, id: str) -> bool:
        """POST the dismiss, retrying once; the window is already hidden."""
        headers = self._headers()
        for _ in range(2):
            try:
                response = self.http.post(f"/api/overlay/{id}/dismiss", headers=headers)
                if response.status_code < 500:
                    return response.is_success
            except httpx.HTTPError:
                pass
        return False

    def run(
        self,
        on_status: Callable[[dict[str, Any]], None],
        on_event: Callable[[str, dict[str, Any]], None],
        stop: threading.Event,
    ) -> None:
        """Stay connected until `stop` is set, resyncing from status on each connect.

        Each time the stream is lost, `on_event` gets a ("Disconnected", {}) event.
        """
        delay = 1.0
        while not stop.is_set():
            try:
                with self.http.stream(
                    "GET",
                    "/api/events",
                    params={"client": "shell"},
                    timeout=httpx.Timeout(5, read=30),
                ) as response:
                    response.raise_for_status()
                    # Connected first, then read status: any event after this point
                    # is queued in the stream, so nothing falls between the two.
                    on_status(self.status())
                    delay = 1.0
                    for name, data in sse_events(response.iter_lines()):
                        if stop.is_set():
                            return
                        try:
                            payload = json.loads(data)
                        except ValueError:
                            continue
                        if isinstance(payload, dict):
                            on_event(name, payload)
            except httpx.HTTPError:
                pass
            if stop.is_set():
                return
            on_event("Disconnected", {})
            stop.wait(delay)
            delay = min(delay * 2, 5.0)
