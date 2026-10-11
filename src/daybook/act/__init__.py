"""The single action catalog and capability boundary."""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal, Mapping, Protocol, cast

from daybook.events import Bus, OverlayHidden, OverlayShown
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
            from ._validation import parameters as validate

            checked = validate(self.catalog.specs[action], parameters)
            return await handler(self.skill, checked)
        except ValueError:
            return Outcome(action, False, "invalid_parameters")
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
        from ._desktop import open_url

        return await open_url(url)

    async def launch(self, app: str) -> Outcome:
        from ._desktop import launch

        return await launch(app)

    async def focus(self, app: str) -> Outcome:
        from ._desktop import focus

        return await focus(app)


class FakeDesktop:
    """Development desktop; records requests without launching anything."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def open_url(self, url: str) -> Outcome:
        self.calls.append(("open_url", url))
        return Outcome("desktop.open_url", True, "fake")

    async def launch(self, app: str) -> Outcome:
        self.calls.append(("launch", app))
        return Outcome("desktop.launch", True, "fake")

    async def focus(self, app: str) -> Outcome:
        self.calls.append(("focus", app))
        return Outcome("desktop.focus", True, "fake")


class Overlay:
    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self._state: OverlayState | None = None
        self._connected = False
        self._timer: asyncio.Task[None] | None = None

    @property
    def state(self) -> OverlayState | None:
        return self._state

    def shell_connected(self, connected: bool) -> None:
        self._connected = connected

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    async def show(self, request: OverlayRequest, owner: str) -> Outcome:
        if not self._connected:
            return Outcome("overlay.show", False, "shell_disconnected")
        if not math.isfinite(request.minutes) or request.minutes <= 0:
            return Outcome("overlay.show", False, "invalid_duration")
        self._cancel_timer()
        id = uuid.uuid4().hex
        self._state = OverlayState(
            id,
            request,
            owner,
            (
                datetime.now(timezone.utc) + timedelta(minutes=request.minutes)
            ).isoformat(),
        )
        self.bus.publish(OverlayShown(id, request))

        async def expire() -> None:
            await asyncio.sleep(request.minutes * 60)
            await self.hide(id)

        self._timer = asyncio.create_task(expire())
        return Outcome("overlay.show", True)

    async def hide(self, id: str | None = None) -> Outcome:
        state = self._state
        if state is None or (id is not None and id != state.id):
            return Outcome("overlay.hide", True, "unchanged")
        self._state = None
        self._cancel_timer()
        self.bus.publish(OverlayHidden(state.id))
        return Outcome("overlay.hide", True)

    async def close(self) -> None:
        await self.hide()


@dataclass(frozen=True)
class MusicTrack:
    file: str
    title: str
    artist: str
    license: str
    source: str


@dataclass(frozen=True)
class MusicLibrary:
    root: Path
    moods: Mapping[str, str]
    tracks: tuple[MusicTrack, ...]

    @classmethod
    def load(cls, root: Path) -> MusicLibrary:
        from ._music import load

        return load(root)

    def check(self) -> tuple[str, ...]:
        from ._music import check

        return check(self)


class MusicPlayer:
    def __init__(
        self,
        library: MusicLibrary,
        home: Path,
        bus: Bus,
        overlay: Overlay,
        *,
        real_effects: bool = False,
    ) -> None:
        from ._music import Player

        self._player = Player(library, home, bus, overlay, real_effects)

    @property
    def transcript(self) -> list[tuple[object, ...]]:
        return list(self._player.transcript)

    async def play_mood(
        self, mood: str, *, skill: str, min_dwell: float = 60
    ) -> Outcome:
        return await self._player.play(mood, skill, min_dwell)

    async def stop(self, *, skill: str | None = None) -> Outcome:
        return await self._player.stop(skill)

    def status(self) -> MusicStatus:
        return self._player.current

    async def close(self) -> None:
        await self._player.close()


def bind_handlers(
    music: MusicPlayer,
    overlay: Overlay,
    desktop: DesktopControl,
    *,
    real_effects: bool = False,
) -> Mapping[str, Handler]:
    from ._validation import duration

    async def play(skill: str, args: Mapping[str, object]) -> Outcome:
        return await music.play_mood(
            str(args["mood"]),
            skill=skill,
            min_dwell=duration(args.get("min_dwell", "60s")),
        )

    async def stop(skill: str, args: Mapping[str, object]) -> Outcome:
        return await music.stop(skill=skill)

    async def show(skill: str, args: Mapping[str, object]) -> Outcome:
        return await overlay.show(
            OverlayRequest(
                str(args["title"]),
                str(args["body"]),
                str(args.get("dismiss", "OK")),
                float(cast(float, args.get("minutes", 1))),
                cast(Literal["calm", "alert"], args.get("style", "calm")),
            ),
            skill,
        )

    async def hide(skill: str, args: Mapping[str, object]) -> Outcome:
        return await overlay.hide()

    async def notify(skill: str, args: Mapping[str, object]) -> Outcome:
        if not real_effects:
            return Outcome("notify.send", True, "fake")
        from ._desktop import command

        return await command(
            "notify.send", "notify-send", "--", str(args["title"]), str(args["body"])
        )

    async def open_url(skill: str, args: Mapping[str, object]) -> Outcome:
        return await desktop.open_url(str(args["url"]))

    async def launch(skill: str, args: Mapping[str, object]) -> Outcome:
        return await desktop.launch(str(args["app"]))

    async def focus(skill: str, args: Mapping[str, object]) -> Outcome:
        return await desktop.focus(str(args["app"]))

    return {
        "music.play_mood": play,
        "music.stop": stop,
        "overlay.show": show,
        "overlay.hide": hide,
        "notify.send": notify,
        "desktop.open_url": open_url,
        "desktop.launch": launch,
        "desktop.focus": focus,
    }
