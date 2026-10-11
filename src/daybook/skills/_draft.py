"""Chat drafts pass the same parser and never load a generated hook."""

from __future__ import annotations

import re
import uuid

import yaml
from pydantic import BaseModel, ConfigDict, Field

from daybook.model import ModelGateway, Priority, Prompt

from . import Skill, SkillBook, SkillEntry

GUIDE = """A skill is YAML front matter between --- lines followed by a plain Markdown goal.
Required fields: id ([a-z0-9-]{1,40}), name, signals (1–4), rules (at least one), allow.
Signals map names to {ask: a concrete question about the present, type: bool | music.mood | {enum: [short, values]} | {number: [min, max]}}. Optional enum describe maps each value to its meaning.
Rules: {when: trigger, cooldown: positive duration (optional), do: [one-key action argument mappings]}.
Exactly four triggers: '<signal> updates', '<signal> becomes <value>', '<signal> is <value> for <duration>', 'every <duration>'.
Durations are positive integer+s/m/h; min_dwell may be 0s. Every action must be listed in allow and match catalog parameters. Arguments may use $signal or $signal.for, including text interpolation.
Ask about the user's situation now. Let triggers handle time. Do not use Python, hook steps, extra fields, or unlisted actions. Becomes requires a previous known reading, so meeting reminders fire after a meeting rather than on startup.
"""


Scalar = str | float | bool


class DraftSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ask: str
    type: str | dict[str, list[str | float]]
    describe: dict[str, str] | None = None


class DraftStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    params: dict[str, Scalar]


class DraftRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when: str
    cooldown: str | None = None
    steps: list[DraftStep]


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    goal: str
    signals: dict[str, DraftSignal]
    rules: list[DraftRule]
    allow: list[str]
    summary: str = Field(max_length=300)


def render(
    draft: Draft, request: str, *, id_override: str | None = None
) -> tuple[str, str]:
    id = id_override or draft.id
    if not re.fullmatch(r"[a-z0-9-]{1,40}", id):
        id = "draft-" + uuid.uuid4().hex[:8]
    rules = []
    for rule in draft.rules:
        value = {
            "when": rule.when,
            "do": [{step.action: step.params} for step in rule.steps],
        }
        if rule.cooldown is not None:
            value["cooldown"] = rule.cooldown
        rules.append(value)
    data = {
        "id": id,
        "name": draft.name,
        "signals": {
            name: signal.model_dump(exclude_none=True)
            for name, signal in draft.signals.items()
        },
        "rules": rules,
        "allow": draft.allow,
        "created_by": "chat",
        "request": request,
    }
    return id, "---\n" + yaml.safe_dump(
        data, sort_keys=False
    ) + "---\n" + draft.goal.strip() + "\n"


async def generate(
    request: str, *, book: SkillBook, gateway: ModelGateway
) -> tuple[SkillEntry, str]:
    examples = "\n\n".join(
        path.read_text() for path in sorted(book._book.builtin.glob("*.skill.md"))
    )
    user = f"Format guide:\n{GUIDE}\nAction catalog:\n{book._book.catalog.describe_for_prompt()}\nBuilt-in examples:\n{examples}\nUser request:\n{request}"
    feedback = ""
    summary = "Draft skill"
    text = ""
    id = "draft-" + uuid.uuid4().hex[:8]
    for _ in range(2):
        result = await gateway.call(
            Priority.INTERACTIVE,
            Prompt(
                system="Choose a small declarative skill plan using the supplied grammar/catalog. Return the structured plan only; code will render YAML. In each rule, steps are action+params objects. Use the fewest questions and rules needed. Reason briefly, then answer. No hook or executable code.",
                user=user + feedback,
                output=Draft,
                effort="low",
                max_tokens=2000,
            ),
        )
        if result is None:
            raise RuntimeError("draft unavailable")
        id, text = render(result, request)
        # Never overwrite a built-in or an existing user skill from a generated ID.
        if any(entry.id == id for entry in book.entries()):
            id, text = render(
                result,
                request,
                id_override=f"{id[:29]}-{uuid.uuid4().hex[:8]}",
            )
        summary = " ".join(result.summary.split())
        parsed = book.parse(id, text)
        if isinstance(parsed, Skill):
            break
        feedback = (
            "\nThe draft failed validation. Return a corrected complete skill. Errors:\n"
            + "\n".join(f"{e.location}: {e.message}" for e in parsed)
        )
    return book.save(id, text), summary
