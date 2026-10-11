"""Command entry point. Real runtime wiring follows the shared skeleton."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

import uvicorn

from daybook.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="daybook")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    modes = run.add_mutually_exclusive_group()
    modes.add_argument("--mock-api", action="store_true")
    modes.add_argument("--replay", type=Path)
    modes.add_argument("--no-capture", action="store_true")
    run.add_argument("--real-effects", action="store_true")
    run.add_argument("--port", type=int, default=8765)
    sub.add_parser("observe-once")
    sub.add_parser("tick-once")
    skill = sub.add_parser("skill").add_subparsers(dest="operation", required=True)
    skill.add_parser("check").add_argument("file", type=Path)
    music = sub.add_parser("music").add_subparsers(dest="operation", required=True)
    music.add_parser("check")
    music.add_parser("play").add_argument("mood")
    overlay = sub.add_parser("overlay").add_subparsers(dest="operation", required=True)
    overlay.add_parser("test")
    args = parser.parse_args()
    if args.command != "run" or not args.mock_api:
        parser.error(
            "The shared skeleton supports run --mock-api; real runtime follows in Lane A."
        )
    if args.real_effects:
        parser.error("--mock-api cannot use --real-effects")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    home = Path(os.environ.get("DAYBOOK_HOME", "~/.daybook")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(home / "token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(token)
    app = create_app(mock=True, token=token, origin=f"http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
