"""Model diagnostics use the active app profile without touching the desktop."""

import io
import json
import runpy
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/gx10/desktop-test.py"


@pytest.fixture
def gx10(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    main = runpy.run_path(str(SCRIPT))["main"]
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAYBOOK_HOME", str(tmp_path / "active"))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "status"])
    monkeypatch.setitem(main.__globals__, "unlocked", lambda: None)
    monkeypatch.setitem(main.__globals__, "screenshot_status", lambda: "mock pixels")
    monkeypatch.setitem(
        main.__globals__, "capture", lambda: pytest.fail("real capture attempted")
    )
    monkeypatch.setenv("DISPLAY", ":mock")

    def command(*args):
        if args[0] == "gsettings":
            return {
                "idle-delay": "uint32 0",
                "lock-enabled": "false",
                "sleep-inactive-ac-type": "'nothing'",
            }[args[-1]]
        if args[0] == "xset":
            return "DPMS is Disabled"
        if args[0] == "wpctl":
            return "Volume: 0.50"
        pytest.fail(f"unexpected desktop command: {args[0]}")

    monkeypatch.setitem(main.__globals__, "command", command)
    return main


def write_profile(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "daybook.toml"
    path.write_text(text)
    return path


def test_status_requests_configured_models_with_authentication(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    key = tmp_path / "profile-key"
    key.write_text("test-only-private-key\n")
    write_profile(
        tmp_path / "active",
        '[model]\nbase_url = "http://profile.invalid/v1/"\n'
        'model = "profile-model"\n'
        f'api_key_file = "{key}"\n',
    )
    requests = []

    def mock_http(request, *, timeout):
        requests.append((request.full_url, request.get_header("Authorization")))
        assert request.data is None
        return io.BytesIO(b'{"data": [{"id": "profile-model"}]}')

    monkeypatch.setattr("urllib.request.urlopen", mock_http)
    assert gx10() == 0
    assert requests == [
        ("http://profile.invalid/v1/models", "Bearer test-only-private-key")
    ]
    output = capsys.readouterr().out
    assert json.loads(output)["model_server"] == {
        "ok": True,
        "value": ["profile-model"],
    }
    assert "test-only-private-key" not in output


def parsed_profile(gx10, monkeypatch: pytest.MonkeyPatch):
    profiles = []

    def status(args):
        profiles.append((args.base_url, args.model, args.key_file))
        return 0

    monkeypatch.setitem(gx10.__globals__, "status", status)
    assert gx10() == 0
    return profiles[0]


def test_default_home_profile(gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAYBOOK_HOME")
    write_profile(
        tmp_path / ".daybook",
        '[model]\nbase_url="http://default.invalid/v1/"\n'
        'model="default-model"\napi_key_file=""\n',
    )
    assert parsed_profile(gx10, monkeypatch) == (
        "http://default.invalid/v1",
        "default-model",
        "",
    )


@pytest.mark.parametrize(
    "flags,expected",
    [
        (
            ["--base-url", "http://override.invalid/v1/"],
            ("http://override.invalid/v1", "profile-model", ""),
        ),
        (
            ["--base-url", "http://profile.invalid/v1/"],
            ("http://profile.invalid/v1", "profile-model", "~/profile-key"),
        ),
        (
            ["--model", "override-model"],
            ("http://profile.invalid/v1", "override-model", "~/profile-key"),
        ),
        (
            ["--key-file", "~/override-key"],
            ("http://profile.invalid/v1", "profile-model", "~/override-key"),
        ),
        (["--key-file", ""], ("http://profile.invalid/v1", "profile-model", "")),
        (
            [
                "--base-url",
                "http://override.invalid/v1",
                "--model",
                "override-model",
                "--key-file",
                "~/override-key",
            ],
            ("http://override.invalid/v1", "override-model", "~/override-key"),
        ),
    ],
)
def test_explicit_options_override_only_selected_profile_fields(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags, expected
):
    write_profile(
        tmp_path / "active",
        '[model]\nbase_url="http://profile.invalid/v1"\n'
        'model="profile-model"\napi_key_file="~/profile-key"\n',
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "status", *flags])
    assert parsed_profile(gx10, monkeypatch) == expected


@pytest.mark.parametrize(
    "config", [None, "", "[model]\n", '[model]\nmodel="custom-model"\n']
)
def test_missing_or_partial_profile_uses_current_qwen_defaults_without_writing(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config
):
    home = tmp_path / "active"
    if config is not None:
        write_profile(home, config)
    assert parsed_profile(gx10, monkeypatch) == (
        "http://127.0.0.1:18300/v1",
        "custom-model" if config and "custom-model" in config else "qwen3.8-flash-next",
        "~/.config/qwen38/api-key",
    )
    if config is None:
        assert not home.exists()
    else:
        assert (home / "daybook.toml").read_text() == config


@pytest.mark.parametrize(
    "config",
    [
        '[model]\nmodel = "unterminated',
        'model = "not-a-table"',
        "[model]\nbase_url = 123",
        '[model]\nmodel = " "',
        "[model]\napi_key_file = false",
    ],
)
def test_invalid_profile_fails_before_any_desktop_or_http_check(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config
):
    path = write_profile(tmp_path / "active", config)
    monkeypatch.setitem(
        gx10.__globals__, "status", lambda _: pytest.fail("invalid profile used")
    )
    with pytest.raises(RuntimeError, match="model profile") as error:
        gx10()
    assert str(path) in str(error.value)


def test_desktop_only_smoke_does_not_read_model_profile(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_profile(tmp_path / "active", "invalid TOML !")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "smoke"])
    # Keep the CLI path but avoid both real locking and the test window.
    monkeypatch.setitem(gx10.__globals__, "open", lambda *_: io.StringIO())
    monkeypatch.setattr("fcntl.flock", lambda *_: None)
    monkeypatch.setitem(gx10.__globals__, "smoke", lambda _: 0)
    assert gx10() == 0


def test_vision_smoke_gets_same_active_model_profile(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_profile(
        tmp_path / "active",
        '[model]\nbase_url="http://vision.invalid/v1/"\n'
        'model="vision-model"\napi_key_file="~/vision-key"\n',
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "smoke", "--vision"])
    profiles = []

    def smoke(args):
        profiles.append((args.base_url, args.model, args.key_file))
        return 0

    monkeypatch.setitem(gx10.__globals__, "open", lambda *_: io.StringIO())
    monkeypatch.setattr("fcntl.flock", lambda *_: None)
    monkeypatch.setitem(gx10.__globals__, "smoke", smoke)
    assert gx10() == 0
    assert profiles == [("http://vision.invalid/v1", "vision-model", "~/vision-key")]


def test_endpoint_override_does_not_forward_profile_credentials(
    gx10, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    key = tmp_path / "profile-key"
    key.write_text("test-only-private-key")
    write_profile(
        tmp_path / "active",
        f'[model]\nbase_url="http://profile.invalid/v1"\napi_key_file="{key}"\n',
    )
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "status", "--base-url", "http://other.invalid/v1"]
    )
    requests = []

    def mock_http(request, *, timeout):
        requests.append((request.full_url, request.get_header("Authorization")))
        return io.BytesIO(b'{"data": [{"id": "other-model"}]}')

    monkeypatch.setattr("urllib.request.urlopen", mock_http)
    assert gx10() == 0
    assert requests == [("http://other.invalid/v1/models", None)]
