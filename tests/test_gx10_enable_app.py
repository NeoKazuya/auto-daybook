"""Exercise first-time enable-app startup without changing the real service."""

import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/gx10/desktop-test.py"


@pytest.mark.parametrize("failure", [None, "restart", "is-active"])
def test_enable_app_tolerates_unloaded_reset_but_checks_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").touch()
    config = tmp_path / ".config/daybook-gx10"
    config.mkdir(parents=True)
    calls: list[list[str]] = []

    def run(
        args: list[str], *, check: bool, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        operation = args[2] if args[0] == "systemctl" else None
        code = (
            1
            if operation is not None
            and (operation == "reset-failed" or operation == failure)
            else 0
        )
        if code and check:
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code)

    main = runpy.run_path(str(SCRIPT))["main"]
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "enable-app", str(checkout)])
    monkeypatch.setitem(main.__globals__, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setitem(main.__globals__, "time", SimpleNamespace(sleep=lambda _: None))
    if failure:
        with pytest.raises(subprocess.CalledProcessError) as error:
            main()
        assert error.value.cmd[2] == failure
    else:
        assert main() == 0

    assert (config / "app-dir").read_text() == str(checkout) + "\n"
    operations = [args[2] for args in calls if args[0] == "systemctl"]
    expected = ["reset-failed", "restart"]
    if failure != "restart":
        expected.append("is-active")
    assert operations == expected
