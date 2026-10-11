import asyncio
from types import SimpleNamespace

import pytest

from daybook.act import (
    FakeDesktop,
    FakeOverlay,
    MusicLibrary,
    MusicPlayer,
    NotAllowed,
    XdoDesktop,
    bind_handlers,
    build_catalog,
)
from daybook.events import Bus
from daybook.journal import Outcome


@pytest.fixture(autouse=True)
def no_real_subprocesses(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("test must not launch a real subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)


@pytest.fixture
async def runtime(tmp_path, monkeypatch):
    state = SimpleNamespace(
        capture="running",
        enabled=True,
        effects=[],
        active="42",
        typed=None,
        boundary=None,
        arrived=asyncio.Event(),
        released=asyncio.Event(),
    )

    def can_act():
        return state.capture == "running" and state.enabled

    async def boundary(name):
        if state.boundary == name:
            state.arrived.set()
            await state.released.wait()

    class Process:
        returncode = 0

        def __init__(self, value="", effect=None):
            self.value, self.effect = value, effect

        async def communicate(self):
            await boundary("query")
            return self.value.encode(), b""

        async def wait(self):
            await boundary(self.effect)
            return 0

        def kill(self):
            raise AssertionError("eligibility denial must not kill helpers")

    async def spawn(*args, **kwargs):
        # Only this external subprocess boundary is fake; command/output/action
        # implementation and their awaited queries/waits run as shipped.
        queries = {
            ("xdotool", "getactivewindow"): state.active,
            ("xdotool", "getwindowname", "42"): "Instagram — Firefox",
            (
                "xprop",
                "-id",
                "42",
                "WM_CLASS",
            ): 'WM_CLASS(STRING) = "Navigator", "firefox"',
            (
                "wmctrl",
                "-lx",
            ): "0x0000002a 0 Navigator.firefox host Instagram — Firefox",
        }
        if args in queries:
            return Process(queries[args])
        if args[0] in ("gtk-launch", "xdg-open", "notify-send"):
            effect = args[0]
        elif args[:3] == ("xdotool", "windowactivate", "--sync"):
            state.active = args[3]
            if "type" in args:
                effect = "type_url"
                state.typed = args[-1]
            elif args[-1] == "ctrl+t":
                effect = "new_tab"
            elif args[-1] == "Return":
                effect = "navigate"
            elif args[-1] == "ctrl+w":
                effect = "close_tab"
            elif "key" in args:
                effect = "switch_tab"
            else:
                effect = "focus"
        else:
            raise AssertionError(args)
        state.effects.append(effect)
        return Process(effect=effect)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    overlay = FakeOverlay(Bus())
    player = MusicPlayer(MusicLibrary(tmp_path, {}, ()), tmp_path, Bus(), overlay)
    catalog = build_catalog(
        {}, bind_handlers(player, overlay, XdoDesktop(), real_effects=True)
    )
    state.actions = catalog.allowed(tuple(catalog.specs), "demo", can_act=can_act)
    state.catalog = catalog
    state.can_act = can_act
    yield state
    await player.close()
    await overlay.close()


def revoke(state, reason):
    if reason == "pause":
        state.capture = "paused"
    else:
        state.enabled = False


async def release_after_revocation(state, task, reason):
    try:
        await asyncio.wait_for(state.arrived.wait(), 1)
        revoke(state, reason)
    finally:
        state.released.set()
    result = await asyncio.wait_for(task, 1)
    assert not result.ok and result.detail == "skill_inactive"
    return result


@pytest.mark.parametrize("reason", ["pause", "disable"])
@pytest.mark.parametrize("boundary", ["new_tab", "type_url"])
async def test_revocation_between_new_tab_effects_preserves_started_work(
    runtime, reason, boundary
):
    runtime.boundary = boundary
    task = asyncio.create_task(
        runtime.actions.run("desktop.open_tab", url="https://example.com")
    )
    await release_after_revocation(runtime, task, reason)
    expected = ["new_tab"] if boundary == "new_tab" else ["new_tab", "type_url"]
    assert runtime.effects == expected  # No navigation, rollback, or cleanup-close.
    assert runtime.typed == (None if boundary == "new_tab" else "https://example.com")


@pytest.mark.parametrize("reason", ["pause", "disable"])
@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("desktop.close_tab", {"title": "*Instagram*"}),
        ("desktop.focus", {"app": "*firefox*"}),
        ("desktop.switch_tab", {"tab": "next"}),
        ("desktop.open_tab", {"url": "https://example.com", "app": "*firefox*"}),
    ],
)
async def test_revocation_during_window_query_prevents_first_effect(
    runtime, reason, action, args
):
    assert (
        await runtime.actions.run(
            "overlay.show", title="Warning", body="Soon", layout="bubble"
        )
    ).ok
    runtime.boundary = "query"
    task = asyncio.create_task(runtime.actions.run(action, **args))
    await release_after_revocation(runtime, task, reason)
    assert runtime.effects == []


async def test_manual_direct_desktop_calls_remain_permitted(runtime):
    assert (await XdoDesktop().open_url("mailto:a@example.com")).ok
    assert (await XdoDesktop().open_tab("https://example.com")).ok
    assert runtime.effects == ["xdg-open", "new_tab", "type_url", "navigate"]


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("desktop.open_url", {"url": "https://example.com"}),
        ("desktop.launch", {"app": "test-editor"}),
        ("notify.send", {"title": "Rest", "body": "Break"}),
    ],
)
async def test_inactive_actions_do_not_spawn_effects(runtime, action, args):
    runtime.enabled = False
    assert (await runtime.actions.run(action, **args)).detail == "skill_inactive"
    assert runtime.effects == []


async def test_denied_capability_stays_revoked_after_reenable(runtime):
    runtime.enabled = False
    assert (
        await runtime.actions.run("desktop.open_url", url="https://example.com")
    ).detail == "skill_inactive"
    runtime.enabled = True
    assert (
        await runtime.actions.run("desktop.open_url", url="https://example.com")
    ).detail == "skill_inactive"
    fresh = runtime.catalog.allowed(
        ("desktop.open_url",), "demo", can_act=runtime.can_act
    )
    assert (await fresh.run("desktop.open_url", url="https://example.com")).ok
    assert runtime.effects == ["xdg-open"]


async def test_context_is_per_capability_and_guards_delayed_handlers():
    enabled = True
    entered, resume = asyncio.Event(), asyncio.Event()
    seen = []
    desktop = FakeDesktop()

    async def delayed(context, args):
        seen.append(context.skill)
        entered.set()
        await resume.wait()
        return await desktop.launch(str(args["app"]), can_act=context.can_act)

    catalog = build_catalog({}, {"desktop.launch": delayed})
    first = catalog.allowed(("desktop.launch",), "first", can_act=lambda: enabled)
    second = catalog.allowed(("desktop.launch",), "second")
    task = asyncio.create_task(first.run("desktop.launch", app="Editor"))
    await asyncio.wait_for(entered.wait(), 1)
    enabled = False
    resume.set()
    assert (await task).detail == "skill_inactive" and desktop.calls == []
    assert (await second.run("desktop.launch", app="Editor")).ok
    assert desktop.calls == [("launch", "Editor")] and seen == ["first", "second"]


async def test_fake_desktop_checks_callback_before_recording():
    desktop = FakeDesktop()
    assert (
        await desktop.open_tab("https://example.com", can_act=lambda: False)
    ).detail == "skill_inactive"
    assert not desktop.calls
    assert (await desktop.switch_tab("next")).ok
    assert desktop.calls == [("switch_tab", "next", "active")]


async def test_permission_and_parameter_checks_stay_in_force(runtime):
    denied = runtime.catalog.allowed((), can_act=lambda: False)
    with pytest.raises(NotAllowed):
        await denied.run("desktop.open_url", url="https://example.com")
    assert (
        await runtime.actions.run("desktop.switch_tab", tab="hidden title")
    ).detail == "invalid_parameters"
    assert not runtime.effects


async def test_task_cancellation_is_not_an_action_failure():
    started = asyncio.Event()

    async def blocked(context, args):
        started.set()
        await asyncio.Event().wait()
        return Outcome("desktop.launch", True)

    actions = build_catalog({}, {"desktop.launch": blocked}).allowed(
        ("desktop.launch",)
    )
    task = asyncio.create_task(actions.run("desktop.launch", app="Editor"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("open_url", {"url": "https://example.com"}),
        ("launch", {"app": "test-editor"}),
        ("focus", {"app": "*firefox*"}),
        ("open_tab", {"url": "https://example.com"}),
        ("switch_tab", {"tab": "next"}),
        ("close_tab", {"title": "*Instagram*"}),
    ],
)
async def test_direct_desktop_backend_rechecks_callback_before_effect(
    runtime, method, args
):
    result = await getattr(XdoDesktop(), method)(**args, can_act=lambda: False)
    assert result.detail == "skill_inactive" and runtime.effects == []


async def test_notification_handler_forwards_guard_to_spawn_boundary(runtime):
    runtime.enabled = False
    result = await runtime.catalog.handlers["notify.send"](
        runtime.actions.context, {"title": "Rest", "body": "Break"}
    )
    assert result.detail == "skill_inactive" and runtime.effects == []
