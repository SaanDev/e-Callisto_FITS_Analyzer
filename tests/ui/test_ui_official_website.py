"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.ui.app.main_window import MainWindow
from src.version import APP_WEBSITE


class _Result:
    def __init__(self, opened: bool, error: str = ""):
        self.opened = opened
        self.method = "test"
        self.error = error


def _app():
    return QApplication.instance() or QApplication([])


def test_website_is_the_official_site():
    assert APP_WEBSITE == "https://ecallistoanalyzer.org/"


def test_help_menu_opens_official_website(monkeypatch):
    _app()
    opened = []
    monkeypatch.setattr("src.ui.app.main_window.open_url_robust", lambda url: opened.append(url) or _Result(True))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    win = MainWindow(theme=None)
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "Help")
    labels = [a.text() for a in help_menu.actions()]
    assert labels == ["User Guide", "Official Website"]

    win.official_website_action.trigger()
    assert opened == [APP_WEBSITE]
    assert warnings == []
    win.close()


def test_official_website_failure_shows_url(monkeypatch):
    _app()
    monkeypatch.setattr("src.ui.app.main_window.open_url_robust", lambda url: _Result(False, "no browser"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    win = MainWindow(theme=None)
    win.open_official_website()
    assert len(warnings) == 1
    title, text = warnings[0][1], warnings[0][2]
    assert title == "Open URL Failed"
    assert APP_WEBSITE in text and "no browser" in text
    win.close()


def test_about_dialog_names_the_website(monkeypatch):
    _app()
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))

    win = MainWindow(theme=None)
    win.show_about_dialog()
    assert "Website: https://ecallistoanalyzer.org\n" in shown[0][2]
    win.close()
