"""daybook.chat — the chat box: recall answers and skill requests through one entry point.

    reply = await chat.respond("what did I do in the last hour?")       # -> Answer
    reply = await chat.respond("remind me to stand up every time I finish a meeting")   # -> DraftReady

Routing is one short INTERACTIVE call (~300 tokens, ~1 s): {"intent": "recall" | "make_skill" | "other"}.
A leading "/skill " or "/ask " in the message skips the router (demo safety net).

Recall:
    1. resolve the window in code from the question with a small rule table ("today", "last hour", "this
       morning", "yesterday"; default: today), not by the model
    2. context = portal.context(window, budget_tokens=8000, detail="observations" if window <= 2h else "segments")
       + portal.totals(window, by="title") and by="kind", rendered as a table   # code does the math
       + portal.search(keywords) hits if the question names a thing ("the auth bug")
    3. one INTERACTIVE call, effort "none": answer in 1–5 sentences, cite segment ids, say "I don't have notes
       for that" when the context lacks it, and never count durations itself (use the table)
"""

from __future__ import annotations

from dataclasses import dataclass

from .act import Catalog
from .journal import Portal, SegmentId, TimeRange
from .model import ModelGateway
from .skills import SkillBook, SkillEntry


@dataclass(frozen=True)
class Answer:
    text: str
    cites: tuple[SegmentId, ...]  # the UI links these to the timeline
    window: TimeRange


@dataclass(frozen=True)
class DraftReady:
    entry: SkillEntry
    summary: str


@dataclass(frozen=True)
class Plain:
    text: str  # "other" intent, or model offline: "The model is busy, try again in a moment."


Reply = Answer | DraftReady | Plain


class Chat:
    def __init__(self, portal: Portal, gateway: ModelGateway, book: SkillBook, catalog: Catalog) -> None:
        raise NotImplementedError

    async def respond(self, message: str) -> Reply:
        raise NotImplementedError


def resolve_window(question: str, now: object) -> TimeRange:
    """Pure rule table. Unit-tested with the demo's exact questions."""
    raise NotImplementedError


async def recall(question: str, *, portal: Portal, gateway: ModelGateway) -> Answer:
    raise NotImplementedError
