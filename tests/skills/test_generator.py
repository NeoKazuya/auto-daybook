import json
from pathlib import Path

import yaml

from daybook.act import build_catalog
from daybook.events import Bus
from daybook.journal import Portal, Situation
from daybook.model import Priority, ScriptedGateway
from daybook.skills import SkillBook, draft_skill

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
    for rule in data["rules"]:
        rule["steps"] = []
        for step in rule.pop("do"):
            action, params = next(iter(step.items()))
            rule["steps"].append(
                {
                    "action": action,
                    "params": params if isinstance(params, dict) else {"value": params},
                }
            )
    return json.dumps({**data, "goal": goal.strip(), "summary": summary})


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
    assert summary == "Stand after meetings"
    assert gateway.priorities == [Priority.INTERACTIVE]
    assert gateway.prompts[0].effort == "none"
    assert "Mood music" in gateway.prompts[0].user
    assert "desktop.focus" in gateway.prompts[0].user


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
