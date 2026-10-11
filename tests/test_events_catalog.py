from pathlib import Path

import pytest

from daybook.act import NotAllowed, build_catalog
from daybook.events import Bus, StatusChanged
from daybook.skills import Skill, SkillSource, parse_skill
from daybook.web.wire import MOCK_MOODS


def test_bus_drops_oldest_without_blocking(caplog):
    bus = Bus(capacity=2)
    with bus.subscribe() as slow, bus.subscribe() as fast:
        for status in ("one", "two", "three"):
            bus.publish(StatusChanged("test", status))
            assert fast.get_nowait() == StatusChanged("test", status)
        assert [slow.get_nowait(), slow.get_nowait()] == [
            StatusChanged("test", "two"),
            StatusChanged("test", "three"),
        ]
    assert "subscriber_overflow" in caplog.text


def test_builtins_parse():
    catalog = build_catalog(MOCK_MOODS)
    for path in (Path(__file__).parents[1] / "skills/builtin").glob("*.skill.md"):
        parsed = parse_skill(
            path.read_text(), source=SkillSource(path, "builtin"), catalog=catalog
        )
        assert isinstance(parsed, Skill), parsed
    assert len(catalog.specs) == 12
    assert {"desktop.open_tab", "desktop.switch_tab"} <= catalog.specs.keys()
    assert "desktop.close_tab" in catalog.specs
    assert all(name in catalog.describe_for_prompt() for name in catalog.specs)


async def test_actions_enforce_permission_even_without_backend():
    actions = build_catalog(MOCK_MOODS).allowed(("music.stop",))
    with pytest.raises(NotAllowed):
        await actions.run("desktop.launch", app="xterm")
    assert not (await actions.run("music.stop")).ok
