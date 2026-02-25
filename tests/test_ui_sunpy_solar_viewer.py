"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import warnings

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

import pyqtgraph as pg

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from src.Backend.sunpy_archive import (
    DATA_KIND_MAP,
    DATA_KIND_TIMESERIES,
    SunPyFetchResult,
    SunPyLoadResult,
    SunPyQuerySpec,
    SunPySearchResult,
    SunPySearchRow,
)
from src.UI.sunpy_plot_window import SunPyPlotWindow
from src.UI import sunpy_solar_viewer as viewer_mod
from src.UI.sunpy_solar_viewer import SunPySolarViewer


def _app():
    return QApplication.instance() or QApplication([])


def _sample_query():
    return SunPyQuerySpec(
        start_dt=datetime(2026, 2, 10, 1, 0, 0),
        end_dt=datetime(2026, 2, 10, 2, 0, 0),
        spacecraft="SDO",
        instrument="AIA",
        wavelength_angstrom=193.0,
    )


def _sample_search_result(rows: list[SunPySearchRow] | None = None, data_kind: str = DATA_KIND_MAP):
    values = rows or []
    return SunPySearchResult(
        spec=_sample_query(),
        data_kind=data_kind,
        rows=values,
        raw_response=[],
        row_index_map=[],
    )


def _fake_map_sequence(values: list[float]):
    class FakeMap:
        observatory = "SDO"
        instrument = "AIA"
        detector = ""
        wavelength = "193 Angstrom"
        date = "2026-02-10T01:00:00"

        def __init__(self, value):
            self.data = np.full((8, 8), value, dtype=float)

    class FakeSequence:
        def __init__(self, vals):
            self.maps = [FakeMap(v) for v in vals]

    return FakeSequence(values)


def _fake_euvi_map_sequence(values: list[float]):
    class FakeMap:
        observatory = "STEREO_A"
        instrument = "SECCHI"
        detector = "EUVI"
        wavelength = "195 Angstrom"
        date = "2026-02-10T01:00:00"

        def __init__(self, value):
            self.data = np.full((8, 8), value, dtype=float)

    class FakeSequence:
        def __init__(self, vals):
            self.maps = [FakeMap(v) for v in vals]

    return FakeSequence(values)


def test_constructs_and_toggles_control_visibility():
    _app()
    win = SunPySolarViewer()
    win.show()
    QApplication.processEvents()
    assert win.spacecraft_combo.currentText() == "SDO"
    assert win.wavelength_combo.isVisible() is True
    assert win.detector_combo.isVisible() is False
    assert win.satellite_combo.isVisible() is False

    win.spacecraft_combo.setCurrentText("GOES")
    QApplication.processEvents()
    assert win.instrument_combo.currentText() == "XRS"
    assert win.satellite_combo.isVisible() is True
    assert win.wavelength_combo.isVisible() is False
    win.close()


def test_set_time_window_validation():
    _app()
    win = SunPySolarViewer()
    start = datetime(2026, 2, 10, 1, 0, 0)
    end = datetime(2026, 2, 10, 2, 0, 0)
    assert win.set_time_window(start, end, auto_query=False) is True
    assert win.set_time_window(end, start, auto_query=False) is False
    win.close()


def test_search_results_population_and_selection():
    _app()
    win = SunPySolarViewer()
    rows = [
        SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 1, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="a.fits",
            size="1 MB",
        ),
        SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 2, 0),
            end=datetime(2026, 2, 10, 1, 3, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="b.fits",
            size="1 MB",
        ),
    ]
    result = _sample_search_result(rows=rows, data_kind=DATA_KIND_MAP)
    win._on_search_finished(result)

    assert win.results_table.rowCount() == 2
    assert win.download_load_btn.isEnabled() is True
    assert win._checked_rows() == [0, 1]

    win.clear_all_rows()
    assert win._checked_rows() == []
    win.select_all_rows()
    assert win._checked_rows() == [0, 1]
    win.close()


def test_map_load_auto_opens_detached_window_and_reuses_instance():
    _app()
    win = SunPySolarViewer()

    fetch_result = SunPyFetchResult(paths=["/tmp/a.fits", "/tmp/b.fits"], requested_count=2, failed_count=0, errors=[])
    load_result = SunPyLoadResult(
        data_kind=DATA_KIND_MAP,
        paths=["/tmp/a.fits", "/tmp/b.fits"],
        maps_or_timeseries=_fake_map_sequence([1.0, 3.0]),
        metadata={},
    )

    win._on_load_finished(fetch_result, load_result)
    QApplication.processEvents()
    assert win._plot_window is not None
    assert win._plot_window.isVisible() is True
    assert win._analysis_payload.get("kind") == "map"

    first_plot_window = win._plot_window

    load_result_2 = SunPyLoadResult(
        data_kind=DATA_KIND_MAP,
        paths=["/tmp/c.fits"],
        maps_or_timeseries=_fake_map_sequence([4.0]),
        metadata={},
    )
    win._on_load_finished(fetch_result, load_result_2)
    QApplication.processEvents()
    assert win._plot_window is first_plot_window
    win.close()


def test_timeseries_load_updates_analysis_payload_and_switches_plot_mode():
    _app()
    pd = pytest.importorskip("pandas")
    win = SunPySolarViewer()

    base = datetime(2026, 2, 10, 1, 0, 0)
    index = [base + timedelta(minutes=i) for i in range(4)]
    frame = pd.DataFrame(
        {
            "xrsa": [1e-7, 2e-7, 4e-7, 2e-7],
            "xrsb": [2e-7, 3e-7, 2e-6, 7e-7],
        },
        index=index,
    )

    class FakeTimeSeries:
        @staticmethod
        def to_dataframe():
            return frame

    fetch_result = SunPyFetchResult(paths=["/tmp/xrs.csv"], requested_count=1, failed_count=0, errors=[])
    load_result = SunPyLoadResult(
        data_kind=DATA_KIND_TIMESERIES,
        paths=["/tmp/xrs.csv"],
        maps_or_timeseries=FakeTimeSeries(),
        metadata={},
    )

    win._on_load_finished(fetch_result, load_result)
    QApplication.processEvents()
    assert win._analysis_payload.get("kind") == "timeseries"
    assert "XRS Analysis" in win.analysis_text.toPlainText()
    assert win._plot_window is not None
    assert win._plot_window.map_controls.isVisible() is False
    win.close()


def test_empty_search_result_shows_empty_state_message():
    _app()
    win = SunPySolarViewer()
    win._on_search_finished(_sample_search_result(rows=[]))
    assert "No results found" in win.analysis_text.toPlainText()
    assert win.download_load_btn.isEnabled() is False
    win.close()


def test_safe_meta_text_handles_quantity_and_non_scalars():
    _app()
    astropy_units = pytest.importorskip("astropy.units")
    win = SunPySolarViewer()

    assert "193" in win._safe_meta_text(193 * astropy_units.angstrom)
    assert win._safe_meta_text(np.array([1, 2, 3])).startswith("array(shape=")
    assert win._safe_meta_text(["A", "B", "C"]) == "A, B, C"
    win.close()


def test_plot_window_playback_slider_and_running_difference():
    _app()
    plot = SunPyPlotWindow()
    plot.show()

    sequence = _fake_map_sequence([1.0, 4.0, 7.0])
    plot.set_map_frames(sequence.maps, metadata={})
    QApplication.processEvents()

    assert plot.frame_slider.isEnabled() is True
    assert plot.running_diff_check.isEnabled() is True
    assert plot.rewind_btn.isEnabled() is True

    plot.frame_slider.setValue(1)
    QApplication.processEvents()
    np.testing.assert_allclose(plot.current_map_data(), np.full((8, 8), 4.0))

    plot.running_diff_check.setChecked(True)
    QApplication.processEvents()
    plot.frame_slider.setValue(0)
    QApplication.processEvents()
    np.testing.assert_allclose(plot.current_map_data(), np.full((8, 8), 3.0))
    plot.frame_slider.setValue(1)
    QApplication.processEvents()
    np.testing.assert_allclose(plot.current_map_data(), np.full((8, 8), 3.0))

    plot.running_diff_check.setChecked(False)
    plot.fps_spin.setValue(10)
    plot.frame_slider.setValue(0)
    QApplication.processEvents()
    plot.play_btn.click()
    QTest.qWait(350)
    QApplication.processEvents()

    assert plot.current_frame_index() == 2
    assert plot.pause_btn.isEnabled() is False
    plot.close()


def test_plot_window_rewind_returns_to_first_frame():
    _app()
    plot = SunPyPlotWindow()
    plot.show()
    plot.set_map_frames(_fake_map_sequence([1.0, 2.0, 3.0]).maps, metadata={})
    QApplication.processEvents()

    plot.frame_slider.setValue(2)
    QApplication.processEvents()
    assert plot.current_frame_index() == 2

    plot.rewind_btn.click()
    QApplication.processEvents()
    assert plot.current_frame_index() == 0
    assert plot.frame_slider.value() == 0
    plot.close()


def test_aia_limb_checkbox_enabled_only_for_stereo_euvi():
    _app()
    plot = SunPyPlotWindow()
    plot.show()
    plot.set_map_frames(_fake_map_sequence([1.0]).maps, metadata={})
    QApplication.processEvents()
    assert plot.aia_limb_check.isEnabled() is False
    assert plot.canvas.has_aia_limb_overlay() is False

    plot.set_map_frames(_fake_euvi_map_sequence([1.0]).maps, metadata={})
    QApplication.processEvents()
    assert plot.aia_limb_check.isEnabled() is True
    assert plot.canvas.has_aia_limb_overlay() is False
    plot.close()


def test_aia_limb_overlay_toggle_draws_curve_for_euvi(monkeypatch):
    _app()
    plot = SunPyPlotWindow()
    plot.show()
    plot.set_map_frames(_fake_euvi_map_sequence([1.0]).maps, metadata={})
    QApplication.processEvents()

    monkeypatch.setattr(
        plot,
        "_compute_aia_limb_arcsec",
        lambda **_kw: (np.array([-900.0, 0.0, 900.0]), np.array([0.0, 900.0, 0.0])),
    )
    plot.aia_limb_check.setChecked(True)
    QApplication.processEvents()
    assert plot.canvas.has_aia_limb_overlay() is True

    plot.aia_limb_check.setChecked(False)
    QApplication.processEvents()
    assert plot.canvas.has_aia_limb_overlay() is False
    plot.close()


def test_aia_limb_frame_detection_accepts_secchi_detector_euvi():
    _app()
    plot = SunPyPlotWindow()

    class Frame:
        observatory = "STEREO A"
        instrument = "SECCHI"
        detector = "EUVI"
        nickname = ""
        source = ""

    assert plot._is_stereo_euvi_frame(Frame()) is True
    plot.close()


def test_aia_limb_checkbox_enabled_from_query_metadata_fallback():
    _app()
    plot = SunPyPlotWindow()
    plot.show()

    class Frame:
        observatory = ""
        instrument = ""
        detector = ""
        nickname = ""
        source = ""
        date = "2026-02-10T01:00:00"
        wavelength = "195 Angstrom"

        def __init__(self):
            self.data = np.ones((8, 8), dtype=float)
            self.meta = {}

    plot.set_map_frames(
        [Frame()],
        metadata={"query_spacecraft": "STEREO_A", "query_instrument": "EUVI"},
    )
    QApplication.processEvents()
    assert plot.aia_limb_check.isEnabled() is True
    plot.close()


def test_aia_limb_overlay_draws_with_metadata_fallback(monkeypatch):
    _app()
    plot = SunPyPlotWindow()
    plot.show()

    class Frame:
        observatory = ""
        instrument = ""
        detector = ""
        nickname = ""
        source = ""
        wavelength = "195 Angstrom"
        date = "2026-02-10T01:00:00"

        def __init__(self):
            self.data = np.ones((8, 8), dtype=float)
            self.meta = {}

    plot.set_map_frames(
        [Frame()],
        metadata={"query_spacecraft": "STEREO_A", "query_instrument": "EUVI"},
    )
    QApplication.processEvents()

    monkeypatch.setattr(
        plot,
        "_compute_aia_limb_arcsec",
        lambda **_kw: (np.array([-900.0, 0.0, 900.0]), np.array([0.0, 900.0, 0.0])),
    )
    plot.aia_limb_check.setChecked(True)
    QApplication.processEvents()
    assert plot.canvas.has_aia_limb_overlay() is True
    plot.close()


def test_aia_limb_overlay_real_secchi_frame_renders():
    _app()
    sunpy_map = pytest.importorskip("sunpy.map")
    reproject_pkg = pytest.importorskip("reproject")

    secchi_path = Path(reproject_pkg.__file__).resolve().parent / "tests" / "data" / "secchi_l0_a.fits"
    if not secchi_path.exists():
        pytest.skip("SECCHI reference FITS file is not available in this environment.")

    m = sunpy_map.Map(str(secchi_path))
    plot = SunPyPlotWindow()
    plot.show()
    plot.set_map_frames([m], metadata={"query_spacecraft": "STEREO_A", "query_instrument": "EUVI"})
    QApplication.processEvents()

    plot.aia_limb_check.setChecked(True)
    QApplication.processEvents()

    assert plot.canvas.has_aia_limb_overlay() is True
    plot.close()


def test_plot_window_timeseries_mode_hides_map_controls():
    _app()
    plot = SunPyPlotWindow()
    plot.set_timeseries(
        [datetime(2026, 2, 10, 1, 0, 0), datetime(2026, 2, 10, 1, 1, 0)],
        channels={"short": np.array([1e-7, 2e-7]), "long": np.array([2e-7, 4e-7])},
        metadata={},
    )
    QApplication.processEvents()
    assert plot.map_controls.isVisible() is False
    plot.close()


def test_plot_window_map_is_equal_aspect_and_timeseries_is_auto():
    _app()
    plot = SunPyPlotWindow()
    plot.show()
    plot.set_map_frames(_fake_map_sequence([1.0, 2.0]).maps, metadata={})
    QApplication.processEvents()

    plot.resize(1100, 760)
    QApplication.processEvents()
    assert abs(plot.canvas.map_plot.width() - plot.canvas.map_plot.height()) <= 1

    assert plot.canvas.map_aspect_locked() is True
    assert plot.canvas.map_axis_labels() == ("Solar X (arcsec)", "Solar Y (arcsec)")
    center_pix = (plot.current_map_data().shape[1] - 1) / 2.0
    x_center_label, y_center_label = plot.canvas.map_arcsec_from_pixel(center_pix, center_pix)
    assert abs(x_center_label) < 1e-6
    assert abs(y_center_label) < 1e-6

    plot.set_timeseries(
        [datetime(2026, 2, 10, 1, 0, 0), datetime(2026, 2, 10, 1, 1, 0)],
        channels={"short": np.array([1e-7, 2e-7]), "long": np.array([2e-7, 4e-7])},
        metadata={},
    )
    QApplication.processEvents()
    assert plot.canvas.is_timeseries_visible() is True
    plot.resize(1120, 760)
    QApplication.processEvents()
    assert abs(plot.width() - plot.height()) >= 20
    plot.close()


def test_plot_window_map_frame_updates_keep_axes_layout_stable():
    _app()
    plot = SunPyPlotWindow()
    plot.show()
    sequence = _fake_map_sequence([1.0, 2.0, 3.0, 4.0])
    plot.set_map_frames(sequence.maps, metadata={})
    QApplication.processEvents()

    initial_rect = plot.canvas.map_view_rect()

    for idx in (1, 2, 3, 2, 1):
        plot.frame_slider.setValue(idx)
        QApplication.processEvents()

    final_rect = plot.canvas.map_view_rect()
    assert abs(initial_rect[2] - final_rect[2]) < 1e-3
    assert abs(initial_rect[3] - final_rect[3]) < 1e-3
    plot.close()


def test_set_map_frames_resets_first_frame_view_range():
    _app()
    plot = SunPyPlotWindow()
    plot.show()

    plot.set_map_frames(_fake_map_sequence([1.0]).maps, metadata={})
    QApplication.processEvents()
    plot.canvas.map_plot.getViewBox().setRange(xRange=(20.0, 28.0), yRange=(20.0, 28.0), padding=0.0)
    QApplication.processEvents()

    plot.set_map_frames(_fake_map_sequence([2.0]).maps, metadata={})
    QApplication.processEvents()
    x0, y0, w, h = plot.canvas.map_view_rect()
    assert abs(x0 + 4.0) < 1e-3
    assert abs(y0 + 4.0) < 1e-3
    assert abs(w - 8.0) < 1e-3
    assert abs(h - 8.0) < 1e-3
    plot.close()


def test_roi_signal_updates_controller_analysis_text():
    _app()
    win = SunPySolarViewer()
    load_result = SunPyLoadResult(
        data_kind=DATA_KIND_MAP,
        paths=["/tmp/a.fits", "/tmp/b.fits"],
        maps_or_timeseries=_fake_map_sequence([1.0, 3.0]),
        metadata={},
    )
    win._apply_loaded_map_result(load_result)
    QApplication.processEvents()

    assert win._plot_window is not None
    win._plot_window.mapRoiChanged.emit((1, 4, 2, 5))
    QApplication.processEvents()
    assert "ROI: x=[1,4], y=[2,5]" in win.analysis_text.toPlainText()
    win.close()


def test_plot_canvas_not_using_tight_layout():
    _app()
    plot = SunPyPlotWindow()
    assert plot.canvas.backend_name() == "pyqtgraph"
    assert isinstance(plot.canvas.opengl_enabled(), bool)
    assert bool(pg.getConfigOption("useOpenGL")) == plot.canvas.opengl_enabled()
    assert plot.canvas.map_plot.getViewBox().state.get("mouseEnabled") == [False, False]
    assert plot.canvas.ts_plot.getViewBox().state.get("mouseEnabled") == [False, False]
    plot.close()


def test_worker_progress_indeterminate_then_smooth_target():
    _app()
    win = SunPySolarViewer()

    win._set_busy(True, "Test")
    win._on_worker_progress(None, "Searching...")
    assert win.progress.maximum() == 0

    win._on_worker_progress(60, "Fetched")
    QApplication.processEvents()
    assert win.progress.maximum() == 100
    assert win._progress_target == 60
    assert win._progress_timer.isActive() is True
    win.close()


def test_worker_no_download_paths_includes_fetch_error_details(monkeypatch, tmp_path):
    _app()
    rows = [
        SunPySearchRow(
            start=datetime(2026, 2, 10, 1, 0, 0),
            end=datetime(2026, 2, 10, 1, 1, 0),
            source="SDO",
            instrument="AIA",
            provider="VSO",
            fileid="a.fits",
            size="1 MB",
        )
    ]
    search_result = SunPySearchResult(
        spec=_sample_query(),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=[],
        row_index_map=[],
    )

    def fake_fetch(_search_result, _cache_dir, selected_rows=None, progress_cb=None):
        return SunPyFetchResult(
            paths=[],
            requested_count=len(selected_rows or []),
            failed_count=1,
            errors=["Row 1: Timeout on reading data from socket"],
        )

    monkeypatch.setattr(viewer_mod, "fetch", fake_fetch)
    worker = viewer_mod.SunPyWorker(
        "fetch_load",
        search_result=search_result,
        selected_rows=[0],
        cache_dir=tmp_path,
    )
    failures: list[str] = []
    worker.failed.connect(lambda tb: failures.append(str(tb)))
    worker.run()

    assert failures
    assert "No files could be downloaded from the selected records." in failures[0]
    assert "Timeout on reading data from socket" in failures[0]


def test_map_load_does_not_emit_tight_layout_warning():
    _app()
    win = SunPySolarViewer()
    fetch_result = SunPyFetchResult(paths=["/tmp/a.fits"], requested_count=1, failed_count=0, errors=[])
    load_result = SunPyLoadResult(
        data_kind=DATA_KIND_MAP,
        paths=["/tmp/a.fits"],
        maps_or_timeseries=_fake_map_sequence([1.0]),
        metadata={},
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        win._on_load_finished(fetch_result, load_result)
        QApplication.processEvents()

    assert not any("Tight layout not applied" in str(w.message) for w in caught)
    win.close()


def test_sunpy_viewer_close_removes_cache_folder(tmp_path):
    _app()
    win = SunPySolarViewer()
    cache_dir = tmp_path / "sunpy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "payload.tmp").write_text("cache", encoding="utf-8")
    win.cache_dir = cache_dir
    win.close()
    QApplication.processEvents()

    assert cache_dir.exists() is False


def test_sunpy_viewer_close_ignored_while_worker_thread_running(monkeypatch):
    _app()
    win = SunPySolarViewer()

    class _FakeRunningThread:
        def __init__(self):
            self.quit_calls = 0
            self.wait_calls: list[int] = []

        def isRunning(self):
            return True

        def quit(self):
            self.quit_calls += 1

        def wait(self, timeout_ms=0):
            self.wait_calls.append(int(timeout_ms))
            return False

    fake_thread = _FakeRunningThread()
    win._active_thread = fake_thread  # type: ignore[assignment]

    cleanup_called = {"value": False}
    monkeypatch.setattr(win, "_cleanup_cache_dir", lambda: cleanup_called.__setitem__("value", True))

    closed = win.close()
    QApplication.processEvents()

    assert closed is False
    assert fake_thread.quit_calls == 1
    assert fake_thread.wait_calls
    assert cleanup_called["value"] is False

    win._active_thread = None
    win.close()
