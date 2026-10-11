"""Run functional browser-module checks without a package dependency."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_functional_browser_modules() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the browser-module checks")
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [
            node,
            "--experimental-vm-modules",
            "--test",
            "tests/web_static/test_functional.mjs",
        ],
        cwd=root,
        check=True,
    )
