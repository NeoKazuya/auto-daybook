"""daybook.act — everything the system can do to the world, as named actions in one catalog.

The catalog is the single list of actions. The skill parser validates against it, the generator's prompt is
rendered from it, the review UI shows permissions from it, and `Actions` enforces it. Adding an action is
one entry in `build_catalog`.

    music.play_mood   {mood: music.mood}                 idempotent: same mood already playing -> no-op
    music.stop        {}
    overlay.show      {title, body, dismiss="OK", minutes=1, style="calm"|"alert"}
    overlay.hide      {}
    notify.send       {title, body}                      notify-send, for low-key nudges
    desktop.open_url  {url}                              via the DesktopControl backend
    desktop.launch    {app: desktop-file id}             gtk-launch
    desktop.focus     {app: WM_CLASS glob}               wmctrl/xdotool, or the cua backend

Computer-control plug-in point: `DesktopControl`. `XdoDesktop` (xdotool + wmctrl + gtk-launch) is the default;
`CuaDesktop` wraps trycua/cua's driver if the one-hour trial on real Xorg passes. Config: desktop.backend.
Skills only ever reach it through the three named desktop.* actions; no coordinates, no free-form clicking.

Layout when built:
    daybook/act/__init__.py   exports: Catalog, Actions, ActionName, Outcome, OverlayRequest, build_catalog
    daybook/act/_music.py     MusicLibrary (library.toml) + MusicPlayer (mpv --idle --input-ipc-server)
    daybook/act/_overlay.py   Overlay: current request state; publishes OverlayShown/OverlayHidden on the bus
    daybook/act/_desktop.py   DesktopControl, XdoDesktop, CuaDesktop
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NewType, Protocol

ActionName = NewType("ActionName", str)  # "overlay.show"


@dataclass(frozen=True)
class Outcome:
    action: ActionName
    ok: bool
    detail: str  # "playing focus: Cipher — Kevin MacLeod" | "overlay shell not connected"


@dataclass(frozen=True)
class Param:
    name: str
    type: Literal["str", "int", "bool"] | str  # or a catalog type name such as "music.mood"
    required: bool
    describe: str


@dataclass(frozen=True)
class ActionSpec:
    name: ActionName
    describe: str
    params: tuple[Param, ...]
    run: Callable[[Mapping[str, Any]], Awaitable[Outcome]]


@dataclass(frozen=True)
class CatalogType:
    """A named enum whose values live somewhere real (music.mood -> library.toml). Read fresh on each parse."""

    name: str
    values: Callable[[], Mapping[str, str]]  # value -> description


class NotAllowed(Exception):
    pass


class Actions:
    """A capability: only the actions in one skill's allow list. Built by Catalog.allowed()."""

    async def do(self, name: str, **args: Any) -> Outcome:
        """Validates args against the spec, runs it, never raises for action failures (returns ok=False).
        Raises NotAllowed for names outside the allow list."""
        raise NotImplementedError


class Catalog:
    def spec(self, name: str) -> ActionSpec | None:
        raise NotImplementedError

    def type(self, name: str) -> CatalogType | None:
        raise NotImplementedError

    def allowed(self, allow: frozenset[str]) -> Actions:
        raise NotImplementedError

    def describe_for_prompt(self) -> str:
        """Every action, its params, and every catalog type's current values. Used by the skill generator."""
        raise NotImplementedError


# --- Backends


@dataclass(frozen=True)
class OverlayRequest:
    title: str
    body: str
    dismiss: str = "OK"
    minutes: float = 1.0  # auto-hide after; the dismiss button hides earlier
    style: Literal["calm", "alert"] = "calm"

    def as_args(self) -> dict[str, Any]:
        raise NotImplementedError


class Overlay:
    """Owns 'which overlay is up'. show() publishes OverlayShown(id, request) on the bus; the shell process
    (subscribed to /api/events) renders /overlay/<id> full-screen; the dismiss button POSTs /api/overlay/<id>/dismiss.
    A new show() replaces the current one. If no shell is subscribed, returns Outcome(ok=False)."""

    async def show(self, request: OverlayRequest) -> Outcome:
        raise NotImplementedError

    async def hide(self) -> Outcome:
        raise NotImplementedError

    def current(self) -> tuple[str, OverlayRequest] | None:
        raise NotImplementedError


@dataclass(frozen=True)
class Track:
    path: Path
    title: str
    artist: str
    license: str
    source: str


class MusicLibrary:
    """~/Music/daybook/library.toml + one folder per mood. The only list of moods (see sketch/skills/library.toml)."""

    def moods(self) -> Mapping[str, str]:
        raise NotImplementedError

    def tracks(self, mood: str) -> list[Track]:
        raise NotImplementedError


class MusicPlayer:
    """One mpv child (`mpv --idle --no-video --input-ipc-server=$DAYBOOK_HOME/mpv.sock`), JSON IPC.

    play_mood(m): if m is the current mood -> no-op. Else fade volume to 0 over 1.5 s, load a shuffled playlist
    of the mood folder with loop-playlist, fade back in, publish MusicChanged. Restarts mpv if the socket is dead.
    """

    async def play_mood(self, mood: str) -> Outcome:
        raise NotImplementedError

    async def stop(self) -> Outcome:
        raise NotImplementedError

    def now_playing(self) -> tuple[str, Track] | None:
        raise NotImplementedError


class DesktopControl(Protocol):
    async def open_url(self, url: str) -> Outcome: ...
    async def launch(self, app: str) -> Outcome: ...
    async def focus(self, app: str) -> Outcome: ...


class XdoDesktop:
    pass


class CuaDesktop:
    """Adapter over trycua/cua's linux-arm64 driver. Only built if the timeboxed trial passes."""


def build_catalog(music: MusicPlayer, library: MusicLibrary, overlay: Overlay, desktop: DesktopControl) -> Catalog:
    """THE action list. One ActionSpec per action above, plus CatalogType('music.mood', library.moods)."""
    raise NotImplementedError
