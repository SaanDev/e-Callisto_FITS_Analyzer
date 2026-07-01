"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.Backend.jsoc_client import SIZE_BIN2, SIZE_CUTOUT, SIZE_FULL
from src.Backend.solar_data_analysis import AiaFrameSet, AiaMetadataRegion
from src.Backend.sunpy_archive import DATA_KIND_MAP, SunPyQuerySpec, SunPySearchResult, SunPySearchRow
from src.UI import solar_data_analysis_window as solar_mod
from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow


def _app():
    return QApplication.instance() or QApplication([])


def _wait_for_worker(win, timeout_ms: int = 5000):
    """Block until a background worker thread finishes and its queued
    completion signals have been delivered on the main thread."""
    thread = getattr(win, "_active_thread", None)
    if thread is not None:
        thread.wait(timeout_ms)
    QApplication.processEvents()
    QApplication.processEvents()


class FakeMap:
    observatory = "SDO"
    instrument = "AIA"
    detector = ""
    wavelength = "193 Angstrom"
    date = "2026-02-10T01:00:00"
    nickname = ""
    source = ""

    def __init__(self, data):
        self.data = np.asarray(data, dtype=float)
        self.meta = {"instrume": "AIA"}


class FakeWcsMap(FakeMap):
    def __init__(self, data):
        super().__init__(data)
        self.meta = {
            "instrume": "AIA",
            "cdelt1": 2.0,
            "cdelt2": 2.0,
            "crpix1": 5.5,
            "crpix2": 5.5,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": 8.0,
        }


def test_solar_data_window_sidebar_keeps_plot_action_visible():
    _app()
    win = SolarDataAnalysisWindow()
    QApplication.processEvents()

    assert win.controls_scroll.minimumWidth() >= 520
    assert win.controls_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert win.plot_mode_btn.isHidden() is False
    assert win.plot_mode_btn.isEnabled() is True
    assert win.plot_mode_btn.objectName() == "SolarPrimaryAction"
    win.close()


def test_solar_data_window_menu_bar_exposes_secondary_actions():
    _app()
    win = SolarDataAnalysisWindow()
    menu_titles = [action.text() for action in win.menuBar().actions()]

    assert "Data" in menu_titles
    assert "Analysis" in menu_titles
    assert "Movie" in menu_titles
    assert "Export" in menu_titles
    assert win.export_regions_action.isEnabled() is False

    data = np.zeros((20, 20), dtype=float)
    data[8:12, 8:12] = 50.0
    win._apply_loaded_frames([FakeMap(data)], paths=["a.fits"], metadata={})
    win.threshold_spin.setValue(90)
    win.min_area_spin.setValue(4)
    win.detect_active_regions()
    QApplication.processEvents()

    assert win.export_regions_action.isEnabled() is True
    win.close()


def test_solar_data_window_archive_results_are_readable():
    _app()
    start = datetime(2026, 2, 10, 1, 0, 0)
    rows = [
        SunPySearchRow(
            start=start + timedelta(minutes=i),
            end=start + timedelta(minutes=i + 1),
            source="SDO",
            instrument="AIA",
            provider="JSOC",
            fileid=f"aia.lev1_euv_12s[{i}]",
            size="12 MB",
            selected=True,
        )
        for i in range(3)
    ]
    result = SunPySearchResult(
        spec=SunPyQuerySpec(start, start + timedelta(minutes=3), "SDO", "AIA", 193.0),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=object(),
        row_index_map=[(0, i) for i in range(3)],
    )

    win = SolarDataAnalysisWindow()
    win._on_search_finished(result)
    QApplication.processEvents()

    assert win.archive_results_group.minimumHeight() >= 300
    assert win.results_table.minimumHeight() >= 225
    assert win.results_table.rowCount() == 3
    assert win.archive_results_status_label.text().startswith("3 record")
    assert win.results_table.item(0, 1).text() == "2026-02-10 01:00:00"
    assert win.results_table.item(0, 4).text() == "aia.lev1_euv_12s[0]"
    assert win._checked_rows() == [0, 1, 2]
    assert win.select_all_results_btn.isEnabled() is True
    assert win.deselect_all_results_btn.isEnabled() is True

    win.deselect_all_results()
    assert win._checked_rows() == []
    win.select_all_results()
    assert win._checked_rows() == [0, 1, 2]
    win.close()


def test_solar_data_window_high_resolution_is_opt_in():
    _app()
    win = SolarDataAnalysisWindow()

    spec = win._build_query_spec()
    assert spec.resolution is None
    assert win.high_resolution_check.isChecked() is False

    win.high_resolution_check.setChecked(True)
    spec = win._build_query_spec()
    assert spec.resolution == 1.0
    win.close()


def test_solar_data_window_warns_before_large_high_resolution_download(monkeypatch):
    _app()
    start = datetime(2026, 2, 10, 1, 0, 0)
    rows = [
        SunPySearchRow(
            start=start + timedelta(minutes=i),
            end=start + timedelta(minutes=i + 1),
            source="SDO",
            instrument="AIA",
            provider="JSOC",
            fileid=f"aia.lev1_euv_12s[{i}]",
            size="12 MB",
            selected=True,
        )
        for i in range(10)
    ]
    result = SunPySearchResult(
        spec=SunPyQuerySpec(start, start + timedelta(minutes=10), "SDO", "AIA", 193.0, resolution=1.0),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=object(),
        row_index_map=[(0, i) for i in range(10)],
    )

    win = SolarDataAnalysisWindow()
    win._on_search_finished(result)
    win.high_resolution_check.setChecked(True)

    monkeypatch.setattr(solar_mod.QMessageBox, "question", staticmethod(lambda *_a, **_k: solar_mod.QMessageBox.No))
    started = []
    monkeypatch.setattr(win, "_start_worker", lambda worker: started.append(worker))
    win.download_and_load_selected()
    assert started == []

    monkeypatch.setattr(solar_mod.QMessageBox, "question", staticmethod(lambda *_a, **_k: solar_mod.QMessageBox.Yes))
    win.download_and_load_selected()
    assert started and started[-1].mode == "fetch_load"
    win.close()


def test_solar_data_window_progress_moves_smoothly():
    _app()
    win = SolarDataAnalysisWindow()

    win._set_busy(True, "Test")
    win._on_worker_progress(None, "Searching...")
    assert win.progress.maximum() == 0

    win._on_worker_progress(60, "Fetched")
    QApplication.processEvents()
    assert win.progress.maximum() == 100
    assert win._progress_target == 60
    assert win._progress_timer.isActive() is True
    win.close()


def test_solar_data_window_download_passes_jsoc_params(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()

    # Pretend a search produced one selectable SDO/AIA row.
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
    win._search_result = SunPySearchResult(
        spec=SunPyQuerySpec(
            start_dt=datetime(2026, 2, 10, 1, 0, 0),
            end_dt=datetime(2026, 2, 10, 2, 0, 0),
            spacecraft="SDO",
            instrument="AIA",
            wavelength_angstrom=193.0,
        ),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "a.fits"}]],
        row_index_map=[(0, 0)],
    )
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])

    captured = {}

    def fake_start_worker(worker):
        captured["worker"] = worker

    monkeypatch.setattr(win, "_start_worker", fake_start_worker)

    win.source_combo.setCurrentIndex(win.source_combo.findData("jsoc"))
    win.jsoc_email_edit.setText("sci@example.org")
    win.download_and_load_selected()

    worker = captured.get("worker")
    assert worker is not None
    assert worker.jsoc_email == "sci@example.org"
    assert worker.prefer_jsoc is True
    win.close()


def _aia_search_result():
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
    return SunPySearchResult(
        spec=SunPyQuerySpec(
            start_dt=datetime(2026, 2, 10, 1, 0, 0),
            end_dt=datetime(2026, 2, 10, 2, 0, 0),
            spacecraft="SDO",
            instrument="AIA",
            wavelength_angstrom=193.0,
        ),
        data_kind=DATA_KIND_MAP,
        rows=rows,
        raw_response=[[{"fileid": "a.fits"}]],
        row_index_map=[(0, 0)],
    )


def test_solar_data_window_cutout_builds_jsoc_process(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._search_result = _aia_search_result()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.jsoc_email_edit.setText("sci@example.org")
    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_CUTOUT))
    win.cutout_x_spin.setValue(100.0)
    win.cutout_y_spin.setValue(-50.0)
    win.cutout_w_spin.setValue(400.0)
    win.cutout_h_spin.setValue(300.0)
    win.download_and_load_selected()

    worker = captured.get("worker")
    assert worker is not None
    assert worker.prefer_jsoc is True
    assert worker.jsoc_process and "im_patch" in worker.jsoc_process
    patch = worker.jsoc_process["im_patch"]
    assert patch["x"] == 100.0 and patch["width"] == 400.0
    win.close()


def test_solar_data_window_binned_requires_email(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win._search_result = _aia_search_result()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])
    started = []
    monkeypatch.setattr(win, "_start_worker", lambda w: started.append(w))
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a))

    win.jsoc_email_edit.setText("")  # no email
    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_BIN2))
    win.download_and_load_selected()

    assert not started      # blocked
    assert info             # user was told to register / pick full disk
    win.close()


def test_solar_data_window_size_estimate_updates(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0, 1, 2])

    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_FULL))
    win._update_size_estimate()
    full_text = win.size_estimate_label.text()
    assert "3 frame" in full_text and "MB" in full_text

    win.frame_size_combo.setCurrentIndex(win.frame_size_combo.findData(SIZE_BIN2))
    win._update_size_estimate()
    assert "JSOC only" in win.size_estimate_label.text()
    win.close()


def _timed_frame(value, *, exptime=2.0, date="2026-02-10T01:00:00"):
    frame = FakeMap(np.full((6, 6), float(value)))
    frame.meta = {"instrume": "AIA", "exptime": exptime}
    frame.date = date
    return frame


def test_solar_data_window_region_lightcurve_dialog(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._map_frames = [
        _timed_frame(10.0, date="2026-02-10T01:00:00"),
        _timed_frame(40.0, date="2026-02-10T01:02:00"),
    ]
    win.crop_check.setChecked(False)
    shown = {}
    monkeypatch.setattr(solar_mod.RegionLightcurveDialog, "show", lambda self: shown.setdefault("ok", True))
    win.show_region_lightcurve()
    assert getattr(win, "_lightcurve_dialog", None) is not None
    assert shown.get("ok") is True
    win.close()


def test_solar_data_window_lightcurve_requires_sequence(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win._map_frames = [_timed_frame(10.0)]  # single frame -> needs a sequence
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a))
    monkeypatch.setattr(solar_mod.RegionLightcurveDialog, "show", lambda self: None)
    win.show_region_lightcurve()
    assert info and getattr(win, "_lightcurve_dialog", None) is None
    win.close()


def test_solar_data_window_radio_reference_window(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()

    class FakeParent:
        def _current_time_window_utc(self):
            return (datetime(2026, 2, 10, 1, 0, 0), datetime(2026, 2, 10, 1, 5, 0))

    monkeypatch.setattr(win, "parent", lambda: FakeParent())
    assert win._radio_reference_window() == (
        datetime(2026, 2, 10, 1, 0, 0),
        datetime(2026, 2, 10, 1, 5, 0),
    )
    win.close()


def test_region_lightcurve_dialog_renders_with_radio_overlay():
    from src.Backend.solar_data_analysis import AiaLightcurve

    _app()
    lc = AiaLightcurve(
        times=[datetime(2026, 2, 10, 1, 0, 0), datetime(2026, 2, 10, 1, 2, 0)],
        values=np.array([5.0, 20.0]),
        bounds=None,
        unit="DN/s",
        statistic="mean",
        wavelength="193 Angstrom",
    )
    dlg = solar_mod.RegionLightcurveDialog(
        lc, radio_window=(datetime(2026, 2, 10, 0, 59, 0), datetime(2026, 2, 10, 1, 1, 0))
    )
    assert dlg.canvas is not None
    dlg.close()


def test_solar_data_window_jsoc_settings_round_trip():
    _app()
    win = SolarDataAnalysisWindow()
    win.jsoc_email_edit.setText("persist@example.org")
    win.source_combo.setCurrentIndex(win.source_combo.findData("vso"))
    win._save_jsoc_settings()
    win.close()

    win2 = SolarDataAnalysisWindow()  # fresh instance restores from settings
    assert win2.jsoc_email_edit.text() == "persist@example.org"
    assert str(win2.source_combo.currentData()) == "vso"
    win2.close()


def test_solar_data_window_close_during_download_cancels_and_defers():
    from PySide6.QtGui import QCloseEvent

    _app()
    win = SolarDataAnalysisWindow()
    worker = solar_mod.SunPyWorker("fetch_load")

    class _FakeRunningThread:
        def isRunning(self):
            return True

    win._active_thread = _FakeRunningThread()
    win._active_worker = worker

    event = QCloseEvent()
    win.closeEvent(event)

    # The download is cancelled and the close is deferred (not destroyed).
    assert worker._cancel_event.is_set() is True
    assert win._pending_close is True
    assert event.isAccepted() is False

    # When the worker thread actually stops, the deferred close completes.
    win._active_thread = None
    win._on_worker_stopped()
    assert win._pending_close is False


def test_solar_data_window_byte_progress_drives_bar_and_defers_ticks():
    from src.Backend.download_manager import AggregateProgress

    _app()
    win = SolarDataAnalysisWindow()
    win._set_busy(True, "Downloading")

    agg = AggregateProgress(
        files_total=2,
        files_done=1,
        bytes_done=50,
        bytes_total=100,
        speed_bps=10,
        eta_seconds=5.0,
    )
    win._on_byte_progress(agg)
    QApplication.processEvents()
    # 1 of 2 files done -> progress_fraction 0.5 maps into the 5..85 band -> 45.
    assert win._byte_active is True
    assert win.progress.value() == 45
    assert win.progress_panel.stats_label.text() != ""

    # A coarse file-count tick inside the download band must NOT overwrite the
    # honest byte bar.
    win._on_worker_progress(70, "Downloading batch 2/4...")
    QApplication.processEvents()
    assert win.progress.value() == 45

    # Crossing into the loading phase (>85) releases byte mode.
    win._on_worker_progress(96, "Finalizing data...")
    QApplication.processEvents()
    assert win._byte_active is False
    win.close()


def test_solar_data_window_progress_pulses_during_long_download(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    clock = {"now": 10.0}
    monkeypatch.setattr(solar_mod.time, "monotonic", lambda: clock["now"])

    win._set_busy(True, "Downloading")
    win._on_worker_progress(5, "Downloading high-resolution batch 1/2...")
    win._progress_value = 5
    win.progress.setValue(5)
    clock["now"] = 11.0
    win._tick_progress()

    assert win.progress.value() == 6
    assert win._progress_activity is True
    win._set_busy(False)
    win.close()


def test_solar_data_window_progress_enters_busy_mode_at_soft_cap(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    clock = {"now": 10.0}
    monkeypatch.setattr(solar_mod.time, "monotonic", lambda: clock["now"])

    win._set_busy(True, "Downloading")
    win._on_worker_progress(5, "Downloading high-resolution batch 1/2...")
    win._progress_value = 86
    win._progress_target = 5
    win._progress_soft_cap = 86
    win.progress.setValue(86)
    clock["now"] = 11.0
    win._tick_progress()

    assert win.progress.maximum() == 0
    win._set_busy(False)
    win.close()


def test_solar_data_stop_active_operation_calls_worker_cancel(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()

    class FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    fake_worker = FakeWorker()
    win._active_worker = fake_worker  # type: ignore[assignment]
    monkeypatch.setattr(win, "is_operation_running", lambda: True)
    win.stop_btn.setEnabled(True)
    win.stop_action.setEnabled(True)

    win.stop_active_operation()

    assert fake_worker.cancelled is True
    assert win.stop_btn.isEnabled() is False
    assert win.stop_action.isEnabled() is False
    monkeypatch.setattr(win, "is_operation_running", lambda: False)
    win.close()


def test_solar_data_window_loads_local_fake_maps(monkeypatch):
    _app()

    progress = []

    def fake_load(paths, *, progress_cb=None, cancel_cb=None):
        if progress_cb is not None:
            for i in range(len(paths)):
                progress_cb(i + 1, len(paths))
        return AiaFrameSet(
            paths=list(paths),
            maps=[FakeMap(np.ones((8, 8))), FakeMap(np.full((8, 8), 3.0))],
            metadata={"n_frames": 2, "instrument": "AIA"},
        )

    monkeypatch.setattr(solar_mod, "load_aia_maps_streaming", fake_load)
    win = SolarDataAnalysisWindow()
    win.load_local_paths(["a.fits", "b.fits"])
    _wait_for_worker(win)

    assert len(win._map_frames) == 2
    assert not hasattr(win, "_plot_window")
    assert win.canvas.map_image.image is not None
    assert win.play_btn.isEnabled() is True
    assert win.export_movie_btn.isEnabled() is True

    win.show()
    win.resize(1500, 900)
    QApplication.processEvents()
    view_w, view_h = win.canvas.map_viewbox_size()
    assert abs(view_w - view_h) <= 1
    assert win.canvas.map_background_lightness() > 180
    assert win.canvas.map_low_color_lightness() < 80
    assert win.canvas.has_visible_colorbar() is True
    assert win.canvas.map_axis_labels() == ("Solar X (arcsec)", "Solar Y (arcsec)")
    assert win.canvas.map_plot.getAxis("bottom").autoSIPrefix is False
    assert win.canvas.map_plot.getAxis("left").autoSIPrefix is False
    assert win.colormap_combo.currentText() == "sdoaia193"
    assert win._resolved_colormap_name() == "sdoaia193"
    assert "8 x 8 px" in win.analysis_text.toPlainText()

    win.colorbar_check.setChecked(False)
    QApplication.processEvents()
    assert win.canvas.has_visible_colorbar() is False
    win.colorbar_check.setChecked(True)
    QApplication.processEvents()
    assert win.canvas.has_visible_colorbar() is True

    win.colormap_combo.setCurrentText("sdoaia193")
    QApplication.processEvents()
    assert win.canvas.colormap_name() == "sdoaia193"
    win.close()


def test_solar_data_window_matplotlib_renderer_is_light_and_square(monkeypatch):
    _app()

    def fake_load(paths, *, progress_cb=None, cancel_cb=None):
        data = np.zeros((12, 12), dtype=float)
        data[4:8, 4:8] = 50.0
        return AiaFrameSet(paths=list(paths), maps=[FakeMap(data)], metadata={"n_frames": 1, "instrument": "AIA"})

    monkeypatch.setattr(solar_mod, "load_aia_maps_streaming", fake_load)
    win = SolarDataAnalysisWindow()
    win.show()
    win.resize(1500, 900)
    win.renderer_combo.setCurrentText("Matplotlib")
    win.load_local_paths(["a.fits"])
    _wait_for_worker(win)

    canvas = win._active_canvas()
    assert canvas.backend_name() == "matplotlib"
    assert canvas.has_plot_content() is True
    assert canvas.map_background_lightness() > 180
    assert canvas.map_low_color_lightness() < 80
    assert win.colormap_combo.currentText() == "sdoaia193"
    assert canvas.colormap_name() == "sdoaia193"
    assert canvas.has_visible_colorbar() is True
    view_w, view_h = canvas.map_viewbox_size()
    assert abs(view_w - view_h) <= 2
    assert canvas.map_axis_labels() == ("Solar X (arcsec)", "Solar Y (arcsec)")
    win.close()


def test_solar_data_window_defaults_colormap_to_loaded_aia_wavelength():
    _app()
    frame = FakeMap(np.ones((6, 6), dtype=float))
    frame.wavelength = "171 Angstrom"

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([frame], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()

    assert win.colormap_combo.currentText() == "sdoaia171"
    assert win.canvas.colormap_name() == "sdoaia171"
    win.wavelength_combo.setCurrentText("AIA 193 A")
    QApplication.processEvents()
    assert win.colormap_combo.currentText() == "sdoaia171"
    win.close()


def test_solar_data_window_detects_regions_and_draws_overlays():
    _app()
    data = np.zeros((30, 30), dtype=float)
    data[6:12, 7:13] = 100.0
    data[20:25, 19:24] = 200.0

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(data)], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()

    win.threshold_spin.setValue(95)
    win.min_area_spin.setValue(8)
    win.detect_active_regions()
    QApplication.processEvents()

    assert win.region_table.rowCount() == 2
    assert not hasattr(win, "_plot_window")
    assert win.canvas.region_overlay_count() >= 2
    win.close()


def test_solar_data_metadata_labels_existing_regions():
    _app()
    data = np.zeros((20, 20), dtype=float)
    data[8:12, 8:12] = 50.0

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(data)], paths=["a.fits"], metadata={})
    QApplication.processEvents()
    win.threshold_spin.setValue(90)
    win.min_area_spin.setValue(4)
    win.detect_active_regions()
    assert win._regions

    region = win._regions[0]
    win._on_metadata_finished(
        [
            AiaMetadataRegion(
                label="NOAA 12345",
                noaa_number="12345",
                center_x_arcsec=region.centroid_x_arcsec,
                center_y_arcsec=region.centroid_y_arcsec,
                source="HEK",
            )
        ]
    )
    QApplication.processEvents()

    assert win.region_table.item(0, 1).text() == "NOAA 12345"
    assert win.region_table.item(0, 2).text() == "12345"
    win.close()


def test_solar_data_window_applies_axis_coordinate_crop():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    win.crop_check.setChecked(True)
    win.crop_x0_spin.setValue(-2.0)
    win.crop_x1_spin.setValue(2.0)
    win.crop_y0_spin.setValue(-2.0)
    win.crop_y1_spin.setValue(2.0)
    win.apply_axis_crop()
    QApplication.processEvents()

    assert win._map_frames[0].data.shape == (5, 5)
    assert win.canvas.map_image.image is not None
    win.close()


def test_solar_data_window_crops_from_coordinates_without_checkbox():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    # The "Rectangle crop" checkbox is OFF; typing bounds + Apply must still work.
    assert win.crop_check.isChecked() is False
    win.crop_x0_spin.setValue(-2.0)
    win.crop_x1_spin.setValue(2.0)
    win.crop_y0_spin.setValue(-2.0)
    win.crop_y1_spin.setValue(2.0)
    win.apply_axis_crop()
    QApplication.processEvents()

    assert win._map_frames[0].data.shape == (5, 5)
    win.close()


def test_solar_data_window_apply_crop_without_frames_informs(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a))
    win.apply_axis_crop()  # no frames loaded
    assert info  # informed, no crash, no "enable checkbox" message
    win.close()


def test_solar_data_window_clip_sliders_drive_live_render(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    # Slider values are exposed as float percent, like the old spin boxes.
    assert win.clip_low_slider.value() == 1.0
    assert win.clip_high_slider.value() == 99.9
    win.clip_low_slider.setValue(5.0)
    assert win.clip_low_slider.value() == 5.0
    assert "5.0%" in win.clip_low_slider.readout.text()

    # Dragging (moving the underlying QSlider) schedules a throttled live render.
    renders = {"n": 0}
    monkeypatch.setattr(win, "_render_current_frame", lambda: renders.__setitem__("n", renders["n"] + 1))
    win.clip_high_slider.slider.setValue(950)  # 95.0% drag -> valueChanged -> _schedule_clip_render
    QApplication.processEvents()
    assert renders["n"] >= 1  # rendered immediately on the leading edge
    assert win.clip_high_slider.value() == 95.0
    win.close()


def test_solar_data_window_uses_fits_wcs_for_crop_coordinates():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    assert win._current_axis_transform["x_scale_arcsec_per_pix"] == 2.0
    assert win._current_axis_transform["y_scale_arcsec_per_pix"] == 2.0
    assert win.canvas.map_view_rect()[2] == 20.0

    win.crop_check.setChecked(True)
    win.crop_x0_spin.setValue(-4.0)
    win.crop_x1_spin.setValue(4.0)
    win.crop_y0_spin.setValue(-4.0)
    win.crop_y1_spin.setValue(4.0)
    bounds = win._crop_bounds_from_axis_fields((10, 10))

    assert bounds == (2, 7, 2, 7)
    win.apply_axis_crop()
    QApplication.processEvents()
    assert win._map_frames[0].data.shape == (5, 5)
    assert win._current_axis_transform["x_ref_pix"] == 2.5
    assert win._current_axis_transform["y_ref_pix"] == 2.5
    assert win.canvas.map_view_rect()[2] == 10.0
    win.close()


def test_solar_data_window_save_selected_to_disk(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QFileDialog

    win = SolarDataAnalysisWindow()
    win._search_result = _aia_search_result()
    monkeypatch.setattr(win, "_checked_rows", lambda: [0])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.save_selected_to_disk()

    worker = captured.get("worker")
    assert worker is not None
    assert str(worker.cache_dir) == str(tmp_path)   # downloads into the chosen folder
    assert win._save_target_dir == str(tmp_path)
    win.close()


def test_solar_data_window_reset_all_clears_state_and_cache(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win.cache_dir = tmp_path
    (tmp_path / "cached_frame.fits").write_bytes(b"x" * 16)

    # Dirty the state and a few controls.
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    win._search_result = _aia_search_result()
    win.wavelength_combo.setCurrentText("AIA 304 A")
    win.clip_low_slider.slider.setValue(120)   # 12.0%
    win.max_records_spin.setValue(500)
    QApplication.processEvents()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.reset_all()
    QApplication.processEvents()

    # State cleared.
    assert win._map_frames == [] and win._search_result is None
    assert win.results_table.rowCount() == 0
    # Controls back to defaults.
    assert win.wavelength_combo.currentText() == "AIA 193 A"
    assert win.clip_low_slider.value() == 1.0
    assert win.clip_high_slider.value() == 99.9
    assert win.max_records_spin.value() == 120
    # Cache deleted.
    assert not (tmp_path / "cached_frame.fits").exists()
    win.close()


def test_solar_data_window_reset_all_declined_keeps_state(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win.cache_dir = tmp_path
    (tmp_path / "keep.fits").write_bytes(b"x" * 8)
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.reset_all()

    assert win._map_frames != []                 # nothing cleared
    assert (tmp_path / "keep.fits").exists()      # cache untouched
    win.close()


def test_solar_data_window_query_spec_hmi_observable():
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("HMI Magnetogram")
    assert idx >= 0
    win.wavelength_combo.setCurrentIndex(idx)
    spec = win._build_query_spec()
    assert spec.instrument == "HMI"
    assert spec.product == "magnetogram"
    assert spec.wavelength_angstrom is None
    # HMI default colormap is the bipolar magnetogram map.
    assert win._default_aia_colormap_name() == "hmimag"
    win.close()


def test_solar_data_window_query_spec_aia_observable():
    _app()
    win = SolarDataAnalysisWindow()
    win.wavelength_combo.setCurrentText("AIA 304 A")
    spec = win._build_query_spec()
    assert spec.instrument == "AIA"
    assert spec.wavelength_angstrom == 304.0
    assert spec.product is None
    win.close()


def _lasco_frame(data=None, detector="C2"):
    frame = FakeMap(np.ones((6, 6), dtype=float) if data is None else data)
    frame.observatory = "SOHO"
    frame.instrument = "LASCO"
    frame.detector = detector
    frame.wavelength = None
    frame.meta = {"instrume": "LASCO"}
    return frame


def test_solar_data_window_query_spec_lasco_observable():
    _app()
    win = SolarDataAnalysisWindow()
    for detector, cmap in (("C2", "soholasco2"), ("C3", "soholasco3")):
        idx = win.wavelength_combo.findText(f"SOHO/LASCO {detector}")
        assert idx >= 0
        win.wavelength_combo.setCurrentIndex(idx)
        spec = win._build_query_spec()
        assert spec.spacecraft == "SOHO"
        assert spec.instrument == "LASCO"
        assert spec.detector == detector
        # Coronagraph: no EUV wavelength, no JSOC resolution, no HMI product.
        assert spec.wavelength_angstrom is None
        assert spec.resolution is None
        assert spec.product is None
        assert win._default_aia_colormap_name() == cmap
    win.close()


def test_solar_data_window_lasco_disables_sdo_download_controls():
    _app()
    win = SolarDataAnalysisWindow()
    idx = win.wavelength_combo.findText("SOHO/LASCO C2")
    win.wavelength_combo.setCurrentIndex(idx)
    # LASCO is VSO-only, full-disk only: JSOC / frame-size / high-res are off.
    assert not win.source_combo.isEnabled()
    assert not win.jsoc_email_edit.isEnabled()
    assert not win.frame_size_combo.isEnabled()
    assert not win.high_resolution_check.isEnabled()
    assert win.frame_size_combo.currentData() == SIZE_FULL
    # Switching back to an SDO/AIA observable restores them.
    win.wavelength_combo.setCurrentText("AIA 193 A")
    assert win.source_combo.isEnabled()
    assert win.frame_size_combo.isEnabled()
    assert win.high_resolution_check.isEnabled()
    win.close()


def test_solar_data_window_lasco_gates_euv_only_tools():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([_lasco_frame(detector="C2")], paths=["c2.fts"], metadata={})
    QApplication.processEvents()

    assert win._loaded_is_lasco() is True
    assert win._loaded_instrument_label() == "LASCO C2"
    assert "C2" in win._frame_title(win._map_frames[0], 0)
    assert win.colormap_combo.currentText() == "soholasco2"

    # EUV/disk-only tools are disabled for a coronagraph sequence...
    assert not win.composite_btn.isEnabled()
    assert not win.magnetogram_btn.isEnabled()
    assert not win.detect_regions_btn.isEnabled()
    assert not win.composite_action.isEnabled()
    assert not win.detect_regions_action.isEnabled()
    # ...while the mission-agnostic tools stay available.
    assert win.difference_mode_btn.isEnabled()
    assert win.crop_check.isEnabled()
    assert win.export_movie_btn.isEnabled()
    assert win.lightcurve_btn.isEnabled()

    # Loading an SDO/AIA sequence re-enables the EUV tools.
    win._apply_loaded_frames([FakeMap(np.ones((6, 6)))], paths=["a.fits"], metadata={"instrument": "AIA"})
    QApplication.processEvents()
    assert win._loaded_is_lasco() is False
    assert win.composite_btn.isEnabled()
    assert win.detect_regions_btn.isEnabled()
    win.close()


def test_solar_data_window_composite_uses_magnetogram_overlay(monkeypatch):
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.random.rand(16, 16) * 100.0)], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    mag = np.zeros((16, 16), dtype=float)
    mag[2:6, 2:6] = 400.0
    mag[10:14, 10:14] = -400.0
    win._overlay_magnetogram = FakeMap(mag)
    win.show_composite_plot()
    QApplication.processEvents()

    # Composite frame is RGB with polarity contours overlaid.
    frame = win._map_frames[0]
    assert frame.data.ndim == 3 and frame.data.shape[-1] == 3
    assert "magnetogram" in win.analysis_text.toPlainText().lower()
    win.close()


def test_solar_data_window_sorts_uploaded_frames_by_time(monkeypatch):
    _app()

    def _frame(value, date):
        f = FakeMap(np.full((6, 6), float(value)))
        f.date = date
        return f

    # Provided out of chronological order (values tag the intended time order).
    shuffled = [
        _frame(3, "2026-02-10T01:04:00"),
        _frame(1, "2026-02-10T01:00:00"),
        _frame(4, "2026-02-10T01:06:00"),
        _frame(2, "2026-02-10T01:02:00"),
    ]

    def fake_load(paths, *, progress_cb=None, cancel_cb=None):
        return AiaFrameSet(paths=list(paths), maps=shuffled, metadata={"n_frames": 4, "instrument": "AIA"})

    monkeypatch.setattr(solar_mod, "load_aia_maps_streaming", fake_load)
    win = SolarDataAnalysisWindow()
    win.load_local_paths(["c.fits", "a.fits", "d.fits", "b.fits"])
    _wait_for_worker(win)

    values = [float(f.data[0, 0]) for f in win._map_frames]
    assert values == [1.0, 2.0, 3.0, 4.0]   # chronological, not upload order
    assert [float(f.data[0, 0]) for f in win._original_frames] == [1.0, 2.0, 3.0, 4.0]
    win.close()


def test_solar_data_window_sort_keeps_untimed_frames_last():
    _app()
    win = SolarDataAnalysisWindow()

    def _frame(value, date=None):
        f = FakeMap(np.full((4, 4), float(value)))
        f.date = date  # None -> no observation time
        return f

    frames = [_frame(9, None), _frame(2, "2026-02-10T01:02:00"), _frame(1, "2026-02-10T01:00:00")]
    ordered = win._sort_frames_by_time(frames)
    vals = [float(f.data[0, 0]) for f in ordered]
    assert vals == [1.0, 2.0, 9.0]   # timed sorted first, untimed kept at the end
    win.close()


def test_solar_data_window_export_movie_runs_in_background(monkeypatch, tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames(
        [FakeMap(np.ones((8, 8))), FakeMap(np.full((8, 8), 2.0))], paths=["a.fits"], metadata={}
    )
    QApplication.processEvents()

    out = str(tmp_path / "m.mp4")
    monkeypatch.setattr(solar_mod, "pick_export_path", lambda *a, **k: (out, ""))
    monkeypatch.setattr(solar_mod, "_imageio_ffmpeg_available", lambda: True)
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.scale_combo.setCurrentText("log")
    win.export_movie()

    worker = captured.get("worker")
    assert isinstance(worker, solar_mod.MovieExportWorker)   # not blocking on the UI thread
    assert worker._spec.path == out
    assert worker._spec.scale == "log"
    assert worker._spec.percentile_low == win.clip_low_slider.value()
    win.close()


def test_solar_data_window_export_movie_offers_gif_without_ffmpeg(monkeypatch, tmp_path):
    _app()
    from PySide6.QtWidgets import QMessageBox

    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeMap(np.ones((8, 8)))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    monkeypatch.setattr(solar_mod, "pick_export_path", lambda *a, **k: (str(tmp_path / "m.mp4"), ""))
    monkeypatch.setattr(solar_mod, "_imageio_ffmpeg_available", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    captured = {}
    monkeypatch.setattr(win, "_start_worker", lambda w: captured.__setitem__("worker", w))

    win.export_movie()

    worker = captured.get("worker")
    assert worker is not None
    assert worker._spec.path.endswith(".gif")   # fell back to GIF
    win.close()


def test_solar_data_window_export_progress_updates_bar():
    _app()
    win = SolarDataAnalysisWindow()
    win._set_busy(True, "Exporting")
    win._on_export_progress(3, 12)
    assert win.progress.value() == 25                       # 3/12
    assert "3 of 12" in win.progress_panel.stats_label.text()
    win.close()


def test_solar_data_window_cropped_image_fills_view():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    win.crop_x0_spin.setValue(-4.0)
    win.crop_x1_spin.setValue(4.0)
    win.crop_y0_spin.setValue(-4.0)
    win.crop_y1_spin.setValue(4.0)
    win.apply_axis_crop()
    QApplication.processEvents()

    # Regression: the cropped image used to be scaled by the *previous* frame's
    # pixel size (setRect before setImage) and shrink into the bottom-left
    # corner. The image extent must now match the zoomed view exactly.
    img = win.pyqt_canvas.map_image
    irect = img.mapRectToView(img.boundingRect()).getRect()
    vrect = win.pyqt_canvas.map_view_rect()
    assert abs(irect[2] - vrect[2]) < 1e-6  # width fills the view
    assert abs(irect[3] - vrect[3]) < 1e-6  # height fills the view
    win.close()


def test_solar_data_window_crops_from_interactive_rectangle():
    _app()
    win = SolarDataAnalysisWindow()
    win._apply_loaded_frames([FakeWcsMap(np.arange(100, dtype=float).reshape(10, 10))], paths=["a.fits"], metadata={})
    QApplication.processEvents()

    win.renderer_combo.setCurrentText("Matplotlib")
    QApplication.processEvents()
    assert win._active_canvas().backend_name() == "matplotlib"

    win.crop_check.setChecked(True)
    QApplication.processEvents()
    assert win._active_canvas().backend_name() == "pyqtgraph"
    assert win.pyqt_canvas.roi_selector_active() is True

    win.pyqt_canvas.set_roi_arcsec_bounds(-4.0, 4.0, -4.0, 4.0)
    QApplication.processEvents()
    roi_bounds = win.pyqt_canvas._roi_bounds

    assert roi_bounds == (3, 7, 3, 7)
    assert win._crop_bounds_from_axis_fields((10, 10)) == roi_bounds

    win.apply_axis_crop()
    QApplication.processEvents()
    assert win._map_frames[0].data.shape == (4, 4)
    assert win.crop_check.isChecked() is False
    assert win.pyqt_canvas.roi_selector_active() is False
    win.close()
