import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "boundaries", Path(__file__).parents[1] / "scripts/check_boundaries.py"
)
assert spec and spec.loader
boundaries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundaries)


@pytest.mark.parametrize(
    ("source", "module"),
    [
        ("import daybook.model._transport", "daybook.sense._loop"),
        ("from daybook.model import _transport", "daybook.sense._loop"),
        ("from ..model._transport import send", "daybook.sense._loop"),
        ("import sqlite3", "daybook.sense._loop"),
        ("import sqlite3", "daybook.skills._parser"),
        ("import httpx", "daybook.web._routes"),
        ("from PySide6 import QtCore", "daybook.act._overlay"),
        ("from Xlib import display", "daybook.act._desktop"),
        ("import mss", "daybook.web.__init__"),
    ],
)
def test_forbidden(source, module):
    assert boundaries.violations(source, module)


@pytest.mark.parametrize(
    ("source", "module"),
    [
        ("from ._transport import send", "daybook.model.__init__"),
        ("import sqlite3", "daybook.journal._db"),
        ("import sqlite3", "daybook.skills._ledger"),
        ("import httpx", "daybook.model._transport"),
        ("import httpx", "daybook.shell.__main__"),
        ("from PySide6 import QtCore", "daybook.shell.__main__"),
        ("from Xlib import display", "daybook.sense._screen"),
        ("import mss", "daybook.sense._screen"),
        ("from daybook.model import ModelGateway", "daybook.sense._loop"),
    ],
)
def test_allowed(source, module):
    assert not boundaries.violations(source, module)


def test_scanner_only_checks_package(tmp_path):
    root = tmp_path / "src/daybook"
    root.mkdir(parents=True)
    (root / "app.py").write_text("import mss")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/record_fixture.py").write_text("import mss")
    assert len(boundaries.check(root)) == 1
