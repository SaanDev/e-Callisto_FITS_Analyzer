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

from src.Backend.coronagraph import fit_height_time
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
    # The measurement tools and CME tracking panel are gated behind the
    # Measurements switch; every test here exercises them, so turn it on.
    win.measurements_check.setChecked(True)
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

    # The fit renders INSIDE the tracking panel (no separate window): the
    # embedded graph gets the fitted line and the label reports v (and a).
    fit_x, fit_y = win.tracking_panel._fit_line.getData()
    assert fit_x is not None and len(fit_x) >= 2
    assert "km/s" in win.tracking_panel.speed_label.text()
    text = win.analysis_text.toPlainText()
    assert "plane-of-sky speed" in text
    # 0.5 Rsun per 600 s = 4 Rsun/h -> ~580 km/s; check the right magnitude.
    import re as _re

    match = _re.search(r"v = ([\d,]+)", text)
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
    # Disable continuous tracking so both clicks land on the same frame.
    win.tracking_panel.auto_advance_check.setChecked(False)
    win._measure.on_canvas_click(4.0, 0.0, "left")
    win._measure.on_canvas_click(6.0, 0.0, "left")  # same frame: replaces
    assert len(win._measure.picks) == 1
    win._measure.clear_height_time()
    assert len(win._measure.picks) == 0
    assert not win.ht_fit_btn.isEnabled()
    win.close()


def test_height_time_auto_advances_frames():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:20:00"),
    ]
    _load(win, frames)
    win.height_time_btn.setChecked(True)
    assert win.tracking_panel.auto_advance_check.isChecked()  # default on
    assert not win.tracking_panel.isHidden()  # panel appears when tracking

    # Three clicks without touching the slider: the frame advances itself.
    win._measure.on_canvas_click(4.0, 0.0, "left")
    QApplication.processEvents()
    assert win._current_frame_index == 1
    win._measure.on_canvas_click(6.0, 0.0, "left")
    QApplication.processEvents()
    assert win._current_frame_index == 2
    win._measure.on_canvas_click(8.0, 0.0, "left")
    QApplication.processEvents()
    assert win._current_frame_index == 2  # last frame: stays put
    assert len(win._measure.picks) == 3

    # The tracking table filled in real time: t(s) relative to the first pick.
    table = win.tracking_panel.table
    assert table.rowCount() == 3
    assert table.item(0, 1).text() == "0"
    assert table.item(1, 1).text() == "600"
    assert table.item(2, 1).text() == "1200"
    # Heights: 4/8, 6/8, 8/8 arcsec/rsun -> 0.5, 0.75, 1.0 R☉ toward the west.
    assert table.item(0, 2).text() == "0.500"
    assert table.item(2, 2).text() == "1.000"
    assert table.item(0, 3).text() == "270.0"  # PA of due-west picks
    # And the live plot has both the picks and a fit line.
    assert len(win.tracking_panel._scatter.data) == 3
    win.close()


def test_clear_all_measurements_resets_everything():
    _app()
    win = SolarDataAnalysisWindow()
    frames = [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
    ]
    _load(win, frames)
    win.height_time_btn.setChecked(True)
    win._measure.on_canvas_click(4.0, 0.0, "left")
    assert len(win._measure.picks) == 1

    win.clear_all_measurements()
    assert len(win._measure.picks) == 0
    assert win._measure.mode is None
    assert not win.height_time_btn.isChecked()
    # The panel is never hidden now — clearing just empties it.
    assert not win.tracking_panel.isHidden()
    assert win.tracking_panel.table.rowCount() == 0
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
    _load(win, _timed_frames(2))

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


def test_tracking_panel_plot_never_uses_opengl_viewport():
    """The height-time graph must render in software even when the app enables
    OpenGL globally (a GL viewport created hidden-in-a-splitter draws solid
    black on many Windows drivers). PlotWidget(useOpenGL=...) is a decoy — the
    kwarg goes to the PlotItem — so this pins the actual viewport type."""
    _app()
    import pyqtgraph as pg

    from src.UI.solar_measure_tools import TrackingPanel

    old = pg.getConfigOption("useOpenGL")
    try:
        pg.setConfigOptions(useOpenGL=True)  # what real app runs do
        panel = TrackingPanel()
        viewport_type = type(panel.plot.viewport()).__name__
        assert "GL" not in viewport_type, f"height-time plot got a GL viewport: {viewport_type}"
    finally:
        pg.setConfigOptions(useOpenGL=bool(old))


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


# --------------------------------------------------------------------------- #
# Circle Fit (CME): N clicks -> least-squares circle -> radius-time kinematics
# --------------------------------------------------------------------------- #
def _timed_frames(count):
    """Frames one minute apart, so the radius-time fit has a real time axis."""
    return [
        WcsMap(np.ones((11, 11)), date=f"2026-02-10T01:{i:02d}:00") for i in range(count)
    ]


def _circle_clicks(win, radius, *, center=(0.0, 0.0)):
    """Click four points on a circle of ``radius`` arcsec about ``center``."""
    cx, cy = center
    for dx, dy in ((radius, 0.0), (0.0, radius), (-radius, 0.0), (0.0, -radius)):
        win._measure.on_canvas_click(cx + dx, cy + dy, "left")


def test_circle_fit_three_clicks_then_commit_records_radius():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    assert win._measure.mode == "circle_fit"
    win._measure.on_canvas_click(4.0, 0.0, "left")
    win._measure.on_canvas_click(0.0, 4.0, "left")
    win._measure.on_canvas_click(-4.0, 0.0, "left")
    win._measure.commit_circle()

    entry = win._measure.circles[0]
    assert entry.radius_arcsec == pytest.approx(4.0)
    assert entry.radius_rsun == pytest.approx(0.5)  # 4" / rsun 8"
    assert entry.leading_edge_rsun == pytest.approx(0.5)  # centred on the disk
    assert entry.n_points == 3
    assert entry.rms_arcsec == pytest.approx(0.0, abs=1e-9)
    win.close()


def test_circle_fit_live_preview_before_commit():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    win._measure.on_canvas_click(4.0, 0.0, "left")
    win._measure.on_canvas_click(0.0, 4.0, "left")
    xs, _ = win.pyqt_canvas._measure_curve.getData()
    assert xs is None or len(xs) == 0  # no circle from two points

    win._measure.on_canvas_click(-4.0, 0.0, "left")
    xs, ys = win.pyqt_canvas._measure_curve.getData()
    assert len(xs) > 2 and len(xs) == len(ys)  # the fitted circle is drawn...
    assert win._measure.circles == {}  # ...but nothing is recorded yet
    win.close()


def test_circle_fit_commit_auto_advances_frames():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(3))

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(True)
    _circle_clicks(win, 4.0)
    win._measure.commit_circle()
    assert win._current_frame_index == 1

    win.tracking_panel.auto_advance_check.setChecked(False)
    _circle_clicks(win, 6.0)
    win._measure.commit_circle()
    assert win._current_frame_index == 1  # stays put with auto-advance off
    assert sorted(win._measure.circles) == [0, 1]
    win.close()


def test_circle_fit_right_click_drops_in_progress_points_only():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(False)
    _circle_clicks(win, 4.0)
    win._measure.commit_circle()

    win._measure.on_canvas_click(7.0, 0.0, "left")
    win._measure.on_canvas_click(0.0, 7.0, "left")
    win._measure.on_canvas_click(0.0, 0.0, "right")
    assert not win._measure._circle_points.get(0)
    assert win._measure.circles[0].radius_arcsec == pytest.approx(4.0)  # commit survives
    assert win._measure.mode == "circle_fit"  # mode survives too
    win.close()


def test_circle_fit_radius_time_series_and_fit():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(3))

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(True)
    for radius in (4.0, 6.0, 8.0):
        _circle_clicks(win, radius)
        win._measure.commit_circle()

    assert win.tracking_panel.table.rowCount() == 3
    assert win.ht_fit_btn.isEnabled()
    win._measure.finish_active_fit()
    # 0.5 R☉ in 120 s -> ~2900 km/s of plane-of-sky radial expansion.
    assert "km/s" in win.tracking_panel.speed_label.text()
    assert "circle fit (3 frames, linear fit)" in win.analysis_text.toPlainText()
    x_fit, _ = win.tracking_panel._fit_line.getData()
    assert len(x_fit) > 0
    win.close()


def test_tracking_panel_swaps_source_without_losing_the_other_store():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.height_time_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(True)
    win._measure.on_canvas_click(4.0, 0.0, "left")
    win._measure.on_canvas_click(8.0, 0.0, "left")
    assert win.tracking_panel.table.columnCount() == 4
    assert win.tracking_panel.table.horizontalHeaderItem(2).text() == "Height (R☉)"

    win.circle_tool_btn.setChecked(True)
    assert win.tracking_panel.table.columnCount() == 6
    assert win.tracking_panel.table.horizontalHeaderItem(2).text() == "Radius (R☉)"
    assert win.tracking_panel.table.rowCount() == 0  # no circles yet
    assert len(win._measure.picks) == 2  # the other tool's work is untouched

    win.height_time_btn.setChecked(True)
    assert win.tracking_panel.table.columnCount() == 4
    assert win.tracking_panel.table.rowCount() == 2  # the picks come back
    win.close()


def test_circle_fit_lock_center_freezes_the_centre():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(False)
    _circle_clicks(win, 4.0)  # centred on the disk
    win._measure.commit_circle()
    win.tracking_panel.lock_center_check.setChecked(True)
    assert win._measure._locked_center == pytest.approx((0.0, 0.0))

    win.frame_slider.setValue(1)
    QApplication.processEvents()
    # An arc that a free fit would centre on (2, 0) instead.
    for point in ((8.0, 0.0), (2.0, 6.0), (-4.0, 0.0)):
        win._measure.on_canvas_click(point[0], point[1], "left")
    win._measure.commit_circle()

    entry = win._measure.circles[1]
    assert entry.center_x_arc == pytest.approx(0.0)
    assert entry.center_y_arc == pytest.approx(0.0)
    win.close()


def test_circle_fit_collinear_clicks_do_not_crash():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    for x in (-4.0, 0.0, 4.0):
        win._measure.on_canvas_click(x, 0.0, "left")
    win._measure.commit_circle()

    assert win._measure.circles == {}
    assert len(win._measure._circle_points[0]) == 3  # points kept, add another
    win.close()


def test_clear_all_measurements_clears_circle_fits():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.lock_center_check.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(False)
    _circle_clicks(win, 4.0)
    win._measure.commit_circle()
    assert win._measure.circles

    win.clear_all_measurements()
    assert win._measure.circles == {}
    assert win._measure._circle_points == {}
    assert win._measure._locked_center is None
    assert win.tracking_panel.lock_center_check.isChecked() is False
    assert win.tracking_panel.table.rowCount() == 0
    assert win.circle_tool_btn.isChecked() is False
    win.close()


def test_circle_tool_needs_a_sequence():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(1))
    assert win.circle_tool_btn.isEnabled() is False  # a single frame has no time axis

    _load(win, _timed_frames(2))
    assert win.circle_tool_btn.isEnabled() is True
    win.close()


def test_circle_fit_csv_row_matches_its_header():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    win.circle_tool_btn.setChecked(True)
    _circle_clicks(win, 4.0)
    win._measure.commit_circle()

    panel = win.tracking_panel
    header = panel.csv_header()
    assert header[:3] == ["time_utc", "t_seconds", "radius_rsun"]
    row = panel.csv_row(panel._entries[0], panel._entries[0][0])
    assert len(row) == len(header)
    assert row[2] == "0.5000"
    win.close()


def test_circle_fit_survives_frames_sharing_one_timestamp():
    """Duplicate DATE-OBS has no time baseline; polyfit must not raise on click."""
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, [WcsMap(np.ones((11, 11))) for _ in range(2)])  # identical dates

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(True)
    _circle_clicks(win, 4.0)
    win._measure.commit_circle()
    _circle_clicks(win, 6.0)
    win._measure.commit_circle()  # must not raise LinAlgError

    assert len(win._measure.circles) == 2
    assert "one observation time" in win.tracking_panel.speed_label.text()
    win._measure.finish_active_fit()
    assert "km/s" not in win.tracking_panel.speed_label.text()
    win.close()


# --------------------------------------------------------------------------- #
# Fit-order dropdown
# --------------------------------------------------------------------------- #
def _accelerating_picks(win, count=6):
    """Height-time picks on an accelerating front: h = 4 + 0.5*a*t^2 arcsec."""
    win.height_time_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(False)
    for i in range(count):
        win.frame_slider.setValue(i)
        QApplication.processEvents()
        win._measure.on_canvas_click(4.0 + 0.05 * (i * 60.0) ** 2 / 60.0, 0.0, "left")


def test_fit_order_combo_offers_linear_through_cubic():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(2))

    combo = win.tracking_panel.fit_order_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [1, 2, 3]
    assert win.tracking_panel.fit_order() == 1  # linear by default
    win.close()


def test_fit_order_changes_the_reported_kinematics():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(6))
    _accelerating_picks(win, 6)

    win._measure.finish_active_fit()
    linear = win.tracking_panel.speed_label.text()
    assert "linear" in linear and "v = " in linear

    win.tracking_panel.fit_order_combo.setCurrentIndex(1)  # quadratic
    QApplication.processEvents()
    quadratic = win.tracking_panel.speed_label.text()
    assert "quadratic" in quadratic
    # A curved fit has no single speed, so both ends are reported.
    assert "v₀ = " in quadratic and "v_end = " in quadratic
    assert quadratic != linear

    win.tracking_panel.fit_order_combo.setCurrentIndex(2)  # cubic
    QApplication.processEvents()
    cubic = win.tracking_panel.speed_label.text()
    assert "cubic" in cubic and "jerk = " in cubic and "a_end = " in cubic
    win.close()


def test_fit_order_reports_one_sigma_errors():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(6))
    _accelerating_picks(win, 6)

    win.tracking_panel.fit_order_combo.setCurrentIndex(1)  # quadratic
    QApplication.processEvents()
    assert "±" in win.tracking_panel.speed_label.text()

    fit = fit_height_time(
        [0.0, 60.0, 120.0, 180.0], [1.0e5, 1.2e5, 1.5e5, 1.9e5], order=2
    )
    summary = win.tracking_panel.fit_summary(fit)
    assert "±" in summary and "km/s" in summary and "m/s²" in summary


def test_fit_order_curve_follows_the_polynomial():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(6))
    _accelerating_picks(win, 6)

    win.tracking_panel.fit_order_combo.setCurrentIndex(1)  # quadratic
    QApplication.processEvents()
    x_fit, y_fit = win.tracking_panel._fit_line.getData()
    assert len(x_fit) > 2
    # A straight line through the ends would sag away from a curved fit.
    chord = np.interp(x_fit, [x_fit[0], x_fit[-1]], [y_fit[0], y_fit[-1]])
    assert np.max(np.abs(y_fit - chord)) > 1e-3
    win.close()


def test_fit_order_needing_more_points_says_so_instead_of_raising():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(3))
    _accelerating_picks(win, 3)

    win.tracking_panel.fit_order_combo.setCurrentIndex(2)  # cubic needs 4
    QApplication.processEvents()
    assert "needs 4" in win.tracking_panel.speed_label.text()
    win._measure.finish_active_fit()  # must not raise
    # No curve was fitted, and the message still explains why.
    x_fit, _ = win.tracking_panel._fit_line.getData()
    assert x_fit is None or len(x_fit) == 0
    assert "needs 4" in win.tracking_panel.speed_label.text()
    win.close()


def test_fit_order_applies_to_circle_fits_too():
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _timed_frames(5))

    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(True)
    for i in range(5):
        _circle_clicks(win, 3.0 + 0.15 * (i * 60.0) ** 2 / 60.0)
        win._measure.commit_circle()

    win.tracking_panel.fit_order_combo.setCurrentIndex(1)  # quadratic
    QApplication.processEvents()
    win._measure.finish_active_fit()
    text = win.analysis_text.toPlainText()
    assert "quadratic fit" in text
    assert "±" in text
    win.close()
