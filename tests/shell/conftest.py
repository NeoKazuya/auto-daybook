import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def app():
    # The tray needs QApplication (widgets); one instance serves every test.
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
