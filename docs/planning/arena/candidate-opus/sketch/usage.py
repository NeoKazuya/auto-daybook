"""Caller's view, written before the types. Every other sketch file is derived from these call sites.

The core idea these call sites show: a skill never calls the model. A skill declares
*questions* ("signals"). Once per tick, the mind asks the one shared model every
enabled skill's questions in a single structured call, next to the rollup it was
doing anyway. Skills react to the answers with a 4-form trigger grammar and run
named actions. Model load is fixed no matter how many skills are enabled.

    pip install -e . && daybook run          # one process: capture, tick, skills, web on :8765
    python -m daybook.shell                  # spawned by `daybook run`: overlay + tray (Qt)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .act import Catalog, OverlayRequest
from .chat import Answer, Chat, DraftReady
from .journal import Journal, Portal, TimeRange
from .mind import Mind
from .skills import HookContext, SkillBook, evaluate, parse_skill
from .skills import Becomes, HeldFor, Rule
from .journal import SignalId, SignalReading, SkillId, TickId


# --- 1. Composition root (daybook/app.py). The whole runtime is three loops in one asyncio process.


async def main_sketch(journal: Journal, sense, mind: Mind, web) -> None:
    import asyncio

    await asyncio.gather(sense.run(), mind.run(), web.serve())


# --- 2. Recall, from the web chat route. One call in, a domain answer out.


async def chat_route(chat: Chat, message: str) -> dict:
    reply = await chat.respond(message)  # routes to recall or to the skill generator
    match reply:
        case Answer(text=text, cites=cites):
            return {"kind": "answer", "text": text, "cites": list(cites)}
        case DraftReady(entry=entry, summary=summary):
            # The UI shows the file text + permissions and an "Enable" button that posts the hash back.
            return {"kind": "draft", "skill": entry.id, "hash": entry.source_hash, "summary": summary}
        case _:
            return {"kind": "text", "text": reply.text}


# --- 3. The user reviews a chat-written skill and enables it. Approval is bound to the exact bytes.


def enable_route(book: SkillBook, skill_id: SkillId, source_hash: str) -> None:
    book.approve(skill_id, source_hash)  # raises StaleApproval if the file changed since review


# --- 4. Reading the notes: the portal is the one read interface for UI, recall, and hooks.


def timeline_route(portal: Portal) -> list[dict]:
    out = []
    for seg in portal.segments(TimeRange.today()):
        # started_at / ended_at / active are derived from member observations, never stored.
        out.append({"title": seg.title, "intent": seg.intent, "kind": seg.kind.value,
                    "from": seg.started_at, "to": seg.ended_at, "active_min": seg.active / timedelta(minutes=1),
                    "authored": seg.authored})
    return out


def how_long_on(portal: Portal) -> str:
    rows = portal.totals(TimeRange.today(), by="title")  # code does the arithmetic, never the model
    return "\n".join(f"{r.label}: {r.active}" for r in rows)


# --- 5. An optional Python hook. Hooks are a *step* in a rule, so they run through the same trigger path.
#        $DAYBOOK_HOME/skills/focus-check.hook.py, called by a rule step `- hook: run`


async def run(ctx: HookContext) -> None:
    seg = ctx.portal.now().segment
    if seg and seg.active > timedelta(hours=2):
        # ctx.actions only exposes what the skill file's `allow:` lists; anything else raises NotAllowed.
        await ctx.actions.do("overlay.show", title="Two hours on one thing",
                             body=f"Still on '{seg.title}'. Worth a check-in?", minutes=1)


# --- 6. Trigger evaluation is a pure function over signal history. Table-testable without a model.


def test_break_fires_once_per_streak() -> None:
    sig = SignalId(SkillId("break"), "focused")
    t0 = datetime(2026, 10, 11, 9, 0)
    history = [SignalReading(sig, True, t0 + timedelta(seconds=90 * i), TickId(i)) for i in range(40)]
    rule = Rule(trigger=HeldFor(sig, True, timedelta(minutes=50)), steps=(), cooldown=None)
    occ = evaluate(rule, history, max_gap=timedelta(minutes=5))
    assert occ is not None and occ.key == "streak@0"  # key = streak start, so a re-run can't double-fire


def test_becomes_needs_a_recent_prior_value() -> None:
    sig = SignalId(SkillId("stand-after-meeting"), "in_meeting")
    t0 = datetime(2026, 10, 11, 9, 0)
    only_false = [SignalReading(sig, False, t0, TickId(1))]
    rule = Rule(trigger=Becomes(sig, False), steps=(), cooldown=None)
    assert evaluate(rule, only_false, max_gap=timedelta(minutes=5)) is None  # startup is not an edge


# --- 7. The generator's output goes through the same parser the loader uses. One parser, one format.


def test_example_skills_parse(catalog: Catalog) -> None:
    for name in ("music", "break", "stand-after-meeting"):
        text = open(f"skills/{name}.skill.md").read()
        skill = parse_skill(text, catalog=catalog, hook_text=None)
        assert not isinstance(skill, list), skill  # a list means SkillError items


# --- 8. Any code that wants a full-screen overlay goes through the catalog, like skills do.


async def demo_overlay(catalog: Catalog) -> None:
    actions = catalog.allowed(frozenset({"overlay.show"}))
    await actions.do("overlay.show", **OverlayRequest(title="Hello", body="It works", minutes=1).as_args())
