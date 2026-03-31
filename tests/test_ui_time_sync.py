"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("requests")
pytest.importorskip("bs4")

from PySide6.QtWidgets import QApplication

from src.UI.dst_index_gui import MainWindow as DstWindow
from src.UI.goes_xrs_gui import MainWindow as GoesWindow
from src.UI.kp_index_gui import MainWindow as KpWindow
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


def test_dst_set_time_window_accepts_cross_day_range():
    _app()
    win = DstWindow()
    start_dt = datetime(2026, 2, 10, 23, 0)
    end_dt = datetime(2026, 2, 11, 1, 0)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)

    assert ok is True
    assert win.start_date.date().year() == 2026
    assert win.start_date.date().day() == 10
    assert int(win.start_hour.currentData()) == 23
    assert win.end_date.date().day() == 11
    assert int(win.end_hour.currentData()) == 1


def test_dst_cursor_text_tracks_time_and_value_along_curve():
    _app()
    win = DstWindow()
    start_dt = datetime(2026, 2, 10, 0, 0, 0)
    end_dt = datetime(2026, 2, 10, 1, 0, 0)
    times = np.array([start_dt, end_dt], dtype=object)
    values = np.array([-20.0, -80.0], dtype=float)

    win.canvas.plot_dst(times, values, start_dt, end_dt, ("Real-time",))
    mid_x = float(win.canvas._time_nums[0] + win.canvas._time_nums[1]) / 2.0

    text = win._format_cursor_text(mid_x, True)

    assert "UTC = 2026-02-10 00:30:00" in text
    assert "Dst = -50.0 nT" in text


def test_kp_set_time_window_snaps_to_overlapping_bins():
    _app()
    win = KpWindow()
    start_dt = datetime(2026, 2, 10, 23, 10)
    end_dt = datetime(2026, 2, 11, 2, 5)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)

    assert ok is True
    assert win.start_date.date().day() == 10
    assert int(win.start_slot.currentData()) == 21
    assert win.end_date.date().day() == 11
    assert int(win.end_slot.currentData()) == 0


def test_kp_cursor_text_uses_interval_and_code():
    _app()
    win = KpWindow()
    result = win.current_result.__class__(
        interval_starts=(datetime(2026, 2, 10, 3, 0, 0),),
        interval_ends=(datetime(2026, 2, 10, 6, 0, 0),),
        kp_decimal=(5.333,),
        kp_code=("5+",),
        status=("pre",),
    )
    win.canvas.plot_kp(result, result.interval_starts[0], result.interval_starts[0])
    x = float(win.canvas._start_nums[0] + (win.canvas._end_nums[0] - win.canvas._start_nums[0]) / 2.0)

    text = win._format_cursor_text(x, True)

    assert "UTC = 2026-02-10 03:00 to 2026-02-10 06:00" in text
    assert "Kp = 5.33 (5+)" in text


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
