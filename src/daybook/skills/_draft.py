"""Chat drafts pass the same parser and never load a generated hook."""

from __future__ import annotations

import json
import re
import uuid
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from daybook.model import ModelGateway, Priority, Prompt

from . import Skill, SkillBook, SkillEntry, UnsupportedSkill

GUIDE = """A skill is YAML front matter between --- lines followed by a plain Markdown goal.
Required fields: id ([a-z0-9-]{1,40}), name, signals (1–4), rules (at least one), allow.
Signals map names to {ask: a concrete question about the present, type: bool | music.mood | {enum: [short, values]} | {number: [min, max]}}. Optional enum describe maps each value to its meaning.
Rules: {when: trigger, cooldown: positive duration (optional), do: [one-key action argument mappings]}. Each rule tests one value of one signal; independent rules do not combine as AND. For a condition with multiple requirements, exclusions, or numeric comparisons, include ALL clauses in one concrete present boolean question and trigger that composite signal. A numeric signal's updates trigger does not test a threshold. Preserve every requested condition when correcting a draft.
Exactly four triggers: '<signal> updates' fires on EVERY known reading (only for continually refreshed actions); '<signal> becomes <value>' fires on the transition from another known value; '<signal> is <value> for <duration>' fires after that value persists; 'every <duration>' is a wall-clock timer. Use becomes true for a newly detected reminder condition, becomes false for activity completion, or a positive held duration when requested; never use updates for a reminder. Updates is only for actions intentionally refreshed on every known reading. Binary present activities use type bool and true/false values.
Durations are positive integer+s/m/h; min_dwell may be 0s. Every action must be listed in allow and match catalog parameters. Requested reminders use overlay.show by default; use notify.send when a desktop notification is explicitly requested. For short, casual reminders use layout=bubble and conversational body text; use fullscreen when a fullscreen reminder is requested. Preserve catalog defaults: omit optional action parameters and rule cooldown unless requested or necessary; a new reminder without a requested duration must omit minutes and cooldown. Explicit built-in timings serve their original goals; do not copy them into new reminders. Arguments may use $signal or $signal.for, including text interpolation.
Ask about the user's situation now. Let triggers handle time. For after/end/finish requests, ask whether the activity is happening now and trigger becomes false (or an enum transition to inactive); never ask whether it recently ended. Do not use Python, hook steps, extra fields, or unlisted actions. Becomes requires a previous known reading, so meeting reminders fire after a meeting rather than on startup.
Available evidence is sampled screen observations: visible apps, windows, text, and supported inferences about the present task. Actions are only those in the supplied catalog. There is no microphone/audio input, voice-tone detection, hidden app state or private app APIs, keyboard hooks, send interception, or access to a user's unexpressed feelings. Do not invent these capabilities or silently replace an unsupported condition. Ordinary visible-condition reminders are supported even though sampling cannot guarantee noticing a quick action or block it. Do not require literal keywords when visible activity supports the requested condition. If the requested evidence or action is unavailable, return no draft and a concrete, concise explanation of the limitation.
"""


Scalar = str | float | bool


class DraftMeaning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str
    meaning: str


class DraftSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    ask: str
    type: Literal["bool", "music.mood", "enum", "number"]
    values: list[str] | None = None
    bounds: list[float] | None = Field(default=None, min_length=2, max_length=2)
    describe: list[DraftMeaning] | None = None


class DraftParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: Scalar


class DraftStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    params: list[DraftParameter]


class DraftRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when: str
    cooldown: str | None = None
    steps: list[DraftStep] = Field(min_length=1)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    goal: str
    signals: list[DraftSignal] = Field(min_length=1, max_length=4)
    rules: list[DraftRule] = Field(min_length=1)
    allow: list[str] = Field(min_length=1)
    summary: str = Field(max_length=300)


class DraftReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: Draft | None
    explanation: str | None = Field(max_length=600)

    @model_validator(mode="after")
    def one_outcome(self) -> Self:
        if self.draft is not None:
            if self.explanation is not None:
                raise ValueError("a supported draft must have explanation=null")
        elif self.explanation is None or not self.explanation.strip():
            raise ValueError("an unsupported request needs a concrete explanation")
        return self


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
            "do": [
                {step.action: {p.name: p.value for p in step.params}}
                for step in rule.steps
            ],
        }
        if rule.cooldown is not None:
            value["cooldown"] = rule.cooldown
        rules.append(value)
    data = {
        "id": id,
        "name": draft.name,
        "signals": {
            signal.name: {
                "ask": signal.ask,
                "type": {"enum": signal.values or []}
                if signal.type == "enum"
                else {"number": signal.bounds or []}
                if signal.type == "number"
                else signal.type,
                **(
                    {"describe": {d.value: d.meaning for d in signal.describe}}
                    if signal.describe is not None
                    else {}
                ),
            }
            for signal in draft.signals
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
    examples = []
    for entry in book.entries():
        if (
            entry.source.origin != "builtin"
            or not isinstance(entry.skill, Skill)
            or any(
                step.action == "hook"
                for rule in entry.skill.rules
                for step in rule.steps
            )
        ):
            continue
        front, goal = entry.text.split("---", 2)[1:]
        data = yaml.safe_load(front)
        data.pop("created_by", None)
        data.pop("request", None)
        data["signals"] = [
            {
                "name": name,
                "ask": signal["ask"],
                "type": signal["type"]
                if isinstance(signal["type"], str)
                else next(iter(signal["type"])),
                **(
                    {"values": signal["type"]["enum"]}
                    if isinstance(signal["type"], dict) and "enum" in signal["type"]
                    else {"bounds": signal["type"]["number"]}
                    if isinstance(signal["type"], dict)
                    else {}
                ),
                **(
                    {
                        "describe": [
                            {"value": value, "meaning": meaning}
                            for value, meaning in signal["describe"].items()
                        ]
                    }
                    if "describe" in signal
                    else {}
                ),
            }
            for name, signal in data["signals"].items()
        ]
        for rule in data["rules"]:
            rule["steps"] = [
                {
                    "action": action,
                    "params": [
                        {"name": name, "value": value}
                        for name, value in params.items()
                        if book._book.catalog.specs[action].parameters[name].required
                    ],
                }
                for step in rule.pop("do")
                for action, params in step.items()
            ]
        examples.append(
            json.dumps(
                {
                    "draft": {**data, "goal": goal.strip(), "summary": data["name"]},
                    "explanation": None,
                }
            )
        )
    user = f"Return exactly one outcome: {{draft: a supported plan instance, explanation: null}} or {{draft: null, explanation: a concrete capability limitation}}. The code renders a supported plan; never return schema definitions ($defs/properties/type) or a YAML/text field. Plan signals are a list of name/ask/type objects; enum uses values, number uses two bounds, optional describe is a list of value/meaning objects. Rule steps use action and params; params is a list of name/value objects, never a map. The renderer converts these lists to the file format mappings.\nFormat guide:\n{GUIDE}\nAction catalog:\n{book._book.catalog.describe_for_prompt()}\nBuilt-in supported reply instances:\n{chr(10).join(examples)}\nUser request:\n{request}\nFor a reminder with no requested duration, include title/body and the appropriate layout; omit minutes and rule cooldown unless requested. Use $signal.for only for a held-value trigger, not a becomes edge. Return the reply object with no schema fields."
    feedback = ""
    summary = "Draft skill"
    text = ""
    id = "draft-" + uuid.uuid4().hex[:8]
    for _ in range(2):
        result = await gateway.call(
            Priority.INTERACTIVE,
            Prompt(
                system="Assess the request against the supplied evidence capabilities and action catalog. Return a supported declarative draft or a concrete unsupported explanation in the reply object, never both. Never fabricate missing capabilities. Code renders supported drafts; never return schema definitions. In each rule, steps are action+params objects, and signals and params are lists of named objects. Use the fewest questions and rules needed. Reason briefly, then answer. No hook or executable code.",
                user=user + feedback,
                output=DraftReply,
                effort="none",
                max_tokens=2000,
            ),
        )
        if result is None:
            raise RuntimeError("draft unavailable")
        if result.draft is None:
            assert result.explanation is not None
            raise UnsupportedSkill(" ".join(result.explanation.split()))
        draft = result.draft
        id, text = render(draft, request)
        # Never overwrite a built-in or an existing user skill from a generated ID.
        if any(entry.id == id for entry in book.entries()):
            id, text = render(
                draft,
                request,
                id_override=f"{id[:29]}-{uuid.uuid4().hex[:8]}",
            )
        summary = " ".join(draft.summary.split())
        parsed = book.parse(id, text)
        if isinstance(parsed, Skill):
            break
        feedback = (
            "\nThe draft failed validation. Return a corrected complete reply. Preserve every requested condition and intended action; do not replace a conditional reminder with updates. Errors:\n"
            + "\n".join(f"{e.location}: {e.message}" for e in parsed)
        )
    return book.save(id, text), summary
