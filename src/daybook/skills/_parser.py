"""One parser shared by files, mock/live editors, and generated drafts."""

from __future__ import annotations

import ast
import re

import yaml

from daybook.act import Catalog
from daybook.journal import SignalId

from . import READABLE, Rule, Signal, Skill, SkillError, SkillSource, Step, Trigger

UPCOMING_NEXT = "$upcoming.next"


def duration(text: object, *, zero: bool = False) -> float:
    match = re.fullmatch(r"(0|[1-9][0-9]*)([smh])", str(text))
    if not match:
        raise ValueError("expected a duration such as 30s, 2m, or 1h")
    result = int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
    if not zero and result == 0:
        raise ValueError("duration must be positive")
    return result


def parse(
    text: str, *, source: SkillSource, catalog: Catalog, hook_text: str | None
) -> Skill | list[SkillError]:
    errors: list[SkillError] = []

    def error(location: str, message: str) -> None:
        errors.append(SkillError(location, message))

    if not text.startswith("---\n") or "\n---" not in text[4:]:
        return [
            SkillError("frontmatter", "expected YAML front matter between --- lines")
        ]
    front, body = text[4:].split("\n---", 1)
    try:
        data = yaml.safe_load(front)
    except yaml.YAMLError:
        return [SkillError("frontmatter", "invalid YAML")]
    if not isinstance(data, dict):
        return [SkillError("frontmatter", "expected a mapping")]
    allowed = {
        "id",
        "name",
        "signals",
        "rules",
        "allow",
        "reads",
        "created_by",
        "request",
    }
    for field in data.keys() - allowed:
        error(str(field), "unknown field")
    id = data.get("id")
    if (
        not isinstance(id, str)
        or not re.fullmatch(r"[a-z0-9-]{1,40}", id)
        or source.path.name != f"{id}.skill.md"
    ):
        error("id", "must match the file name and [a-z0-9-]{1,40}")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        error("name", "a display name is required")
    permissions = data.get("allow")
    if not isinstance(permissions, list) or any(
        not isinstance(a, str) or a not in catalog.specs for a in permissions
    ):
        error("allow", "expected a list of catalog action names")
        permissions = []
    reads = data.get("reads", [])
    if (
        not isinstance(reads, list)
        or any(not isinstance(r, str) or r not in READABLE for r in reads)
        or len(set(reads)) != len(reads)
    ):
        error("reads", "expected a list of: " + ", ".join(READABLE))
        reads = []
    reads = tuple(reads)
    raw_signals = data.get("signals")
    if not isinstance(raw_signals, dict) or not 1 <= len(raw_signals) <= 4:
        error("signals", "expected 1 to 4 signals")
        raw_signals = {}
    signals: list[Signal] = []
    for name, raw in raw_signals.items():
        loc = f"signals.{name}"
        if not isinstance(name, str) or not re.fullmatch(
            r"[a-zA-Z_][a-zA-Z0-9_]*", name
        ):
            error(loc, "invalid signal name")
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("ask"), str):
            error(loc, "expected ask and type")
            continue
        if name in reads:
            error(loc, "signal name is reserved by reads")
            continue
        if raw.keys() - {"ask", "type", "describe"}:
            error(loc, "unknown signal field")
        type_ = raw.get("type")
        if "describe" in raw:
            descriptions = raw["describe"]
            if (
                not isinstance(descriptions, dict)
                or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in descriptions.items()
                )
                or not isinstance(type_, dict)
                or not isinstance(type_.get("enum"), list)
                or any(k not in type_["enum"] for k in descriptions)
            ):
                error(
                    loc + ".describe",
                    "expected a mapping from declared enum values to text",
                )
                continue
        sid = SignalId(str(id), name)
        if type_ == "bool":
            signals.append(Signal(sid, raw["ask"], "bool", reads=reads))
        elif type_ == "music.mood":
            signals.append(
                Signal(
                    sid,
                    raw["ask"],
                    "music.mood",
                    tuple(catalog.moods),
                    catalog.moods,
                    reads=reads,
                )
            )
        elif (
            isinstance(type_, dict)
            and set(type_) == {"enum"}
            and isinstance(type_["enum"], list)
            and type_["enum"]
            and all(isinstance(v, str) for v in type_["enum"])
        ):
            signals.append(
                Signal(
                    sid,
                    raw["ask"],
                    "enum",
                    tuple(type_["enum"]),
                    raw.get("describe"),
                    reads=reads,
                )
            )
        elif (
            isinstance(type_, dict)
            and set(type_) == {"number"}
            and isinstance(type_["number"], list)
            and len(type_["number"]) == 2
            and all(type(v) in (int, float) for v in type_["number"])
            and type_["number"][0] <= type_["number"][1]
        ):
            signals.append(
                Signal(
                    sid,
                    raw["ask"],
                    "number",
                    minimum=type_["number"][0],
                    maximum=type_["number"][1],
                    reads=reads,
                )
            )
        else:
            error(loc + ".type", "unknown or invalid signal type")
    by_name = {s.id.name: s for s in signals}
    rules: list[Rule] = []
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        error("rules", "at least one rule is required")
        raw_rules = []
    for index, raw in enumerate(raw_rules):
        loc = f"rules[{index}]"
        if not isinstance(raw, dict):
            error(loc, "expected a rule mapping")
            continue
        if raw.keys() - {"when", "cooldown", "do"}:
            error(loc, "unknown rule field")
        when = raw.get("when", "")
        trigger: Trigger | None = None
        try:
            if not isinstance(when, str):
                raise ValueError("expected a trigger string")
            if match := re.fullmatch(r"every (\d+[smh])", when):
                trigger = Trigger("every", seconds=duration(match[1]))
            elif match := re.fullmatch(r"(\w+) updates", when):
                trigger = Trigger("updates", signal=match[1])
            elif match := re.fullmatch(r"(\w+) becomes (.+)", when):
                trigger = Trigger(
                    "becomes", signal=match[1], value=yaml.safe_load(match[2])
                )
            elif match := re.fullmatch(r"(\w+) is (.+) for (\d+[smh])", when):
                trigger = Trigger(
                    "is",
                    signal=match[1],
                    value=yaml.safe_load(match[2]),
                    seconds=duration(match[3]),
                )
            else:
                raise ValueError("expected updates, becomes, is … for, or every")
            if trigger.signal and trigger.signal not in by_name:
                raise ValueError("trigger references an undeclared signal")
            if trigger.signal and trigger.form in ("becomes", "is"):
                signal = by_name[trigger.signal]
                value = trigger.value
                valid = (
                    (signal.type == "bool" and type(value) is bool)
                    or (
                        signal.type in ("enum", "music.mood") and value in signal.values
                    )
                    or (
                        signal.type == "number"
                        and (
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                        )
                        and signal.minimum is not None
                        and signal.maximum is not None
                        and signal.minimum <= value <= signal.maximum
                    )
                )
                if not valid:
                    raise ValueError("trigger value does not match signal type")
            cooldown = duration(raw["cooldown"]) if "cooldown" in raw else 0
        except (ValueError, yaml.YAMLError) as exc:
            error(loc + ".when", str(exc))
            continue
        steps: list[Step] = []
        raw_steps = raw.get("do")
        if not isinstance(raw_steps, list) or not raw_steps:
            error(loc + ".do", "at least one action is required")
            raw_steps = []
        for n, step in enumerate(raw_steps):
            at = f"{loc}.do[{n}]"
            if not isinstance(step, dict) or len(step) != 1:
                error(at, "expected one action per step")
                continue
            action, args = next(iter(step.items()))
            if action == "hook":
                if source.origin != "builtin":
                    error(at, "hooks are allowed only in built-in skills")
                else:
                    try:
                        functions = {
                            n.name
                            for n in ast.walk(ast.parse(hook_text or ""))
                            if isinstance(n, ast.AsyncFunctionDef)
                        }
                        if args not in functions:
                            error(at, "missing async hook function")
                    except (SyntaxError, TypeError):
                        error(at, "invalid hook file")
                steps.append(Step("hook", {"function": args}))
                continue
            if action not in permissions:
                error(at, "action is not in allow")
            spec = catalog.specs.get(action)
            if spec is None:
                error(at, "unknown action")
                continue
            if not isinstance(args, dict):
                error(at, "expected action arguments")
                continue
            for param in args.keys() - spec.parameters.keys():
                error(at + "." + str(param), "unknown argument")
            for name, param in spec.parameters.items():
                if name not in args:
                    if param.required:
                        error(at + "." + name, "required argument")
                    continue
                value = args[name]
                if isinstance(value, str) and UPCOMING_NEXT in value:
                    if "upcoming" not in reads:
                        error(at + "." + name, "$upcoming.next needs reads: [upcoming]")
                    elif param.type != "str":
                        error(at + "." + name, "$upcoming.next is text")
                    value = value.replace(UPCOMING_NEXT, "")
                references = (
                    re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)(\.for)?", value)
                    if isinstance(value, str)
                    else []
                )
                if references:
                    for ref, held in references:
                        if ref not in by_name:
                            error(at + "." + name, "undeclared signal reference")
                            continue
                        signal = by_name[ref]
                        if param.type == "str":
                            continue
                        exact = value == "$" + ref and not held
                        fits = exact and (
                            (param.type == "number" and signal.type == "number")
                            or (
                                param.type in ("music.mood", "enum")
                                and signal.type in ("music.mood", "enum")
                                and bool(signal.values)
                                and set(signal.values) <= set(param.values)
                            )
                        )
                        if not fits:
                            error(
                                at + "." + name,
                                "signal reference does not fit action parameter",
                            )
                    continue
                valid = True
                if param.type == "str":
                    valid = isinstance(value, str)
                elif param.type == "number":
                    valid = (
                        isinstance(value, (int, float)) and not isinstance(value, bool)
                    ) and value > 0
                elif param.type in ("enum", "music.mood"):
                    valid = value in param.values
                elif param.type == "duration":
                    try:
                        duration(value, zero=True)
                    except ValueError:
                        valid = False
                if not valid:
                    error(at + "." + name, "value does not match parameter type")
            steps.append(Step(action, args))
        if trigger:
            rules.append(Rule(when, trigger, tuple(steps), cooldown))
    created_by = data.get("created_by", source.origin)
    if created_by not in ("builtin", "user", "chat"):
        error("created_by", "expected builtin, user, or chat")
    if "request" in data and not isinstance(data["request"], str):
        error("request", "expected text")
    if errors:
        return errors
    return Skill(
        str(id),
        data["name"],
        body.strip(),
        tuple(signals),
        tuple(rules),
        tuple(permissions),
        created_by,
        data.get("request"),
        reads,
    )
