"""daybook.skills — the skill file format, approval, triggers, and running rules. Also writes skills from chat.

A skill file is `<id>.skill.md`: YAML front matter (signals, rules, allow) + a Markdown body (the goal, in
the user's words). An optional Python hook is the sibling file `<id>.hook.py`. One layout, no folders.

    ---
    id: break
    name: Break reminder
    signals:                       # questions the mind asks the model every tick, on this skill's behalf
      focused: { ask: "Is the user doing sustained, focused work ...?", type: bool }
    rules:
      - when: focused is true for 50m
        cooldown: 30m              # optional
        do:
          - overlay.show: { title: Time for a break, body: "Focused for $focused.for.", minutes: 5 }
          - music.play_mood: { mood: break }
          - hook: run              # optional: awaits <id>.hook.py:run(ctx) as a step
    allow: [overlay.show, music.play_mood]
    ---
    After a long stretch of focused work, remind me to take a break.

Signal types:   bool | enum (values: [...]) | number (min, max) | <catalog type> e.g. music.mood
Trigger grammar (exactly four forms; anything else is a parse error the generator sees and fixes):
    <signal> updates                    every tick with a known value (level-triggered; pair with idempotent actions)
    <signal> becomes <value>            edge: previous known reading (within max_gap) != value, current == value
    <signal> is <value> for <duration>  streak of contiguous readings == value (no gap > max_gap) lasting >= duration
    every <duration>                    at most once per wall-clock bucket, only on ticks (so only while the user is there)
Arguments:      literals, `$signal` (current value), `$signal.for` (current streak length, "52 min")
Durations:      30s | 10m | 2h

Load-time invariants (parse_skill fails the file otherwise; the UI shows it as "invalid" with the errors):
- every `do` action is in `allow`; every `allow` entry is in the catalog; a hook step needs `<id>.hook.py`
  to define that async function (what a hook may *do* is enforced at runtime by the `Actions` it receives)
- args match the catalog's param types, including catalog types (`mood: break` must be a library mood)
- signals referenced in triggers and `$args` are declared in this file
- at most 4 signals per skill and 12 active signals overall (keeps the tick prompt small and answers good)

Approval invariant: a skill is active only if an approval row exists for its *current* sha256 (skill file
bytes + hook bytes). Editing the file by hand, in the UI, or by the generator silently returns it to
"needs review". Skills in the repo's skills/builtin/ are approved by being in the repo.

Layout when built:
    daybook/skills/__init__.py   exports: SkillBook, parse_skill, evaluate, draft_skill, and the types below
    daybook/skills/_format.py    YAML -> types, error collection (private)
    daybook/skills/_ledger.py    approvals + firings tables in journal.db, owned by this package (private)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from .act import ActionName, Actions, Catalog, Outcome
from .journal import Portal, RecordedTick, SignalId, SignalReading, SignalValue, SkillId
from .model import ModelGateway

# --- Parsed domain types. YAML never leaves _format.py.


@dataclass(frozen=True)
class BoolType:
    pass


@dataclass(frozen=True)
class EnumType:
    values: tuple[str, ...]
    describe: Mapping[str, str]  # value -> one-line meaning; rendered into the tick prompt


@dataclass(frozen=True)
class NumberType:
    min: float
    max: float


SignalType = BoolType | EnumType | NumberType  # catalog types (music.mood) resolve to EnumType at parse time


@dataclass(frozen=True)
class Question:
    """One signal, as the mind sees it."""

    signal: SignalId
    ask: str
    type: SignalType


@dataclass(frozen=True)
class Updates:
    signal: SignalId


@dataclass(frozen=True)
class Becomes:
    signal: SignalId
    value: SignalValue


@dataclass(frozen=True)
class HeldFor:
    signal: SignalId
    value: SignalValue
    duration: timedelta


@dataclass(frozen=True)
class Every:
    interval: timedelta


Trigger = Updates | Becomes | HeldFor | Every


@dataclass(frozen=True)
class SignalRef:
    signal: SignalId
    field: Literal["value", "for"]


Arg = str | int | float | bool | SignalRef


@dataclass(frozen=True)
class ActionStep:
    action: ActionName
    args: Mapping[str, Arg]


@dataclass(frozen=True)
class HookStep:
    function: str  # name in hook.py


Step = ActionStep | HookStep


@dataclass(frozen=True)
class Rule:
    trigger: Trigger
    steps: tuple[Step, ...]
    cooldown: timedelta | None


@dataclass(frozen=True)
class Skill:
    id: SkillId
    name: str
    goal: str  # the Markdown body
    questions: tuple[Question, ...]
    rules: tuple[Rule, ...]
    allow: frozenset[ActionName]
    hook_source: str | None
    created_by: Literal["builtin", "user", "chat"]
    request: str | None  # the chat message it was generated from
    source_hash: str  # sha256(skill bytes + hook bytes)


@dataclass(frozen=True)
class SkillError:
    where: str  # "rules[0].when", "allow", "signals.mood.type"
    message: str


def parse_skill(text: str, *, catalog: Catalog, hook_text: str | None) -> Skill | list[SkillError]:
    """The only parser. The loader, the UI editor's live check, and the generator all call it."""
    raise NotImplementedError


# --- Trigger evaluation: pure.


@dataclass(frozen=True)
class Occurrence:
    """A trigger matched. `key` makes firing idempotent: the ledger stores (skill, rule index, key) once."""

    key: str  # Updates/Becomes: "tick@<id>"; HeldFor: "streak@<first tick id>"; Every: "every@<bucket start iso>"
    at: datetime
    streak: timedelta | None  # for $signal.for


def evaluate(rule: Rule, history: Sequence[SignalReading], *, max_gap: timedelta,
             now: datetime | None = None) -> Occurrence | None:
    """Did `rule` match at the newest reading in `history` (oldest first)?

    TODO:
      Updates:  last reading is from the newest tick and value is not None -> key tick@last.tick
      Becomes:  prev = latest non-None before last; require last.at - prev.at <= max_gap
                and prev.value != v and last.value == v
      HeldFor:  walk back from last while value == v and gaps <= max_gap; streak = last.at - first.at
                if streak >= duration -> key streak@first.tick
      Every:    bucket = floor(now / interval); key every@bucket   (history unused)
    Cooldown is not checked here; the ledger checks it, because it needs past firings.
    """
    raise NotImplementedError


# --- The book: loading, approval, reacting to ticks.


SkillStatus = Literal["active", "needs_review", "disabled", "invalid"]


@dataclass(frozen=True)
class SkillEntry:
    id: SkillId
    path: Path
    status: SkillStatus
    text: str  # current file text, for the review/edit UI
    hook_text: str | None
    source_hash: str
    skill: Skill | None  # None iff invalid
    errors: tuple[SkillError, ...]


@dataclass(frozen=True)
class Firing:
    skill: SkillId
    rule: int
    key: str
    at: datetime
    outcomes: tuple[Outcome, ...]


class StaleApproval(Exception):
    """The hash the user reviewed is not the file's current hash. The UI reloads and shows the new text."""


class SkillBook:
    """Owns skill files under $DAYBOOK_HOME/skills/ (+ the repo's skills/builtin/), the approvals table,
    and the firings ledger. Rescans files on every call to `entries()`/`questions()` (cheap; < 50 files),
    so there is no cache to invalidate when someone edits a file by hand."""

    def __init__(self, builtin_dir: Path, user_dir: Path, catalog: Catalog, portal: Portal, db_path: Path) -> None:
        raise NotImplementedError

    def entries(self) -> list[SkillEntry]:
        raise NotImplementedError

    def questions(self) -> list[Question]:
        """Questions of active skills, for the tick prompt."""
        raise NotImplementedError

    def approve(self, id: SkillId, source_hash: str) -> SkillEntry:
        raise NotImplementedError

    def disable(self, id: SkillId) -> SkillEntry:
        raise NotImplementedError

    def write(self, id: SkillId | None, text: str, hook_text: str | None = None) -> SkillEntry:
        """Create or overwrite a user skill file (UI editor, generator). Never auto-approves. Invalid text is
        still saved so the user can fix it; it shows as 'invalid'."""
        raise NotImplementedError

    def delete(self, id: SkillId) -> None:
        raise NotImplementedError

    async def react(self, tick: RecordedTick) -> list[Firing]:
        """Called by mind right after each recorded tick. Runs matching rules' steps in order.

        TODO:
          for skill in active skills, for i, rule in enumerate(skill.rules):
            history = portal.readings(signal of rule, TimeRange(tick.at - lookback(rule), tick.at))
            occ = evaluate(rule, history, max_gap=MAX_GAP, now=tick.at)
            if occ is None: continue
            if rule.cooldown and last firing of (skill, i) within cooldown: continue
            if not ledger.claim(skill.id, i, occ.key): continue     # INSERT OR IGNORE; at-most-once
            actions = catalog.allowed(skill.allow)
            for step in rule.steps:
              ActionStep -> outcome = await actions.do(step.action, **resolve(step.args, occ, tick))
              HookStep   -> await hook.<fn>(HookContext(portal, actions, skill, occ)), with a 10 s timeout
            ledger.record_outcomes(...); bus.publish(SkillFired(...))
          One failing step records its error and the rule continues with the next step.
        """
        raise NotImplementedError


MAX_GAP = timedelta(minutes=5)


@dataclass(frozen=True)
class HookContext:
    """What a hook gets. Read the notes through `portal`; act only through `actions` (the skill's allow list)."""

    portal: Portal
    actions: Actions
    skill: Skill
    occurrence: Occurrence


# --- Generator: chat request -> draft skill file.


@dataclass(frozen=True)
class Draft:
    entry: SkillEntry  # status needs_review (or invalid if two attempts failed; the user can still edit it)
    summary: str  # one plain-language line: "Shows a 1-minute stand-up reminder when a meeting ends."


async def draft_skill(request: str, *, gateway: ModelGateway, book: SkillBook, catalog: Catalog) -> Draft:
    """Priority.INTERACTIVE, effort 'low'.

    TODO:
      system = FORMAT_GUIDE (this module's docstring grammar) + catalog.describe_for_prompt()
               + the three example skills verbatim (builtin dir) + "no hook unless the grammar can't express it"
      out = await gateway.call(INTERACTIVE, Prompt(system, request, output=_DraftOut))   # {id, text, summary}
      parsed = parse_skill(out.text, catalog=catalog, hook_text=None)
      if errors: one retry with the errors listed; if still errors, save anyway as invalid
      entry = book.write(None, text with `created_by: chat` and `request:` set)
      return Draft(entry, out.summary)
    """
    raise NotImplementedError
