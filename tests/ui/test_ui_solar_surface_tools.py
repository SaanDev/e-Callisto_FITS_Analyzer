"""
e-CALLISTO FITS Analyzer
Offscreen tests for the Solar Image Analysis length / area tools
(src/ui/solar/solar_surface_tools.py + canvas and window wiring).

Clicks and drags are driven through the controller with data (arcsec)
coordinates, as in test_ui_solar_measure_tools.py; the canvas drag hook is
tested on its own with a stand-in event.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from src.backend.solar.helioviewer_jp2 import QUICKLOOK_KEY, QUICKLOOK_VALUE
from src.backend.solar.surface_measure import (
    SurfaceGeometry,
    fit_quiet_sun_profile,
    mask_surface_area,
    mu_map,
    normalize_to_quiet_sun,
    polyline_surface_length,
    rectangle_mask,
    sunspot_masks,
)
from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow
from src.ui.solar.solar_surface_tools import SurfaceShape, format_lonlat

N = 401
SCALE = 5.0  # arcsec / px: a 2000" field around a 960" disk
RSUN = 960.0
SPOT = (300.0, 150.0)  # arcsec
UMBRA_R = 20.0
PENUMBRA_R = 45.0


def _app():
    return QApplication.instance() or QApplication([])


def _disk(*, spot_scale: float = 1.0) -> np.ndarray:
    g = SurfaceGeometry(
        rsun_arcsec=RSUN,
        x_ref_pix=(N - 1) / 2.0,
        y_ref_pix=(N - 1) / 2.0,
        x_scale_arcsec_per_pix=SCALE,
        y_scale_arcsec_per_pix=SCALE,
    )
    mu = mu_map((N, N), g)
    image = 40_000.0 * (1.0 - 0.88 * (1.0 - mu) + 0.23 * (1.0 - mu * mu))
    image = np.where(np.isfinite(mu), image, 5.0)
    tx, ty = g.pixel_to_arcsec(np.arange(N)[None, :], np.arange(N)[:, None])
    r = np.hypot(tx - SPOT[0], ty - SPOT[1])
    image[r < PENUMBRA_R * spot_scale] *= 0.75
    image[r < UMBRA_R * spot_scale] *= 0.3 / 0.75
    return image


class DiskMap:
    """A limb-darkened HMI-continuum-like disk with one round sunspot."""

    observatory = "SDO"
    instrument = "HMI"
    detector = ""
    wavelength = "6173 Angstrom"
    nickname = ""
    source = ""

    def __init__(self, data, *, date="2026-02-10T01:00:00"):
        self.data = np.asarray(data, dtype=float)
        self.date = date
        self.meta = {
            "instrume": "HMI_FRONT2",
            "cdelt1": SCALE,
            "cdelt2": SCALE,
            "crpix1": (N + 1) / 2.0,
            "crpix2": (N + 1) / 2.0,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": RSUN,
            "hglt_obs": 0.0,
            "hgln_obs": 0.0,
        }


def _geometry() -> SurfaceGeometry:
    return SurfaceGeometry(
        rsun_arcsec=RSUN,
        x_ref_pix=(N - 1) / 2.0,
        y_ref_pix=(N - 1) / 2.0,
        x_scale_arcsec_per_pix=SCALE,
        y_scale_arcsec_per_pix=SCALE,
    )


@pytest.fixture
def win(monkeypatch):
    _app()
    window = SolarDataAnalysisWindow()
    yield window
    window.close()


def _load(win, frames):
    win._apply_loaded_frames(frames, paths=[f"f{i}.fits" for i in range(len(frames))], metadata={})
    win.measurements_check.setChecked(True)
    QApplication.processEvents()


def _last_line(win) -> str:
    return win.analysis_text.toPlainText().strip().splitlines()[-1]


def _spot_box(win, margin: float = 70.0) -> None:
    """Drag a rectangle around the sunspot."""
    surface = win._measure.surface
    win.surface_panel.set_shape("rectangle")
    assert surface.on_drag("start", SPOT[0] - margin, SPOT[1] - margin)
    surface.on_drag("move", SPOT[0], SPOT[1])
    surface.on_drag("finish", SPOT[0] + margin, SPOT[1] + margin)


def _expected_spot():
    g = _geometry()
    image = _disk()
    profile = fit_quiet_sun_profile(image, g)
    c0, r0 = g.arcsec_to_pixel(SPOT[0] - 70.0, SPOT[1] - 70.0)
    c1, r1 = g.arcsec_to_pixel(SPOT[0] + 70.0, SPOT[1] + 70.0)
    region = rectangle_mask((N, N), (float(c0), float(r0)), (float(c1), float(r1)))
    norm = normalize_to_quiet_sun(image, g, profile, region=region)
    masks = sunspot_masks(norm, region, min_cluster_px=3)
    return mask_surface_area(masks.umbra, g), mask_surface_area(masks.penumbra, g)


# ----------------------------------------------------------------------- tools
def test_length_tool_measures_along_the_surface(win):
    _load(win, [DiskMap(_disk())])
    win.length_tool_btn.setChecked(True)
    assert win._measure.mode == "length"
    assert win.measure_side_tabs.currentWidget() is win.surface_panel
    assert not win.surface_panel.threshold_group.isVisibleTo(win.surface_panel)

    points = [(-600.0, 100.0), (-300.0, 200.0), (100.0, 250.0), (500.0, 150.0)]
    for x, y in points:
        win._measure.on_canvas_click(x, y, "left")
    assert win.surface_panel.finish_btn.isEnabled()
    assert "Length so far" in win.statusBar().currentMessage()
    win._measure.surface.on_double_click(*points[-1])

    expected = polyline_surface_length(points, _geometry())
    line = _last_line(win)
    assert line.startswith("Length")
    assert f"{expected.total_km / 1000.0:,.1f} Mm along the surface" in line
    assert "4 points" in line
    assert expected.total_km > expected.plane_of_sky_km
    assert win._measure.surface.shapes["length"].points == points
    assert "Frame 1" in win.surface_panel.result_label.text()
    xs, _ = win.pyqt_canvas._measure_curve.getData()
    assert len(xs) == 4  # the polyline itself, not closed


def test_undo_point_and_cancel_keep_the_finished_shape(win):
    _load(win, [DiskMap(_disk())])
    win.length_tool_btn.setChecked(True)
    surface = win._measure.surface
    for x in (0.0, 100.0):
        win._measure.on_canvas_click(x, 0.0, "left")
    win.surface_panel.finish_btn.click()
    finished = surface.shapes["length"]

    win._measure.on_canvas_click(-50.0, 0.0, "left")
    win._measure.on_canvas_click(-80.0, 0.0, "left")
    win.surface_panel.undo_btn.click()
    assert surface.drawing.points == [(-50.0, 0.0)]
    assert not win.surface_panel.finish_btn.isEnabled()  # one point is no line
    win._measure.on_canvas_click(0.0, 0.0, "right")  # cancel
    assert surface.drawing is None
    assert surface.shapes["length"] is finished
    win.surface_panel.clear_shape_btn.click()
    assert "length" not in surface.shapes
    assert win.surface_panel.result_label.text() == ""


def test_polygon_area_at_disk_centre(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    assert not win.length_tool_btn.isChecked()
    win.surface_panel.set_shape("polygon")
    corners = [(-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)]
    for x, y in corners[:2]:
        win._measure.on_canvas_click(x, y, "left")
    assert not win.surface_panel.finish_btn.isEnabled()  # two corners are no polygon
    for x, y in corners[2:]:
        win._measure.on_canvas_click(x, y, "left")
    win.surface_panel.finish_btn.click()

    line = _last_line(win)
    assert line.startswith("Area (polygon)")
    result = win._measure.surface.last_result
    # 40 x 40 pixel centres of 25 arcsec² at disk centre.
    assert result.region.n_pixels == 1600
    assert result.region.on_disk_msh == pytest.approx(
        mask_surface_area(win._measure.surface.shapes["area"].mask((N, N), _geometry()), _geometry()).on_disk_msh
    )
    assert "foreshortening" not in line  # negligible at disk centre
    assert "mean" in line
    xs, ys = win.pyqt_canvas._measure_curve.getData()
    assert (xs[0], ys[0]) == (xs[-1], ys[-1])  # closed outline


def test_drag_shapes_ellipse_rectangle_and_lasso(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    surface = win._measure.surface

    win.surface_panel.set_shape("ellipse")
    assert surface.on_drag("start", -100.0, -50.0)
    surface.on_drag("move", 0.0, 0.0)
    surface.on_drag("finish", 100.0, 50.0)
    ellipse = surface.last_result.region
    assert ellipse.n_pixels == pytest.approx(math.pi * 20 * 10, rel=0.05)
    assert _last_line(win).startswith("Area (ellipse)")

    win.surface_panel.set_shape("rectangle")
    surface.on_drag("start", -100.0, -50.0)
    surface.on_drag("finish", 100.0, 50.0)
    # Pixel centres lying exactly on the dragged edges count: 41 x 21.
    assert surface.last_result.region.n_pixels == 41 * 21

    win.surface_panel.set_shape("lasso")
    angles = np.linspace(0.0, 2.0 * math.pi, 200)
    path = [(200.0 * math.cos(a), 200.0 * math.sin(a)) for a in angles]
    surface.on_drag("start", *path[0])
    for point in path[1:-1]:
        surface.on_drag("move", *point)
    surface.on_drag("finish", *path[-1])
    lasso = surface.last_result.region
    assert lasso.n_pixels == pytest.approx(math.pi * 40 * 40, rel=0.02)
    assert len(surface.shapes["area"].points) > 50


def test_drags_are_declined_for_click_shapes_and_other_tools(win):
    _load(win, [DiskMap(_disk())])
    surface = win._measure.surface
    win.ruler_tool_btn.setChecked(True)
    assert surface.on_drag("start", 0.0, 0.0) is False
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("polygon")
    assert surface.on_drag("start", 0.0, 0.0) is False
    # Clicking with a drag shape explains how to draw it instead of placing a point.
    win.surface_panel.set_shape("ellipse")
    win._measure.on_canvas_click(0.0, 0.0, "left")
    assert surface.drawing is None
    assert "drag" in win.statusBar().currentMessage().lower()


def test_tiny_drag_is_discarded(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", 10.0, 10.0)
    surface.on_drag("finish", 10.0, 10.0)
    assert "area" not in surface.shapes
    assert surface.drawing is None


# ------------------------------------------------------------------ thresholds
def test_sunspot_threshold_splits_umbra_and_penumbra(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)

    umbra, penumbra = _expected_spot()
    result = win._measure.surface.last_result
    assert result.umbra.on_disk_msh == pytest.approx(umbra.on_disk_msh)
    assert result.penumbra.on_disk_msh == pytest.approx(penumbra.on_disk_msh)
    # The drawn disk spot: umbra 20", penumbra out to 45", at mu ~ 0.94.
    expected_umbra_px = math.pi * (UMBRA_R / SCALE) ** 2
    assert result.umbra.n_pixels == pytest.approx(expected_umbra_px, rel=0.15)
    line = _last_line(win)
    assert line.startswith("Sunspot (rectangle)")
    assert f"umbra {umbra.on_disk_msh:,.1f} MSH" in line
    lon, lat = result.selected.centroid_lonlat
    assert format_lonlat((lon, lat)).startswith("N")
    assert win.pyqt_canvas.has_surface_mask_overlay()


def test_threshold_changes_update_live_without_logging(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    lines_before = len(win.analysis_text.toPlainText().splitlines())
    umbra_before = win._measure.surface.last_result.umbra.n_pixels

    win.surface_panel.umbra_spin.setValue(0.2)  # below the 0.3 umbra: nothing left
    result = win._measure.surface.last_result
    assert result.umbra.n_pixels == 0 < umbra_before
    assert len(win.analysis_text.toPlainText().splitlines()) == lines_before
    win.surface_panel.record_btn.click()
    assert len(win.analysis_text.toPlainText().splitlines()) == lines_before + 1
    assert "umbra 0.0 MSH" in _last_line(win)

    win.surface_panel.tint_check.setChecked(False)
    assert not win.pyqt_canvas.has_surface_mask_overlay()


def test_dark_and_bright_thresholds(win):
    image = _disk()
    g = _geometry()
    tx, ty = g.pixel_to_arcsec(np.arange(N)[None, :], np.arange(N)[:, None])
    plage = np.hypot(tx + 300.0, ty + 100.0) < 30.0
    image[plage] *= 2.0
    _load(win, [DiskMap(image)])
    win.area_tool_btn.setChecked(True)
    surface = win._measure.surface

    win.surface_panel.set_threshold_mode("bright")
    assert win.surface_panel.level_spin.value() == pytest.approx(1.5)
    win.surface_panel.set_shape("rectangle")
    surface.on_drag("start", -380.0, -180.0)
    surface.on_drag("finish", -220.0, -20.0)
    selected = surface.last_result.selected
    assert selected.n_pixels == int(plage.sum())
    assert "brighter than 1.50 I_qs" in _last_line(win)

    win.surface_panel.set_threshold_mode("dark")
    assert win.surface_panel.level_spin.value() == pytest.approx(0.85)
    assert surface.last_result.selected.n_pixels == 0


def test_threshold_uses_frame_data_in_difference_mode(win):
    frames = [DiskMap(_disk(), date="2026-02-10T01:00:00"), DiskMap(_disk(), date="2026-02-10T01:12:00")]
    _load(win, frames)
    win.movie_content_combo.setCurrentText("Running Difference")
    QApplication.processEvents()
    assert float(np.nanmax(np.abs(win._current_map_data))) == 0.0  # identical frames
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    umbra, _ = _expected_spot()
    assert win._measure.surface.last_result.umbra.on_disk_msh == pytest.approx(umbra.on_disk_msh)


def test_frame_change_remeasures_the_shape(win):
    frames = [
        DiskMap(_disk(spot_scale=1.0), date="2026-02-10T01:00:00"),
        DiskMap(_disk(spot_scale=1.4), date="2026-02-10T01:12:00"),
    ]
    _load(win, frames)
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    first = win._measure.surface.last_result.selected.on_disk_msh
    lines = len(win.analysis_text.toPlainText().splitlines())
    win.frame_slider.setValue(1)
    QApplication.processEvents()
    second = win._measure.surface.last_result.selected.on_disk_msh
    assert second == pytest.approx(first * 1.4**2, rel=0.15)
    assert "Frame 2" in win.surface_panel.result_label.text()
    assert len(win.analysis_text.toPlainText().splitlines()) == lines  # live only


def test_quicklook_frames_measure_geometry_but_not_thresholds(win):
    frame = DiskMap(_disk())
    frame.meta[QUICKLOOK_KEY] = QUICKLOOK_VALUE
    _load(win, [frame])
    win.area_tool_btn.setChecked(True)
    assert not win.surface_panel.threshold_group.isEnabled()
    win.surface_panel.set_threshold_mode("sunspot")
    assert win.surface_panel.threshold_settings().mode == "none"
    _spot_box(win)
    result = win._measure.surface.last_result
    assert result.selected is None
    assert result.region.on_disk_msh > 0.0
    assert "mean" not in _last_line(win)


# --------------------------------------------------------------- window state
def test_tabs_follow_the_tool(win):
    frames = [DiskMap(_disk(), date="2026-02-10T01:00:00"), DiskMap(_disk(), date="2026-02-10T01:12:00")]
    _load(win, frames)
    win.area_tool_btn.setChecked(True)
    assert win.measure_side_tabs.currentWidget() is win.surface_panel
    win.height_time_btn.setChecked(True)
    assert not win.area_tool_btn.isChecked()
    assert win.measure_side_tabs.currentWidget() is win.tracking_panel
    assert not win.tracking_panel.isHidden()
    win.ruler_tool_btn.setChecked(True)  # the ruler leaves the tab alone
    assert win.measure_side_tabs.currentWidget() is win.tracking_panel


def test_leaving_the_tool_hides_the_tint_and_returning_restores_it(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    assert win.pyqt_canvas.has_surface_mask_overlay()
    win.ruler_tool_btn.setChecked(True)
    assert not win.pyqt_canvas.has_surface_mask_overlay()
    assert win.pyqt_canvas.map_plot.getViewBox().drag_handler is None
    win.area_tool_btn.setChecked(True)
    assert win.pyqt_canvas.has_surface_mask_overlay()
    assert win.pyqt_canvas.map_plot.getViewBox().drag_handler is not None


def test_clear_and_measurements_switch_reset_the_tools(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    win.clear_all_measurements()
    assert win._measure.surface.shapes == {}
    assert not win.area_tool_btn.isChecked()
    assert not win.pyqt_canvas.has_surface_mask_overlay()

    win.length_tool_btn.setChecked(True)
    win.measurements_check.setChecked(False)
    assert not win.length_tool_btn.isChecked()
    assert not win.length_tool_btn.isEnabled()
    assert not win.surface_panel.isEnabled()
    assert win._measure.mode is None


def test_off_limb_shape_reports_plane_of_sky(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", 900.0, -50.0)
    surface.on_drag("finish", 1050.0, 50.0)
    result = surface.last_result.region
    assert result.on_disk_pixels > 0.0 and result.off_limb_pixels > 0.0
    line = _last_line(win)
    assert "off-limb" in line and "not corrected" in line
    assert "⚠" in line


def test_shape_outline_helpers():
    ellipse = SurfaceShape("ellipse", [(-10.0, -5.0), (10.0, 5.0)])
    xs, ys = ellipse.outline()
    assert max(xs) == pytest.approx(10.0) and max(ys) == pytest.approx(5.0)
    assert not SurfaceShape("rectangle", [(0.0, 0.0), (0.0, 5.0)]).is_complete()
    assert SurfaceShape("polygon", [(0, 0), (1, 0), (0, 1)]).is_complete()
    assert format_lonlat((-12.34, -5.0)) == "S5.0° E12.3°"
    assert format_lonlat(None) == "off disk"


# ---------------------------------------------------------------- canvas hooks
class _FakeDrag:
    def __init__(self, start, pos, *, first=False, last=False, button=Qt.LeftButton):
        self._start = QPointF(*start)
        self._pos = QPointF(*pos)
        self._first = first
        self._last = last
        self._button = button
        self.accepted = False

    def button(self):
        return self._button

    def isStart(self):  # noqa: N802
        return self._first

    def isFinish(self):  # noqa: N802
        return self._last

    def buttonDownScenePos(self):  # noqa: N802
        return self._start

    def scenePos(self):  # noqa: N802
        return self._pos

    def accept(self):
        self.accepted = True

    def ignore(self):
        pass


def test_map_view_box_routes_drags_to_the_handler(win, monkeypatch):
    import pyqtgraph as pg

    vb = win.pyqt_canvas.map_plot.getViewBox()
    fell_through = []
    monkeypatch.setattr(pg.ViewBox, "mouseDragEvent", lambda self, ev, axis=None: fell_through.append(ev))
    calls = []

    def handler(phase, x, y):
        calls.append(phase)
        return True

    win.pyqt_canvas.set_drag_callback(handler)
    for event in (
        _FakeDrag((0, 0), (5, 5), first=True),
        _FakeDrag((0, 0), (8, 8)),
        _FakeDrag((0, 0), (9, 9), last=True),
    ):
        vb.mouseDragEvent(event)
        assert event.accepted
    assert calls == ["start", "move", "move", "finish"]
    assert fell_through == []

    # A declined drag, a right drag and no handler all reach the normal pan/zoom.
    win.pyqt_canvas.set_drag_callback(lambda phase, x, y: False)
    vb.mouseDragEvent(_FakeDrag((0, 0), (1, 1), first=True))
    calls.clear()
    win.pyqt_canvas.set_drag_callback(handler)
    vb.mouseDragEvent(_FakeDrag((0, 0), (1, 1), first=True, button=Qt.RightButton))
    assert calls == []
    win.pyqt_canvas.set_drag_callback(None)
    vb.mouseDragEvent(_FakeDrag((0, 0), (1, 1), first=True))
    assert len(fell_through) == 3


def test_mask_overlay_covers_only_the_bounding_box(win):
    _load(win, [DiskMap(_disk())])
    canvas = win.pyqt_canvas
    mask = np.zeros((N, N), dtype=bool)
    mask[100:110, 200:230] = True
    canvas.set_surface_mask_overlay([(mask, (255, 0, 0))])
    item = canvas._surface_mask_item
    assert item.image.shape == (10, 30, 4)
    rect = item.mapRectToParent(item.boundingRect())
    tx, ty = _geometry().pixel_to_arcsec(199.5, 99.5)
    assert rect.left() == pytest.approx(float(tx))
    assert rect.top() == pytest.approx(float(ty))
    assert rect.width() == pytest.approx(30 * SCALE)
    canvas.set_surface_mask_overlay([(np.zeros((N, N), dtype=bool), (255, 0, 0))])
    assert not canvas.has_surface_mask_overlay()


def test_test_suite_never_points_the_window_at_the_real_cache(win, tmp_path):
    """Reset All deletes the cache folder; under pytest that must be a temp folder."""
    assert str(win.cache_dir).startswith(str(tmp_path))
