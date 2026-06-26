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

from src.Backend.solar_data_analysis import AiaFrameSet, AiaMetadataRegion
from src.Backend.sunpy_archive import DATA_KIND_MAP, SunPyQuerySpec, SunPySearchResult, SunPySearchRow
from src.UI import solar_data_analysis_window as solar_mod
from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow


def _app():
    return QApplication.instance() or QApplication([])


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

    def fake_load(paths):
        return AiaFrameSet(
            paths=list(paths),
            maps=[FakeMap(np.ones((8, 8))), FakeMap(np.full((8, 8), 3.0))],
            metadata={"n_frames": 2, "instrument": "AIA"},
        )

    monkeypatch.setattr(solar_mod, "load_aia_maps", fake_load)
    win = SolarDataAnalysisWindow()
    win.load_local_paths(["a.fits", "b.fits"])
    QApplication.processEvents()

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

    def fake_load(paths):
        data = np.zeros((12, 12), dtype=float)
        data[4:8, 4:8] = 50.0
        return AiaFrameSet(paths=list(paths), maps=[FakeMap(data)], metadata={"n_frames": 1, "instrument": "AIA"})

    monkeypatch.setattr(solar_mod, "load_aia_maps", fake_load)
    win = SolarDataAnalysisWindow()
    win.show()
    win.resize(1500, 900)
    win.renderer_combo.setCurrentText("Matplotlib")
    win.load_local_paths(["a.fits"])
    QApplication.processEvents()

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
