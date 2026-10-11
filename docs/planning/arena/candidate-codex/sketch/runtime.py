"""Composition root and thin localhost web boundary; owns consent + lifecycle only.

Start one core service using a per-user flock. Configure DISPLAY=:1 and the
session XAUTHORITY outside source code. Backend URL/model/key are config, not
skill data. Localhost HTTP serves static UI + JSON; no broker or event bus.
"""

from dataclasses import dataclass
from typing import Literal
from .desktop import PrivacyRules


@dataclass(frozen=True)
class Status:
    state: Literal["paused", "running", "pausing", "degraded"]
    generation: int
    model_busy: bool
    queued_jobs: int
    desktop_connected: bool
    message: str


class Runtime:
    async def start(self) -> None:
        """Compose owners, acquire singleton lock, restore as paused, connect helper."""
        # TODO: web/tray forward commands to this owner; neither stores its own enabled bit.
        # TODO: reserve model time -> capture/observe -> recheck returned permit -> record.
        # TODO: gate check + synchronous SQLite transaction execute in one event-loop turn.
        # TODO: rollup input -> Intelligence.rollup -> Portal.commit_rollup.
        # TODO: Portal.context -> Intelligence.decide -> Skills.apply.
        # TODO: every final write/action checks the relevant privacy/evidence/revision token.
        raise NotImplementedError

    async def set_enabled(self, enabled: bool) -> Status:
        """Idempotent start/pause for both web and tray; pause includes a desktop barrier."""
        # TODO: pause increments generation first, drops jobs, ignores model results,
        # calls desktop.quiesce, and reports paused only after its acknowledgment.
        # TODO: already-submitted inference may finish locally; it can no longer commit.
        # TODO: helper failure means degraded, with visible truthful pause/error state.
        raise NotImplementedError

    async def set_exclusions(self, rules: PrivacyRules) -> Status:
        """Atomically persist rules and advance generation; invalidate captured/in-flight work."""
        raise NotImplementedError

    async def status(self) -> Status:
        """Derive health from owners; no mirrored status database."""
        raise NotImplementedError

    async def run_capture_loop(self) -> None:
        """15-second opportunities; no catch-up captures or queued image backlog."""
        # TODO: no unchanged-screen heuristic in MVP: periodic evidence makes gaps honest.
        raise NotImplementedError

    async def run_rollup_loop(self) -> None:
        """120-second normal windows; early rollup on activity change, at most twice/minute."""
        # TODO: close at privacy gaps; retain only a bounded overlap, not an ever-growing prompt.
        raise NotImplementedError

    async def run_skill_loop(self) -> None:
        """Coalesce segment updates/timers to at most two joint decisions per minute."""
        # TODO: require fresh segments; timer alone never extends observed focus duration.
        raise NotImplementedError
