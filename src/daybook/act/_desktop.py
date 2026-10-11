"""Desktop effects use argument arrays and return only error kinds."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from urllib.parse import urlparse

from daybook.journal import Outcome


async def command(action: str, *args: str) -> Outcome:
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


async def open_url(url: str) -> Outcome:
    if urlparse(url).scheme not in (
        "http",
        "https",
        "file",
        "mailto",
    ) or url.startswith("-"):
        return Outcome("desktop.open_url", False, "invalid_url")
    return await command("desktop.open_url", "xdg-open", url)


async def launch(app: str) -> Outcome:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", app):
        return Outcome("desktop.launch", False, "invalid_app")
    return await command("desktop.launch", "gtk-launch", app)


async def focus(app: str) -> Outcome:
    try:
        process = await asyncio.create_subprocess_exec(
            "wmctrl",
            "-lx",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()
            return Outcome("desktop.focus", False, "timeout")
        for line in stdout.decode(errors="replace").splitlines():
            columns = line.split(None, 4)
            if len(columns) >= 4 and fnmatch.fnmatchcase(
                columns[2].lower(), app.lower()
            ):
                return await command(
                    "desktop.focus", "xdotool", "windowactivate", "--sync", columns[0]
                )
        return Outcome("desktop.focus", False, "window_not_found")
    except OSError:
        return Outcome("desktop.focus", False, "command_unavailable")


async def active_title() -> str | None:
    """The active window's title, or None when it can't be read."""
    try:
        process = await asyncio.create_subprocess_exec(
            "xdotool",
            "getactivewindow",
            "getwindowname",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
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


def specific(pattern: str) -> bool:
    """A title pattern must name something: at least 3 non-wildcard characters,
    so a skill can never close whatever tab happens to be active."""
    return len(re.sub(r"[*?\[\]]", "", pattern).strip()) >= 3


async def close_tab(title: str) -> Outcome:
    """Press Ctrl+W, but only if the active window's title still matches `title`
    (case-insensitive glob), so a tab the user already left is never closed."""
    if not specific(title):
        return Outcome("desktop.close_tab", False, "pattern_too_broad")
    current = await active_title()
    if current is None:
        return Outcome("desktop.close_tab", False, "window_unreadable")
    if not fnmatch.fnmatchcase(current.lower(), title.lower()):
        return Outcome("desktop.close_tab", False, "tab_not_active")
    return await command(
        "desktop.close_tab", "xdotool", "key", "--clearmodifiers", "ctrl+w"
    )
