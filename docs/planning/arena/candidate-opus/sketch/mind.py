"""daybook.mind — the tick. One text-only model call that does two jobs at once:

1. Rollup: fold the unassigned observations into the open segment, or start a new one (title, kind, intent).
2. Signals: answer every active skill's questions about the user's situation right now.

Then it records the tick in the journal and lets the skill book react. That's the whole interpretation layer.
No skill ever makes its own model call, so background model load is one observe call per screen change plus
one tick per ~90 s, however many skills are enabled.

When a tick runs:
    - every TICK_INTERVAL (90 s), or
    - SWITCH_DEBOUNCE (20 s) after sense reports an app switch (so music follows you within ~30 s),
    - never more often than MIN_SPACING (30 s), and
    - only if there are unassigned observations (no new evidence -> no tick -> no readings -> no firings).
After a gap longer than skills.MAX_GAP with no observations, the open segment is closed.

Tick prompt (text only, ~1.5–3K tokens):
    system: rules for segments (observations are evidence; intent is a short inference, hedged when unsure;
            prefer Continue unless the task clearly changed) + "answer every question; use null if you can't tell"
    user:   portal.context(last 30 min, detail="segments")      # recent history, for continuity
            open segment + its last 3 observations
            NEW observations (the unassigned ones), with times and window facts
            QUESTIONS: one line each: `break.focused (bool): Is the user doing sustained, focused work ...?`
                       enum lines list values with their descriptions
    output: _TickOut (private pydantic model generated per tick from the active questions, so the JSON schema
            constrains each signal's type):
            {"segment": {"continues": bool, "title": str, "kind": ActivityKind, "intent": str},
             "signals": {"music.mood": "focus", "break.focused": true, "stand-after-meeting.in_meeting": false}}
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn

from .journal import Journal, Observation, RecordedTick, Segment, Tick
from .model import ModelGateway, Prompt
from .skills import Question, SkillBook

TICK_INTERVAL = timedelta(seconds=90)
SWITCH_DEBOUNCE = timedelta(seconds=20)
MIN_SPACING = timedelta(seconds=30)


@dataclass(frozen=True)
class TickInput:
    at: datetime
    history: str  # portal.context(...)
    open_segment: Segment | None
    new: tuple[Observation, ...]  # non-empty
    questions: tuple[Question, ...]


class Mind:
    def __init__(self, journal: Journal, gateway: ModelGateway, skills: SkillBook) -> None:
        raise NotImplementedError

    def nudge(self, app_switched: bool) -> None:
        """Called by sense after each new observation. Schedules an early tick on app switch. Never blocks."""
        raise NotImplementedError

    async def run(self) -> NoReturn:
        """TODO: loop { await next due time (interval / debounce / spacing); await self.tick_now() }; log and continue on errors."""
        raise NotImplementedError

    async def tick_now(self) -> RecordedTick | None:
        """One tick. Safe to call twice (journal.record_tick is idempotent on its observations).

        TODO:
          new = portal.observations(TimeRange.last(hours=2), unassigned_only=True)
          if not new: maybe close_open_segment on gap; return None
          inp = TickInput(...); out = await gateway.call(Priority.TICK, build_tick_prompt(inp))
          tick = parse_tick(inp, out)
          recorded = journal.record_tick(tick)
          await skills.react(recorded)
          return recorded
        """
        raise NotImplementedError


def build_tick_prompt(inp: TickInput) -> Prompt:
    raise NotImplementedError


def parse_tick(inp: TickInput, out: object) -> Tick:
    """Pure. Turns the model's answer into a journal Tick.

    TODO:
      segment: Continue(open.id, ...) if out.segment.continues and open_segment else Start(...)
      readings: for q in inp.questions: coerce(out.signals.get(str(q.signal)), q.type) -> value or None
                (an enum answer outside the values, a non-bool for bool, a number out of range -> None)
      Missing questions -> None. Extra keys -> ignored.
    """
    raise NotImplementedError


def tick_schema(questions: Sequence[Question]) -> type:
    """Build the per-tick pydantic output model so the endpoint's JSON-schema mode enforces signal types."""
    raise NotImplementedError
