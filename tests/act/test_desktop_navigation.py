import pytest

from daybook.act import (
    FakeDesktop,
    FakeOverlay,
    MusicLibrary,
    MusicPlayer,
    NotAllowed,
    XdoDesktop,
    _desktop,
    bind_handlers,
    build_catalog,
)
from daybook.events import Bus


@pytest.fixture(autouse=True)
def no_real_desktop(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("test must not launch a real desktop command")

    monkeypatch.setattr(_desktop.asyncio, "create_subprocess_exec", forbidden)


@pytest.fixture
def desktop_files(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))

    def write(id, name, wm_class="", *, user=False, hidden=False, no_display=False):
        root = tmp_path / ("user" if user else "system") / "applications"
        root.mkdir(parents=True, exist_ok=True)
        (root / id).write_text(
            "[Desktop Entry]\nType=Application\n"
            f"Name={name}\nStartupWMClass={wm_class}\nHidden={'true' if hidden else 'false'}\n"
            f"NoDisplay={'true' if no_display else 'false'}\n"
            "Exec=never-execute-this %U\n"
            "[Desktop Action Other]\nName=Ignored action name\n"
        )

    # Keep this fixture independent of the machine's installed exports.
    original = _desktop.applications

    def applications():
        return tuple(a for a in original() if a.id.startswith("navigation-test-"))

    monkeypatch.setattr(_desktop, "applications", applications)
    return write


@pytest.fixture
def screen(monkeypatch):
    state = {
        "active": "42",
        "class": 'WM_CLASS(STRING) = "google-chrome", "Google-chrome"',
        "listing": "0x0000002a 0 google-chrome.Google-chrome host Research - Chrome",
        "commands": [],
    }

    async def output(*args):
        if args == ("xdotool", "getactivewindow"):
            return state["active"]
        if args == ("wmctrl", "-lx"):
            return state["listing"]
        if args[:1] == ("xprop",):
            return state["class"]
        raise AssertionError(args)

    async def command(action, *args, can_act=None):
        state["commands"].append(args)
        if args[:3] == ("xdotool", "windowactivate", "--sync"):
            state["active"] = args[3]
        return _desktop.Outcome(action, True)

    monkeypatch.setattr(_desktop, "output", output)
    monkeypatch.setattr(_desktop, "command", command)
    return state


async def test_catalog_defaults_permissions_and_fake_requests(tmp_path):
    desktop = FakeDesktop()
    bus = Bus()
    overlay = FakeOverlay(bus)
    player = MusicPlayer(MusicLibrary(tmp_path, {}, ()), tmp_path, bus, overlay)
    catalog = build_catalog({}, bind_handlers(player, overlay, desktop))
    actions = catalog.allowed(
        ("desktop.open_tab", "desktop.switch_tab", "desktop.focus")
    )
    assert (await actions.run("desktop.open_tab", url="https://example.com")).ok
    assert (await actions.run("desktop.switch_tab", tab="last", app="Firefox")).ok
    assert (await actions.run("desktop.focus", app="Editor", title="*project*")).ok
    assert desktop.calls == [
        ("open_tab", "https://example.com", "active"),
        ("switch_tab", "last", "Firefox"),
        ("focus", "Editor", "*project*"),
    ]
    assert (
        await actions.run("desktop.switch_tab", tab="9")
    ).detail == "invalid_parameters"
    assert (
        await actions.run("desktop.switch_tab", tab="Research")
    ).detail == "invalid_parameters"
    with pytest.raises(NotAllowed):
        await catalog.allowed(("desktop.open_url",)).run(
            "desktop.open_tab", url="https://example.com"
        )
    assert "No hidden-tab title lookup" in catalog.describe_for_prompt()
    assert not any(
        "close_app" in action or "quit" in action for action in catalog.specs
    )
    await player.close()
    await overlay.close()


async def test_app_names_ids_and_user_hidden_entries(desktop_files, screen):
    desktop_files("navigation-test-editor.desktop", "Visual Studio Code", "Code")
    assert (await XdoDesktop().launch("Visual Studio Code")).ok
    assert screen["commands"][-1] == ("gtk-launch", "navigation-test-editor.desktop")
    assert (await XdoDesktop().launch("navigation-test-editor")).ok
    desktop_files(
        "navigation-test-editor.desktop",
        "Visual Studio Code",
        "Code",
        user=True,
        hidden=True,
    )
    assert not (await XdoDesktop().launch("Visual Studio Code")).ok
    assert _desktop.resolve("Ignored action name") == (None, "")


async def test_focus_names_and_window_titles_select_the_right_window(
    desktop_files, screen
):
    desktop_files("navigation-test-editor.desktop", "Visual Studio Code", "Code")
    screen["listing"] = (
        "0x0000002b 0 code.Code host Other — Visual Studio Code\n"
        "0x0000002c 0 code.Code host Project — Visual Studio Code"
    )
    desktop = XdoDesktop()
    assert (await desktop.focus("Visual Studio Code")).detail == "ambiguous_window"
    assert not screen["commands"]
    assert (await desktop.focus("Visual Studio Code", "*project*")).ok
    assert screen["commands"][-1] == ("xdotool", "windowactivate", "--sync", "44")
    for alias in ("code", "Code", "[Cc]ode", "*Code*"):
        assert (await desktop.focus(alias)).ok  # Prefer the active matching window.
    assert (await desktop.focus("*Code*", "*missing*")).detail == "window_not_found"


async def test_installed_name_shorthand_matches_qualified_classes(
    desktop_files, screen
):
    desktop_files(
        "navigation-test-vscode.desktop", "Visual Studio Code", "com.microsoft.VSCode"
    )
    desktop_files(
        "navigation-test-url.desktop",
        "Visual Studio Code - URL Handler",
        no_display=True,
    )
    screen["listing"] = (
        "0x0000002a 0 com.microsoft.vscode.com.microsoft.VSCode host Project — Visual Studio Code"
    )
    for alias in ("Visual Studio Code", "VS Code", "code", "Code", "[Cc]ode", "*Code*"):
        assert (await XdoDesktop().focus(alias)).ok, alias
    assert (await XdoDesktop().launch("code")).ok
    assert screen["commands"][-1] == ("gtk-launch", "navigation-test-vscode.desktop")
    desktop_files("navigation-test-chrome.desktop", "Google Chrome", "Google-chrome")
    chrome, error = _desktop.resolve("Chrome")
    assert chrome is not None and not error
    assert chrome.id == "navigation-test-chrome.desktop"


async def test_duplicate_app_names_are_refused(desktop_files, screen):
    desktop_files("navigation-test-editor.desktop", "Editor", "Code")
    desktop_files("navigation-test-other.desktop", "Editor", "Other")
    assert (await XdoDesktop().launch("Editor")).detail == "ambiguous_app"
    assert (await XdoDesktop().focus("Editor")).detail == "ambiguous_app"
    assert not screen["commands"]


@pytest.mark.parametrize(
    ("wm_class", "tab", "key"),
    [
        ('"google-chrome", "Google-chrome"', "2", "ctrl+2"),
        ('"Navigator", "firefox"', "2", "alt+2"),
        ('"Navigator", "librewolf"', "last", "alt+9"),
        ('"brave-browser", "Brave-browser"', "last", "ctrl+9"),
        ('"firefox", "firefox"', "next", "ctrl+Page_Down"),
        ('"chromium", "Chromium"', "previous", "ctrl+Page_Up"),
    ],
)
async def test_tab_shortcuts_follow_the_checked_browser(screen, wm_class, tab, key):
    screen["class"] = f"WM_CLASS(STRING) = {wm_class}"
    assert (await XdoDesktop().switch_tab(tab)).ok
    assert screen["commands"] == [
        ("xdotool", "windowactivate", "--sync", "42", "key", "--clearmodifiers", key)
    ]


async def test_named_browser_is_checked_before_focusing(desktop_files, screen):
    desktop_files("navigation-test-browser.desktop", "Google Chrome", "Google-chrome")
    screen["active"] = "100"
    assert (await XdoDesktop().switch_tab("next", "Google Chrome")).ok
    assert screen["active"] == "42"
    screen["commands"].clear()
    screen["active"] = "100"
    screen["class"] = 'WM_CLASS(STRING) = "code", "Code"'
    assert (
        await XdoDesktop().switch_tab("next", "Google Chrome")
    ).detail == "not_a_browser"
    assert not screen["commands"] and screen["active"] == "100"


async def test_open_tab_creates_a_new_tab_then_types_the_url(screen):
    url = "https://example.com/?q=a%20b&path=one"
    assert (await XdoDesktop().open_tab(url)).ok
    assert [c[4:] for c in screen["commands"]] == [
        ("key", "--clearmodifiers", "ctrl+t"),
        ("type", "--clearmodifiers", "--delay", "0", "--", url),
        ("key", "--clearmodifiers", "Return"),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/\nquit",
        "https://example.com/\t",
        "https://example.com/\x7f",
        "javascript:alert(1)",
        "file:///tmp/a",
        "mailto:x@example.com",
        "https:",
        "https://[",
        "-url",
    ],
)
async def test_invalid_new_tab_urls_do_nothing(screen, url):
    assert (await XdoDesktop().open_tab(url)).detail == "invalid_url"
    assert not screen["commands"]


@pytest.mark.parametrize(
    ("wm_class", "detail"),
    [("code", "not_a_browser"), ("vivaldi", "unsupported_browser")],
)
async def test_tab_actions_refuse_other_or_unsupported_apps(screen, wm_class, detail):
    screen["class"] = f'WM_CLASS(STRING) = "{wm_class}", "{wm_class}"'
    assert (await XdoDesktop().switch_tab("next")).detail == detail
    assert (await XdoDesktop().open_tab("https://example.com")).detail == detail
    assert not screen["commands"]


async def test_focus_change_during_checks_does_not_restore_browser(screen, monkeypatch):
    original = _desktop.output
    checks = 0

    async def moving(*args):
        nonlocal checks
        if args == ("xdotool", "getactivewindow"):
            checks += 1
            if checks == 2:
                screen["active"] = "100"
        return await original(*args)

    monkeypatch.setattr(_desktop, "output", moving)
    assert (await XdoDesktop().switch_tab("next")).detail == "window_not_active"
    assert not screen["commands"] and screen["active"] == "100"


async def test_focus_change_after_new_tab_prevents_typing_into_another_app(
    screen, monkeypatch
):
    original = _desktop.command

    async def moving(action, *args, can_act=None):
        result = await original(action, *args, can_act=can_act)
        screen["active"] = "100"
        return result

    monkeypatch.setattr(_desktop, "command", moving)
    assert (
        await XdoDesktop().open_tab("https://example.com")
    ).detail == "window_not_active"
    assert len(screen["commands"]) == 1  # Only Ctrl+T, no URL typing or Return.
