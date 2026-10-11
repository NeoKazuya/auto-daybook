"""The single action catalog and capability boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Mapping, Protocol

from daybook.events import Bus
from daybook.journal import Outcome


@dataclass(frozen=True)
class OverlayRequest:
    title: str
    body: str
    dismiss: str = "OK"
    minutes: float = 1
    style: Literal["calm", "alert"] = "calm"


@dataclass(frozen=True)
class OverlayState:
    id: str
    request: OverlayRequest
    owner: str
    expires_at: str


@dataclass(frozen=True)
class MusicStatus:
    mood: str | None = None
    title: str | None = None
    playing: bool = False


@dataclass(frozen=True)
class Parameter:
    type: str
    required: bool = True
    default: str | float | None = None
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    parameters: Mapping[str, Parameter]


class NotAllowed(Exception):
    pass


Handler = Callable[[str, Mapping[str, object]], Awaitable[Outcome]]


class Catalog:
    def __init__(
        self,
        specs: tuple[ActionSpec, ...],
        moods: Mapping[str, str],
        handlers: Mapping[str, Handler] | None = None,
    ) -> None:
        self.specs = {s.name: s for s in specs}
        self.moods = dict(moods)
        self.handlers = dict(handlers or {})

    def allowed(self, allow: tuple[str, ...], skill: str = "manual") -> Actions:
        return Actions(self, allow, skill)

    def describe_for_prompt(self) -> str:
        return (
            "\n".join(
                f"{s.name}: {s.description}; {dict(s.parameters)}"
                for s in self.specs.values()
            )
            + f"\nMoods: {self.moods}"
        )


class Actions:
    def __init__(self, catalog: Catalog, allow: tuple[str, ...], skill: str) -> None:
        self.catalog, self.allow, self.skill = catalog, allow, skill

    async def run(self, action: str, **parameters: object) -> Outcome:
        if action not in self.allow:
            raise NotAllowed(action)
        if action not in self.catalog.specs:
            return Outcome(action, False, "unknown_action")
        handler = self.catalog.handlers.get(action)
        if handler is None:
            return Outcome(action, False, "backend_unavailable")
        try:
            return await handler(self.skill, parameters)
        except Exception:
            return Outcome(action, False, "action_failed")


def build_catalog(
    moods: Mapping[str, str], handlers: Mapping[str, Handler] | None = None
) -> Catalog:
    text = Parameter("str")
    specs = (
        ActionSpec(
            "music.play_mood",
            "Play local music for this mood",
            {
                "mood": Parameter("music.mood", values=tuple(moods)),
                "min_dwell": Parameter("duration", False, "60s"),
            },
        ),
        ActionSpec("music.stop", "Stop music", {}),
        ActionSpec(
            "overlay.show",
            "Show a fullscreen reminder",
            {
                "title": text,
                "body": text,
                "dismiss": Parameter("str", False, "OK"),
                "minutes": Parameter("number", False, 1),
                "style": Parameter("enum", False, "calm", ("calm", "alert")),
            },
        ),
        ActionSpec("overlay.hide", "Dismiss the overlay", {}),
        ActionSpec(
            "notify.send", "Show a desktop notification", {"title": text, "body": text}
        ),
        ActionSpec("desktop.open_url", "Open a URL", {"url": text}),
        ActionSpec("desktop.launch", "Launch a desktop file ID", {"app": text}),
        ActionSpec("desktop.focus", "Focus an app by WM_CLASS glob", {"app": text}),
    )
    return Catalog(specs, moods, handlers)


class DesktopControl(Protocol):
    async def open_url(self, url: str) -> Outcome: ...
    async def launch(self, app: str) -> Outcome: ...
    async def focus(self, app: str) -> Outcome: ...


class XdoDesktop:
    async def open_url(self, url: str) -> Outcome:
        raise NotImplementedError

    async def launch(self, app: str) -> Outcome:
        raise NotImplementedError

    async def focus(self, app: str) -> Outcome:
        raise NotImplementedError


class Overlay:
    def __init__(self, bus: Bus) -> None:
        raise NotImplementedError

    @property
    def state(self) -> OverlayState | None:
        raise NotImplementedError

    def shell_connected(self, connected: bool) -> None:
        raise NotImplementedError

    async def show(self, request: OverlayRequest, owner: str) -> Outcome:
        raise NotImplementedError

    async def hide(self, id: str | None = None) -> Outcome:
        raise NotImplementedError


class MusicPlayer:
    async def play_mood(
        self, mood: str, *, skill: str, min_dwell: float = 60
    ) -> Outcome:
        raise NotImplementedError

    async def stop(self) -> Outcome:
        raise NotImplementedError

    def status(self) -> MusicStatus:
        raise NotImplementedError
