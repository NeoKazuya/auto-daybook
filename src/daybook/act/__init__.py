"""The single action catalog and capability boundary."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal, Mapping, Protocol, cast

from daybook.events import Bus, OverlayHidden, OverlayShown
from daybook.journal import Outcome

# desktop.close_tab only acts within this many seconds of a successful
# overlay.show by the same skill: a destructive step never happens unannounced.
CLOSE_TAB_WARNING_WINDOW = 30

# The `wait` step accepts these durations: at most 10 s, like a hook step, because
# steps hold up the rest of the tick's reactions.
WAITS = tuple(f"{n}s" for n in range(1, 11))
TAB_TARGETS = ("next", "previous", *(str(n) for n in range(1, 9)), "last")


@dataclass(frozen=True)
class OverlayRequest:
    title: str
    body: str
    dismiss: str = "OK"
    minutes: float = 1
    style: Literal["calm", "alert"] = "calm"
    layout: Literal["fullscreen", "bubble"] = "fullscreen"


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
            "Show a fullscreen reminder or speech bubble; use casual body text for bubbles",
            {
                "title": text,
                "body": text,
                "dismiss": Parameter("str", False, "OK"),
                "minutes": Parameter("number", False, 1),
                "style": Parameter("enum", False, "calm", ("calm", "alert")),
                "layout": Parameter(
                    "enum", False, "fullscreen", ("fullscreen", "bubble")
                ),
            },
        ),
        ActionSpec("overlay.hide", "Dismiss the overlay", {}),
        ActionSpec(
            "notify.send", "Show a desktop notification", {"title": text, "body": text}
        ),
        ActionSpec("desktop.open_url", "Open a URL", {"url": text}),
        ActionSpec(
            "desktop.launch",
            "Open an installed app by name or desktop file ID",
            {"app": text},
        ),
        ActionSpec(
            "desktop.focus",
            "Switch to an app by installed name or WM_CLASS glob; optional title "
            "is a case-insensitive window-title glob. Ambiguous windows are refused",
            {"app": text, "title": Parameter("str", False, "*")},
        ),
        ActionSpec(
            "desktop.open_tab",
            "Open an http/https URL in a NEW tab in the active browser, or focus "
            "the browser named by app (installed name or WM_CLASS glob). Supports "
            "Firefox/LibreWolf and Chrome/Chromium/Brave/Edge; requires a running window",
            {"url": text, "app": Parameter("str", False, "active")},
        ),
        ActionSpec(
            "desktop.switch_tab",
            "Switch browser tabs by next, previous, position 1–8, or last. "
            "No hidden-tab title lookup. app targets the active browser by default, "
            "or an installed name/WM_CLASS glob. Supports Firefox/LibreWolf and "
            "Chrome/Chromium/Brave/Edge; requires a running window",
            {
                "tab": Parameter("enum", values=TAB_TARGETS),
                "app": Parameter("str", False, "active"),
            },
        ),
        ActionSpec(
            "desktop.close_tab",
            "Close the active browser tab, only if its window title matches a glob "
            "such as *Instagram* and this skill showed an overlay or bubble in the "
            f"last {CLOSE_TAB_WARNING_WINDOW}s (warn first, then close)",
            {"title": text},
        ),
        ActionSpec(
            "wait",
            "Pause before the next step (1s to 10s); the next step runs only if "
            "the skill may still act (not paused, still enabled)",
            {"for": Parameter("enum", values=WAITS)},
        ),
    )
    return Catalog(specs, moods, handlers)


class DesktopControl(Protocol):
    async def open_url(self, url: str) -> Outcome: ...
    async def launch(self, app: str) -> Outcome: ...
    async def focus(self, app: str, title: str = "*") -> Outcome: ...
    async def open_tab(self, url: str, app: str = "active") -> Outcome: ...
    async def switch_tab(self, tab: str, app: str = "active") -> Outcome: ...
    async def close_tab(self, title: str) -> Outcome: ...


class XdoDesktop:
    async def open_url(self, url: str) -> Outcome:
        from ._desktop import open_url

        return await open_url(url)

    async def launch(self, app: str) -> Outcome:
        from ._desktop import launch

        return await launch(app)

    async def focus(self, app: str, title: str = "*") -> Outcome:
        from ._desktop import focus

        return await focus(app, title)

    async def open_tab(self, url: str, app: str = "active") -> Outcome:
        from ._desktop import open_tab

        return await open_tab(url, app)

    async def switch_tab(self, tab: str, app: str = "active") -> Outcome:
        from ._desktop import switch_tab

        return await switch_tab(tab, app)

    async def close_tab(self, title: str) -> Outcome:
        from ._desktop import close_tab

        return await close_tab(title)


class FakeDesktop:
    """Development desktop; records requests without launching anything."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def open_url(self, url: str) -> Outcome:
        self.calls.append(("open_url", url))
        return Outcome("desktop.open_url", True, "fake")

    async def launch(self, app: str) -> Outcome:
        self.calls.append(("launch", app))
        return Outcome("desktop.launch", True, "fake")

    async def focus(self, app: str, title: str = "*") -> Outcome:
        self.calls.append(("focus", app, title))
        return Outcome("desktop.focus", True, "fake")

    async def open_tab(self, url: str, app: str = "active") -> Outcome:
        self.calls.append(("open_tab", url, app))
        return Outcome("desktop.open_tab", True, "fake")

    async def switch_tab(self, tab: str, app: str = "active") -> Outcome:
        self.calls.append(("switch_tab", tab, app))
        return Outcome("desktop.switch_tab", True, "fake")

    async def close_tab(self, title: str) -> Outcome:
        self.calls.append(("close_tab", title))
        return Outcome("desktop.close_tab", True, "fake")


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
                    or request.layout not in ("fullscreen", "bubble")
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
        if request.layout not in ("fullscreen", "bubble"):
            return Outcome("overlay.show", False, "invalid_layout")
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


class FakeOverlay(Overlay):
    """Records normal state/events without starting or requiring a shell."""

    def __init__(self, bus: Bus, *, home: Path | None = None) -> None:
        super().__init__(bus, home=home)
        self._connected = True

    def shell_connected(self, connected: bool) -> None:
        super().shell_connected(True)

    async def show(self, request: OverlayRequest, owner: str) -> Outcome:
        result = await super().show(request, owner)
        return Outcome(result.action, result.ok, "fake" if result.ok else result.detail)


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

    async def start(self) -> None:
        await self._player.start()

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

    warned: dict[str, float] = {}  # skill -> monotonic time of its last shown overlay

    async def show(skill: str, args: Mapping[str, object]) -> Outcome:
        outcome = await overlay.show(
            OverlayRequest(
                str(args["title"]),
                str(args["body"]),
                str(args.get("dismiss", "OK")),
                float(cast(float, args.get("minutes", 1))),
                cast(Literal["calm", "alert"], args.get("style", "calm")),
                cast(Literal["fullscreen", "bubble"], args.get("layout", "fullscreen")),
            ),
            skill,
        )
        # Only the latest attempt counts: a failed warning withdraws an earlier one.
        if outcome.ok:
            warned[skill] = time.monotonic()
        else:
            warned.pop(skill, None)
        return outcome

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
        return await desktop.focus(str(args["app"]), str(args["title"]))

    async def open_tab(skill: str, args: Mapping[str, object]) -> Outcome:
        return await desktop.open_tab(str(args["url"]), str(args["app"]))

    async def switch_tab(skill: str, args: Mapping[str, object]) -> Outcome:
        return await desktop.switch_tab(str(args["tab"]), str(args["app"]))

    async def close_tab(skill: str, args: Mapping[str, object]) -> Outcome:
        shown = warned.pop(skill, None)  # one warning authorizes one close
        if shown is None or time.monotonic() - shown > CLOSE_TAB_WARNING_WINDOW:
            return Outcome("desktop.close_tab", False, "no_warning_shown")
        return await desktop.close_tab(str(args["title"]))

    async def wait(skill: str, args: Mapping[str, object]) -> Outcome:
        # Only sleeps: SkillBook.react re-checks eligibility before the next step,
        # so pausing Daybook or disabling the skill during the wait stops it.
        await asyncio.sleep(duration(args["for"]))
        return Outcome("wait", True)

    return {
        "music.play_mood": play,
        "music.stop": stop,
        "overlay.show": show,
        "overlay.hide": hide,
        "notify.send": notify,
        "desktop.open_url": open_url,
        "desktop.launch": launch,
        "desktop.focus": focus,
        "desktop.open_tab": open_tab,
        "desktop.switch_tab": switch_tab,
        "desktop.close_tab": close_tab,
        "wait": wait,
    }
