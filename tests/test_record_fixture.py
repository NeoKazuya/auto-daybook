import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

PATH = Path(__file__).parents[1] / "scripts/record_fixture.py"
SPEC = importlib.util.spec_from_file_location("record_fixture", PATH)
assert SPEC is not None and SPEC.loader is not None
recorder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recorder)


def facts(app="Code", title="daybook-fixture.py", locked=False):
    return {
        "active": {"app": app, "title": title},
        "windows": [
            {
                "id": "42",
                "info": {"app": app, "title": title},
                "rect": {"x": 0, "y": 0, "width": 100, "height": 100},
                "opacity": 1,
                "pid": 1,
            }
        ],
        "locked": locked,
        "idle_ms": 0,
        "microphone_active": False,
        "overlay": False,
        "size": [100, 100],
    }


def test_allowed_capture_and_offsets(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "read_state", facts)
    monkeypatch.setattr(
        recorder, "capture", lambda: Image.new("RGB", (100, 100), "green")
    )
    assert recorder.record_step(tmp_path, 0, ("*daybook-fixture*",)) == "actual"
    assert recorder.record_step(tmp_path, 30, ("*daybook-fixture*",)) == "actual"
    steps = json.loads((tmp_path / "steps.json").read_text())
    assert steps == [{"at": 0, "directory": "000"}, {"at": 30, "directory": "001"}]
    assert Image.open(tmp_path / "000/frame.png").getpixel((0, 0)) == (0, 128, 0)
    with pytest.raises(ValueError):
        recorder.record_step(tmp_path, 30, ("*",))


@pytest.mark.parametrize("private_or_locked", ["private", "locked"])
def test_private_pixels_never_captured(tmp_path, monkeypatch, private_or_locked):
    current = facts(
        "KeePassXC", "highly sensitive secret", private_or_locked == "locked"
    )
    monkeypatch.setattr(recorder, "read_state", lambda: current)
    monkeypatch.setattr(recorder, "capture", lambda: pytest.fail("private pixels read"))
    policy = recorder.record_step(tmp_path, 0, ("*",))
    assert policy == (
        "black-private" if private_or_locked == "private" else "black-locked"
    )
    assert Image.open(tmp_path / "000/frame.png").getextrema() == ((0, 0),) * 3
    assert "sensitive" not in (tmp_path / "000/window.json").read_text()


def test_unapproved_window_and_post_grab_change_never_write(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "read_state", lambda: facts(title="unapproved"))
    monkeypatch.setattr(
        recorder, "capture", lambda: pytest.fail("unapproved pixels read")
    )
    with pytest.raises(RuntimeError, match="not approved"):
        recorder.record_step(tmp_path, 0, ("daybook-fixture*",))
    assert not list(tmp_path.iterdir())
    snapshots = iter([facts(), facts(locked=True)])
    monkeypatch.setattr(recorder, "read_state", lambda: next(snapshots))
    monkeypatch.setattr(recorder, "capture", lambda: Image.new("RGB", (100, 100)))
    with pytest.raises(RuntimeError, match="changed"):
        recorder.record_step(tmp_path, 0, ("*",))
    assert not list(tmp_path.iterdir())


def test_record_tool_has_no_application_dependency():
    import ast

    tree = ast.parse(PATH.read_text())
    imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert all(module is None or not module.startswith("daybook") for module in imports)
