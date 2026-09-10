"""
e-CALLISTO FITS Analyzer
Offscreen tests for the GCS CME fitting tool: the canvas overlay
(src/UI/sunpy_plot_window.py), the "gcs" measurement mode
(src/UI/solar_measure_tools.py) and its wiring in the Solar Image Analysis
window.

The load-bearing test here is
``test_a_parameter_change_never_rerenders_the_frame``: the whole design rests on
a GCS update touching only the overlay item, so that contract is asserted rather
than trusted.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from PySide6.QtWidgets import QApplication

from src.Backend.gcs_model import (
    GCSParameters,
    ObserverGeometry,
    apex_arcsec,
    handle_positions_arcsec,
    wireframe_arcsec,
)
from src.UI.solar_measure_tools import GCSFitEntry, MeasurementController
from src.UI.sunpy_plot_window import SunPyPlotCanvas

PARAMS = GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32)
OBSERVER = ObserverGeometry(
    lon_deg=0.0, lat_deg=-4.0, dsun_rsun=215.0, rsun_arcsec=960.0, label="LASCO C3"
)


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 4):
    app = _app()
    for _ in range(times):
        app.processEvents()


@pytest.fixture
def coronagraph_map():
    """A synthetic LASCO-C3-like frame with a real WCS."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import get_earth
    from sunpy.map.header_helper import make_fitswcs_header

    obstime = "2012-07-12T16:00:00"
    data = np.random.default_rng(3).random((64, 64))
    ref = SkyCoord(
        0 * u.arcsec,
        0 * u.arcsec,
        obstime=obstime,
        observer=get_earth(obstime),
        frame="helioprojective",
    )
    header = make_fitswcs_header(
        data, ref, scale=[112, 112] * u.arcsec / u.pix, instrument="LASCO", detector="C3"
    )
    return sunpy.map.Map(data, header)


# --- Canvas overlay --------------------------------------------------------


@pytest.fixture
def canvas():
    _app()
    widget = SunPyPlotCanvas()
    yield widget
    widget.close()


def test_canvas_draws_and_clears_the_wireframe(canvas):
    x, y, _ = wireframe_arcsec(PARAMS, OBSERVER)
    canvas.set_gcs_overlay(x, y)
    assert canvas.has_gcs_overlay()
    canvas.clear_gcs_overlay()
    assert not canvas.has_gcs_overlay()


def test_canvas_rejects_degenerate_wireframe_input(canvas):
    canvas.set_gcs_overlay([], [])
    assert not canvas.has_gcs_overlay()
    canvas.set_gcs_overlay([1.0, 2.0], [1.0])  # mismatched lengths
    assert not canvas.has_gcs_overlay()
    canvas.set_gcs_overlay([np.nan, np.nan], [np.nan, np.nan])
    assert not canvas.has_gcs_overlay()


def test_overlay_reuses_one_curve_item_across_updates(canvas):
    """Item churn per slider tick is exactly the jank this design avoids."""
    before = canvas._gcs_curve
    for height in (6.0, 7.0, 8.0, 9.0):
        x, y, _ = wireframe_arcsec(PARAMS.replace_values(height_rsun=height), OBSERVER)
        canvas.set_gcs_overlay(x, y)
    assert canvas._gcs_curve is before
    assert len(canvas._gcs_handles) == 0  # handles are opt-in


def test_handles_are_created_once_and_then_only_moved(canvas):
    canvas.set_gcs_handles(handle_positions_arcsec(PARAMS, OBSERVER))
    assert set(canvas._gcs_handles) == {"apex", "flank", "rim"}
    first = canvas._gcs_handles["apex"]
    moved = PARAMS.replace_values(height_rsun=12.0)
    canvas.set_gcs_handles(handle_positions_arcsec(moved, OBSERVER))
    assert canvas._gcs_handles["apex"] is first


def test_moving_handles_programmatically_does_not_fire_the_callback(canvas):
    """setPos re-emits sigPositionChanged, which would otherwise recurse."""
    seen: list[tuple] = []
    canvas.set_gcs_handle_callback(lambda *args: seen.append(args))
    canvas.set_gcs_handles(handle_positions_arcsec(PARAMS, OBSERVER))
    canvas.set_gcs_handles(
        handle_positions_arcsec(PARAMS.replace_values(height_rsun=14.0), OBSERVER)
    )
    assert seen == []


def test_a_handle_drag_reports_and_then_releases_the_click_guard(canvas):
    seen: list[tuple] = []
    canvas.set_gcs_handle_callback(lambda *args: seen.append(args))
    canvas.set_gcs_handles(handle_positions_arcsec(PARAMS, OBSERVER))

    canvas._on_gcs_handle_moved("apex", canvas._gcs_handles["apex"], False)
    assert seen and seen[-1][0] == "apex"
    assert canvas.gcs_drag_active()

    canvas._on_gcs_handle_moved("apex", canvas._gcs_handles["apex"], True)
    assert canvas.gcs_drag_active()  # still set until the click has been delivered
    _flush()
    assert not canvas.gcs_drag_active()


def test_non_finite_handle_positions_hide_rather_than_crash(canvas):
    canvas.set_gcs_handles({"apex": (math.nan, 0.0), "flank": (10.0, 20.0)})
    assert not canvas._gcs_handles.get("apex", None) or "apex" not in canvas._gcs_handles
    assert canvas._gcs_handles["flank"].isVisible()


# --- Measurement mode ------------------------------------------------------


@pytest.fixture
def window(coronagraph_map):
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    win = SolarDataAnalysisWindow()
    win._map_frames = [coronagraph_map]
    win._original_frames = [coronagraph_map]
    win._current_frame_index = 0
    win._current_map_data = np.asarray(coronagraph_map.data, dtype=float)
    yield win
    win.close()


def test_gcs_is_a_mode_and_excludes_the_other_tools(window):
    assert "gcs" in MeasurementController.MODES
    measure = window._measure
    measure.set_mode("gcs")
    assert measure.mode == "gcs"
    measure.set_mode("circle_fit")
    assert measure.mode == "circle_fit"


def test_the_seed_fit_is_visible_not_merely_valid(window):
    """A head-on seed lands inside the C3 occulter and shows nothing."""
    measure = window._measure
    measure.set_mode("gcs")
    params = measure.gcs_parameters()
    observer = measure._gcs_observer()
    assert observer is not None
    assert params.is_physical
    # Roughly on the limb, so it clears the occulter and is un-foreshortened.
    offset = abs(((params.lon_deg - observer.lon_deg + 180.0) % 360.0) - 180.0)
    assert offset == pytest.approx(90.0, abs=1.0)
    # Height starts inside the detector's field of view.
    inner, outer = measure._gcs_fov()
    assert inner < params.height_rsun < outer

    measure.set_gcs_parameters(params)
    _, _, projection = wireframe_arcsec(
        params, observer, fov_rsun=measure._gcs_fov()
    )
    assert projection.visible_fraction > 0.2
    assert window.pyqt_canvas.has_gcs_overlay()


def test_a_parameter_change_never_rerenders_the_frame(window, monkeypatch):
    """The smoothness contract, as a test.

    ``_render_current_frame`` rebuilds the display array, recomputes percentiles
    and refreshes four overlays. A GCS control must never reach it.
    """
    measure = window._measure
    measure.set_mode("gcs")
    measure.set_gcs_parameters(measure.gcs_parameters())

    calls: list[int] = []
    monkeypatch.setattr(
        window, "_render_current_frame", lambda *a, **k: calls.append(1), raising=False
    )
    for height in (5.0, 6.0, 7.0, 8.0):
        measure.set_gcs_parameters(measure.gcs_parameters().replace_values(height_rsun=height))
    assert calls == []
    assert window.pyqt_canvas.has_gcs_overlay()


def test_the_mesh_is_rebuilt_only_when_a_shape_parameter_changes(window):
    measure = window._measure
    measure.set_mode("gcs")
    base = measure.gcs_parameters()
    measure.set_gcs_parameters(base)
    mesh = measure._gcs_mesh
    assert mesh is not None

    # Orientation only: the mesh must survive untouched.
    measure.set_gcs_parameters(base.replace_values(lon_deg=base.lon_deg + 20.0, tilt_deg=44.0))
    assert measure._gcs_mesh is mesh

    measure.set_gcs_parameters(base.replace_values(height_rsun=base.height_rsun + 3.0))
    assert measure._gcs_mesh is not mesh


def test_clicking_collects_front_points(window):
    measure = window._measure
    measure.set_mode("gcs")
    for x, y in ((4000.0, 1000.0), (4200.0, 1400.0), (3800.0, 600.0)):
        measure.on_canvas_click(x, y, "left")
    assert len(measure._gcs_points[0]) == 3


def test_a_handle_drag_does_not_also_land_a_front_point(window):
    """The guard for TargetItem not accepting the click that ends its drag."""
    measure = window._measure
    measure.set_mode("gcs")
    canvas = window.pyqt_canvas
    canvas.set_gcs_handles(handle_positions_arcsec(measure.gcs_parameters(), OBSERVER))
    canvas._on_gcs_handle_moved("apex", canvas._gcs_handles["apex"], True)

    measure.on_canvas_click(4000.0, 1000.0, "left")
    assert measure._gcs_points.get(0) is None
    _flush()
    measure.on_canvas_click(4000.0, 1000.0, "left")
    assert len(measure._gcs_points[0]) == 1


def test_dragging_the_apex_moves_the_fit(window):
    measure = window._measure
    measure.set_mode("gcs")
    before = measure.gcs_parameters()
    apex = apex_arcsec(before, measure._gcs_observer())
    measure.on_gcs_handle_dragged("apex", apex[0] * 1.4, apex[1] * 1.4, True)
    assert measure.gcs_parameters().height_rsun > before.height_rsun


def test_refine_without_clicks_reports_instead_of_raising(window):
    measure = window._measure
    measure.set_mode("gcs")
    measure.refine_gcs_fit()  # must not raise out of a Qt handler
    assert measure._gcs_params is not None


def test_commit_stores_a_fit_keyed_by_frame(window):
    measure = window._measure
    measure.set_mode("gcs")
    measure.commit_gcs()
    assert 0 in measure.gcs_fits
    entry = measure.gcs_fits[0]
    assert isinstance(entry, GCSFitEntry)
    # Field 1 is the apex height, which is what the kinematics fit consumes.
    assert entry.apex_height_rsun == pytest.approx(measure.gcs_parameters().height_rsun)
    assert entry.refined is False

    measure.clear_gcs_fits()
    assert measure.gcs_fits == {}


def test_committed_fits_round_trip_through_restore(window):
    measure = window._measure
    measure.set_mode("gcs")
    measure.commit_gcs()
    saved = {index: tuple(entry) for index, entry in measure.gcs_fits.items()}
    points = {0: [(1.0, 2.0), (3.0, 4.0)]}

    measure.clear_gcs_fits()
    measure.restore_gcs_fits(saved, points, measure.gcs_parameters().as_array())
    assert set(measure.gcs_fits) == {0}
    assert measure._gcs_points[0] == [(1.0, 2.0), (3.0, 4.0)]


def test_a_committed_fit_stays_drawn_after_switching_tools(window):
    measure = window._measure
    measure.set_mode("gcs")
    measure.commit_gcs()
    measure.set_mode("ruler")
    assert window.pyqt_canvas.has_gcs_overlay()


def test_a_frame_without_coordinates_yields_no_observer(window):
    measure = window._measure

    class Bare:
        data = np.zeros((8, 8))

    window._map_frames = [Bare()]
    assert measure._gcs_observer() is None
    measure.set_mode("gcs")
    measure.refine_gcs_fit()  # degrades with a status message, does not raise
    measure.commit_gcs()
    assert measure.gcs_fits == {}


def test_the_detector_field_of_view_is_picked_up(window):
    """So the wireframe hides behind a C3 occulter instead of drawing over it."""
    assert window._measure._gcs_fov() == pytest.approx((3.7, 30.0))


# --- Parameter panel -------------------------------------------------------


def test_the_panel_echoes_parameters_without_feeding_back(window):
    """Drag -> parameters -> sliders must not re-emit, or it oscillates."""
    from src.UI.solar_measure_tools import GCSParameterPanel

    panel = GCSParameterPanel()
    seen: list = []
    panel.parametersChanged.connect(seen.append)
    panel.show_parameters(PARAMS)
    assert seen == []
    for name in ("lon_deg", "lat_deg", "height_rsun", "kappa"):
        assert panel.sliders[name].value() == pytest.approx(getattr(PARAMS, name), abs=0.1)


def test_moving_a_panel_slider_emits_only_that_change(window):
    from src.UI.solar_measure_tools import GCSParameterPanel

    panel = GCSParameterPanel()
    panel.show_parameters(PARAMS)
    seen: list = []
    panel.parametersChanged.connect(seen.append)
    slider = panel.sliders["height_rsun"]
    slider.slider.setValue(slider.slider.value() + 40)
    assert len(seen) == 1
    assert seen[0].height_rsun != pytest.approx(PARAMS.height_rsun)
    assert seen[0].lon_deg == pytest.approx(PARAMS.lon_deg)


def test_the_panel_reports_the_derived_geometry(window):
    from src.UI.solar_measure_tools import GCSParameterPanel

    panel = GCSParameterPanel()
    panel.show_parameters(PARAMS)
    text = panel.derived_label.text()
    assert "apex" in text and "leg h" in text and "full width" in text


def test_entering_gcs_mode_shows_the_panel_and_hides_circle_controls(window):
    measure = window._measure
    panel = window.tracking_panel
    measure.set_mode("gcs")
    _flush()
    assert panel.gcs_panel.isVisibleTo(panel)
    assert not panel.lock_center_check.isVisibleTo(panel)
    assert "3-D" in panel.fit_btn.text()

    measure.set_mode("circle_fit")
    _flush()
    assert not panel.gcs_panel.isVisibleTo(panel)
    assert panel.lock_center_check.isVisibleTo(panel)


def test_a_panel_slider_drives_the_overlay(window):
    measure = window._measure
    measure.set_mode("gcs")
    panel = window.tracking_panel.gcs_panel
    slider = panel.sliders["height_rsun"]
    slider.slider.setValue(slider.slider.value() + 60)
    _flush()
    assert measure.gcs_parameters().height_rsun == pytest.approx(slider.value(), abs=1e-6)
    assert window.pyqt_canvas.has_gcs_overlay()


def test_the_panel_buttons_reach_the_controller(window):
    measure = window._measure
    # The tracking panel is gated behind the Measurements switch, so a click on a
    # panel button does nothing until it is on — which is the real user flow.
    window.measurements_check.setChecked(True)
    measure.set_mode("gcs")
    panel = window.tracking_panel.gcs_panel
    panel.commit_gcs_btn.click()
    _flush()
    assert 0 in measure.gcs_fits
    panel.refine_btn.click()  # no clicks yet: reports, does not raise
    _flush()


def test_committed_fits_reach_the_tracking_table(window):
    measure = window._measure
    measure.set_mode("gcs")
    measure.commit_gcs()
    _flush()
    table = window.tracking_panel.table
    assert table.rowCount() == 1
    assert table.columnCount() == 6
    tip = table.item(0, 0).toolTip()
    # The honesty caveat has to reach the user, not just the docstring.
    assert "formal only" in tip
    assert "viewpoint" in tip


def test_the_gcs_button_is_mutually_exclusive_with_the_other_tools(window):
    window.measurements_check.setChecked(True)
    window.gcs_tool_btn.setChecked(True)
    _flush()
    assert window._measure.mode == "gcs"
    window.circle_tool_btn.setChecked(True)
    _flush()
    assert window._measure.mode == "circle_fit"
    assert not window.gcs_tool_btn.isChecked()


# --- Kinematics, export and session ---------------------------------------


def _commit_series(window, heights=(6.0, 7.5, 9.0)):
    """Commit one GCS fit per synthetic frame, at increasing apex heights."""
    from datetime import datetime, timedelta

    from src.UI.solar_measure_tools import GCSFitEntry

    measure = window._measure
    measure.set_mode("gcs")
    base = datetime(2012, 7, 12, 16, 0, 0)
    for index, height in enumerate(heights):
        measure.gcs_fits[index] = GCSFitEntry(
            when=base + timedelta(minutes=12 * index),
            apex_height_rsun=height,
            lon_deg=35.0,
            lat_deg=-12.0,
            tilt_deg=25.0,
            alpha_deg=32.0,
            kappa=0.32,
            rms_arcsec=60.0,
            n_points=20,
            n_viewpoints=2,
            separation_deg=62.0,
            lon_err_deg=0.3,
            lat_err_deg=0.2,
            height_err_rsun=0.04,
            refined=True,
        )
    measure._refresh_tracking_panel()
    return measure


def test_the_kinematics_fit_reports_a_three_d_speed(window):
    """The apex height is de-projected, so calling it plane-of-sky would throw
    away the entire point of using GCS."""
    measure = _commit_series(window)
    window.analysis_text.setPlainText("")
    measure.finish_gcs_fit()
    _flush()
    text = window.analysis_text.toPlainText()
    assert "3-D" in text and "de-projected" in text
    assert "plane-of-sky" not in text
    assert "km/s" in text or "speed" in text


def test_the_kinematics_fit_needs_a_time_baseline(window):
    measure = _commit_series(window, heights=(6.0,))
    measure.finish_gcs_fit()  # one frame: reports, does not raise
    _flush()
    assert window.tracking_panel.table.rowCount() == 1


def test_the_active_fit_and_clear_follow_the_gcs_panel(window):
    measure = _commit_series(window)
    assert measure._panel_source == "gcs"
    window.analysis_text.setPlainText("")
    measure.finish_active_fit()
    _flush()
    assert "3-D" in window.analysis_text.toPlainText()
    measure.clear_active()
    assert measure.gcs_fits == {}


def test_csv_export_keeps_every_field_and_its_error_bars(window):
    measure = _commit_series(window)
    panel = window.tracking_panel
    header = panel.csv_header()
    assert header[:3] == ["time_utc", "t_seconds", "apex_height_rsun"]
    for column in ("lon_err_deg", "lat_err_deg", "apex_height_err_rsun", "n_viewpoints"):
        assert column in header

    entries = sorted(measure.gcs_fits.values(), key=lambda item: item[0])
    row = panel.csv_row(entries[1], entries[0][0])
    assert len(row) == len(header)
    assert float(row[2]) == pytest.approx(7.5)
    assert float(row[1]) == pytest.approx(720.0)  # 12 minutes on


def test_a_session_round_trips_the_gcs_fits(window, tmp_path):
    from src.Backend.solar_session import deserialize_gcs_fits, serialize_gcs_fits

    measure = _commit_series(window)
    meta = window._collect_session_meta()
    saved = meta["measurements"]["gcs_fits"]
    assert len(saved) == 3
    assert meta["measurements"]["gcs_parameters"] is None or len(
        meta["measurements"]["gcs_parameters"]
    ) == 6

    restored = deserialize_gcs_fits(serialize_gcs_fits(measure.gcs_fits))
    measure.clear_gcs_fits()
    measure.restore_gcs_fits(restored)
    assert len(measure.gcs_fits) == 3
    assert measure.gcs_fits[1].apex_height_rsun == pytest.approx(7.5)
