"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtWidgets import QApplication, QTextBrowser

from src.UI.dialogs.user_guide_dialog import UserGuideDialog
from src.UI.user_guide_content import (
    USER_GUIDE_HTML,
    build_default_stylesheet,
    user_guide_html,
)
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------- content


def test_guide_html_is_non_empty_and_accessor_matches():
    assert isinstance(USER_GUIDE_HTML, str)
    assert len(USER_GUIDE_HTML.strip()) > 500
    assert user_guide_html() == USER_GUIDE_HTML


def test_every_internal_link_has_a_matching_anchor():
    names = set(re.findall(r'name="([^"]+)"', USER_GUIDE_HTML))
    internal_hrefs = set(re.findall(r'href="#([^"]+)"', USER_GUIDE_HTML))

    assert internal_hrefs, "expected a table of contents with internal links"
    missing = internal_hrefs - names
    assert not missing, f"internal links without matching anchors: {sorted(missing)}"
    # The Home button relies on a top-of-document anchor.
    assert "top" in names


def test_guide_covers_quick_start_and_every_top_level_menu():
    for needle in (
        "Quick Start",
        "File menu",
        "Edit menu",
        "Download menu",
        "Solar Events menu",
        "View menu",
        "Analysis menu",
        "Processing menu",
        "About menu",
    ):
        assert needle in USER_GUIDE_HTML, f"guide is missing coverage of: {needle}"


def test_stylesheet_differs_between_light_and_dark():
    dark = build_default_stylesheet(True)
    light = build_default_stylesheet(False)
    assert dark.strip()
    assert light.strip()
    assert dark != light


# ---------------------------------------------------------------------- dialog


def test_dialog_constructs_and_renders_text():
    _app()
    dlg = UserGuideDialog(parent=None)
    browser = dlg.findChild(QTextBrowser)
    assert browser is not None
    assert len(browser.toPlainText().strip()) > 200
    # Navigation helpers must not raise.
    dlg.go_home()
    dlg.close()


def test_dialog_find_reports_missing_term(monkeypatch):
    _app()
    seen = {}
    monkeypatch.setattr(
        "src.UI.dialogs.user_guide_dialog.QMessageBox.information",
        lambda *a, **k: seen.setdefault("called", True),
    )
    dlg = UserGuideDialog(parent=None)
    dlg.find_edit.setText("zzz-not-in-guide-zzz")
    dlg.find_next()
    assert seen.get("called") is True
    dlg.close()


# ------------------------------------------------------------------ integration


def test_main_window_help_action_bound_to_f1_and_reuses_one_dialog():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()

    assert hasattr(win, "user_guide_action")
    assert win.user_guide_action.shortcut().toString() == "F1"

    win.open_user_guide()
    QApplication.processEvents()
    first = win._user_guide_dialog
    assert isinstance(first, UserGuideDialog)
    assert first.isVisible() is True

    # Opening again must reuse the same live window, not spawn a second one.
    win.open_user_guide()
    QApplication.processEvents()
    assert win._user_guide_dialog is first

    first.close()
    win.close()
