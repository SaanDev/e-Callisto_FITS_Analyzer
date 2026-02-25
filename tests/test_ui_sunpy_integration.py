"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")
pytest.importorskip("openpyxl")
pytest.importorskip("requests")

from PySide6.QtWidgets import QApplication

from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_main_window_exposes_sunpy_archive_action_and_opens_window():
    _app()
    win = MainWindow(theme=None)
    assert hasattr(win, "open_sunpy_action")
    assert win.open_sunpy_action.text() == "SunPy Multi-Mission Explorer"

    win.open_sunpy_action.trigger()
    QApplication.processEvents()
    assert win._sunpy_window is not None
    assert win._sunpy_window.isVisible() is True

    win._sunpy_window.close()
    win.close()


def test_sync_context_includes_sunpy_status(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    start = datetime(2026, 2, 10, 1, 0, 0)
    end = datetime(2026, 2, 10, 2, 0, 0)

    monkeypatch.setattr(win, "_current_time_window_utc", lambda: (start, end))
    monkeypatch.setattr(win, "_sync_window_to_goes", lambda *_a, **_k: True)
    monkeypatch.setattr(win, "_sync_window_to_cme", lambda *_a, **_k: False)
    monkeypatch.setattr(win, "_sync_window_to_sunpy", lambda *_a, **_k: True)

    win.sync_current_time_window_to_solar_events()
    assert win._last_time_sync_context["goes_synced"] is True
    assert win._last_time_sync_context["cme_synced"] is False
    assert win._last_time_sync_context["sunpy_synced"] is True
    win.close()


def test_main_window_close_blocked_when_sunpy_busy(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_maybe_prompt_save_dirty", lambda: True)

    class _BusySunPyWindow:
        @staticmethod
        def is_operation_running():
            return True

        @staticmethod
        def close():
            return False

        @staticmethod
        def deleteLater():
            return None

    win._sunpy_window = _BusySunPyWindow()
    closed = win.close()
    QApplication.processEvents()

    assert closed is False
    assert win._sunpy_window is not None

    win._sunpy_window = None
    win.close()
