"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("bs4")

from PySide6.QtWidgets import QApplication

from src.UI.goes_xrs_gui import MainWindow as GoesWindow
from src.UI.soho_lasco_viewer import CMEViewer, CMECatalogRow, FetchOutcome, STATUS_OK


def _app():
    return QApplication.instance() or QApplication([])


def test_goes_set_time_window_success_same_day():
    _app()
    win = GoesWindow()
    start_dt = datetime(2026, 2, 10, 1, 2, tzinfo=timezone.utc).replace(tzinfo=None)
    end_dt = datetime(2026, 2, 10, 3, 4, tzinfo=timezone.utc).replace(tzinfo=None)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)
    assert ok is True
    assert win.start_date.date().year() == 2026
    assert int(win.start_hour.currentData()) == 1
    assert int(win.end_hour.currentData()) == 3


def test_goes_set_time_window_rejects_cross_day():
    _app()
    win = GoesWindow()
    start_dt = datetime(2026, 2, 10, 23, 0)
    end_dt = datetime(2026, 2, 11, 0, 1)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)
    assert ok is False


def test_cme_set_target_datetime_updates_controls_without_search(monkeypatch):
    _app()
    win = CMEViewer()

    called = {"n": 0}

    def fake_search():
        called["n"] += 1

    monkeypatch.setattr(win, "search_cmes", fake_search)

    target = datetime(2026, 2, 10, 12, 30, 0)
    win.set_target_datetime(target, auto_search=False, auto_select_nearest=True)

    assert win.year_combo.currentText() == "2026"
    assert win.month_combo.currentText() == "02"
    assert win.day_combo.currentText() == "10"
    assert called["n"] == 0


def test_cme_populate_table_auto_selects_nearest():
    _app()
    win = CMEViewer()
    win._pending_target_dt = datetime(2026, 2, 10, 10, 15, 0)

    rows = [
        CMECatalogRow(
            timestamp=datetime(2026, 2, 10, 9, 0, 0),
            values=["v"] * 9,
            catalog_movie_url="",
            fallback_movie_url="https://example.com/a",
        ),
        CMECatalogRow(
            timestamp=datetime(2026, 2, 10, 10, 10, 0),
            values=["v"] * 9,
            catalog_movie_url="",
            fallback_movie_url="https://example.com/b",
        ),
    ]

    outcome = FetchOutcome(status=STATUS_OK, rows=rows)
    win.populate_table(outcome)
    assert win._selected_row == 1
