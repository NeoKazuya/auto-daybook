#!/usr/bin/env python3
"""Record safe, explicitly allowed test windows. Standalone: no Daybook imports.

Run with gx10 run -- uv run python scripts/record_fixture.py SCENARIO --at 0
--allow-title '*daybook-fixture*'. An isolated test desktop window must cover
unapproved content. Locked/private steps receive a black placeholder and
sanitized facts, with the policy recorded in source.json.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path

import mss
from PIL import Image
from Xlib import X, display, error
from Xlib.ext import screensaver  # noqa: F401

PRIVATE_APPS = ("keepassxc", "1password", "bitwarden")
PRIVATE_TITLES = ("*private browsing*", "*incognito*", "*daybook ·*")


def command(*args):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def read_state():
    connection = display.Display(os.environ.get("DISPLAY", ":1"))
    root = connection.screen().root

    def prop(window, name):
        result = window.get_full_property(
            connection.intern_atom(name), X.AnyPropertyType
        )
        return None if result is None else result.value

    def info(window):
        classes = window.get_wm_class()
        title = prop(window, "_NET_WM_NAME")
        if title is None:
            title = window.get_wm_name() or ""
        if isinstance(title, bytes):
            title = title.decode("utf-8", errors="replace")
        return {"app": str(classes[-1] if classes else "unknown"), "title": str(title)}

    try:
        dimensions = root.get_geometry()
        active_prop = prop(root, "_NET_ACTIVE_WINDOW")
        active_id = (
            int(active_prop[0]) if active_prop is not None and len(active_prop) else 0
        )
        windows, active = [], None
        stack = prop(root, "_NET_CLIENT_LIST_STACKING")
        for wid in () if stack is None else stack:
            window = connection.create_resource_object("window", int(wid))
            try:
                if window.get_attributes().map_state != X.IsViewable:
                    continue
                geometry = window.get_geometry()
                xy = root.translate_coords(window, 0, 0)
                extents = prop(window, "_NET_FRAME_EXTENTS")
                left, right, top, bottom = (
                    tuple(int(v) for v in extents)
                    if extents is not None and len(extents) == 4
                    else (0, 0, 0, 0)
                )
                rect = clip(
                    (
                        xy.x - left,
                        xy.y - top,
                        geometry.width + left + right,
                        geometry.height + top + bottom,
                    ),
                    (0, 0, dimensions.width, dimensions.height),
                )
                if not rect[2] or not rect[3]:
                    continue
                facts = info(window)
                opacity = prop(window, "_NET_WM_WINDOW_OPACITY")
                pid = prop(window, "_NET_WM_PID")
                windows.append(
                    {
                        "id": str(int(wid)),
                        "info": facts,
                        "rect": dict(zip(("x", "y", "width", "height"), rect)),
                        "opacity": 1.0
                        if opacity is None
                        else int(opacity[0]) / 0xFFFFFFFF,
                        "pid": int(pid[0]) if pid is not None and len(pid) else None,
                    }
                )
                if int(wid) == active_id:
                    active = facts
            except error.XError:
                continue
        locked = None
        for row in command("loginctl", "list-sessions", "--no-legend").splitlines():
            fields = row.split()
            if "seat0" in fields:
                answer = command(
                    "loginctl", "show-session", fields[0], "-p", "LockedHint", "--value"
                )
                if answer in ("yes", "no"):
                    locked = answer == "yes"
                    break
        return {
            "active": active,
            "windows": windows,
            "locked": locked,
            "idle_ms": int(root.screensaver_query_info().idle),
            "microphone_active": bool(
                command("pactl", "list", "short", "source-outputs")
            ),
            "overlay": any(
                w["info"]["app"].lower() == "daybook-shell" for w in windows
            ),
            "size": [dimensions.width, dimensions.height],
        }
    finally:
        connection.close()


def clip(left, right):
    x, y = max(left[0], right[0]), max(left[1], right[1])
    end_x = min(left[0] + left[2], right[0] + right[2])
    end_y = min(left[1] + left[3], right[1] + right[3])
    return x, y, max(0, end_x - x), max(0, end_y - y)


def subtract(rect, cover):
    x, y, width, height = rect
    cx, cy, cw, ch = clip(rect, cover)
    if cw == 0 or ch == 0:
        return [rect]
    pieces = [
        (x, y, width, cy - y),
        (x, cy + ch, width, y + height - cy - ch),
        (x, cy, cx - x, ch),
        (cx + cw, cy, x + width - cx - cw, ch),
    ]
    return [p for p in pieces if p[2] > 0 and p[3] > 0]


def visible(windows, index):
    window = windows[index]
    if window["opacity"] <= 0:
        return False
    parts = [tuple(window["rect"][key] for key in ("x", "y", "width", "height"))]
    for cover in windows[index + 1 :]:
        if cover["opacity"] < 1:
            continue
        rect = tuple(cover["rect"][key] for key in ("x", "y", "width", "height"))
        parts = [p for old in parts for p in subtract(old, rect)]
    return bool(parts)


def private(info):
    return info["app"].lower() in PRIVATE_APPS or any(
        fnmatchcase(info["title"].lower(), pattern) for pattern in PRIVATE_TITLES
    )


def sanitized(info):
    if not private(info):
        return info
    # Preserve only the exclusion marker, never the actual private title.
    marker = next(
        (
            p.replace("*", "").strip()
            for p in PRIVATE_TITLES
            if fnmatchcase(info["title"].lower(), p)
        ),
        "private",
    )
    return {"app": info["app"], "title": f"{marker} (redacted fixture)"}


def capture():
    with mss.mss(display=os.environ.get("DISPLAY", ":1")) as screen:
        shot = screen.grab(screen.monitors[0])
        return Image.frombytes("RGB", shot.size, shot.rgb)


def record_step(folder: Path, at: float, allow_titles: tuple[str, ...]):
    manifest = folder / "steps.json"
    steps = json.loads(manifest.read_text()) if manifest.exists() else []
    if (
        not math.isfinite(at)
        or at < 0
        or (steps and at <= steps[-1]["at"])
        or (not steps and at != 0)
    ):
        raise ValueError("step times must strictly increase from zero")
    before = read_state()
    windows = before["windows"]
    private_visible = any(
        private(w["info"]) and visible(windows, i) for i, w in enumerate(windows)
    )
    if before["locked"] is not False or before["active"] is None:
        policy = "black-locked"
    elif private_visible:
        policy = "black-private"
    else:
        for i, window in enumerate(windows):
            if visible(windows, i) and not any(
                fnmatchcase(window["info"]["title"], allowed)
                for allowed in allow_titles
            ):
                raise RuntimeError("visible window is not approved; no pixels captured")
        if not any(
            fnmatchcase(before["active"]["title"], allowed) for allowed in allow_titles
        ):
            raise RuntimeError("active window is not approved; no pixels captured")
        policy = "actual"
    image = (
        capture()
        if policy == "actual"
        else Image.new("RGB", tuple(before["size"]), "black")
    )
    after = read_state()
    if any(
        before[key] != after[key] for key in ("locked", "active", "windows", "overlay")
    ):
        raise RuntimeError("screen changed during capture; frame discarded")
    directory = f"{len(steps):03}"
    path = folder / directory
    path.mkdir(parents=True, exist_ok=False)
    image.save(path / "frame.png", optimize=True)

    def safe_info(info):
        clean = sanitized(info)
        if not private(info) and not any(
            fnmatchcase(info["title"], allowed) for allowed in allow_titles
        ):
            clean = {"app": info["app"], "title": "(unrecorded window)"}
        return clean

    facts = {
        "active": safe_info(before["active"]) if before["active"] else None,
        "windows": [{**w, "info": safe_info(w["info"])} for w in windows],
        "microphone_active": before["microphone_active"],
        "overlay": before["overlay"],
    }
    (path / "window.json").write_text(json.dumps(facts, indent=2) + "\n")
    (path / "locked").write_text(json.dumps(before["locked"]) + "\n")
    (path / "idle_ms").write_text(json.dumps(before["idle_ms"]) + "\n")
    (path / "source.json").write_text(
        json.dumps(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "machine": "GX10",
                "frame_policy": policy,
                "size": before["size"],
            },
            indent=2,
        )
        + "\n"
    )
    steps.append({"at": at, "directory": directory})
    manifest.write_text(json.dumps(steps, indent=2) + "\n")
    if not (folder / "labels.json").exists():
        (folder / "labels.json").write_text("[]\n")
    return policy


def wrapper_ancestors():
    """uv may sit between the recorder and the GX10 capture-lock wrapper."""
    wrappers = set()
    pid = os.getppid()
    visited = set()
    wrapper = str(Path.home() / ".local/share/daybook-gx10/desktop-test.py").encode()
    while pid > 1 and pid not in visited:
        visited.add(pid)
        try:
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if len(arguments) > 2 and arguments[1:3] == [wrapper, b"run"]:
                wrappers.add(pid)
            status = Path(f"/proc/{pid}/status").read_text().splitlines()
            pid = int(next(row.split()[1] for row in status if row.startswith("PPid:")))
        except (OSError, ValueError, StopIteration):
            break
    return wrappers


@contextmanager
def capture_lock():
    # Standalone owns the lock; only the known gx10 wrapper may already own it.
    with open("/tmp/daybook-capture.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            wrappers = wrapper_ancestors()
            stat = os.fstat(lock.fileno())
            identity = (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino)

            def parent_owns_capture(line):
                fields = line.split()
                if (
                    len(fields) < 6
                    or fields[1] != "FLOCK"
                    or fields[4] not in {str(pid) for pid in wrappers}
                ):
                    return False
                device = fields[5].split(":")
                try:
                    return (
                        len(device) == 3
                        and (int(device[0], 16), int(device[1], 16), int(device[2]))
                        == identity
                    )
                except ValueError:
                    return False

            held_by_parent = any(
                parent_owns_capture(line)
                for line in Path("/proc/locks").read_text().splitlines()
            )
            if not held_by_parent:
                raise RuntimeError("real screen is owned by another process") from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--at", type=float, default=0)
    parser.add_argument("--allow-title", action="append", required=True)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--labels", type=Path)
    args = parser.parse_args()
    if (
        not all(math.isfinite(v) for v in (args.at, args.seconds, args.interval))
        or args.seconds < 0
        or args.interval <= 0
    ):
        parser.error("seconds must be nonnegative; interval must be positive")
    with capture_lock():
        started = time.monotonic()
        offset = 0.0
        while True:
            policy = record_step(args.folder, args.at + offset, tuple(args.allow_title))
            print(json.dumps({"at": args.at + offset, "frame_policy": policy}))
            if offset >= args.seconds:
                break
            next_offset = min(args.seconds, offset + args.interval)
            time.sleep(max(0, started + next_offset - time.monotonic()))
            offset = next_offset
        if args.labels:
            labels = json.loads(args.labels.read_text())
            if not isinstance(labels, list):
                raise ValueError("labels must be an array")
            (args.folder / "labels.json").write_text(
                json.dumps(labels, indent=2) + "\n"
            )


if __name__ == "__main__":
    main()
