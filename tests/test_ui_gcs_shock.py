"""The shock model in the three-viewpoint GCS window (src/UI/gcs_fitting_window.py).

The load-bearing tests pin what makes fitting a shock beside the flux rope safe:
the two models share the images but never their parameters, front points,
recorded fits or kinematics.
"""

from datetime import datetime, timedelta
import json

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from test_ui_gcs_window import _app, _flush, _sequence
from src.Backend.shock_model import (
    ELLIPSOID,
    SPHEROID,
    shock_apex_arcsec,
    shock_semi_axes,
    shock_silhouette_arcsec,
)
from src.UI.gcs_fitting_window import GCS_MODEL, SHOCK_MODEL, GCSFittingWindow
from src.UI.solar_measure_tools import ShockFitEntry, TrackingPanel

BASE = datetime(2012, 7, 12, 16)
LONGITUDES = (0.0, 62.0, -70.0)


@pytest.fixture
def window():
    _app()
    widget = GCSFittingWindow(target_time=BASE)
    yield widget
    widget.close()


def _load(window, n=4):
    for panel, lon in zip(window.panels, LONGITUDES):
        panel.set_frames(_sequence(lon, n=n))
    _flush()


def _handles_visible(canvas, layer):
    state = canvas._overlay_layer(layer, create=False)
    return state is not None and any(item.isVisible() for item in state["handles"].values())


# --- Showing and editing ------------------------------------------------------------


def test_the_shock_stays_hidden_until_it_is_edited(window):
    _load(window)
    assert window.editing_model() == GCS_MODEL
    assert not window.shock_check.isChecked()
    assert all(panel.canvas.has_gcs_overlay() and not panel.canvas.has_gcs_overlay(SHOCK_MODEL)
               for panel in window.panels)

    window.set_editing_model(SHOCK_MODEL)
    assert window.shock_check.isChecked()
    assert window.model_stack.currentWidget() is window.shock_panel
    assert window.kinematics_card.title_label.text() == "Kinematics · Shock"
    assert window.tracking_panel._source == "shock"
    for panel in window.panels:
        # Both models drawn together; only the edited one can be dragged.
        assert panel.canvas.has_gcs_overlay() and panel.canvas.has_gcs_overlay(SHOCK_MODEL)
        assert _handles_visible(panel.canvas, SHOCK_MODEL)
        assert not _handles_visible(panel.canvas, "gcs")
    radios = {str(button.property("segment_key")): button for button in window._model_group.buttons()}
    assert radios[SHOCK_MODEL].isChecked() and not radios[GCS_MODEL].isChecked()


def test_editing_a_hidden_model_shows_it_again(window):
    _load(window)
    window.wireframe_check.setChecked(False)
    window.set_editing_model(SHOCK_MODEL)
    window.set_editing_model(GCS_MODEL)
    assert window.wireframe_check.isChecked()
    assert all(panel.canvas.has_gcs_overlay() for panel in window.panels)


def test_the_shock_is_drawn_even_on_a_view_outside_the_time_tolerance(window):
    window.panels[0].set_frames(_sequence(0.0, n=2, cadence_min=20))
    window.panels[1].set_frames(_sequence(62.0, n=1, start_min=12))
    window.set_editing_model(SHOCK_MODEL)
    assert not window.panels[1].is_synchronized()
    assert window.panels[1].canvas.has_gcs_overlay(SHOCK_MODEL)
    assert not _handles_visible(window.panels[1].canvas, SHOCK_MODEL)


def test_the_shock_starts_along_the_gcs_direction_in_its_own_colour(window):
    _load(window)
    gcs, shock = window.parameters(), window.shock_parameters()
    assert (shock.lon_deg, shock.lat_deg, shock.model) == (gcs.lon_deg, gcs.lat_deg, SPHEROID)
    window.set_editing_model(SHOCK_MODEL)
    canvas = window.panels[0].canvas
    assert canvas.gcs_style(SHOCK_MODEL)["color"] != canvas.gcs_style()["color"]
    assert canvas.gcs_style(SHOCK_MODEL)["width"] == pytest.approx(canvas.gcs_style()["width"])


def test_shock_and_gcs_sliders_never_move_each_others_model(window):
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    gcs, shock = window.parameters(), window.shock_parameters()
    slider = window.shock_panel.sliders["height_rsun"]
    slider.slider.setValue(slider.slider.value() + 50)
    assert window.shock_parameters().height_rsun > shock.height_rsun
    assert window.parameters() == gcs
    window.set_editing_model(GCS_MODEL)
    slider = window.gcs_panel.sliders["kappa"]
    slider.slider.setValue(slider.slider.value() + 50)
    assert window.parameters().kappa != gcs.kappa
    assert window.shock_parameters().kappa == window.shock_panel.parameters().kappa


def test_the_ellipsoid_shows_tilt_and_alpha_and_a_spheroid_drops_them(window):
    window.set_editing_model(SHOCK_MODEL)
    window.resize(1470, 900)
    window.show()
    _flush(10)
    try:
        panel = window.shock_panel
        assert not panel.sliders["tilt_deg"].isVisible() and not panel.sliders["alpha"].isVisible()
        panel.shape_combo.setCurrentIndex(panel.shape_combo.findData(ELLIPSOID))
        assert window.shock_parameters().model == ELLIPSOID
        assert panel.sliders["tilt_deg"].isVisible() and panel.sliders["alpha"].isVisible()
        slider = panel.sliders["tilt_deg"]
        slider.slider.setValue(slider.slider.value() + 100)
        assert window.shock_parameters().tilt_deg != 0.0
        panel.shape_combo.setCurrentIndex(panel.shape_combo.findData(SPHEROID))
        params = window.shock_parameters()
        assert (params.model, params.tilt_deg, params.alpha) == (SPHEROID, 0.0, 1.0)
    finally:
        window.hide()


def test_a_spheroid_shock_panel_costs_the_images_no_height(window):
    """Stacked with the GCS panel, a taller shock page would enlarge the deck."""
    assert window.shock_panel.minimumSizeHint().height() <= window.gcs_panel.minimumSizeHint().height()


# --- Front points and dragging ------------------------------------------------------


def test_front_points_belong_to_the_model_they_were_clicked_for(window):
    _load(window)
    window._on_canvas_click("A", 4000.0, 500.0, "left")
    window.set_editing_model(SHOCK_MODEL)
    window._on_canvas_click("A", 6000.0, 900.0, "left")
    window._on_canvas_click("B", 6100.0, 950.0, "left")
    assert window._clicks["A"] == [(4000.0, 500.0)]
    assert window._tracks[SHOCK_MODEL].clicks["A"] == [(6000.0, 900.0)]
    assert window._viewpoints()[0].clicks_arcsec.tolist() == [[6000.0, 900.0]]
    assert window._viewpoints(GCS_MODEL)[0].clicks_arcsec.tolist() == [[4000.0, 500.0]]
    shown = window.panels[0].canvas._measure_points.getData()[0]
    assert list(shown) == [6000.0]  # the overlay shows the edited model's points
    window._clear_points()
    assert not any(window._tracks[SHOCK_MODEL].clicks.values())
    assert window._clicks["A"] == [(4000.0, 500.0)]


def test_each_models_points_return_with_their_frame(window):
    for panel, lon in zip(window.panels[:1], LONGITUDES):
        panel.set_frames(_sequence(lon, n=3, cadence_min=10))
    window.time_slider.setValue(0)
    window.set_editing_model(SHOCK_MODEL)
    window._on_canvas_click("A", 6000.0, 900.0, "left")
    window.set_editing_model(GCS_MODEL)
    window._on_canvas_click("A", 4000.0, 500.0, "left")
    window.time_slider.setValue(1)
    assert window._clicks["A"] == [] and window._tracks[SHOCK_MODEL].clicks["A"] == []
    window.time_slider.setValue(0)
    assert window._clicks["A"] == [(4000.0, 500.0)]
    assert window._tracks[SHOCK_MODEL].clicks["A"] == [(6000.0, 900.0)]


def test_reloading_a_channel_drops_both_models_points(window):
    _load(window)
    window._on_canvas_click("A", 4000.0, 500.0, "left")
    window.set_editing_model(SHOCK_MODEL)
    window._on_canvas_click("A", 6000.0, 900.0, "left")
    window.panels[0].set_frames(_sequence(0.0, n=4))
    assert window._clicks["A"] == [] and window._tracks[SHOCK_MODEL].clicks["A"] == []


def test_dragging_the_shock_apex_moves_only_the_shock(window):
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    gcs, shock = window.parameters(), window.shock_parameters()
    observer = window.panels[2].observer
    apex = shock_apex_arcsec(shock, observer)
    window._on_handle("C", "shock:apex", apex[0] * 1.3, apex[1] * 1.3, True)
    moved = window.shock_parameters()
    assert moved.height_rsun > shock.height_rsun
    assert (moved.kappa, moved.epsilon) == (shock.kappa, shock.epsilon)
    assert window.parameters() == gcs


def test_a_drag_on_one_layer_survives_the_other_layer_being_cleared(window):
    """Clearing the hidden GCS layer mid-drag must not turn the release into a click."""
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    window.wireframe_check.setChecked(False)
    canvas = window.panels[0].canvas
    handle = canvas._overlay_layer(SHOCK_MODEL)["handles"]["apex"]
    canvas._on_gcs_handle_moved("shock:apex", handle, False)
    assert canvas.gcs_drag_active()
    canvas.clear_gcs_overlay()  # what every redraw does to the hidden GCS layer
    assert canvas.gcs_drag_active()
    window._on_canvas_click("A", 6000.0, 900.0, "left")
    assert window._tracks[SHOCK_MODEL].clicks["A"] == []
    canvas.clear_gcs_overlay(SHOCK_MODEL)
    assert not canvas.gcs_drag_active()


# --- Refine, commit and kinematics ------------------------------------------------------


def test_refine_fits_the_shock_to_its_own_front_points(window):
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    truth = window.shock_parameters().replace_values(lon_deg=40.0, lat_deg=-8.0, height_rsun=10.0, kappa=0.6, epsilon=0.2)
    rng = np.random.default_rng(2)
    for panel in window.panels:
        x, y = shock_silhouette_arcsec(truth, panel.observer, samples=1000, fov_rsun=(3.7, 30.0))
        visible = np.nonzero(np.isfinite(x))[0]
        chosen = rng.choice(visible, size=12, replace=False)
        window._tracks[SHOCK_MODEL].clicks[panel.label] = [(float(x[i]), float(y[i])) for i in chosen]
    window._on_shock_parameters(truth.replace_values(lon_deg=44.0, lat_deg=-5.0, height_rsun=10.6, kappa=0.65))
    gcs = window.parameters()
    assert window.shock_panel.refine_btn.isEnabled()
    window._on_refine()
    result = window._tracks[SHOCK_MODEL].last_refinement
    assert result is not None and result.converged
    fitted = window.shock_parameters()
    assert fitted.height_rsun == pytest.approx(10.0, abs=0.05)
    assert fitted.lon_deg == pytest.approx(40.0, abs=0.5)
    assert window.status_label.text().startswith("Shock refine")
    assert window.parameters() == gcs and window._last_refinement is None

    window._on_commit()
    entry = window._tracks[SHOCK_MODEL].fits[window._shared_time]
    assert isinstance(entry, ShockFitEntry)
    assert entry.refined and entry.n_viewpoints == 3 and entry.n_points == 36
    assert entry.apex_height_rsun == pytest.approx(fitted.height_rsun)
    assert np.isfinite(entry.height_err_rsun)
    assert window._fits == {}  # the GCS record is untouched
    assert window.tracking_panel.table.rowCount() == 1


def test_a_failed_shock_refine_names_the_shock(window):
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    window._on_refine()
    assert window.status_label.text().startswith("Shock refine: click at least 2 points along the shock front")


def test_commits_build_a_separate_shock_height_time_series(window):
    for panel, lon in zip(window.panels[:2], LONGITUDES):
        panel.set_frames(_sequence(lon, n=4, cadence_min=15))
    window._on_commit()  # one GCS record
    window.set_editing_model(SHOCK_MODEL)
    for index in range(3):
        window.time_slider.setValue(index)
        window._on_shock_parameters(window.shock_parameters().replace_values(height_rsun=8.0 + index))
        window._on_commit()
    shock_fits = window._tracks[SHOCK_MODEL].fits
    assert len(shock_fits) == 3 and len(window._fits) == 1
    assert window.tracking_panel.table.rowCount() == 3
    assert "shock" in window.status_label.text()
    window._on_fit_kinematics()
    assert "shock-apex kinematics over 3 commit(s)" in window.status_label.text()

    window.set_editing_model(GCS_MODEL)
    assert window.tracking_panel.table.rowCount() == 1
    window.set_editing_model(SHOCK_MODEL)
    window._on_clear_fits()
    assert not shock_fits and len(window._fits) == 1
    assert window.status_label.text() == "Recorded shock fits cleared."


def test_the_same_images_cannot_be_a_second_shock_sample(window):
    window.panels[0].set_frames(_sequence(0.0, n=2, cadence_min=20))
    window.panels[1].set_frames(_sequence(60.0, n=2, cadence_min=20, start_min=1))
    window.set_editing_model(SHOCK_MODEL)
    window._on_commit()
    window.time_slider.setValue(1)
    window._on_commit()
    assert list(window._tracks[SHOCK_MODEL].fits) == [BASE]
    assert "same observations" in window.status_label.text()


def test_restore_and_delete_act_on_the_edited_models_record(window):
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    recorded = window.shock_parameters().replace_values(height_rsun=11.0, epsilon=0.3)
    window._on_shock_parameters(recorded)
    window._on_commit()
    window.set_editing_model(GCS_MODEL)
    window._on_commit()
    window.set_editing_model(SHOCK_MODEL)
    window._on_shock_parameters(recorded.replace_values(height_rsun=14.0))
    window._restore_recorded_model()
    assert window.shock_parameters() == recorded
    window._delete_recorded_fit()
    assert not window._tracks[SHOCK_MODEL].fits and window._fits


def test_reset_puts_the_shock_back_along_the_gcs_direction(window):
    _load(window)
    window.set_editing_model(SHOCK_MODEL)
    window._on_shock_parameters(window.shock_parameters().replace_values(lon_deg=-120.0, height_rsun=20.0))
    gcs = window.parameters()
    window._reset_model()
    shock = window.shock_parameters()
    assert (shock.lon_deg, shock.lat_deg, shock.height_rsun) == (gcs.lon_deg, gcs.lat_deg, 9.0)
    assert window.parameters() == gcs


def test_moving_to_another_event_archives_shock_fits_too(window):
    original = window.event_range()
    window.panels[0].set_frames(_sequence(0.0, n=2))
    window.set_editing_model(SHOCK_MODEL)
    window._on_commit()
    entry = window._tracks[SHOCK_MODEL].fits[BASE]
    tomorrow = BASE + timedelta(days=1)
    window.set_time_window(tomorrow, tomorrow + timedelta(hours=1))
    track = window._tracks[SHOCK_MODEL]
    assert not track.fits and track.archived_fits[BASE] == entry
    window.set_time_window(*original)
    assert track.fits[BASE] == entry and BASE not in track.archived_fits


# --- Playback, export and menus ---------------------------------------------------------


def test_playback_follows_both_models_recorded_fits(window):
    window.panels[0].set_frames(_sequence(0, n=3, cadence_min=12))
    window.panels[1].set_frames(_sequence(60, n=3, cadence_min=12))
    window.time_slider.setValue(0)
    window._on_parameters(window.parameters().replace_values(height_rsun=6.0))
    window._on_commit()
    window.set_editing_model(SHOCK_MODEL)
    for index, height in ((0, 7.0), (2, 11.0)):
        window.time_slider.setValue(index)
        window._on_shock_parameters(window.shock_parameters().replace_values(height_rsun=height))
        window._on_commit()
    window.time_slider.setValue(1)
    window.play()
    try:
        assert window.shock_parameters().height_rsun == pytest.approx(9.0)
        assert window.parameters().height_rsun == pytest.approx(6.0)
        assert window.status_label.text().startswith(
            "▶ Shell: recorded fit 16:00:00 held (after last fit) · "
            "Shock: interpolated between recorded fits 16:00:00 and 16:24:00 · "
        )
        assert all(panel.canvas.has_gcs_overlay(SHOCK_MODEL) for panel in window.panels[:2])
    finally:
        window.pause()
    assert "▶" not in window.status_label.text()


def test_export_adds_the_shock_without_changing_version_one_keys(window, tmp_path):
    _load(window)
    window._on_commit()
    window.set_editing_model(SHOCK_MODEL)
    window._on_canvas_click("A", 6000.0, 900.0, "left")
    window._on_shock_parameters(window.shock_parameters().as_model(ELLIPSOID).replace_values(alpha=1.2, tilt_deg=15.0))
    window._on_commit()
    path = tmp_path / "analysis.json"
    window._write_analysis(path)
    document = json.loads(path.read_text(), parse_constant=lambda value: pytest.fail(f"Invalid JSON {value}"))
    assert document["schema_version"] == 2
    assert document["model"] == "Graduated Cylindrical Shell"
    assert len(document["recorded_fits"]) == 1 and "alpha_deg" in document["recorded_fits"][0]["parameters"]
    shock = document["shock"]
    assert "PyThea" in shock["parameter_convention"]
    record = shock["recorded_fits"][0]
    params = window.shock_parameters()
    axes = shock_semi_axes(params)
    assert record["parameters"]["model"] == ELLIPSOID
    assert record["parameters"]["orthoaxis2_rsun"] == pytest.approx(axes.orthoaxis2_rsun)
    assert record["parameters"]["rcenter_rsun"] == pytest.approx(axes.rcenter_rsun)
    assert record["observations"]["frames"][0]["front_points_arcsec"] == [[6000.0, 900.0]]
    assert shock["current_model"]["alpha"] == pytest.approx(1.2)
    # The GCS record keeps the GCS points (none here), not the shock's.
    assert document["recorded_fits"][0]["observations"]["frames"][0]["front_points_arcsec"] == []


def test_the_shock_csv_uses_pytheas_names_and_derived_axes():
    _app()
    panel = TrackingPanel()
    panel.set_source("shock")
    header = panel.csv_header()
    for name in ("model", "kappa", "epsilon", "alpha", "tilt_deg", "rcenter_rsun", "radaxis_rsun",
                 "orthoaxis1_rsun", "orthoaxis2_rsun", "apex_height_rsun"):
        assert name in header
    entry = ShockFitEntry(BASE, 10.0, 40.0, -8.0, 0.0, 0.6, 0.2, 1.0, SPHEROID, 12.0, 30, 3, 70.0,
                          0.1, 0.05, 0.02, True)
    row = dict(zip(header, panel.csv_row(entry, BASE)))
    b = 0.6 * 9.0
    a = b / np.sqrt(1 - 0.2**2)
    assert float(row["orthoaxis1_rsun"]) == pytest.approx(b, abs=1e-4)
    assert float(row["radaxis_rsun"]) == pytest.approx(a, abs=1e-4)
    assert float(row["rcenter_rsun"]) == pytest.approx(10.0 - a, abs=1e-4)
    assert row["model"] == SPHEROID and row["refined"] == 1
    panel.refresh_shock({0: entry})
    assert panel.table.rowCount() == 1 and "Spheroid" in panel.table.item(0, 0).toolTip()


def test_the_menus_switch_and_show_the_edited_model(window):
    _load(window)
    window.menu_actions["edit_shock"].trigger()
    assert window.editing_model() == SHOCK_MODEL
    window._sync_menu_actions()
    assert window.menu_actions["edit_shock"].isChecked() and not window.menu_actions["edit_gcs"].isChecked()
    assert window.menu_actions["shock"].isChecked()
    window.menu_actions["shock"].trigger()
    assert not window.shock_check.isChecked()
    assert not window.panels[0].canvas.has_gcs_overlay(SHOCK_MODEL)
    window.menu_actions["edit_gcs"].trigger()
    assert window.editing_model() == GCS_MODEL


@pytest.mark.parametrize("key, widget", [("wireframe", "wireframe_check"), ("shock", "shock_check"),
                                         ("pick", "pick_points_check"), ("limb", "limb_check")])
def test_every_view_and_fit_toggle_in_the_menus_drives_its_check_box(window, key, widget):
    check = getattr(window, widget)
    for _ in range(2):
        expected = not check.isChecked()
        window.menu_actions[key].trigger()
        assert check.isChecked() is expected


def test_stepping_moves_the_shock_with_its_recorded_fits(window):
    for panel, lon in zip(window.panels[:2], LONGITUDES):
        panel.set_frames(_sequence(lon, n=3, cadence_min=12))
    window.set_editing_model(SHOCK_MODEL)
    for index, height in ((0, 7.0), (2, 11.0)):
        window.time_slider.setValue(index)
        window._on_shock_parameters(window.shock_parameters().replace_values(height_rsun=height))
        window._on_commit()
    gcs = window.parameters()
    window.previous_frame()
    assert window.shock_parameters().height_rsun == pytest.approx(9.0)
    assert window.status_label.text().startswith("Shock: interpolated between recorded fits 16:00:00 and 16:24:00 · ")
    assert window.parameters() == gcs  # nothing recorded for GCS, so it keeps its sliders
    window._rewind()
    assert window.shock_parameters().height_rsun == pytest.approx(7.0)

