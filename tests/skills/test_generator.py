import json
from pathlib import Path

import pytest
import yaml

from daybook.act import build_catalog
from daybook.events import Bus
from daybook.journal import Portal, Situation
from daybook.model import Priority, ScriptedGateway
from daybook.skills import NeedsReview, SkillBook, UnsupportedSkill, draft_skill

TEXT = """---
id: stand-after-meeting
name: Stand after meeting
signals:
  in_meeting:
    ask: Is the user in a video meeting right now?
    type: bool
rules:
  - when: in_meeting becomes false
    cooldown: 10m
    do:
      - overlay.show: {title: Meeting over, body: Stand up, minutes: 1}
allow: [overlay.show]
---
Remind me to stand after a meeting.
"""


def payload(text, summary):
    front, goal = text[4:].split("\n---", 1)
    data = yaml.safe_load(front)
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
        rule["steps"] = []
        for step in rule.pop("do"):
            action, params = next(iter(step.items()))
            rule["steps"].append(
                {
                    "action": action,
                    "params": [
                        {"name": name, "value": value}
                        for name, value in (
                            params if isinstance(params, dict) else {"value": params}
                        ).items()
                    ],
                }
            )
    return json.dumps(
        {
            "draft": {**data, "goal": goal.strip(), "summary": summary},
            "explanation": None,
        }
    )


class MemoryPortal(Portal):
    def __init__(self):
        pass

    def now(self):
        return Situation("running", None, None, ())


def book(tmp_path):
    return SkillBook(
        tmp_path,
        Path(__file__).parents[2] / "skills/builtin",
        tmp_path / "db",
        MemoryPortal(),
        build_catalog({"focus": "work", "calm": "read", "break": "rest"}),
        Bus(),
        clear_readings=lambda skill: None,
    )


async def test_valid_draft_has_provenance_and_needs_review(tmp_path):
    skills = book(tmp_path)
    request = "remind me to stand up every time I finish a meeting"
    gateway = ScriptedGateway([payload(TEXT, "Stand after meetings")])
    entry, summary = await draft_skill(request, book=skills, gateway=gateway)
    assert entry.status == "needs_review" and not entry.enabled
    assert (
        entry.skill
        and entry.skill.created_by == "chat"
        and entry.skill.request == request
    )
    assert entry.skill.rules[0].trigger.form == "becomes"
    assert entry.skill.rules[0].trigger.value is False
    assert summary == "Stand after meetings"
    assert gateway.priorities == [Priority.INTERACTIVE]
    assert gateway.prompts[0].effort == "low"
    assert gateway.prompts[0].max_tokens == 2000
    assert "Mood music" in gateway.prompts[0].user
    assert "desktop.focus" in gateway.prompts[0].user
    with pytest.raises(NeedsReview):
        skills.enable(entry.id)
    approved = skills.approve(entry.id, entry.hash)
    assert approved.status == "active" and approved.hash == entry.hash
    assert approved.text == entry.text and approved.skill == entry.skill


async def test_parse_retry_then_invalid_is_saved(tmp_path):
    skills = book(tmp_path)
    bad = TEXT.replace("in_meeting becomes false", "unsupported trigger")
    gateway = ScriptedGateway(
        [
            payload(bad, "Draft"),
            payload(bad, "Still draft"),
        ]
    )
    entry, _ = await draft_skill("remind", book=skills, gateway=gateway)
    assert entry.status == "invalid" and entry.errors
    assert len(gateway.prompts) == 2
    assert "rules[0].when" in gateway.prompts[1].user
    assert all(prompt.effort == "low" for prompt in gateway.prompts)
    assert entry.source.path.exists()


async def test_generated_hook_rejected_and_existing_skill_not_overwritten(tmp_path):
    skills = book(tmp_path)
    skills.save("stand-after-meeting", TEXT)
    unsafe = TEXT.replace(
        "      - overlay.show: {title: Meeting over, body: Stand up, minutes: 1}",
        "      - hook: attack",
    )
    gateway = ScriptedGateway(
        [
            payload(unsafe, "Draft"),
            payload(TEXT, "Fixed"),
        ]
    )
    entry, _ = await draft_skill("remind", book=skills, gateway=gateway)
    assert entry.id != "stand-after-meeting" and entry.status == "needs_review"
    existing = next(e for e in skills.entries() if e.id == "stand-after-meeting")
    assert existing.text == TEXT


async def test_quoted_yaml_id_collision_never_overwrites(tmp_path):
    skills = book(tmp_path)
    existing_text = TEXT.replace("id: stand-after-meeting", "id: 'yes'")
    skills.save("yes", existing_text)
    gateway = ScriptedGateway([payload(existing_text, "New draft")])
    entry, _ = await draft_skill("remind", book=skills, gateway=gateway)
    assert entry.id != "yes"
    assert next(e for e in skills.entries() if e.id == "yes").text == existing_text


async def test_builtin_hook_and_invalid_file_do_not_break_declarative_generation(
    tmp_path,
):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "invalid.skill.md").write_text("no front matter")
    hook = TEXT.replace("id: stand-after-meeting", "id: developer-hook").replace(
        "      - overlay.show: {title: Meeting over, body: Stand up, minutes: 1}",
        "      - hook: run",
    )
    (builtin / "developer-hook.skill.md").write_text(hook)
    (builtin / "developer-hook.hook.py").write_text("async def run(ctx):\n    pass\n")
    skills = SkillBook(
        tmp_path / "home",
        builtin,
        tmp_path / "db",
        MemoryPortal(),
        build_catalog({"focus": "work"}),
        Bus(),
        clear_readings=lambda skill: None,
    )
    gateway = ScriptedGateway([payload(TEXT, "Stand after meetings")])
    entry, _ = await draft_skill("remind", book=skills, gateway=gateway)
    assert entry.status == "needs_review"
    assert "developer-hook" not in gateway.prompts[0].user


async def test_openai_draft_schema_does_not_erase_named_questions_or_arguments(
    tmp_path,
):
    from daybook.model import ModelConfig
    from daybook.model._gateway import request_body

    skills = book(tmp_path)
    gateway = ScriptedGateway([payload(TEXT, "Stand after meetings")])
    entry, _ = await draft_skill("remind", book=skills, gateway=gateway)
    assert entry.status == "needs_review"
    schema = request_body(ModelConfig(dialect="openai"), gateway.prompts[0])[
        "response_format"
    ]["json_schema"]["schema"]

    def check_objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                # Strict output closes objects: names must remain representable
                # through declared properties instead of erased map keys.
                assert node.get("properties"), node
                assert node["additionalProperties"] is False
            for value in node.values():
                check_objects(value)
        elif isinstance(node, list):
            for value in node:
                check_objects(value)

    check_objects(schema)


async def test_list_plan_renders_enum_descriptions_and_numeric_bounds(tmp_path):
    skills = book(tmp_path)
    text = TEXT.replace(
        "    type: bool",
        "    type: {enum: [active, inactive]}\n"
        "    describe: {active: Meeting in progress, inactive: Other visible activity}\n"
        "  level:\n"
        "    ask: What is the current effort level?\n"
        "    type: {number: [0, 10]}",
    ).replace("in_meeting becomes false", "in_meeting becomes inactive")
    gateway = ScriptedGateway([payload(text, "Typed draft")])
    entry, _ = await draft_skill("remind", book=skills, gateway=gateway)
    assert entry.status == "needs_review" and entry.skill
    meeting, level = entry.skill.signals
    assert meeting.type == "enum" and meeting.values == ("active", "inactive")
    assert meeting.descriptions == {
        "active": "Meeting in progress",
        "inactive": "Other visible activity",
    }
    assert level.type == "number" and level.minimum == 0 and level.maximum == 10
    assert entry.skill.rules[0].steps[0].arguments["minutes"] == 1


async def test_unsupported_request_does_not_save_a_skill(tmp_path):
    skills = book(tmp_path)
    before = skills.entries()
    reason = (
        "Daybook cannot read hidden app state; it can only use visible screen notes."
    )
    gateway = ScriptedGateway([json.dumps({"draft": None, "explanation": reason})])
    with pytest.raises(UnsupportedSkill, match="hidden app state") as error:
        await draft_skill(
            "Remind me from a hidden app's status", book=skills, gateway=gateway
        )
    assert error.value.explanation == reason
    assert skills.entries() == before
    assert not list((tmp_path / "skills").glob("*.skill.md"))
    assert len(gateway.prompts) == 1


async def test_grammar_repair_can_explain_an_unsupported_request_without_saving(
    tmp_path,
):
    skills = book(tmp_path)
    before = skills.entries()
    bad = TEXT.replace("in_meeting becomes false", "unsupported trigger")
    reason = "Daybook cannot intercept keyboard events or block an action."
    gateway = ScriptedGateway(
        [payload(bad, "Draft"), json.dumps({"draft": None, "explanation": reason})]
    )
    with pytest.raises(UnsupportedSkill, match="keyboard"):
        await draft_skill(
            "Block an action from a keyboard event", book=skills, gateway=gateway
        )
    assert skills.entries() == before
    assert not list((tmp_path / "skills").glob("*.skill.md"))
    assert len(gateway.prompts) == 2


@pytest.mark.parametrize("outcome", ["both", "neither", "blank"])
async def test_inconsistent_outcome_gets_one_bounded_structured_repair(
    tmp_path, outcome
):
    import httpx

    from daybook.model import ModelConfig, ModelGateway

    skills = book(tmp_path)
    good = json.loads(payload(TEXT, "Stand after meetings"))
    invalid = {
        "draft": good["draft"] if outcome == "both" else None,
        "explanation": "unavailable"
        if outcome == "both"
        else " "
        if outcome == "blank"
        else None,
    }
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        content = invalid if len(requests) == 1 else good
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    gateway = ModelGateway(ModelConfig(base_url="http://model.test/v1"))
    await gateway._client.aclose()
    gateway._client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://model.test/v1/"
    )
    try:
        entry, _ = await draft_skill(
            "Remind me after a meeting", book=skills, gateway=gateway
        )
        assert entry.status == "needs_review"
        assert len(requests) == 2
        assert "Validation errors:" in requests[1]["messages"][0]["content"]
        assert all(request["max_tokens"] == 2000 for request in requests)
    finally:
        await gateway.close()


async def test_inconsistent_outcomes_exhaust_existing_repair_without_saving(tmp_path):
    import httpx

    from daybook.model import ModelConfig, ModelGateway, ModelUnavailable

    skills = book(tmp_path)
    before = skills.entries()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"draft":null,"explanation":null}'}}
                ]
            },
        )

    gateway = ModelGateway(ModelConfig(base_url="http://model.test/v1"))
    await gateway._client.aclose()
    gateway._client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://model.test/v1/"
    )
    try:
        with pytest.raises(ModelUnavailable):
            await draft_skill("Remind me", book=skills, gateway=gateway)
        assert len(requests) == 2
        assert skills.entries() == before
        assert not list((tmp_path / "skills").glob("*.skill.md"))
    finally:
        await gateway.close()


async def test_activity_example_fires_on_first_detection_without_changing_builtin(
    tmp_path,
):
    from datetime import datetime, timedelta, timezone

    from daybook.journal import SignalId, SignalReading
    from daybook.skills import evaluate

    skills = book(tmp_path)
    before = next(e for e in skills.entries() if e.id == "instagram-nudge")
    gateway = ScriptedGateway([payload(TEXT, "Meeting reminder")])
    await draft_skill(
        "kick me off of TikTok when I open it", book=skills, gateway=gateway
    )
    examples = gateway.prompts[0].user.split("Built-in supported reply instances:\n")[1]
    example = next(
        json.loads(line)
        for line in examples.splitlines()
        if line.startswith('{"draft":') and '"id": "instagram-nudge"' in line
    )
    # Exercise a model copying the provided example, the failure seen on the GX10.
    copied = ScriptedGateway([json.dumps(example)])
    entry, _ = await draft_skill(
        "kick me off of Instagram", book=skills, gateway=copied
    )
    assert entry.status == "needs_review" and entry.skill
    rule = entry.skill.rules[0]
    at = datetime(2026, 10, 11, tzinfo=timezone.utc)
    signal = SignalId(entry.id, "on_instagram")
    first = SignalReading(signal, True, at, "first")
    occurrence = evaluate(rule, [first], now=at)
    assert occurrence is not None, "copying the example must fire on first detection"
    later = SignalReading(signal, True, at + timedelta(seconds=30), "later")
    repeated = evaluate(rule, [first, later], now=later.at)
    assert repeated and repeated.key == occurrence.key  # One claim per true streak.
    assert next(e for e in skills.entries() if e.id == "instagram-nudge") == before
    assert [step.action for step in rule.steps] == [
        "overlay.show",
        "wait",
        "desktop.close_tab",
    ]
    assert rule.steps[1].arguments == {"for": "5s"}
    assert rule.steps[2].arguments == {"title": "*Instagram*"}


async def test_other_builtin_positive_transition_example_is_preserved(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    text = (
        TEXT.replace("in_meeting", "complete")
        .replace("becomes false", "becomes true")
        .replace(
            "Is the user in a video meeting right now?", "Is the task marked complete?"
        )
    )
    (builtin / "stand-after-meeting.skill.md").write_text(text)
    skills = SkillBook(
        tmp_path / "home",
        builtin,
        tmp_path / "db",
        MemoryPortal(),
        build_catalog({"focus": "work"}),
        Bus(),
        clear_readings=lambda skill: None,
    )
    gateway = ScriptedGateway([payload(TEXT, "Meeting reminder")])
    await draft_skill("remind me after a meeting", book=skills, gateway=gateway)
    examples = gateway.prompts[0].user.split("Built-in supported reply instances:\n")[1]
    example = json.loads(examples.splitlines()[0])
    assert example["draft"]["rules"][0]["when"] == "complete becomes true"
