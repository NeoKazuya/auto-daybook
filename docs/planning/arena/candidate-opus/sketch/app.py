"""daybook.app — composition root and the CLI. The only module that imports every other module.

    daybook run [--replay DIR] [--no-capture] [--port 8765]   # daybookd: capture + tick + skills + web; spawns mpv and the shell
    daybook observe-once                                      # one real capture -> printed observation (no journal)
    daybook tick-once                                         # one tick on current unassigned observations, prints readings
    daybook skill check FILE                                  # parse_skill + print errors
    daybook music check | play MOOD                           # library/folder agreement; play a mood
    daybook overlay test                                      # show a test overlay via a running daybookd

$DAYBOOK_HOME (default ~/.daybook; per-dev-instance in worktrees, e.g. ~/.daybook-<agent>) holds:
    daybook.toml  journal.db  privacy.json  skills/  mpv.sock  capture.lock

Only one capture loop may watch the real screen: `run` takes an flock on /tmp/daybook-capture.lock when using
X11Screen; a second instance fails fast with a message saying to use --replay or --no-capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    port: int
    model: object  # model.ModelConfig
    display: str  # ":1"
    xauthority: Path
    music_dir: Path  # ~/Music/daybook
    desktop_backend: str  # "xdo" | "cua"
    builtin_skills: Path  # <repo>/skills/builtin
    keep_frames: bool  # debug only; default False


def load_config(home: Path) -> Config:
    raise NotImplementedError


async def run(config: Config, *, replay: Path | None, capture: bool) -> None:
    """TODO:
      bus = Bus(); journal = Journal(home/'journal.db', bus)
      gateway = ModelGateway(config.model)
      library, music = MusicLibrary(config.music_dir), MusicPlayer(...); overlay = Overlay(bus); desktop = XdoDesktop()|CuaDesktop()
      catalog = build_catalog(music, library, overlay, desktop)
      book = SkillBook(config.builtin_skills, home/'skills', catalog, journal.portal, home/'journal.db')
      mind = Mind(journal, gateway, book)
      screen = ReplayScreen(replay) if replay else X11Screen(config.display, config.xauthority)
      sense = Sense(screen, gateway, journal, home/'privacy.json', on_observed=mind.nudge)
      chat = Chat(journal.portal, gateway, book, catalog)
      web = uvicorn server of create_app(WebDeps(...))
      spawn shell child (restart on exit)
      await gather(sense.run() if capture else idle, mind.run(), web.serve())
    """
    raise NotImplementedError
