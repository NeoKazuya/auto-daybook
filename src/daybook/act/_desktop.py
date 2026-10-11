"""Desktop effects use argument arrays and return only error kinds."""

from __future__ import annotations

import asyncio
import configparser
import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from daybook.journal import Outcome

from . import TAB_TARGETS, CanAct


async def command(action: str, *args: str, can_act: CanAct | None = None) -> Outcome:
    # Only suppress a helper before it starts. An already-started helper may
    # finish; revocation never rolls back desktop state or kills an application.
    if can_act is not None and not can_act():
        return Outcome(action, False, "skill_inactive")
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            code = await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()
            return Outcome(action, False, "timeout")
        return Outcome(action, code == 0, "" if code == 0 else "command_failed")
    except OSError:
        return Outcome(action, False, "command_unavailable")


async def open_url(url: str, *, can_act: CanAct | None = None) -> Outcome:
    if urlparse(url).scheme not in (
        "http",
        "https",
        "file",
        "mailto",
    ) or url.startswith("-"):
        return Outcome("desktop.open_url", False, "invalid_url")
    return await command("desktop.open_url", "xdg-open", url, can_act=can_act)


@dataclass(frozen=True)
class Application:
    id: str
    name: str
    wm_class: str


def applications() -> tuple[Application, ...]:
    """Read installed names and classes without executing desktop-file commands."""
    roots = [Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))]
    roots.extend(
        Path(root)
        for root in os.environ.get(
            "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
        ).split(":")
        if root
    )
    # These are desktop-file exports, including on SSH sessions without XDG vars.
    roots.extend(
        [Path("/var/lib/snapd/desktop"), Path("/var/lib/flatpak/exports/share")]
    )
    found, seen = [], set()
    for root in roots:
        directory = root / "applications"
        for path in sorted(directory.rglob("*.desktop")):
            id = "-".join(path.relative_to(directory).parts)
            if id in seen:
                continue
            seen.add(id)  # A user's Hidden entry also masks the system entry.
            entry = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                entry.read_string(path.read_text())
                section = entry["Desktop Entry"]
                if (
                    section.get("Type") != "Application"
                    or section.get("Hidden") == "true"
                    or section.get("NoDisplay") == "true"
                ):
                    continue
                found.append(
                    Application(
                        id, section.get("Name", ""), section.get("StartupWMClass", "")
                    )
                )
            except (OSError, UnicodeError, configparser.Error, KeyError):
                continue
    return tuple(found)


def normalized(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


def resolve(app: str, *, pattern: bool = False) -> tuple[Application | None, str]:
    installed = applications()
    exact = [
        a
        for a in installed
        if a.id.casefold().removesuffix(".desktop")
        == app.casefold().removesuffix(".desktop")
    ]
    matches = exact or [
        a
        for a in installed
        if any(
            normalized(app) == normalized(alias)
            or (pattern and fnmatch.fnmatchcase(alias.casefold(), app.casefold()))
            for alias in (
                a.name,
                a.wm_class,
                a.name.split()[-1] if a.name.split() else "",
                a.wm_class.rsplit(".", 1)[-1],
            )
            if alias
        )
    ]
    if len(matches) > 1:
        return None, "ambiguous_app"
    return (matches[0], "") if matches else (None, "")


async def launch(app: str, *, can_act: CanAct | None = None) -> Outcome:
    if not app.strip() or any(ord(c) < 32 or ord(c) == 127 for c in app):
        return Outcome("desktop.launch", False, "invalid_app")
    installed, error = resolve(app)
    if error:
        return Outcome("desktop.launch", False, error)
    if installed is not None:
        app = installed.id
    elif not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", app):
        return Outcome("desktop.launch", False, "app_not_found")
    return await command("desktop.launch", "gtk-launch", app, can_act=can_act)


async def find_window(app: str, title: str = "*") -> tuple[str | None, str]:
    if not app.strip():
        return None, "invalid_app"
    installed, error = resolve(app, pattern=True)
    if error:
        return None, error
    aliases = {normalized(app)}
    if installed is not None:
        aliases.update(
            normalized(v)
            for v in (
                installed.name,
                installed.wm_class,
                installed.wm_class.rsplit(".", 1)[-1],
            )
            if v
        )
    listing = await output("wmctrl", "-lx")
    if listing is None:
        return None, "window_unreadable"
    matches = []
    for line in listing.splitlines():
        columns = line.split(None, 4)
        if len(columns) < 5 or not re.fullmatch(r"0x[0-9a-fA-F]+", columns[0]):
            continue
        wm_class = columns[2]
        named = any(
            normalized(part) in aliases for part in (wm_class, *wm_class.split("."))
        )
        if (
            any(
                fnmatch.fnmatchcase(part.lower(), app.lower())
                for part in (wm_class, *wm_class.split("."))
            )
            or named
        ) and fnmatch.fnmatchcase(columns[4].lower(), title.lower()):
            matches.append(str(int(columns[0], 16)))
    if not matches:
        return None, "window_not_found"
    # Prefer the current matching window; multiple others need a title selector.
    active = await output("xdotool", "getactivewindow")
    if active in matches:
        return active, ""
    if len(matches) > 1:
        return None, "ambiguous_window"
    return matches[0], ""


async def focus(
    app: str, title: str = "*", *, can_act: CanAct | None = None
) -> Outcome:
    window, error = await find_window(app, title)
    if window is None:
        return Outcome("desktop.focus", False, error)
    return await command(
        "desktop.focus", "xdotool", "windowactivate", "--sync", window, can_act=can_act
    )


async def output(*args: str) -> str | None:
    """A command's stripped stdout, or None if it failed or couldn't run."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None
        if process.returncode != 0:
            return None
        return stdout.decode(errors="replace").strip()
    except OSError:
        return None


# Known browsers and their Linux tab shortcut family. None means close_tab
# recognizes the browser, but tab navigation doesn't claim shortcut support.
BROWSERS = {
    "*firefox*": "firefox",
    "*chrome*": "chromium",
    "*chromium*": "chromium",
    "*brave*": "chromium",
    "*microsoft-edge*": "chromium",
    "*vivaldi*": None,
    "*opera*": None,
    "*librewolf*": "firefox",
}


def browser_classes(wm_class: str) -> tuple[str, ...]:
    return tuple(re.findall(r'"([^"]*)"', wm_class))


def browser(wm_class: str) -> bool:
    """True for xprop WM_CLASS output naming a known browser."""
    return any(
        fnmatch.fnmatchcase(name.lower(), glob)
        for name in browser_classes(wm_class)
        for glob in BROWSERS
    )


def tab_family(wm_class: str) -> str | None:
    for name in browser_classes(wm_class):
        for glob, family in BROWSERS.items():
            if fnmatch.fnmatchcase(name.lower(), glob):
                return family
    return None


async def tab_window(
    action: str, app: str, *, can_act: CanAct | None = None
) -> tuple[str, str] | Outcome:
    if app.casefold() == "active":
        window = await output("xdotool", "getactivewindow")
        if not window or not window.isdigit():
            return Outcome(action, False, "window_unreadable")
    else:
        window, error = await find_window(app)
        if window is None:
            return Outcome(action, False, error)
    wm_class = await output("xprop", "-id", window, "WM_CLASS")
    if wm_class is None:
        return Outcome(action, False, "window_unreadable")
    if not browser(wm_class):
        return Outcome(action, False, "not_a_browser")
    family = tab_family(wm_class)
    if family is None:
        return Outcome(action, False, "unsupported_browser")
    if app.casefold() != "active":
        activated = await command(
            action, "xdotool", "windowactivate", "--sync", window, can_act=can_act
        )
        if not activated.ok:
            return activated
    if await output("xdotool", "getactivewindow") != window:
        return Outcome(action, False, "window_not_active")
    return window, family


async def tab_command(
    action: str, window: str, *args: str, can_act: CanAct | None = None
) -> Outcome:
    # Abort if the user left the checked window; activate its ID before input.
    # These sequential X11 operations still have a small focus-change race.
    if await output("xdotool", "getactivewindow") != window:
        return Outcome(action, False, "window_not_active")
    return await command(
        action, "xdotool", "windowactivate", "--sync", window, *args, can_act=can_act
    )


async def open_tab(
    url: str, app: str = "active", *, can_act: CanAct | None = None
) -> Outcome:
    action = "desktop.open_tab"
    try:
        parsed = urlparse(url)
    except ValueError:
        return Outcome(action, False, "invalid_url")
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
    ):
        return Outcome(action, False, "invalid_url")
    target = await tab_window(action, app, can_act=can_act)
    if isinstance(target, Outcome):
        return target
    window, _ = target
    # Creating the tab is separate from typing: never replace the active tab's URL.
    for args in (
        ("key", "--clearmodifiers", "ctrl+t"),
        ("type", "--clearmodifiers", "--delay", "0", "--", url),
        ("key", "--clearmodifiers", "Return"),
    ):
        result = await tab_command(action, window, *args, can_act=can_act)
        if not result.ok:
            return result
    return result


async def switch_tab(
    tab: str, app: str = "active", *, can_act: CanAct | None = None
) -> Outcome:
    action = "desktop.switch_tab"
    if tab not in TAB_TARGETS:
        return Outcome(action, False, "invalid_tab")
    target = await tab_window(action, app, can_act=can_act)
    if isinstance(target, Outcome):
        return target
    window, family = target
    key = {"next": "ctrl+Page_Down", "previous": "ctrl+Page_Up"}.get(tab)
    if key is None:
        # Firefox uses Alt+digits on Linux; Chromium uses Ctrl+digits.
        key = f"{'alt' if family == 'firefox' else 'ctrl'}+{'9' if tab == 'last' else tab}"
    return await tab_command(
        action, window, "key", "--clearmodifiers", key, can_act=can_act
    )


def literals(pattern: str) -> str:
    """The characters a glob matches literally, parsed the way fnmatch does:
    * and ? are wildcards; a [...] class (with an optional leading ! and a
    leading ] as a member) is one wildcard; an unterminated [ is literal."""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        i += 1
        if c in "*?":
            continue
        if c == "[":
            j = i
            if j < n and pattern[j] == "!":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n:
                i = j + 1  # the whole class matches one arbitrary character
                continue
        out.append(c)
    return "".join(out)


def specific(pattern: str) -> bool:
    """A title pattern must name something: at least 3 literal characters, so a
    skill can never close whatever browser tab happens to be active."""
    return len(literals(pattern).strip()) >= 3


async def close_tab(title: str, *, can_act: CanAct | None = None) -> Outcome:
    """Press Ctrl+W in the active window, only if it's a known browser and its
    title matches `title` (case-insensitive glob). The keystroke is bound to the
    window that passed the check: one xdotool call re-activates that window ID
    and sends the key, so a focus change can't redirect it to another window."""
    if not specific(title):
        return Outcome("desktop.close_tab", False, "pattern_too_broad")
    window = await output("xdotool", "getactivewindow")
    if not window or not window.isdigit():
        return Outcome("desktop.close_tab", False, "window_unreadable")
    name = await output("xdotool", "getwindowname", window)
    if name is None:
        return Outcome("desktop.close_tab", False, "window_unreadable")
    if not fnmatch.fnmatchcase(name.lower(), title.lower()):
        return Outcome("desktop.close_tab", False, "tab_not_active")
    wm_class = await output("xprop", "-id", window, "WM_CLASS")
    if wm_class is None:
        return Outcome("desktop.close_tab", False, "window_unreadable")
    if not browser(wm_class):
        return Outcome("desktop.close_tab", False, "not_a_browser")
    # Abort if focus moved while we were checking, so the activation below never
    # brings back a window the user has left; it then only binds the key to it.
    if await output("xdotool", "getactivewindow") != window:
        return Outcome("desktop.close_tab", False, "tab_not_active")
    return await command(
        "desktop.close_tab",
        "xdotool",
        "windowactivate",
        "--sync",
        window,
        "key",
        "--clearmodifiers",
        "ctrl+w",
        can_act=can_act,
    )
