"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QToolBar

from src.UI.dialogs.citation_dialog import CitationDialog, RECOMMENDED_CITATION_TEXT
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_main_window_exposes_citation_toolbar_button_and_opens_dialog():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()

    toolbars = win.findChildren(QToolBar)
    assert len(toolbars) == 1
    assert hasattr(win, "cite_software_button")
    assert win.cite_software_button.text() == "Cite this Software"
    assert win.cite_software_button.parent() is toolbars[0]

    win.cite_software_button.click()
    QApplication.processEvents()
    dlg = win._citation_dialog
    assert dlg is not None
    assert isinstance(dlg, CitationDialog)
    assert dlg.isVisible() is True

    dlg.close()
    win.close()


def test_citation_dialog_copy_citation_updates_clipboard(monkeypatch):
    _app()
    monkeypatch.setattr("src.UI.dialogs.citation_dialog.QMessageBox.information", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.UI.dialogs.citation_dialog.QMessageBox.warning", lambda *_a, **_k: 0)

    dlg = CitationDialog(parent=None)
    dlg.copy_citation_text()

    cb = QGuiApplication.clipboard()
    assert cb is not None
    assert cb.text() == RECOMMENDED_CITATION_TEXT
    dlg.close()
