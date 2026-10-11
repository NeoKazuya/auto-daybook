from pathlib import Path

import pytest

from daybook.act import build_catalog
from daybook.skills import Skill, SkillSource, parse_skill
from daybook.web.wire import MOCK_MOODS


@pytest.mark.parametrize(
    ("type_text", "action", "argument"),
    [
        ("bool", "music.play_mood", "mood: $value"),
        ("music.mood", "music.play_mood", "mood: prefix-$value"),
        ("bool", "overlay.show", "title: Break, body: Rest, minutes: $value"),
    ],
)
def test_references_must_fit_action_parameters(type_text, action, argument):
    text = f"""---
id: sample
name: Sample
signals:
  value:
    ask: What is on screen?
    type: {type_text}
rules:
  - when: value updates
    do:
      - {action}: {{ {argument} }}
allow: [{action}]
---
Sample goal.
"""
    parsed = parse_skill(
        text,
        source=SkillSource(Path("sample.skill.md"), "user"),
        catalog=build_catalog(MOCK_MOODS),
    )
    assert not isinstance(parsed, Skill)
    assert any("rules[0].do[0]" in e.location for e in parsed)
