"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtWidgets import QApplication

from src.UI.dialogs.bug_report_dialog import BugReportDialog
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_main_window_exposes_report_bug_action_and_opens_dialog():
    _app()
    win = MainWindow(theme=None)
    assert hasattr(win, "report_bug_action")
    assert win.report_bug_action.text() == "Report a Bug..."

    win.report_bug_action.trigger()
    QApplication.processEvents()
    dlg = win._bug_report_dialog
    assert dlg is not None
    assert isinstance(dlg, BugReportDialog)
    assert dlg.isVisible() is True

    dlg.close()
    win.close()


def test_open_github_issue_uses_open_url_robust(monkeypatch):
    _app()
    captured = {"url": ""}

    class Result:
        opened = True
        method = "test"
        error = ""

    def fake_open(url):
        captured["url"] = url
        return Result()

    monkeypatch.setattr("src.UI.dialogs.bug_report_dialog.open_url_robust", fake_open)

    dlg = BugReportDialog(
        repo="SaanDev/e-Callisto_FITS_Analyzer",
        context_provider=lambda: {"environment": {"app_version": "2.1"}, "summary": {"filename": "demo.fit"}},
        provenance_provider=lambda: {},
        default_dir_provider=lambda: ".",
        parent=None,
    )
    dlg.title_edit.setText("Bug title")
    dlg.details_edit.setPlainText("repro steps")
    dlg.open_github_issue()
    assert "github.com/SaanDev/e-Callisto_FITS_Analyzer/issues/new" in captured["url"]
    assert "Bug+title" in captured["url"]
    dlg.close()


def test_generate_diagnostics_creates_bundle_and_updates_status(monkeypatch, tmp_path: Path):
    _app()

    target = tmp_path / "diag.zip"
    monkeypatch.setattr(
        "src.UI.dialogs.bug_report_dialog.QFileDialog.getSaveFileName",
        lambda *_a, **_k: (str(target), "Zip Archive (*.zip)"),
    )
    monkeypatch.setattr("src.UI.dialogs.bug_report_dialog.QMessageBox.information", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.UI.dialogs.bug_report_dialog.QMessageBox.warning", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.UI.dialogs.bug_report_dialog.QMessageBox.critical", lambda *_a, **_k: 0)

    dlg = BugReportDialog(
        repo="SaanDev/e-Callisto_FITS_Analyzer",
        context_provider=lambda: {"summary": {"filename": "demo.fit"}, "environment": {"platform": "test"}},
        provenance_provider=lambda: {"generated_at": "2026-01-01T00:00:00", "app": {"name": "x"}},
        default_dir_provider=lambda: str(tmp_path),
        parent=None,
    )
    dlg.generate_diagnostics_bundle()
    assert target.exists()
    assert dlg._bundle_path.endswith(".zip")
    assert "Diagnostics bundle:" in dlg.diag_label.text()
    dlg.close()
