from __future__ import annotations

import io
from types import SimpleNamespace
from typing import TypedDict

import pytest
from PIL import Image
from Xlib import X

from daybook.events import Bus
from daybook.journal import Journal, WindowInfo
from daybook.model import ScriptedGateway
from daybook.sense import (
    PrivacySettings,
    Rect,
    ScreenState,
    Sense,
    VisibleWindow,
    X11Screen,
    gate,
)

CODE = WindowInfo("Code", "example.py")
BUBBLE = WindowInfo("daybook-bubble", "Daybook bubble")
BOUNDS = Rect(0, 0, 1920, 1080)


class WindowRecord(TypedDict):
    info: WindowInfo
    rect: Rect
    pid: int


def rgb(image: Image.Image, position: tuple[int, int]) -> tuple[int, int, int]:
    value = image.getpixel(position)
    assert isinstance(value, tuple) and len(value) == 3
    return value[0], value[1], value[2]


@pytest.fixture
def x11(monkeypatch):
    records: dict[int, WindowRecord] = {
        1: {"info": CODE, "rect": BOUNDS, "pid": 101},
        2: {"info": BUBBLE, "rect": Rect(1360, 80, 480, 400), "pid": 777},
    }
    control = SimpleNamespace(
        active=1, bounds=BOUNDS, captures=0, during_grab=lambda: None
    )
    commands = []

    class Window:
        def __init__(self, wid):
            self.wid = wid

        def get_attributes(self):
            return SimpleNamespace(map_state=X.IsViewable)

        def get_geometry(self):
            rect = records[self.wid]["rect"]
            return SimpleNamespace(width=rect.width, height=rect.height)

        def get_wm_class(self):
            app = records[self.wid]["info"].app
            return (app, app)

        def get_wm_name(self):
            return records[self.wid]["info"].title

    root = SimpleNamespace(
        get_geometry=lambda: SimpleNamespace(
            width=control.bounds.width, height=control.bounds.height
        ),
        translate_coords=lambda window, *_: SimpleNamespace(
            x=records[window.wid]["rect"].x, y=records[window.wid]["rect"].y
        ),
        screensaver_query_info=lambda: SimpleNamespace(idle=0),
    )
    screen = X11Screen.__new__(X11Screen)
    screen._name = ":unused"
    screen._root = root
    monkeypatch.setattr(
        screen,
        "_display",
        SimpleNamespace(create_resource_object=lambda _, wid: Window(wid)),
        raising=False,
    )
    screen._shell_pid = 777

    def prop(window, name):
        if window is root:
            return {
                "_NET_ACTIVE_WINDOW": [control.active],
                "_NET_CLIENT_LIST_STACKING": list(records),
            }.get(name)
        if name == "_NET_WM_PID":
            return [records[window.wid]["pid"]]
        return None

    monkeypatch.setattr(screen, "_property", prop)
    monkeypatch.setattr(screen, "_locked", lambda: False)
    monkeypatch.setattr(
        "daybook.sense._screen._command", lambda *args: commands.append(args) or ""
    )

    raw = Image.new("RGB", (BOUNDS.width, BOUNDS.height), (40, 120, 180))
    raw.paste((255, 0, 0), (1360, 80, 1840, 480))

    class Capture:
        monitors = [dict(left=0, top=0, width=BOUNDS.width, height=BOUNDS.height)]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def grab(self, region):
            control.captures += 1
            control.during_grab()
            return SimpleNamespace(size=raw.size, rgb=raw.tobytes())

    monkeypatch.setattr("daybook.sense._screen.mss.mss", lambda **_: Capture())
    return SimpleNamespace(
        screen=screen, records=records, control=control, raw=raw, commands=commands
    )


@pytest.mark.parametrize(
    "rects",
    [
        [Rect(1360, 80, 480, 400)],
        [Rect(1360, 80, 480, 400), Rect(1280, 300, 400, 400)],
        [Rect(-100, 20, 300, 400)],
    ],
)
def test_bubble_masks_ram_pixels_before_thumbnail_and_jpeg_scaling(x11, rects):
    x11.records.pop(2)
    for wid, rect in enumerate(rects, 2):
        x11.records[wid] = {"info": BUBBLE, "rect": rect, "pid": 777}
    x11.raw.paste((40, 120, 180), (0, 0, BOUNDS.width, BOUNDS.height))
    boxes = []
    for rect in rects:
        left, top = max(0, rect.x), max(0, rect.y)
        right = min(BOUNDS.width, rect.x + rect.width)
        bottom = min(BOUNDS.height, rect.y + rect.height)
        box = (left, top, right, bottom)
        boxes.append(box)
        x11.raw.paste((255, 0, 0), box)
    reference = x11.raw.copy()
    for box in boxes:
        reference.paste((0, 0, 0), box)
    facts = x11.screen.state()
    x11.commands.clear()
    assert (
        x11.screen.thumbnail(facts) == reference.convert("L").resize((32, 18)).tobytes()
    )
    actual = Image.open(io.BytesIO(x11.screen.grab(facts)))
    encoded = io.BytesIO()
    reference.resize((1280, 720)).save(encoded, "JPEG", quality=80)
    expected = Image.open(io.BytesIO(encoded.getvalue()))
    assert actual.size == (1280, 720)
    assert actual.tobytes() == expected.tobytes()
    left, top, right, bottom = boxes[0]
    inside = (round((left + right) / 2 * 2 / 3), round((top + bottom) / 2 * 2 / 3))
    assert max(rgb(actual, inside)) <= 5
    assert all(abs(a - b) <= 3 for a, b in zip(rgb(actual, (400, 400)), (40, 120, 180)))
    assert x11.commands == [], "pixel capture performed full-state subprocess checks"


@pytest.mark.parametrize(
    "app,overlay",
    [("daybook-bubble", False), ("daybook-shell", True), ("unknown", True)],
)
def test_only_dedicated_bubble_class_exempts_shell_pid_fullscreen(x11, app, overlay):
    x11.records[2]["info"] = WindowInfo(app, "Reminder")
    facts = x11.screen.state()
    assert facts.overlay is overlay
    assert facts.active == CODE


def test_unexpected_bubble_focus_fails_closed(x11):
    x11.control.active = 2
    facts = x11.screen.state()
    assert facts.active is None
    assert gate(facts, PrivacySettings()) == "locked"


@pytest.mark.parametrize("cover,expected", [(BUBBLE, "withheld"), (CODE, "running")])
def test_bubble_cannot_cover_and_unblock_an_excluded_window(cover, expected):
    rect = Rect(0, 0, 100, 100)
    facts = ScreenState(
        CODE,
        (
            VisibleWindow("private", WindowInfo("KeePassXC", "Private"), rect),
            VisibleWindow("cover", cover, rect),
        ),
        False,
        0,
    )
    assert gate(facts, PrivacySettings()) == expected


@pytest.mark.parametrize("method", ["thumbnail", "grab"])
@pytest.mark.parametrize(
    "change",
    ["appears", "moves", "vanishes", "other_window", "focus", "fullscreen", "bounds"],
)
def test_capture_discards_window_instability_including_bubble_races(
    x11, method, change
):
    if change == "appears":
        x11.records.pop(2)

    def race():
        if change == "appears":
            x11.records[2] = {
                "info": BUBBLE,
                "rect": Rect(1360, 80, 480, 400),
                "pid": 777,
            }
        elif change == "moves":
            x11.records[2]["rect"] = Rect(1200, 80, 480, 400)
        elif change == "vanishes":
            x11.records.pop(2)
        elif change == "other_window":
            x11.records[1]["rect"] = Rect(0, 0, 1800, 1080)
        elif change == "focus":
            x11.control.active = 2
        elif change == "fullscreen":
            x11.records[2]["info"] = WindowInfo("daybook-shell", "Reminder")
        else:
            x11.control.bounds = Rect(0, 0, 1600, 900)

    x11.control.during_grab = race
    with pytest.raises(RuntimeError, match="changed"):
        getattr(x11.screen, method)(x11.screen.state())


async def test_sense_sends_no_model_pixels_when_bubble_moves_during_grab(x11, tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    gateway = ScriptedGateway(['{"seen":"Editing code.","visible_text":""}'])
    sense = Sense(x11.screen, journal, gateway, bus, tmp_path, lambda _: None)

    def race():
        if x11.control.captures == 2:
            x11.records[2]["rect"] = Rect(1200, 80, 480, 400)

    x11.control.during_grab = race
    try:
        await sense.poll()
        await sense.wait_idle()
        assert x11.control.captures == 2
        assert gateway.prompts == [] and gateway.priorities == []
        assert journal.portal.now().observation is None
        assert not list(tmp_path.glob("*.png")) and not list(tmp_path.glob("*.jpg"))
    finally:
        await sense.close()
        journal.close()


async def test_sense_keeps_capturing_underlying_app_with_stable_bubble(x11, tmp_path):
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    gateway = ScriptedGateway(['{"seen":"Editing code.","visible_text":"example.py"}'])
    sense = Sense(x11.screen, journal, gateway, bus, tmp_path, lambda _: None)
    try:
        await sense.poll()
        await sense.wait_idle()
        observation = journal.portal.now().observation
        assert observation is not None and observation.window == CODE
        assert journal.portal.now().capture == "running"
        assert len(gateway.prompts) == 1
        decoded = Image.open(io.BytesIO(gateway.prompts[0].images[0]))
        assert max(rgb(decoded, (1000, 150))) <= 5
        assert all(
            abs(a - b) <= 3 for a, b in zip(rgb(decoded, (400, 400)), (40, 120, 180))
        )
        assert not list(tmp_path.glob("*.png")) and not list(tmp_path.glob("*.jpg"))
    finally:
        await sense.close()
        journal.close()


def test_replay_uses_same_bubble_mask_without_writing_captured_frames(tmp_path):
    import hashlib
    import json

    from daybook.sense import ReplayScreen

    folder = tmp_path / "fixture"
    step = folder / "one"
    step.mkdir(parents=True)
    (folder / "steps.json").write_text('[{"at":0,"directory":"one"}]')
    (step / "window.json").write_text(
        json.dumps(
            {
                "active": {"app": CODE.app, "title": CODE.title},
                "windows": [
                    {
                        "id": "app",
                        "info": {"app": CODE.app, "title": CODE.title},
                        "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                    },
                    {
                        "id": "bubble",
                        "info": {"app": BUBBLE.app, "title": BUBBLE.title},
                        "rect": {"x": 1360, "y": 80, "width": 480, "height": 400},
                    },
                ],
            }
        )
    )
    (step / "locked").write_text("false")
    (step / "idle_ms").write_text("0")
    raw = Image.new("RGB", (1920, 1080), (40, 120, 180))
    raw.paste((255, 0, 0), (1360, 80, 1840, 480))
    path = step / "frame.png"
    raw.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    screen = ReplayScreen(folder)
    reference = raw.copy()
    reference.paste((0, 0, 0), (1360, 80, 1840, 480))
    assert (
        screen.thumbnail(screen.state())
        == reference.convert("L").resize((32, 18)).tobytes()
    )
    decoded = Image.open(io.BytesIO(screen.grab(screen.state())))
    assert max(rgb(decoded, (1000, 150))) <= 5
    assert all(
        abs(a - b) <= 3 for a, b in zip(rgb(decoded, (400, 400)), (40, 120, 180))
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert list(folder.rglob("*.png")) == [path]
    assert not list(folder.rglob("*.jpg"))


@pytest.mark.parametrize("phase", ["thumbnail", "grab"])
async def test_sense_rejects_pixels_not_bound_to_outer_gated_window_facts(
    x11, tmp_path, monkeypatch, phase
):
    # Outer reads A/A are admitted Code facts; the image transaction sees stable
    # B/B with an excluded window. Stability alone must not admit those pixels.
    admitted = x11.screen.state()
    assert gate(admitted, PrivacySettings()) == "running"
    calls = 0

    def outer_state():
        nonlocal calls
        calls += 1
        if phase == "thumbnail" or calls >= 2:
            x11.records[3] = {
                "info": WindowInfo("KeePassXC", "Passwords"),
                "rect": Rect(100, 100, 600, 400),
                "pid": 202,
            }
            x11.raw.paste((220, 20, 140), (100, 100, 700, 500))
        return admitted

    monkeypatch.setattr(x11.screen, "state", outer_state)
    bus = Bus()
    journal = Journal(tmp_path / "journal.db", bus)
    gateway = ScriptedGateway(['{"seen":"Passwords visible.","visible_text":"secret"}'])
    sense = Sense(x11.screen, journal, gateway, bus, tmp_path, lambda _: None)
    try:
        await sense.poll()
        await sense.wait_idle()
        assert gateway.prompts == [] and gateway.priorities == []
        assert journal.portal.now().observation is None
        assert x11.control.captures == (0 if phase == "thumbnail" else 1)
        assert not list(tmp_path.glob("*.png")) and not list(tmp_path.glob("*.jpg"))
    finally:
        await sense.close()
        journal.close()


@pytest.mark.parametrize("method", ["thumbnail", "grab"])
@pytest.mark.parametrize("change", ["appears", "moves", "vanishes", "private_title"])
def test_x11_rejects_stable_window_changes_since_privacy_admission(x11, method, change):
    if change == "appears":
        x11.records.pop(2)
    expected = x11.screen.state()
    if change == "appears":
        x11.records[2] = {
            "info": BUBBLE,
            "rect": Rect(1360, 80, 480, 400),
            "pid": 777,
        }
    elif change == "moves":
        x11.records[2]["rect"] = Rect(1200, 80, 480, 400)
    elif change == "vanishes":
        x11.records.pop(2)
    else:
        x11.records[1]["info"] = WindowInfo("Code", "Private Browsing")
    x11.commands.clear()
    with pytest.raises(RuntimeError, match="since privacy admission"):
        getattr(x11.screen, method)(expected)
    assert x11.control.captures == 0
    assert x11.commands == []


@pytest.mark.parametrize("method", ["thumbnail", "grab"])
def test_replay_rejects_step_changed_since_privacy_admission(monkeypatch, method):
    import json
    from pathlib import Path

    from daybook.sense import ReplayScreen

    clock = [0.0]
    monkeypatch.setattr("daybook.sense._screen.time.monotonic", lambda: clock[0])
    folder = Path(__file__).parents[2] / "fixtures/replay/example"
    screen = ReplayScreen(folder)
    expected = screen.state()
    steps = json.loads((folder / "steps.json").read_text())
    clock[0] = steps[1]["at"]
    with pytest.raises(RuntimeError, match="since privacy admission"):
        getattr(screen, method)(expected)
