import json
from pathlib import Path

from PIL import Image

from daybook.journal import WindowInfo
from daybook.sense import Rect, ScreenState, VisibleWindow


def test_three_step_example_matches_contract():
    root = Path(__file__).parents[1] / "fixtures/replay/example"
    steps = json.loads((root / "steps.json").read_text())
    labels = json.loads((root / "labels.json").read_text())
    assert len(steps) == len(labels) == 3
    assert [s["at"] for s in steps] == [0, 30, 60]
    for step in steps:
        folder = root / step["directory"]
        facts = json.loads((folder / "window.json").read_text())
        state = ScreenState(
            active=WindowInfo(**facts["active"]),
            windows=tuple(
                VisibleWindow(
                    id=w["id"],
                    info=WindowInfo(**w["info"]),
                    rect=Rect(**w["rect"]),
                    opacity=w["opacity"],
                    pid=w["pid"],
                )
                for w in facts["windows"]
            ),
            locked=json.loads((folder / "locked").read_text()),
            idle_ms=json.loads((folder / "idle_ms").read_text()),
        )
        assert state.windows and state.idle_ms >= 0
        with Image.open(folder / "frame.png") as image:
            assert image.size == (1280, 720) and image.mode == "RGB"
