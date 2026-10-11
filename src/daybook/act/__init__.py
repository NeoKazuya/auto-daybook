"""The single action catalog and capability boundary."""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from dataclasses import asdict, dataclass
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
    def __init__(self, bus: Bus, *, home: Path | None = None) -> None:
        self.bus = bus
        self._path = home / "overlay.json" if home is not None else None
        self._state: OverlayState | None = None
        self._connected = False
        self._timer: asyncio.Task[None] | None = None
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text())
                request = OverlayRequest(**saved["request"])
                expiry = datetime.fromisoformat(saved["expires_at"])
                if (
                    expiry.tzinfo is None
                    or not math.isfinite(request.minutes)
                    or request.minutes <= 0
                    or request.style not in ("calm", "alert")
                ):
                    raise ValueError("invalid overlay snapshot")
                if expiry > datetime.now(timezone.utc):
                    self._state = OverlayState(
                        saved["id"], request, saved["owner"], saved["expires_at"]
                    )
                    self._arm_timer()
                else:
                    self._persist(None)
            except (OSError, ValueError, TypeError, KeyError):
                self._state = None

    def _persist(self, state: OverlayState | None) -> None:
        if self._path is None:
            return
        if state is None:
            self._path.unlink(missing_ok=True)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(asdict(state)))
            temporary.replace(self._path)

    def _arm_timer(self) -> None:
        state = self._state
        if state is None or self._timer is not None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # Construction may precede the daemon's event loop.

        async def expire() -> None:
            delay = (
                datetime.fromisoformat(state.expires_at) - datetime.now(timezone.utc)
            ).total_seconds()
            await asyncio.sleep(max(0, delay))
            await self.hide(state.id)

        self._timer = asyncio.create_task(expire())

    @property
    def state(self) -> OverlayState | None:
        self._arm_timer()
        return self._state

    def shell_connected(self, connected: bool) -> None:
        self._connected = connected
        self._arm_timer()

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    async def show(self, request: OverlayRequest, owner: str) -> Outcome:
        if not self._connected:
            return Outcome("overlay.show", False, "shell_disconnected")
        if not math.isfinite(request.minutes) or request.minutes <= 0:
            return Outcome("overlay.show", False, "invalid_duration")
        id = uuid.uuid4().hex
        state = OverlayState(
            id,
            request,
            owner,
            (
                datetime.now(timezone.utc) + timedelta(minutes=request.minutes)
            ).isoformat(),
        )
        try:
            self._persist(state)
        except OSError:
            return Outcome("overlay.show", False, "state_unavailable")
        self._cancel_timer()
        self._state = state
        self._arm_timer()
        self.bus.publish(OverlayShown(id, request))
        return Outcome("overlay.show", True)

    async def hide(self, id: str | None = None) -> Outcome:
        state = self._state
        if state is None or (id is not None and id != state.id):
            return Outcome("overlay.hide", True, "unchanged")
        try:
            self._persist(None)
        except OSError:
            return Outcome("overlay.hide", False, "state_unavailable")
        self._state = None
        self._cancel_timer()
        self.bus.publish(OverlayHidden(state.id))
        return Outcome("overlay.hide", True)

    async def close(self) -> None:
        if self._path is None:
            await self.hide()
        else:
            # Shutdown suspends the timer; a restart retains the original deadline.
            self._cancel_timer()
            self._state = None


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
