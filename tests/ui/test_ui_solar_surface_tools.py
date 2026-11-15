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
    assert line.startswith("[Length 1] Length")
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
    assert line.startswith("[Area 1] Area (polygon)")
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
    assert "] Area (ellipse)" in _last_line(win)

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
    assert line.startswith("[Sunspot 1] Sunspot (rectangle)")
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


# ----------------------------------------------------------------- the table
def _two_frames(*, second_scale: float = 1.4):
    return [
        DiskMap(_disk(spot_scale=1.0), date="2026-02-10T01:00:00"),
        DiskMap(_disk(spot_scale=second_scale), date="2026-02-10T01:12:00"),
    ]


def test_finished_shapes_add_named_rows(win):
    _load(win, [DiskMap(_disk())])
    table = win.surface_panel.table
    win.length_tool_btn.setChecked(True)
    for x in (-200.0, 0.0, 200.0):
        win._measure.on_canvas_click(x, 0.0, "left")
    win.surface_panel.finish_btn.click()
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)

    surface = win._measure.surface
    assert [r.name for r in surface.records] == ["Length 1", "Sunspot 1"]
    assert table.rowCount() == 2
    assert table.item(0, 0).text() == "Length 1"
    assert table.item(0, 2).text() == "Length"
    assert table.item(0, 3).text().endswith(" Mm")
    assert table.item(1, 2).text() == "Sunspot"
    assert "MSH (U " in table.item(1, 3).text()
    assert table.item(1, 1).text() == "01:00:00"
    assert "Centroid: N" in table.item(1, 3).toolTip()
    assert table.item(1, 4).text() == f"{surface.records[1].values['mean_mu']:.2f}"
    assert win.surface_panel.selected_row() == 1  # the newest row is active
    assert win.surface_panel.table_btn.isEnabled()
    assert win.surface_panel.export_action.isEnabled()
    # Only the name is editable.
    assert table.item(1, 0).flags() & Qt.ItemIsEditable
    assert not table.item(1, 3).flags() & Qt.ItemIsEditable


def test_threshold_edits_never_rewrite_a_recorded_row(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    surface = win._measure.surface
    recorded = dict(surface.records[0].values)
    assert "Not in the table" not in win.surface_panel.result_label.text()

    # Getting the settings ready for the next shape leaves the row alone...
    win.surface_panel.set_threshold_mode("none")
    win.surface_panel.umbra_spin.setValue(0.2)
    assert surface.records[0].values == recorded
    assert surface.records[0].threshold["mode"] == "sunspot"
    assert "Not in the table" in win.surface_panel.result_label.text()

    # ...until Record, which updates the row in place on the same frame.
    win.surface_panel.set_threshold_mode("sunspot")
    win.surface_panel.record_btn.click()
    assert len(surface.records) == 1
    assert surface.records[0].values["umbra_msh"] == 0.0 < recorded["umbra_msh"]
    assert surface.records[0].threshold["umbra_level"] == pytest.approx(0.2)
    assert "Not in the table" not in win.surface_panel.result_label.text()


def test_record_on_another_frame_continues_the_feature(win):
    _load(win, _two_frames())
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    win.frame_slider.setValue(1)
    QApplication.processEvents()
    surface = win._measure.surface
    assert len(surface.records) == 1  # moving frames only measures live
    win.surface_panel.record_btn.click()
    assert [(r.name, r.frame_index) for r in surface.records] == [("Sunspot 1", 0), ("Sunspot 1", 1)]
    first, second = (r.values["area_msh"] for r in surface.records)
    assert second == pytest.approx(first * 1.4**2, rel=0.15)
    assert _last_line(win).startswith("[Sunspot 1]")
    # A brand-new shape starts a new feature.
    _spot_box(win, margin=80.0)
    assert surface.records[-1].name == "Sunspot 2"


def test_selecting_a_row_returns_to_its_frame_tool_and_settings(win):
    _load(win, _two_frames())
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    win.surface_panel.umbra_spin.setValue(0.5)
    _spot_box(win)
    win.frame_slider.setValue(1)
    win.length_tool_btn.setChecked(True)
    for x in (-200.0, 200.0):
        win._measure.on_canvas_click(x, 0.0, "left")
    win.surface_panel.finish_btn.click()
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("none")
    QApplication.processEvents()

    win.surface_panel.table.selectRow(0)
    QApplication.processEvents()
    surface = win._measure.surface
    assert win._current_frame_index == 0
    assert win._measure.mode == "area"
    assert win.surface_panel.threshold_settings().mode == "sunspot"
    assert win.surface_panel.umbra_spin.value() == pytest.approx(0.5)
    assert win.surface_panel.shape() == "rectangle"
    assert surface.shapes["area"].points == surface.records[0].points
    assert surface.last_result.umbra.on_disk_msh == pytest.approx(surface.records[0].values["umbra_msh"])

    win.surface_panel.table.selectRow(1)
    QApplication.processEvents()
    assert win._measure.mode == "length"
    assert win._current_frame_index == 1
    assert win.length_tool_btn.isChecked()


def test_rename_delete_and_clear_rows(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _load(win, [DiskMap(_disk())])
    win.length_tool_btn.setChecked(True)
    surface = win._measure.surface
    for start in (0.0, 300.0):
        win._measure.on_canvas_click(start, 0.0, "left")
        win._measure.on_canvas_click(start + 100.0, 0.0, "left")
        win.surface_panel.finish_btn.click()
    table = win.surface_panel.table
    table.item(0, 0).setText("Filament A")
    assert surface.records[0].name == "Filament A"
    assert surface.records[0].line.startswith("[Filament A] Length")
    table.item(1, 0).setText("   ")  # blank names are refused
    assert table.item(1, 0).text() == "Length 2"

    table.selectRow(0)
    win.surface_panel.delete_row_action.trigger()
    assert [r.name for r in surface.records] == ["Length 2"]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.surface_panel.clear_table_action.trigger()
    assert len(surface.records) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.surface_panel.clear_table_action.trigger()
    assert surface.records == []
    assert table.rowCount() == 0
    assert not win.surface_panel.export_action.isEnabled()
    assert not win.surface_panel.table_btn.isEnabled()


def test_export_csv_writes_every_row(win, tmp_path, monkeypatch):
    import csv

    from PySide6.QtWidgets import QFileDialog

    from src.backend.solar.surface_records import CSV_NOTES

    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    target = tmp_path / "spots"  # no extension: one is added
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "CSV files (*.csv)"))
    win.surface_panel.export_action.trigger()
    path = tmp_path / "spots.csv"
    lines = path.read_text(encoding="utf-8").splitlines()[len(CSV_NOTES):]
    rows = list(csv.DictReader(lines))
    assert len(rows) == 1
    assert rows[0]["name"] == "Sunspot 1"
    assert rows[0]["threshold"] == "sunspot"
    umbra, _ = _expected_spot()
    assert float(rows[0]["umbra_msh"]) == pytest.approx(umbra.on_disk_msh, rel=1e-5)
    assert "Exported 1" in win.statusBar().currentMessage()


def test_clear_all_measurements_empties_the_table(win):
    _load(win, [DiskMap(_disk())])
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(0.0, 0.0, "left")
    win._measure.on_canvas_click(100.0, 0.0, "left")
    win.surface_panel.finish_btn.click()
    win.clear_all_measurements()
    assert win._measure.surface.records == []
    assert win.surface_panel.table.rowCount() == 0


# ------------------------------------------------------------------ projects
def test_project_round_trip_restores_table_shapes_and_settings(win):
    _load(win, _two_frames())
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    win.surface_panel.penumbra_spin.setValue(0.85)
    win.surface_panel.tint_check.setChecked(False)
    _spot_box(win)
    win.frame_slider.setValue(1)
    win.surface_panel.record_btn.click()
    win.surface_panel.table.item(0, 0).setText("AR 13664")
    saved = [(r.name, r.frame_index, r.values["area_msh"]) for r in win._measure.surface.records]
    meta = win._collect_session_meta()
    assert len(meta["measurements"]["surface_records"]) == 2

    win2 = SolarDataAnalysisWindow()
    try:
        win2._pending_session_restore = meta
        win2._apply_loaded_frames(_two_frames(), paths=["a.fits", "b.fits"], metadata={})
        QApplication.processEvents()
        surface = win2._measure.surface
        assert [(r.name, r.frame_index, r.values["area_msh"]) for r in surface.records] == saved
        assert win2.surface_panel.table.rowCount() == 2
        assert win2.surface_panel.table.item(0, 0).text() == "AR 13664"
        assert win2.measurements_check.isChecked()
        assert win2.measure_side_tabs.currentWidget() is win2.surface_panel
        assert surface.shapes["area"].kind == "rectangle"
        assert win2.surface_panel.threshold_settings().mode == "sunspot"
        assert win2.surface_panel.penumbra_spin.value() == pytest.approx(0.85)
        assert not win2.surface_panel.tint_enabled()
        assert "2 surface measurement(s)" in win2.statusBar().currentMessage()
        # New features keep counting after the restored names.
        win2.area_tool_btn.setChecked(True)
        _spot_box(win2, margin=90.0)
        assert surface.records[-1].name == "Sunspot 2"
    finally:
        win2.close()


def test_project_restore_drops_rows_on_missing_frames(win):
    _load(win, _two_frames())
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(0.0, 0.0, "left")
    win._measure.on_canvas_click(100.0, 0.0, "left")
    win.surface_panel.finish_btn.click()
    meta = win._collect_session_meta()
    meta["measurements"]["surface_records"][0]["frame_index"] = 7
    meta["source"]["frame_times"] = []  # no time remap: indices are taken as saved

    win2 = SolarDataAnalysisWindow()
    try:
        import src.ui.solar.solar_data_analysis_window as mod

        warnings = []
        orig = mod.QMessageBox.warning
        mod.QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[2]))
        try:
            win2._pending_session_restore = meta
            win2._apply_loaded_frames(_two_frames(), paths=["a.fits", "b.fits"], metadata={})
            QApplication.processEvents()
        finally:
            mod.QMessageBox.warning = orig
        assert win2._measure.surface.records == []
        assert warnings and "1 measurement(s) were dropped" in warnings[0]
    finally:
        win2.close()


# ---------------------------------------------------------- tracking (Phase 4)
from datetime import datetime, timedelta

from src.backend.solar.surface_measure import (
    stonyhurst_from_arcsec,
    synodic_rotation_deg,
)

SPOT_LON0 = -20.0
SPOT_LAT = 15.0


def _rotating_disk(hours: float, *, lon0: float = SPOT_LON0) -> np.ndarray:
    """A disk whose round spot (a cap on the sphere) has rotated for ``hours``."""
    g = _geometry()
    mu = mu_map((N, N), g)
    image = 40_000.0 * (1.0 - 0.88 * (1.0 - mu) + 0.23 * (1.0 - mu * mu))
    image = np.where(np.isfinite(mu), image, 5.0)
    tx, ty = g.pixel_to_arcsec(np.arange(N)[None, :], np.arange(N)[:, None])
    lon, lat = stonyhurst_from_arcsec(tx, ty, g)
    lon_c = lon0 + float(synodic_rotation_deg(SPOT_LAT, hours * 3600.0))
    l1, b1 = np.radians(lon), np.radians(lat)
    cos_d = np.sin(b1) * math.sin(math.radians(SPOT_LAT)) + np.cos(b1) * math.cos(math.radians(SPOT_LAT)) * np.cos(
        l1 - math.radians(lon_c)
    )
    dist = np.degrees(np.arccos(np.clip(np.nan_to_num(cos_d, nan=-1.0), -1.0, 1.0)))
    image[dist < 4.0] *= 0.75
    image[dist < 2.0] *= 0.3 / 0.75
    return image


def _sequence(hours=(0.0, 12.0, 24.0, 36.0), **kw):
    t0 = datetime(2026, 2, 10, 0, 0, 0)
    return [
        DiskMap(_rotating_disk(h, **kw), date=(t0 + timedelta(hours=h)).isoformat())
        for h in hours
    ]


def _spot_xy(hours: float, lon0: float = SPOT_LON0):
    from src.backend.solar.surface_measure import arcsec_from_stonyhurst

    tx, ty, _ = arcsec_from_stonyhurst(lon0 + float(synodic_rotation_deg(SPOT_LAT, hours * 3600.0)), SPOT_LAT, _geometry())
    return float(tx), float(ty)


def _box_on_spot(win, centre, margin=110.0):
    surface = win._measure.surface
    win.surface_panel.set_shape("rectangle")
    surface.on_drag("start", centre[0] - margin, centre[1] - margin)
    surface.on_drag("finish", centre[0] + margin, centre[1] + margin)


def test_track_through_frames_follows_a_rotating_spot(win):
    _load(win, _sequence())
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _box_on_spot(win, _spot_xy(0.0))
    surface = win._measure.surface
    assert win.surface_panel.track_btn.isEnabled()
    first = surface.records[0].values["area_msh"]

    assert surface.track_feature() == 3
    rows = [r for r in surface.records if r.name == "Sunspot 1"]
    assert [r.frame_index for r in rows] == [0, 1, 2, 3]
    # The spot keeps its true size, so the tracked area stays put (pixel noise only).
    for row in rows:
        assert row.values["area_msh"] == pytest.approx(first, rel=0.12)
        assert row.threshold["mode"] == "sunspot"
    # Each tracked shape was carried west with the spot.
    xs = [np.mean([p[0] for p in r.points]) for r in rows]
    assert xs == sorted(xs)
    assert xs[-1] - xs[0] > 200.0
    assert "measured on 3 more frame(s), rotating with the Sun" in _last_line(win)
    # The plot shows the four points and a near-zero rate.
    xs_plot, ys_plot = win.surface_panel._series_main.getData()
    assert len(xs_plot) == 4
    assert xs_plot[1] - xs_plot[0] == pytest.approx(12 * 3600.0)
    assert "4 frames" in win.surface_panel.series_label.text()
    assert "MSH/h" in win.surface_panel.series_label.text()
    _, umbra = win.surface_panel._series_second.getData()
    assert len(umbra) == 4


def test_track_without_rotation_loses_the_spot(win):
    _load(win, _sequence())
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _box_on_spot(win, _spot_xy(0.0), margin=80.0)
    win.surface_panel.rotate_check.setChecked(False)
    surface = win._measure.surface
    surface.track_feature()
    areas = [r.values["area_msh"] for r in surface.records if r.name == "Sunspot 1"]
    assert areas[0] > 0.0
    assert areas[-1] < 0.2 * areas[0]  # the spot rotated out of the fixed box
    assert all(r.points == surface.records[0].points for r in surface.records)


def test_track_skips_frames_where_the_shape_goes_behind_the_limb(win):
    frames = _sequence(hours=(0.0, 24.0, 48.0), lon0=62.0)
    _load(win, frames)
    win.area_tool_btn.setChecked(True)
    _box_on_spot(win, _spot_xy(0.0, lon0=62.0), margin=60.0)
    surface = win._measure.surface
    written = surface.track_feature()
    assert written == 1  # 24 h: lon ~75 deg, fine; 48 h: past the limb
    assert "1 skipped (the shape rotated behind the limb)" in _last_line(win)


def test_track_replaces_the_features_old_rows_only(win):
    _load(win, _sequence(hours=(0.0, 12.0, 24.0)))
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _box_on_spot(win, _spot_xy(0.0))
    win.frame_slider.setValue(1)
    win.surface_panel.record_btn.click()  # an untracked row on frame 2
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(-500.0, -500.0, "left")
    win._measure.on_canvas_click(-400.0, -500.0, "left")
    win.surface_panel.finish_btn.click()
    surface = win._measure.surface
    win.surface_panel.table.selectRow(0)
    QApplication.processEvents()
    surface.track_feature()
    names = [(r.name, r.frame_index) for r in surface.records]
    assert names == [("Sunspot 1", 0), ("Sunspot 1", 1), ("Sunspot 1", 2), ("Length 1", 1)]
    assert surface.records[surface.active].frame_index == 0


def test_stepping_frames_follows_the_tracked_shape(win):
    _load(win, _sequence(hours=(0.0, 12.0, 24.0)))
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _box_on_spot(win, _spot_xy(0.0))
    surface = win._measure.surface
    surface.track_feature()
    win.frame_slider.setValue(2)
    QApplication.processEvents()
    record = surface.records[surface.active]
    assert record.frame_index == 2
    assert surface.shapes["area"].points == record.points
    assert win.surface_panel.selected_row() == surface.active
    # The live result is the recorded one: nothing to Record.
    assert "Not in the table" not in win.surface_panel.result_label.text()


def test_track_needs_a_selected_row_and_a_sequence(win):
    _load(win, [DiskMap(_disk())])
    surface = win._measure.surface
    assert surface.track_feature() == 0
    assert "Select a row" in win.statusBar().currentMessage()
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(0.0, 0.0, "left")
    win._measure.on_canvas_click(100.0, 0.0, "left")
    win.surface_panel.finish_btn.click()
    assert surface.track_feature() == 0
    assert "sequence" in win.statusBar().currentMessage()


def test_series_plot_follows_the_selected_feature(win):
    _load(win, _sequence(hours=(0.0, 12.0)))
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(-300.0, 100.0, "left")
    win._measure.on_canvas_click(-100.0, 150.0, "left")
    win.surface_panel.finish_btn.click()
    assert "1 frame" in win.surface_panel.series_label.text()
    assert "Track Frames" in win.surface_panel.series_label.text()
    win._measure.surface.track_feature()
    assert win.surface_panel.plot.getPlotItem().getAxis("left").labelText == "Length (Mm)"
    assert "2 frames" in win.surface_panel.series_label.text()
    win._measure.surface.clear_records()
    xs, _ = win.surface_panel._series_main.getData()
    assert xs is None or len(xs) == 0


def test_series_plot_follows_the_theme(win):
    panel = win.surface_panel
    panel.apply_theme(dark=False)
    assert panel.plot.backgroundBrush().color().lightness() > 200
    panel.apply_theme(dark=True)
    assert panel.plot.backgroundBrush().color().lightness() < 50
    win._is_dark_ui = lambda: False
    win._on_theme_changed()
    assert panel.plot.backgroundBrush().color().lightness() > 200
    assert win.tracking_panel.plot.backgroundBrush().color().lightness() > 200


def test_surface_tab_scrolls_vertically_and_never_clips_its_width(win):
    panel = win.surface_panel
    win.show()
    win.resize(1350, 800)
    _load(win, _sequence(hours=(0.0, 12.0, 24.0)))
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _box_on_spot(win, _spot_xy(0.0))
    win._measure.surface.track_feature()  # a filled table widens the content
    for _ in range(5):
        QApplication.processEvents()
    viewport = panel.scroll_area.viewport().width()
    assert panel._content.width() <= viewport
    assert panel._content.minimumSizeHint().width() <= viewport
    # The tab's content height is not the window's floor.
    assert panel.minimumSizeHint().height() < panel._content.minimumSizeHint().height()


def test_tab_shows_only_what_the_active_tool_uses(win):
    _load(win, [DiskMap(_disk())])
    panel = win.surface_panel
    win.measure_side_tabs.setCurrentWidget(panel)
    assert not panel.draw_section.isVisibleTo(panel)  # no tool: table and graph only
    assert not panel.threshold_group.isVisibleTo(panel)
    assert not panel.result_label.isVisibleTo(panel)
    win.length_tool_btn.setChecked(True)
    assert panel.draw_section.isVisibleTo(panel)
    assert not panel.shape_combo.isVisibleTo(panel)
    assert not panel.threshold_group.isVisibleTo(panel)
    win.area_tool_btn.setChecked(True)
    assert panel.shape_combo.isVisibleTo(panel)
    assert panel.threshold_group.isVisibleTo(panel)
    _spot_box(win)
    assert panel.result_label.isVisibleTo(panel)
    assert panel.result_text().splitlines()[0].startswith("Frame 1")
    # Field captions use the window's own label roles.
    assert panel.umbra_label.objectName() == "SolarFieldLabel"
    assert panel.hint_label.objectName() == "SolarHintLabel"


def test_table_actions_are_in_the_right_click_menu_too(win):
    panel = win.surface_panel
    assert panel.table.contextMenuPolicy() == Qt.ActionsContextMenu
    assert panel.table.actions() == [panel.export_action, panel.delete_row_action, panel.clear_table_action]


def test_quicklook_tooltip_is_restored_after_fits_frames(win):
    panel = win.surface_panel
    original = panel.threshold_group.toolTip()
    panel.set_quicklook(True)
    assert "quicklook" in panel.threshold_group.toolTip()
    panel.set_quicklook(False)
    assert panel.threshold_group.toolTip() == original
