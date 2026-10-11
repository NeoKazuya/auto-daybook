#!/usr/bin/env python3
"""Small real-Xorg checks, independent of the Daybook application."""

import argparse
import base64
import fcntl
import io
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path


def command(*args):
    return subprocess.check_output(
        args, text=True, stderr=subprocess.STDOUT, timeout=15
    ).strip()


def unlocked():
    state = command(
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.gnome.ScreenSaver",
        "--object-path",
        "/org/gnome/ScreenSaver",
        "--method",
        "org.gnome.ScreenSaver.GetActive",
    )
    if state != "(false,)":
        raise RuntimeError(f"Desktop is locked: {state}")
    if os.environ.get("XDG_SESSION_TYPE") != "x11":
        raise RuntimeError("These checks require the GNOME X11 session")
    command("xset", "q")


def capture():
    from PIL import ImageGrab, features

    if not features.check_feature("xcb"):
        raise RuntimeError("Pillow must support XCB for in-memory X11 capture")
    return ImageGrab.grab(xdisplay=os.environ["DISPLAY"])


def request_json(url, payload=None, key_file=None):
    headers = {"Content-Type": "application/json"}
    if key_file:
        headers["Authorization"] = (
            "Bearer " + Path(key_file).expanduser().read_text().strip()
        )
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def model_profile(args):
    """Read the app's model settings without importing or creating the app."""
    path = Path(os.environ.get("DAYBOOK_HOME", "~/.daybook")).expanduser()
    path = path / "daybook.toml"
    try:
        with path.open("rb") as config:
            data = tomllib.load(config)
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Cannot read model profile from {path}: {exc}") from None
    model = data.get("model", {})
    if not isinstance(model, dict):
        raise RuntimeError(
            f"Invalid model profile in {path}: model must be a TOML table"
        )
    profile = {}
    for option, setting, default in [
        ("base_url", "base_url", "http://127.0.0.1:18300/v1"),
        ("model", "model", "qwen3.8-flash-next"),
        ("key_file", "api_key_file", "~/.config/qwen38/api-key"),
    ]:
        value = model.get(setting, default)
        if not isinstance(value, str) or (not value.strip() and option != "key_file"):
            raise RuntimeError(
                f"Invalid model profile in {path}: model.{setting} must be a string"
                + (" (nonempty)" if option != "key_file" else "")
            )
        profile[option] = value
    if (
        args.base_url is not None
        and args.base_url.rstrip("/") != profile["base_url"].rstrip("/")
        and args.key_file is None
    ):
        # An endpoint override must not send the profile's key to another server.
        args.key_file = ""
    for option, value in profile.items():
        if getattr(args, option) is None:
            setattr(args, option, value)
    args.base_url = args.base_url.rstrip("/")


def screenshot_status():
    extrema = capture().getextrema()
    if all(high == 0 for _, high in extrema):
        raise RuntimeError("Captured frame is black")
    return str(extrema)


def status(args):
    checks = {}
    for name, check in {
        "unlocked_x11": lambda: unlocked() or os.environ["DISPLAY"],
        "screen_pixels": screenshot_status,
        "no_idle_blank": lambda: command(
            "gsettings", "get", "org.gnome.desktop.session", "idle-delay"
        ),
        "no_auto_lock": lambda: command(
            "gsettings", "get", "org.gnome.desktop.screensaver", "lock-enabled"
        ),
        "no_idle_suspend": lambda: command(
            "gsettings",
            "get",
            "org.gnome.settings-daemon.plugins.power",
            "sleep-inactive-ac-type",
        ),
        "display_power": lambda: (
            command("xset", "q")
            .split("DPMS (Display Power Management Signaling):")[-1]
            .strip()
        ),
        "audio_output": lambda: command("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"),
        "model_server": lambda: [
            item["id"]
            for item in request_json(args.base_url + "/models", key_file=args.key_file)[
                "data"
            ]
        ],
    }.items():
        try:
            checks[name] = {"ok": True, "value": check()}
        except (
            RuntimeError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
        ) as exc:
            checks[name] = {"ok": False, "error": str(exc)}
    expected = {
        "no_idle_blank": "uint32 0",
        "no_auto_lock": "false",
        "no_idle_suspend": "'nothing'",
    }
    for name, value in expected.items():
        checks[name]["ok"] = checks[name].get("value") == value
    power = checks["display_power"]
    power["ok"] = power["ok"] and "DPMS is Disabled" in power.get("value", "")
    print(json.dumps(checks, indent=2))
    return 0 if all(item["ok"] for item in checks.values()) else 1


def smoke(args):
    import tkinter as tk

    unlocked()
    try:
        previous = command("xdotool", "getactivewindow")
    except subprocess.CalledProcessError:
        previous = None
    root = tk.Tk(className="DaybookGX10Test")
    root.title("GX10 desktop smoke test")
    root.geometry("800x500+80+80")
    root.configure(background="#184d32")
    clicks = []
    escaped = []
    tk.Label(root, text="GX10READY", font=("Sans", 48), bg="#184d32", fg="white").pack(
        pady=35
    )
    entry = tk.Entry(root, font=("Sans", 24))
    entry.pack(pady=20)
    button = tk.Button(
        root, text="Test click", command=lambda: clicks.append(True), font=("Sans", 24)
    )
    button.pack()
    root.bind("<Escape>", lambda _: escaped.append(True))

    def pump(seconds=0.25):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            root.update()
            time.sleep(0.01)

    def click(widget):
        command(
            "xdotool",
            "mousemove",
            "--sync",
            str(widget.winfo_rootx() + widget.winfo_width() // 2),
            str(widget.winfo_rooty() + widget.winfo_height() // 2),
            "click",
            "1",
        )
        pump()

    try:
        pump()
        # WM_CLASS is unique to this test; don't search or operate on another app's title.
        window = command(
            "xdotool", "search", "--onlyvisible", "--class", "^DaybookGX10Test$"
        ).splitlines()[-1]
        command("wmctrl", "-ia", hex(int(window)))
        pump()
        if command("xdotool", "getactivewindow") != window:
            raise RuntimeError("Window focus failed")
        click(entry)
        command(
            "xdotool",
            "type",
            "--clearmodifiers",
            "--delay",
            "30",
            "daybook-keyboard-ok",
        )
        pump()
        if entry.get() != "daybook-keyboard-ok":
            raise RuntimeError("Keyboard input did not reach the test window")
        click(button)
        if len(clicks) != 1:
            raise RuntimeError("Mouse click did not reach the test button")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        pump(0.5)
        image = capture()
        pixel = image.getpixel((root.winfo_rootx() + 10, root.winfo_rooty() + 10))
        if any(
            abs(actual - expected) > 5 for actual, expected in zip(pixel, (24, 77, 50))
        ):
            raise RuntimeError("Screenshot does not contain the visible test scene")
        print(
            "PASS: launch, focus, keyboard, mouse, fullscreen, in-memory screenshot",
            flush=True,
        )
        if args.vision:
            image.thumbnail((1280, 1280))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
            data_url = (
                "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
            )
            print(f"Checking real vision input with {args.model}...", flush=True)
            response = request_json(
                args.base_url + "/chat/completions",
                {
                    "model": args.model,
                    "max_tokens": 80,
                    "temperature": 0,
                    "reasoning_effort": "none",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Read the large white test code at the top of this screen. Reply with only that code.",
                                },
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                },
                args.key_file,
            )
            answer = response["choices"][0]["message"]["content"] or ""
            if "GX10READY" not in answer.upper():
                raise RuntimeError(
                    "Vision response did not contain the visible test code"
                )
            print(
                "PASS: model read GX10READY from a real desktop screenshot", flush=True
            )
        command("xdotool", "key", "--clearmodifiers", "Escape")
        pump()
        if not escaped:
            raise RuntimeError("Escape did not reach the fullscreen window")
        print("PASS: Escape dismissal; test window cleaned up")
    finally:
        root.destroy()
        if previous:
            subprocess.run(
                ["wmctrl", "-ia", hex(int(previous))],
                check=False,
                capture_output=True,
                timeout=5,
            )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "smoke", "run", "enable-app"])
    parser.add_argument(
        "--vision",
        action="store_true",
        help="also read the synthetic screen with the local model",
    )
    parser.add_argument("--base-url", help="override the active model profile endpoint")
    parser.add_argument("--model", help="override the active model profile model ID")
    parser.add_argument(
        "--key-file", help="override the profile key file; empty disables auth"
    )
    args, args_command = parser.parse_known_args()
    args.command = args_command
    if args.action in ("status", "smoke") and args.command:
        parser.error("unexpected arguments: " + " ".join(args.command))
    if args.action == "status" or (args.action == "smoke" and args.vision):
        model_profile(args)
    if args.action == "enable-app":
        if len(args.command) != 1:
            parser.error("enable-app requires the absolute checkout path")
        checkout = Path(args.command[0]).expanduser().resolve()
        if not (checkout / "pyproject.toml").exists():
            raise RuntimeError("No application pyproject.toml in this checkout yet")
        subprocess.run(["uv", "sync"], cwd=checkout, check=True)
        subprocess.run(
            ["uv", "run", "--no-sync", "daybook", "--help"], cwd=checkout, check=True
        )
        config = Path.home() / ".config/daybook-gx10/app-dir"
        config.write_text(str(checkout) + "\n")
        # A never-started unit may not be loaded; restart below checks startup.
        subprocess.run(
            ["systemctl", "--user", "reset-failed", "daybook.service"], check=False
        )
        subprocess.run(
            ["systemctl", "--user", "restart", "daybook.service"], check=True
        )
        time.sleep(1)
        subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "daybook.service"],
            check=True,
        )
        print("Daybook configured for automatic startup and crash recovery.")
        return 0
    if args.action == "status":
        return status(args)
    with open("/tmp/daybook-capture.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Desktop is in use by another test or capture loop"
            ) from None
        if args.action == "smoke":
            return smoke(args)
        cmd = args.command
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            parser.error("run requires a command after --")
        unlocked()
        return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
