"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")
pytest.importorskip("openpyxl")
pytest.importorskip("requests")

from PySide6.QtWidgets import QApplication

from src.UI.gui_main import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_main_window_exposes_check_updates_action():
    _app()
    window = MainWindow(theme=None)
    assert hasattr(window, "check_updates_action")
    assert window.check_updates_action.text() == "Check for Updates..."


def test_main_window_exposes_report_bug_action():
    _app()
    window = MainWindow(theme=None)
    assert hasattr(window, "report_bug_action")
    assert window.report_bug_action.text() == "Report a Bug..."


def test_release_notes_preview_truncates_long_text():
    _app()
    window = MainWindow(theme=None)
    preview = window._release_notes_preview("a" * 2500, limit=120)
    assert len(preview) == 123
    assert preview.endswith("...")


def test_suggest_update_filename_uses_url_basename():
    _app()
    window = MainWindow(theme=None)
    name = window._suggest_update_filename(
        "https://example.com/releases/download/v2.2-dev/e-CALLISTO_FITS_Analyzer_v2.2-dev_Setup.exe"
    )
    assert name == "e-CALLISTO_FITS_Analyzer_v2.2-dev_Setup.exe"


def test_extract_whats_new_section_only():
    _app()
    window = MainWindow(theme=None)
    notes = """
# Release v2.2-dev

## Overview
- Background info

## What's New
- Added **feature A**
- Added [feature B](https://example.com)

## Installation
- Run installer
"""
    section = window._extract_whats_new_section(notes)
    assert "What's New" in section
    assert "feature A" in section
    assert "Installation" not in section


def test_release_notes_preview_returns_plain_compiled_text():
    _app()
    window = MainWindow(theme=None)
    notes = """
## What's New
- Added **feature A**
- Added [feature B](https://example.com)
- Use `fast mode`
"""
    preview = window._release_notes_preview(notes, limit=500)
    assert "**" not in preview
    assert "[" not in preview
    assert "(https://example.com)" not in preview
    assert "feature A" in preview
    assert "feature B" in preview
    assert "fast mode" in preview
