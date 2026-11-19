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
    assert panel.table.actions() == [
        panel.export_action, panel.export_json_action, panel.delete_row_action, panel.clear_table_action,
    ]


def test_quicklook_tooltip_is_restored_after_fits_frames(win):
    panel = win.surface_panel
    original = panel.threshold_group.toolTip()
    panel.set_quicklook(True)
    assert "quicklook" in panel.threshold_group.toolTip()
    panel.set_quicklook(False)
    assert panel.threshold_group.toolTip() == original


# ------------------------------------------- real maps through the click path
_TRUTH_HEADER = {
    "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": "arcsec", "CUNIT2": "arcsec",
    "CRPIX1": 256.5, "CRPIX2": 256.5, "CRVAL1": 0.0, "CRVAL2": 0.0, "CDELT1": 4.4, "CDELT2": 4.4,
    "DATE-OBS": "2024-05-10T12:00:00", "RSUN_REF": 696000000.0,
}
_HMI_ROTATED = dict(
    _TRUTH_HEADER, TELESCOP="SDO/HMI", INSTRUME="HMI_FRONT2", CONTENT="CONTINUUM INTENSITY", WAVELNTH=6173.0,
    CROTA2=179.93, DSUN_OBS=1.5115e11, HGLN_OBS=0.0, HGLT_OBS=-3.2, RSUN_OBS=949.9, T_OBS="2024-05-10T12:00:00",
)
_EUVI_NO_RSUN = dict(
    _TRUTH_HEADER, TELESCOP="STEREO", OBSRVTRY="STEREO_A", INSTRUME="SECCHI", DETECTOR="EUVI", WAVELNTH=195.0,
    CROTA=0.0, DSUN_OBS=0.96 * 1.495978707e11, HGLN_OBS=-20.0, HGLT_OBS=5.0,
)


def _load_fits_map(win, tmp_path, header):
    pytest.importorskip("sunpy")
    from astropy.io import fits

    from src.backend.solar.solar_data_analysis import load_aia_maps_streaming

    path = tmp_path / "frame.fits"
    fits.writeto(path, np.ones((512, 512), dtype="float32"), fits.Header(header), overwrite=True)
    frames = load_aia_maps_streaming([str(path)])
    win._apply_loaded_frames(list(frames.maps), paths=list(frames.paths), metadata=dict(frames.metadata))
    win.measurements_check.setChecked(True)
    QApplication.processEvents()
    # The frame as displayed (north-up when North Up is on): a user's clicks
    # land on its pixels.
    return win._map_frames[0]


def _user_clicks(win, source_map, lons, lats):
    """Canvas arcsec a user clicks for these Stonyhurst points (via the map's own WCS)."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import frames

    coords = SkyCoord(
        np.asarray(lons) * u.deg, np.asarray(lats) * u.deg, frame=frames.HeliographicStonyhurst,
        obstime=source_map.date, observer=source_map.observer_coordinate, rsun=source_map.rsun_meters,
    )
    px, py = source_map.world_to_pixel(coords)
    origin = getattr(win._map_frames[0], "_crop_origin_px", (0, 0))
    return [
        win.pyqt_canvas.map_arcsec_from_pixel(float(x) - origin[0], float(y) - origin[1])
        for x, y in zip(np.asarray(getattr(px, "value", px)), np.asarray(getattr(py, "value", py)))
    ]


def _measure_box(win, source_map, lon0, lat0, size=10.0):
    n = 30
    e = np.linspace(0.0, size, n)
    lons = np.concatenate([lon0 + e, np.full(n, lon0 + size), lon0 + size - e, np.full(n, lon0)])
    lats = np.concatenate([np.full(n, lat0), lat0 + e, np.full(n, lat0 + size), lat0 + size - e])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("polygon")
    win.surface_panel.set_threshold_mode("none")
    for x, y in _user_clicks(win, source_map, lons, lats):
        win._measure.on_canvas_click(x, y, "left")
    win._measure.surface.finish()
    truth = math.radians(size) * (math.sin(math.radians(lat0 + size)) - math.sin(math.radians(lat0))) / (2 * math.pi) * 1e6
    return win._measure.surface.last_result.region, truth


def _measure_meridian_arc(win, source_map, lon0, lat0, size=10.0):
    win.length_tool_btn.setChecked(True)
    lats = lat0 + np.linspace(0.0, size, 21)
    for x, y in _user_clicks(win, source_map, np.full(21, lon0), lats):
        win._measure.on_canvas_click(x, y, "left")
    win._measure.surface.finish()
    rsun_km = float(source_map.rsun_meters.to_value("km"))
    return win._measure.surface.last_result, rsun_km * math.radians(size)


@pytest.mark.parametrize("north_up", [True, False])
def test_rotated_hmi_frame_measures_and_places_features_correctly(win, tmp_path, north_up):
    # North Up off keeps the frame as stored: the measurement geometry then has
    # to apply the 180-deg rotation itself.
    win._set_north_up_silently(north_up)
    source = _load_fits_map(win, tmp_path, _HMI_ROTATED)
    for lon0, lat0 in [(-40.0, 10.0), (30.0, -30.0)]:
        area, truth = _measure_box(win, source, lon0, lat0)
        assert area.on_disk_msh == pytest.approx(truth, rel=0.02)
        # The centroid lands on the box, not on the mirrored hemisphere.
        lon, lat = area.centroid_lonlat
        assert lon == pytest.approx(lon0 + 5.0, abs=0.3)
        assert lat == pytest.approx(lat0 + 5.0, abs=0.5)
    length, truth = _measure_meridian_arc(win, source, 20.0, -10.0)
    assert length.total_km == pytest.approx(truth, rel=0.005)
    assert length.start_lonlat == pytest.approx((20.0, -10.0), abs=0.2)


def test_frame_without_rsun_obs_measures_with_its_true_radius(win, tmp_path):
    source = _load_fits_map(win, tmp_path, _EUVI_NO_RSUN)
    assert win._solar_radius_arcsec(win._map_frames[0]) == pytest.approx(999.6, abs=0.1)
    area, truth = _measure_box(win, source, -40.0, 10.0)
    assert area.on_disk_msh == pytest.approx(truth, rel=0.02)
    length, truth = _measure_meridian_arc(win, source, 0.0, 0.0)
    assert length.total_km == pytest.approx(truth, rel=0.005)


def test_cropped_frame_keeps_the_radius_and_measures_correctly(win, tmp_path):
    source = _load_fits_map(win, tmp_path, _EUVI_NO_RSUN)
    for spin, value in zip(
        (win.crop_x0_spin, win.crop_x1_spin, win.crop_y0_spin, win.crop_y1_spin), (-700.0, 700.0, -700.0, 700.0)
    ):
        spin.setValue(value)
    win.apply_axis_crop()
    QApplication.processEvents()
    cropped = win._map_frames[0]
    assert cropped.data.shape[0] < 512  # really cropped
    # Before the fix a crop lost the derived radius and fell back to 960".
    assert win._solar_radius_arcsec(cropped) == pytest.approx(999.6, abs=0.1)
    length, truth = _measure_meridian_arc(win, source, -10.0, -5.0)
    assert length.total_km == pytest.approx(truth, rel=0.005)
    area, truth = _measure_box(win, source, -15.0, -5.0)
    assert area.on_disk_msh == pytest.approx(truth, rel=0.02)


def test_dark_thresholds_ignore_the_sky_and_extreme_limb(win):
    """The off-disk sky and the outer limb are darker than the quiet Sun but are not spots.

    Before the fix a shape reaching the limb counted them: a full-disk HMI
    frame came out at ~69,000 MSH of "sunspot" instead of ~4,000.
    """
    _load(win, [DiskMap(_disk())])  # sky pixels are 5.0, far below the quiet Sun
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", -1050.0, -1050.0)
    surface.on_drag("finish", 1050.0, 1050.0)
    result = surface.last_result
    umbra, penumbra = _expected_spot()
    # Only the real spot is found, not the ring of sky round the disk.
    assert result.selected.off_limb_pixels == 0.0
    assert result.selected.on_disk_msh == pytest.approx(umbra.on_disk_msh + penumbra.on_disk_msh, rel=0.05)
    assert result.limb_excluded_px > 0
    assert "left out of the threshold" in _last_line(win)
    # A bright threshold still reaches off the limb (arcades live there).
    win.surface_panel.set_threshold_mode("bright")
    assert surface.last_result.limb_excluded_px == 0


# ------------------------------------------------------------------ North Up
def test_rotated_hmi_is_shown_north_up_by_default(win, tmp_path):
    assert win.north_up_check.isChecked()
    _load_fits_map(win, tmp_path, _HMI_ROTATED)
    frame = win._map_frames[0]
    assert float(frame.meta["crota2"]) == pytest.approx(-0.07, abs=1e-6)
    assert "north-up" in win.analysis_text.toPlainText()
    # Reading the axes now finds the feature where the solar coordinates say it is.
    from src.backend.solar.surface_measure import SurfaceGeometry, arcsec_from_stonyhurst

    reader = SurfaceGeometry(rsun_arcsec=949.9, b0_deg=-3.2)
    tx, ty, _ = arcsec_from_stonyhurst(30.0, -20.0, reader)
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(float(tx), float(ty), "left")
    win._measure.on_canvas_click(float(tx) + 50.0, float(ty), "left")
    win._measure.surface.finish()
    lon, lat = win._measure.surface.last_result.start_lonlat
    assert (lon, lat) == pytest.approx((30.0, -20.0), abs=0.3)


def test_north_up_off_reloads_the_stored_frames(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _load_fits_map(win, tmp_path, _HMI_ROTATED)
    win.north_up_check.setChecked(False)
    assert float(win._map_frames[0].meta["crota2"]) == pytest.approx(179.93)
    # With measurements present it asks first; "No" keeps everything as it was.
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(0.0, 0.0, "left")
    win._measure.on_canvas_click(100.0, 0.0, "left")
    win._measure.surface.finish()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.north_up_check.setChecked(True)
    assert not win.north_up_check.isChecked()
    assert win._measure.surface.records
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.north_up_check.setChecked(True)
    assert win._measure.surface.records == []
    assert float(win._map_frames[0].meta["crota2"]) == pytest.approx(-0.07, abs=1e-6)


def test_project_keeps_the_orientation_of_its_measurements(win, tmp_path):
    _load_fits_map(win, tmp_path, _HMI_ROTATED)
    meta = win._collect_session_meta()
    assert meta["view"]["north_up"] is True
    # A project saved before North Up existed was never rotated: it reopens unrotated.
    old = dict(meta)
    old["view"] = {k: v for k, v in meta["view"].items() if k != "north_up"}
    win._set_north_up_silently(True)
    old["frame_files"] = ["frame.fits"]
    loads = []
    # Stand in for both loaders (local files, archive re-fetch): a real one
    # starts a background thread that would outlive the test.
    win.load_local_paths = lambda paths, **kw: loads.append(list(paths))
    win._refetch_project_frames = lambda *a, **kw: loads.append("refetch")
    win.restore_project_part(old, {"frame.fits": str(tmp_path / "frame.fits")})
    assert loads == [[str(tmp_path / "frame.fits")]]  # the frames were sent to be reloaded...
    assert not win.north_up_check.isChecked()  # ...unrotated, as the project was made


# ------------------------------------------------- corrected / as-seen control
def test_values_control_switches_between_corrected_and_as_seen(win):
    _load(win, _sequence(hours=(0.0, 12.0)))
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _box_on_spot(win, _spot_xy(0.0))
    surface = win._measure.surface
    surface.track_feature()
    record = surface.records[surface.active]
    panel = win.surface_panel
    assert panel.corrected()
    corrected_cell = panel.table.item(surface.active, 3).text()

    panel.set_corrected(False)
    seen_cell = panel.table.item(surface.active, 3).text()
    assert seen_cell == record.headline_for(corrected=False) != corrected_cell
    assert "as seen" in panel.result_text()
    assert "(corrected " in panel.result_text()
    assert "not corrected for curvature" in record.summary or "not corrected for curvature" in surface._format(surface.last_result)
    assert "as seen" in panel.plot.getPlotItem().getAxis("left").labelText
    _, ys = panel._series_main.getData()
    assert list(ys) == pytest.approx([r.value("area_msh", corrected=False) for r in surface.records if r.name == record.name])
    # The switch only changes what is shown: the stored numbers are untouched,
    # and the table still knows the shape is recorded.
    assert record.values["area_msh"] > record.values["area_as_seen_msh"]
    assert "Not in the table" not in panel.result_text()
    # It travels with the project.
    meta = win._collect_session_meta()
    assert meta["measurements"]["surface_settings"]["corrected"] is False


def test_length_as_seen_is_the_plane_of_sky_length(win):
    _load(win, [DiskMap(_disk())])
    win.length_tool_btn.setChecked(True)
    win.surface_panel.set_corrected(False)
    for x in (500.0, 700.0, 850.0):
        win._measure.on_canvas_click(x, 100.0, "left")
    win.surface_panel.finish_btn.click()
    result = win._measure.surface.last_result
    line = _last_line(win)
    assert f"{result.plane_of_sky_km / 1000.0:,.1f} Mm as seen" in line
    assert f"{result.total_km / 1000.0:,.1f} Mm along the surface" in line


def test_toolbar_north_up_follows_the_display_option(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    assert win.north_up_bar_check.isChecked() and win.north_up_check.isChecked()
    _load_fits_map(win, tmp_path, _HMI_ROTATED)
    win.north_up_bar_check.setChecked(False)  # the toolbar drives the real setting
    assert not win.north_up_check.isChecked()
    assert float(win._map_frames[0].meta["crota2"]) == pytest.approx(179.93)
    win.north_up_check.setChecked(True)  # and follows it
    assert win.north_up_bar_check.isChecked()
    # A refused change puts both back.
    win.length_tool_btn.setChecked(True)
    win._measure.on_canvas_click(0.0, 0.0, "left")
    win._measure.on_canvas_click(100.0, 0.0, "left")
    win._measure.surface.finish()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.north_up_bar_check.setChecked(False)
    assert win.north_up_check.isChecked() and win.north_up_bar_check.isChecked()


# ------------------------------------------- HMI area plan (sections 5-8)
def test_area_method_controls_default_to_the_plan(win):
    _load(win, [DiskMap(_disk())])
    panel = win.surface_panel
    win.measure_side_tabs.setCurrentWidget(panel)
    assert not panel.area_method_group.isVisibleTo(panel)
    win.area_tool_btn.setChecked(True)
    assert panel.area_method_group.isVisibleTo(panel)
    settings = panel.area_settings()
    assert (settings.method, settings.mu_min, settings.subsamples) == ("finite_distance", pytest.approx(0.30), 1)
    assert panel.threshold_combo.itemText(0).startswith("Selected region")


def test_near_limb_pixels_are_excluded_tinted_and_reported_partial(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", 700.0, -100.0)
    surface.on_drag("finish", 950.0, 100.0)  # runs to mu ~0.2
    result = surface.last_result
    assert result.status == "partial"
    assert result.region.near_limb_px > 0
    assert result.region.min_mu >= 0.30
    report = win.surface_panel.result_text()
    assert report.startswith("● PARTIAL:")
    assert "below μ 0.30" in report and "quality PARTIAL" in report
    assert "Method: Finite-distance WCS integration, pixel centres" in report
    # Measured and excluded pixels get distinct tints.
    colors = [color for _mask, color in result.layers]
    from src.ui.solar.solar_surface_tools import EXCLUDED_COLOR, REGION_COLOR

    assert colors == [REGION_COLOR, EXCLUDED_COLOR]
    assert result.layers[1][0].any()
    # Lowering the cutoff brings them back in.
    win.surface_panel.cutoff_spin.setValue(0.1)
    assert surface.last_result.region.on_disk_msh > result.region.on_disk_msh
    assert surface.last_result.region.near_limb_px < result.region.near_limb_px
    assert win.surface_panel.table.item(surface.active, 3).text().endswith("⚠")


def test_off_disc_selection_is_invalid_not_zero(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", 1000.0, 300.0)
    surface.on_drag("finish", 1100.0, 400.0)  # entirely beyond the limb
    assert surface.last_result.status == "invalid"
    assert "no valid area" in win.surface_panel.result_text()
    assert win.surface_panel.table.item(surface.active, 3).text() == "invalid"
    assert surface.records[surface.active].values["status"] == "invalid"


def test_spotless_region_is_a_valid_zero(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", -400.0, -300.0)
    surface.on_drag("finish", -200.0, -100.0)  # quiet Sun, no spot
    result = surface.last_result
    assert result.status == "ok"
    assert "none detected: 0 MSH (valid)" in win.surface_panel.result_text()
    assert "no sunspot detected" in _last_line(win)


def test_orthographic_comparison_and_subpixel_refinement(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("ellipse")
    surface = win._measure.surface
    surface.on_drag("start", -52.0, -27.0)
    surface.on_drag("finish", 52.0, 27.0)
    finite = surface.last_result.region.on_disk_msh
    win.surface_panel.geometry_combo.setCurrentIndex(win.surface_panel.geometry_combo.findData("orthographic"))
    ortho = surface.last_result
    assert ortho.area_settings.method == "orthographic"
    assert "Orthographic approximation (comparison)" in win.surface_panel.result_text()
    # At disc centre parallel rays overestimate by 1/(1 - R/D)^2, about 0.9%.
    assert ortho.region.on_disk_msh / finite == pytest.approx(1.0095, abs=0.002)
    win.surface_panel.geometry_combo.setCurrentIndex(0)
    win.surface_panel.boundary_combo.setCurrentIndex(win.surface_panel.boundary_combo.findData(4))
    refined = surface.last_result
    assert refined.area_settings.subsamples == 4
    assert "subpixel 4×4" in win.surface_panel.result_text()
    # Fractional coverage follows the drawn ellipse (52" x 27" semi-axes, 5"/px).
    expected_px = math.pi * (52.0 / SCALE) * (27.0 / SCALE)
    assert refined.region.selected_weight == pytest.approx(expected_px, rel=0.02)


def test_sensitivity_range_brackets_the_area(win):
    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    result = win._measure.surface.last_result
    low, high, how = result.sensitivity
    assert low <= result.selected.on_disk_msh <= high
    assert how == "penumbra level ±0.02"
    assert "Sensitivity" in win.surface_panel.result_text()
    win.surface_panel.set_threshold_mode("none")
    low, high, how = win._measure.surface.last_result.sensitivity
    assert how == "mask ±1 px" and low < high


def test_inconsistent_header_refuses_measurement(win, tmp_path):
    bad = dict(_HMI_ROTATED, RSUN_OBS=990.0)  # asin(R/D) is 949.8": 4% apart
    _load_fits_map(win, tmp_path, bad)
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", -100.0, -100.0)
    surface.on_drag("finish", 100.0, 100.0)
    assert surface.last_result is None
    assert "inconsistent metadata" in win.surface_panel.result_text()


def test_json_export_carries_the_provenance(win, tmp_path, monkeypatch):
    import json

    from PySide6.QtWidgets import QFileDialog

    _load_fits_map(win, tmp_path, _HMI_ROTATED)
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", -150.0, -150.0)
    surface.on_drag("finish", 150.0, 150.0)
    target = tmp_path / "provenance"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "JSON files (*.json)"))
    win.surface_panel.export_json_action.trigger()
    document = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    values = document["measurements"][0]["values"]
    assert values["method"] == "finite_distance"
    assert values["mu_cutoff"] == pytest.approx(0.30)
    assert values["status"] == "ok"
    assert values["dsun_km"] == pytest.approx(1.5115e8)
    assert values["rsun_km"] == pytest.approx(696000.0)
    assert values["rsun_obs_header_arcsec"] == pytest.approx(949.9)
    assert values["source_file"] == "frame.fits"
    assert values["hdr_telescop"] == "SDO/HMI"
    assert values["north_up"] is True
    assert values["projected_mm2"] > 0 and values["correction_factor"] >= 1.0
    assert document["measurements"][0]["points"]  # the original ROI vertices


def test_area_method_travels_with_the_project(win):
    _load(win, [DiskMap(_disk())])
    panel = win.surface_panel
    panel.cutoff_spin.setValue(0.4)
    panel.boundary_combo.setCurrentIndex(panel.boundary_combo.findData(4))
    meta = win._collect_session_meta()
    saved = meta["measurements"]["surface_settings"]["area_method"]
    assert saved == {"method": "finite_distance", "mu_min": pytest.approx(0.4), "subsamples": 4}
    panel.apply_area_settings({"method": "orthographic", "mu_min": 0.2, "subsamples": 1})
    assert panel.area_settings().method == "orthographic"
    assert panel.cutoff_spin.value() == pytest.approx(0.2)


# ------------------------------------------------------ pixel-geometry check
def _write_hmi_continuum(tmp_path, n, true_cdelt, header_cdelt, name="hmi.fits"):
    """An HMI-like continuum disc whose header may be left at a finer (unbinned) grid."""
    pytest.importorskip("sunpy")
    from astropy.io import fits

    dsun, rsun_ref = 1.4746e11, 696_000_000.0
    rsun_arcsec = math.degrees(math.asin(rsun_ref / dsun)) * 3600.0
    c = (n - 1) / 2.0
    yy, xx = np.mgrid[0:n, 0:n]
    rr = np.hypot(xx - c, yy - c) / (rsun_arcsec / true_cdelt)
    mu = np.sqrt(np.clip(1 - rr**2, 0, 1))
    image = np.where(rr <= 1.0, 50000.0 * (1 - 0.88 * (1 - mu) + 0.23 * (1 - mu**2)), 300.0)
    image[rr > 1.05] = np.nan
    f = true_cdelt / header_cdelt
    header = {
        "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": "arcsec", "CUNIT2": "arcsec",
        "CDELT1": header_cdelt, "CDELT2": header_cdelt,
        "CRPIX1": (c + 0.5) * f + 0.5, "CRPIX2": (c + 0.5) * f + 0.5, "CRVAL1": 0.0, "CRVAL2": 0.0,
        "DATE-OBS": "2025-12-03T00:00:00", "T_OBS": "2025-12-03T00:00:00", "DSUN_OBS": dsun, "RSUN_REF": rsun_ref,
        "RSUN_OBS": rsun_arcsec, "HGLN_OBS": 0.0, "HGLT_OBS": 0.6, "CRLT_OBS": 0.6, "CRLN_OBS": 100.0,
        "TELESCOP": "SDO/HMI", "INSTRUME": "HMI_FRONT2", "WAVELNTH": 6173.0, "CONTENT": "CONTINUUM INTENSITY",
    }
    path = tmp_path / name
    fits.writeto(path, image.astype("float32"), fits.Header(header), overwrite=True)
    return str(path), rsun_arcsec


def _load_path(win, path):
    from src.backend.solar.solar_data_analysis import load_aia_maps_streaming

    frames = load_aia_maps_streaming([path])
    win._apply_loaded_frames(list(frames.maps), paths=list(frames.paths), metadata=dict(frames.metadata))
    win.measurements_check.setChecked(True)
    QApplication.processEvents()


def test_binned_frame_with_a_stale_header_is_corrected_at_load(win, tmp_path):
    path, rsun_arcsec = _write_hmi_continuum(tmp_path, 1024, true_cdelt=2.016, header_cdelt=0.504)
    _load_path(win, path)
    frame = win._map_frames[0]
    assert float(frame.scale.axis1.value) == pytest.approx(2.016)
    text = win.analysis_text.toPlainText()
    assert "Pixel check: 1024² (binned ¼ or synoptic)" in text
    assert "Corrected the header of 1 frame(s)" in text
    # A centred disc-centre box now measures the true area, and the card says what was checked.
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", -100.0, -100.0)
    surface.on_drag("finish", 100.0, 100.0)
    result = surface.last_result
    assert result.status == "ok"
    report = win.surface_panel.result_text()
    assert "Image: 1024² (binned ¼ or synoptic) · 2.016″/px" in report
    assert "header corrected for 4× binning" in report
    # 200" x 200" at disc centre is ~ (200/rsun)^2 R^2 of disc: about 6,700 MSH, not 16 times off.
    expected = (200.0 / rsun_arcsec) ** 2 / (2 * math.pi) * 1e6
    assert result.region.on_disk_msh == pytest.approx(expected, rel=0.05)


def test_correctly_binned_frames_measure_the_same_at_every_size(win, tmp_path):
    areas = {}
    for n, cdelt in ((1024, 2.016), (2048, 1.008)):
        path, _ = _write_hmi_continuum(tmp_path, n, true_cdelt=cdelt, header_cdelt=cdelt, name=f"hmi{n}.fits")
        _load_path(win, path)
        assert win._map_frames[0].meta["ECA_PIXCHK"].endswith("✓")
        win.area_tool_btn.setChecked(True)
        win.surface_panel.set_shape("ellipse")
        surface = win._measure.surface
        surface.on_drag("start", -300.0, -150.0)
        surface.on_drag("finish", 300.0, 150.0)
        areas[n] = surface.last_result.region.on_disk_msh
    assert areas[2048] == pytest.approx(areas[1024], rel=0.01)


def test_unexplained_plate_scale_mismatch_blocks_measurement(win, tmp_path):
    path, _ = _write_hmi_continuum(tmp_path, 1024, true_cdelt=2.016, header_cdelt=2.016 * 1.3)
    _load_path(win, path)
    assert "fail the pixel check" in win.analysis_text.toPlainText()
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_shape("rectangle")
    surface = win._measure.surface
    surface.on_drag("start", -100.0, -100.0)
    surface.on_drag("finish", 100.0, 100.0)
    assert surface.last_result is None
    assert "pixel geometry" in win.surface_panel.result_text()


def test_a_non_radial_background_warns_that_thresholds_are_unreliable(win):
    """AIA 4500 / 1700 on real data: the quiet Sun is not radially symmetric, so a
    fixed fraction of I_qs(mu) reads background as spot. The result says so."""
    from src.backend.solar.surface_records import area_values

    _load(win, [DiskMap(_disk())])
    win.area_tool_btn.setChecked(True)
    win.surface_panel.set_threshold_mode("sunspot")
    _spot_box(win)
    flat = win._measure.surface.last_result
    assert flat.background_spread is not None and flat.background_spread < 0.02
    assert not any("radially symmetric" in n for n in flat.notes)

    xs = np.arange(N, dtype=float)[None, :] - (N - 1) / 2.0
    tilted = _disk() * (1.0 + 0.3 * xs / N)
    win2 = SolarDataAnalysisWindow()
    try:
        _load(win2, [DiskMap(tilted)])
        win2.area_tool_btn.setChecked(True)
        win2.surface_panel.set_threshold_mode("sunspot")
        _spot_box(win2)
        result = win2._measure.surface.last_result
        assert result.background_spread > 0.05
        assert any("radially symmetric" in n for n in result.notes)
        assert "radially symmetric" in win2.surface_panel.result_label.text()
        assert area_values(result)["background_spread"] == pytest.approx(result.background_spread)
    finally:
        win2.close()
