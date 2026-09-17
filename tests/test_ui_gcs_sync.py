"""Regressions for UTC event handoff and observation-scoped GCS fit state."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("sunpy.map")

from test_ui_gcs_window import _app, _sequence
from src.UI.gcs_fitting_window import GCSFittingWindow


BASE = datetime(2012, 7, 12, 16)


@pytest.fixture
def window():
    _app()
    widget = GCSFittingWindow(target_time=BASE)
    yield widget
    widget.close()


def test_requested_target_survives_out_of_order_channel_completions():
    _app()
    target = BASE + timedelta(minutes=16)
    widget = GCSFittingWindow(target_time=target)
    try:
        widget.panels[0].set_frames(_sequence(0, n=2, cadence_min=40))
        assert widget._shared_time == BASE
        widget.panels[1].set_frames(_sequence(60, n=3, cadence_min=15))
        assert widget._shared_time == BASE + timedelta(minutes=15)
        assert widget._requested_time == target
        widget.time_slider.setValue(widget.time_slider.maximum())
        selected = widget._shared_time
        widget.panels[2].set_frames(_sequence(-60, n=3, cadence_min=16))
        assert widget._shared_time == selected
    finally:
        widget.close()


def test_external_aware_event_times_are_converted_to_utc(window):
    local = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2012, 7, 12, 21, 30, tzinfo=local)
    window.set_time_window(start, start + timedelta(hours=1))
    assert window.event_range() == (BASE, BASE + timedelta(hours=1))
    assert all(panel.date_range() == window.event_range() for panel in window.panels)
    assert window._requested_time == BASE + timedelta(minutes=30)


def test_aware_initial_target_is_preserved_in_utc():
    _app()
    target = datetime(2012, 7, 12, 21, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    widget = GCSFittingWindow(target_time=target)
    try:
        assert widget.event_range() == (BASE - timedelta(hours=1), BASE + timedelta(hours=1))
        assert widget._requested_time == BASE
    finally:
        widget.close()


def test_out_of_tolerance_views_cannot_supply_points_or_geometry(window):
    window.panels[0].set_frames(_sequence(0, n=2, cadence_min=20))
    window.panels[1].set_frames(_sequence(60, n=1, start_min=12))
    assert [panel.label for panel in window._active_panels()] == ["A"]
    assert not window.panels[1].canvas.has_gcs_overlay()
    window._on_canvas_click("B", 5000, 1000, "left")
    assert not window._clicks["B"]
    assert "outside time tolerance" in window.status_label.text()
    window.sync_tolerance_spin.setValue(12)
    assert [panel.label for panel in window._active_panels()] == ["A", "B"]
    assert window.panels[1].canvas.has_gcs_overlay()
    window._on_canvas_click("B", 5000, 1000, "left")
    assert len(window._viewpoints()[1].clicks_arcsec) == 1


def test_front_points_follow_actual_image_and_return_on_revisit(window):
    window.panels[0].set_frames(_sequence(0, n=3, cadence_min=10))
    window.panels[1].set_frames(_sequence(60, n=2, cadence_min=20))
    window._on_canvas_click("A", 4000, 500, "left")
    window._on_canvas_click("B", 4200, 600, "left")
    window._last_refinement = object()
    window.time_slider.setValue(1)
    assert window._clicks["A"] == []
    # B still displays the same actual image; its points remain associated with
    # that image but the panel is excluded until it meets the time tolerance.
    assert window._clicks["B"] == [(4200, 600)]
    assert window._last_refinement is None
    window._on_canvas_click("A", 5000, 700, "left")
    window.time_slider.setValue(0)
    assert window._clicks["A"] == [(4000, 500)]
    window.time_slider.setValue(1)
    assert window._clicks["A"] == [(5000, 700)]


def test_reload_drops_points_even_if_observation_timestamps_match(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0, n=2))
    window._on_canvas_click("A", 4000, 500, "left")
    window._last_refinement = object()
    panel.set_frames(_sequence(0, n=2))
    assert window._clicks["A"] == []
    assert not any(key[0] == "A" for key in window._frame_points)
    assert window._last_refinement is None


@pytest.mark.parametrize("change", ["parameters", "points", "clear", "tolerance"])
def test_refinement_errors_are_invalidated_when_fit_inputs_change(window, change):
    window.panels[0].set_frames(_sequence(0, n=2))
    window._last_refinement = object()
    if change == "parameters":
        window._on_parameters(window.parameters().replace_values(height_rsun=9))
    elif change == "points":
        window._on_canvas_click("A", 5000, 1000, "left")
    elif change == "clear":
        window._clear_points()
    else:
        window.sync_tolerance_spin.setValue(6)
    assert window._last_refinement is None


def test_empty_timeline_cannot_commit_old_shared_time(window):
    window.panels[0].set_frames(_sequence(0, n=2))
    window.panels[0].set_frames([])
    assert window._shared_time is None
    assert window._time_axis == []
    window._on_commit()
    assert window._fits == {}


def test_identical_nearest_frame_tuple_is_not_a_second_kinematic_sample(window):
    window.panels[0].set_frames(_sequence(0, n=2, cadence_min=20))
    window.panels[1].set_frames(_sequence(60, n=2, cadence_min=20, start_min=1))
    window._on_commit()
    assert list(window._fits) == [BASE]
    window.time_slider.setValue(1)
    assert window._shared_time == BASE + timedelta(minutes=1)
    assert window._recorded_time_for_current_frames() == BASE
    window._on_parameters(window.parameters().replace_values(height_rsun=10))
    window._on_commit()
    assert list(window._fits) == [BASE]
    assert window._fits[BASE].apex_height_rsun != 10
    assert "same observations" in window.status_label.text()
    window.time_slider.setValue(2)
    window._on_commit()
    assert len(window._fits) == 2


def test_disjoint_event_archives_fits_without_mixing_kinematics(window):
    original_range = window.event_range()
    window.panels[0].set_frames(_sequence(0, n=2))
    window._on_commit()
    original_entry = window._fits[BASE]
    assert window._fit_provenance[BASE]["frames"][0]["observation_time_utc"] == BASE
    tomorrow = BASE + timedelta(days=1)
    window.set_time_window(tomorrow, tomorrow + timedelta(hours=1))
    assert not window._fits and not window._fit_provenance
    assert window._archived_fits[BASE] == original_entry
    assert not any(panel.frames for panel in window.panels)
    assert window._shared_time is None
    window.set_time_window(*original_range)
    assert window._fits[BASE] == original_entry
    assert BASE in window._fit_provenance
    assert BASE not in window._archived_fits


def test_adjusting_same_event_preserves_recorded_work(window):
    window.panels[0].set_frames(_sequence(0, n=2))
    window._on_commit()
    window.set_time_window(BASE - timedelta(minutes=10), BASE + timedelta(minutes=30))
    assert BASE in window._fits
    assert not window._archived_fits


def test_nonconverged_refinement_does_not_change_model(window, monkeypatch):
    from src.UI import gcs_fitting_window as module

    seed = window.parameters()
    result = SimpleNamespace(converged=False, parameters=seed.replace_values(height_rsun=15),
                             message="Iteration limit reached")
    monkeypatch.setattr(module, "refine_gcs", lambda *_args: result)
    window._on_refine()
    assert window.parameters() == seed
    assert window._last_refinement is None
    assert "did not converge" in window.status_label.text()


def test_reopening_parent_updates_existing_window_target(monkeypatch):
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    parent = SolarDataAnalysisWindow()
    try:
        parent._map_frames = _sequence(0, n=3, cadence_min=10)
        parent._current_frame_index = 0
        parent.open_gcs_fitting_window()
        gcs = parent._gcs_window
        gcs.panels[0].set_frames(_sequence(0, n=3, cadence_min=10))
        parent._current_frame_index = 2
        parent.open_gcs_fitting_window()
        assert parent._gcs_window is gcs
        assert gcs._requested_time == BASE + timedelta(minutes=20)
        assert gcs._shared_time == BASE + timedelta(minutes=20)
        gcs.close()
    finally:
        parent._map_frames = []
        parent.close()


def test_fits_analyzer_hands_selected_event_to_new_and_existing_window(monkeypatch):
    from src.UI.main_window import MainWindow
    from src.UI import gcs_fitting_window as module

    calls = []

    class FittingWindow:
        def __init__(self, parent, *, event_range):
            calls.append(("create", event_range))

        def windowTitle(self):
            return "GCS"

        def set_time_window(self, *event_range):
            calls.append(("update", event_range))

        def show(self):
            pass

        raise_ = show
        activateWindow = show

    monkeypatch.setattr(module, "GCSFittingWindow", FittingWindow)
    first_range = (BASE, BASE + timedelta(minutes=30))
    parent = SimpleNamespace(_gcs_window=None, _current_time_window_utc=lambda: first_range)
    MainWindow.open_gcs_fitting_window(parent)
    second_range = (BASE + timedelta(days=1), BASE + timedelta(days=1, minutes=30))
    parent._current_time_window_utc = lambda: second_range
    MainWindow.open_gcs_fitting_window(parent)
    assert calls == [("create", first_range), ("update", second_range)]
