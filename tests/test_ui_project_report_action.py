"""
e-CALLISTO FITS Analyzer
Version 2.4.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

import numpy as np
from PySide6.QtWidgets import QApplication

import src.UI.main_window as main_window_module
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _find_menu_action(menu_bar, text: str):
    for menu_action in menu_bar.actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.text() == text:
                return action
    return None


def test_file_menu_exposes_project_report_action_and_enablement():
    _app()
    win = MainWindow(theme=None)

    action = _find_menu_action(win.menuBar(), "Generate Project Report...")
    assert action is win.generate_project_report_action
    assert action.isEnabled() is False

    win.raw_data = np.ones((2, 3), dtype=float)
    win.freqs = np.array([90.0, 80.0])
    win.time = np.array([0.0, 1.0, 2.0])
    win.filename = "demo.fit"
    win._sync_project_actions()

    assert win.generate_project_report_action.isEnabled() is True
    win.close()


def test_generate_project_report_without_data_shows_message(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    messages = []
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: messages.append(args))

    win.generate_project_report()

    assert messages
    assert messages[0][1] == "Generate Project Report"
    assert "Load a FITS file first" in messages[0][2]
    win.close()


def test_pick_project_report_path_appends_pdf_extension(monkeypatch, tmp_path):
    _app()
    win = MainWindow(theme=None)
    target = tmp_path / "report_without_ext"
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "PDF (*.pdf)"),
    )

    assert win._pick_project_report_path() == f"{target}.pdf"
    win.close()
