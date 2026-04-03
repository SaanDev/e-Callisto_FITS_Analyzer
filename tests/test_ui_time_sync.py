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
pytest.importorskip("netCDF4")
pytest.importorskip("cftime")

from PySide6.QtWidgets import QApplication

from src.Backend.sep_proton import SepProtonRangeData
from src.UI import goes_xrs_gui as goes_xrs_gui_module
from src.UI.dst_index_gui import MainWindow as DstWindow
from src.UI.goes_sgps_gui import MainWindow as SepWindow
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


def test_goes_spacecraft_selector_includes_legacy_satellites():
    _app()
    win = GoesWindow()

    values = [int(win.spacecraft_cb.itemData(i)) for i in range(win.spacecraft_cb.count())]

    assert values == list(range(8, 20))


def test_goes_set_time_window_prefers_legacy_satellite_for_historic_day():
    _app()
    win = GoesWindow()
    start_dt = datetime(2015, 3, 11, 1, 2)
    end_dt = datetime(2015, 3, 11, 3, 4)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)

    assert ok is True
    assert int(win.spacecraft_cb.currentData()) == 15


def test_load_goes_xrs_range_uses_archive_loader_for_legacy_satellites(monkeypatch):
    start_dt = datetime(2015, 3, 11, 0, 0)
    end_dt = datetime(2015, 3, 11, 0, 2)

    monkeypatch.setattr(
        goes_xrs_gui_module,
        "fetch_goes_overlay",
        lambda **_kwargs: {
            "base_utc": datetime(2015, 3, 11, 0, 0, tzinfo=timezone.utc),
            "series": {
                "xrsa": {
                    "x_seconds": np.array([0.0, 60.0], dtype=float),
                    "flux_wm2": np.array([1e-8, 2e-8], dtype=float),
                },
                "xrsb": {
                    "x_seconds": np.array([0.0, 60.0], dtype=float),
                    "flux_wm2": np.array([2e-8, 3e-8], dtype=float),
                },
            },
        },
    )

    times, xrsa, xrsb, source = goes_xrs_gui_module.load_goes_xrs_range(15, start_dt, end_dt)

    assert tuple(times) == (
        datetime(2015, 3, 11, 0, 0),
        datetime(2015, 3, 11, 0, 1),
    )
    assert np.allclose(xrsa, np.array([1e-8, 2e-8], dtype=float))
    assert np.allclose(xrsb, np.array([2e-8, 3e-8], dtype=float))
    assert source == "GOES-15 XRS archive"


def test_goes_set_time_window_rejects_cross_day():
    _app()
    win = GoesWindow()
    start_dt = datetime(2026, 2, 10, 23, 0)
    end_dt = datetime(2026, 2, 11, 0, 1)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)
    assert ok is False


def test_sep_set_time_window_accepts_cross_day_range():
    _app()
    win = SepWindow()
    start_dt = datetime(2026, 2, 10, 23, 0)
    end_dt = datetime(2026, 2, 11, 1, 15)

    ok = win.set_time_window(start_dt, end_dt, auto_plot=False)

    assert ok is True
    assert win.start_date.date().day() == 10
    assert int(win.start_hour.currentData()) == 23
    assert win.end_date.date().day() == 11
    assert int(win.end_hour.currentData()) == 1
    assert int(win.end_min.currentData()) == 15


def test_sep_cursor_text_tracks_both_channels():
    _app()
    win = SepWindow()
    result = SepProtonRangeData(
        times=(
            datetime(2026, 2, 10, 0, 0, 0),
            datetime(2026, 2, 10, 0, 5, 0),
        ),
        low_flux=(1.0, 2.0),
        high_flux=(10.0, 20.0),
        low_channel_label="P2 (9-12 MeV)",
        high_channel_label="P4 (90-120 MeV)",
        units="pfu",
        spacecraft="GOES-19",
        source_files=("day1.nc",),
    )
    win.current_result = result
    win.canvas.plot_sep(result, result.times[0], result.times[-1])

    mid_x = float(win.canvas._time_nums[0] + win.canvas._time_nums[1]) / 2.0
    text = win._format_cursor_text(mid_x, True)

    assert "UTC = 2026-02-10 00:00:00" in text or "UTC = 2026-02-10 00:05:00" in text
    assert "P2 (9-12 MeV) = " in text
    assert "P4 (90-120 MeV) = " in text
    assert "pfu" in text


def test_sep_selection_updates_analysis_metrics():
    _app()
    win = SepWindow()
    result = SepProtonRangeData(
        times=(
            datetime(2026, 2, 10, 0, 0, 0),
            datetime(2026, 2, 10, 0, 5, 0),
            datetime(2026, 2, 10, 0, 10, 0),
        ),
        low_flux=(1.0, 5.0, 2.0),
        high_flux=(10.0, 12.0, 11.0),
        low_channel_label="P2 (9-12 MeV)",
        high_channel_label="P4 (90-120 MeV)",
        units="pfu",
        spacecraft="GOES-19",
        source_files=("day1.nc",),
    )
    win.current_result = result
    win.canvas.plot_sep(result, result.times[0], result.times[-1])

    class _Evt:
        def __init__(self, xdata):
            self.xdata = xdata

    win.canvas._on_select(_Evt(float(win.canvas._time_nums[0])), _Evt(float(win.canvas._time_nums[-1])))

    assert "2026-02-10 00:00:00" in win.info_selection.text()
    assert win.info_peak_flux.text().startswith("5.000e+00")
    assert "00:05:00" in win.info_peak_time.text()
    assert win.info_event_time.text() == "10m 00s"


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
