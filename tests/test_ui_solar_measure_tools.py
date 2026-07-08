"""
e-CALLISTO FITS Analyzer
Offscreen tests for the Solar Image Analysis measurement tools
(src/UI/solar_measure_tools.py + window wiring).

Clicks are driven through MeasurementController.on_canvas_click with data
(arcsec) coordinates — no synthetic QGraphicsScene events, which are unreliable
offscreen.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication

from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow


def _app():
    return QApplication.instance() or QApplication([])


class WcsMap:
    """Minimal frame with a full arcsec WCS (2"/px, centre pixel 5,5 -> 0,0)."""

    observatory = "SDO"
    instrument = "AIA"
    detector = ""
    wavelength = "193 Angstrom"
    nickname = ""
    source = ""

    def __init__(self, data, *, date="2026-02-10T01:00:00"):
        self.data = np.asarray(data, dtype=float)
        self.date = date
        self.meta = {
            "instrume": "AIA",
            "cdelt1": 2.0,
            "cdelt2": 2.0,
            "crpix1": 6.0,  # FITS 1-based -> pixel 5 is disk centre
            "crpix2": 6.0,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": 8.0,
        }


class CorWcsMap(WcsMap):
    observatory = "STEREO_A"
    instrument = "SECCHI"
    detector = "COR2"
    wavelength = ""

    def __init__(self, data, *, date="2012-07-12T16:00:00"):
        super().__init__(data, date=date)
        self.meta.update({"instrume": "SECCHI", "detector": "COR2", "polar": 1001.0})


def _load(win, frames, n=None):
    paths = [f"f{i}.fits" for i in range(len(frames))]
    win._apply_loaded_frames(frames, paths=paths, metadata={})
    QApplication.processEvents()


def test_ruler_two_clicks_report_distance_and_pa():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, [WcsMap(np.ones((11, 11)))])

    win.ruler_tool_btn.setChecked(True)
    assert win._measure.mode == "ruler"
    win._measure.on_canvas_click(0.0, 0.0, "left")
    win._measure.on_canvas_click(4.0, 0.0, "left")  # 4" toward solar west

    text = win.analysis_text.toPlainText()
    assert "4.0″" in text
    assert "0.500 R☉" in text  # 4" / rsun 8"
    assert "PA 270.0°" in text  # due west
    win.close()


def test_right_click_cancels_pending_pick():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, [WcsMap(np.ones((11, 11)))])

    win.ruler_tool_btn.setChecked(True)
    win._measure.on_canvas_click(0.0, 0.0, "left")
    assert win._measure._pending is not None
    win._measure.on_canvas_click(0.0, 0.0, "right")
    assert win._measure._pending is None
    assert win._measure.mode == "ruler"  # mode survives, only the pick resets
    win.close()


def test_profile_two_clicks_open_dialog():
    _app()
    win = SolarDataAnalysisWindow()
    gradient = np.tile(np.arange(11, dtype=float), (11, 1))
    _load(win, [WcsMap(gradient)])

    win.profile_tool_btn.setChecked(True)
    win._measure.on_canvas_click(-10.0, 0.0, "left")
    win._measure.on_canvas_click(10.0, 0.0, "left")

    dialog = getattr(win, "_profile_dialog", None)
    assert dialog is not None
    axes = dialog._figure.get_axes()
    assert len(axes) == 1
    line = axes[0].lines[0]
    assert len(line.get_xdata()) >= 2
    win.close()


def test_height_time_picks_and_fit():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:20:00"),
    ]
    _load(win, frames)
    assert win.height_time_btn.isEnabled()

    win.height_time_btn.setChecked(True)
    assert win._measure.mode == "height_time"

    # Leading edge marches outward: 4", 8", 12" (rsun=8" -> 0.5, 1.0, 1.5 Rsun).
    for i, x_arc in enumerate((4.0, 8.0, 12.0)):
        win.frame_slider.setValue(i)
        QApplication.processEvents()
        win._measure.on_canvas_click(x_arc, 0.0, "left")

    assert len(win._measure.picks) == 3
    assert win.ht_fit_btn.isEnabled()
    win._measure.finish_height_time()

    dialog = getattr(win, "_height_time_dialog", None)
    assert dialog is not None
    text = win.analysis_text.toPlainText()
    assert "plane-of-sky speed" in text
    # 0.5 Rsun per 600 s = 4 Rsun/h -> ~580 km/s; check the right magnitude.
    import re as _re

    match = _re.search(r"speed ([\d,]+) km/s", text)
    assert match is not None
    speed = float(match.group(1).replace(",", ""))
    assert 400 < speed < 800
    win.close()


def test_height_time_replaces_pick_on_same_frame():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
    ]
    _load(win, frames)
    win.height_time_btn.setChecked(True)
    win._measure.on_canvas_click(4.0, 0.0, "left")
    win._measure.on_canvas_click(6.0, 0.0, "left")  # same frame: replaces
    assert len(win._measure.picks) == 1
    win._measure.clear_height_time()
    assert len(win._measure.picks) == 0
    assert not win.ht_fit_btn.isEnabled()
    win.close()


def test_region_stats_uses_crop_bounds():
    _app()
    win = SolarDataAnalysisWindow()
    data = np.zeros((11, 11))
    data[5, 5] = 100.0
    _load(win, [WcsMap(data)])

    # Crop fields cover the whole image (arcsec bounds).
    win.crop_x0_spin.setValue(-10.0)
    win.crop_x1_spin.setValue(10.0)
    win.crop_y0_spin.setValue(-10.0)
    win.crop_y1_spin.setValue(10.0)
    win._measure.report_region_stats()

    text = win.analysis_text.toPlainText()
    assert "Region stats" in text
    assert "mean" in text
    assert "centroid" in text
    win.close()


def test_tools_are_mutually_exclusive_and_crop_conflicts():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, [WcsMap(np.ones((11, 11))), WcsMap(np.ones((11, 11)))])

    win.ruler_tool_btn.setChecked(True)
    assert win._measure.mode == "ruler"
    win.profile_tool_btn.setChecked(True)
    assert win._measure.mode == "profile"
    assert not win.ruler_tool_btn.isChecked()

    # Turning on the crop ROI deactivates the measurement mode.
    win.crop_check.setChecked(True)
    QApplication.processEvents()
    assert win._measure.mode is None
    assert not win.profile_tool_btn.isChecked()
    win.close()


def test_nrgf_toggle_renders_and_disables_for_difference():
    _app()
    win = SolarDataAnalysisWindow()
    rng = np.random.default_rng(1)
    base = 1000.0 / (1.0 + np.hypot(*np.mgrid[-5:6, -5:6]))
    frames = [
        CorWcsMap(base + rng.normal(0, 5, (11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(base + rng.normal(0, 5, (11, 11)), date="2012-07-12T16:10:00"),
    ]
    _load(win, frames)
    assert win.nrgf_check.isEnabled()

    win.nrgf_check.setChecked(True)
    assert "(NRGF)" in win.plot_title_label.text()

    # Switching to a difference mode greys the toggle out and unchecks it.
    win.movie_content_combo.setCurrentText("Running Difference")
    QApplication.processEvents()
    assert not win.nrgf_check.isEnabled()
    assert not win.nrgf_check.isChecked()
    assert "(NRGF)" not in win.plot_title_label.text()
    win.close()


def test_hi_jmap_builds_dialog():
    _app()
    win = SolarDataAnalysisWindow()

    class HiMap(WcsMap):
        observatory = "STEREO_A"
        instrument = "SECCHI"
        detector = "HI1"
        wavelength = ""

        def __init__(self, data, *, date):
            super().__init__(data, date=date)
            self.meta.update({"instrume": "SECCHI", "detector": "HI1"})

    frames = [
        HiMap(np.random.default_rng(i).normal(10, 1, (11, 11)), date=f"2012-07-12T16:{i:02d}:00")
        for i in range(4)
    ]
    _load(win, frames)
    assert not win.hi_group.isHidden()
    assert win.hi_jmap_btn.isEnabled()

    win.build_hi_jmap()
    dialog = getattr(win, "_jmap_dialog", None)
    assert dialog is not None
    axes = dialog._figure.get_axes()
    assert len(axes) == 1
    assert len(axes[0].images) == 1
    win.close()


def test_canvas_click_callback_forwarding():
    _app()
    from src.UI.sunpy_plot_window import SunPyPlotCanvas

    canvas = SunPyPlotCanvas()
    received = []
    canvas.set_click_callback(lambda x, y, b: received.append((x, y, b)))

    class _Ev:
        def __init__(self, scene_pos, button):
            self._pos = scene_pos
            self._button = button

        def scenePos(self):
            return self._pos

        def button(self):
            return self._button

        def accept(self):
            pass

    from PySide6.QtCore import Qt

    vb = canvas.map_plot.getViewBox()
    # The centre of the viewbox scene rect is guaranteed inside the view.
    center = vb.sceneBoundingRect().center()
    canvas._on_scene_mouse_clicked(_Ev(center, Qt.LeftButton))
    assert len(received) == 1
    x, y, button = received[0]
    assert button == "left"
    expected = vb.mapSceneToView(center)
    assert (x, y) == pytest.approx((expected.x(), expected.y()))

    # Overlay set/clear round trip.
    canvas.set_measurement_overlay([0.0, 10.0], [0.0, 5.0], connect=True)
    assert canvas._measure_points.data is not None
    canvas.clear_measurement_overlay()
