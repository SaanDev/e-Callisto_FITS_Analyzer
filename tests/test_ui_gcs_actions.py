"""User-facing menu actions and reproducible GCS analysis exports."""

import json
from datetime import timedelta, timezone

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("sunpy.map")

from test_ui_gcs_window import _app, _sequence
from src.UI.gcs_fitting_window import GCSFittingWindow


@pytest.fixture
def window():
    _app()
    widget = GCSFittingWindow()
    yield widget
    widget.close()


def test_menu_controls_follow_visible_toolbar_state(window):
    assert [item.text().replace("&", "") for item in window.menuBar().actions()] == [
        "File", "Event", "View", "Fit", "Help",
    ]
    window._sync_menu_actions()
    assert not window.menu_actions["commit"].isEnabled()
    assert not window.menu_actions["export_csv"].isEnabled()
    window.panels[0].set_frames(_sequence(0.0))
    # Shortcut actions must enable immediately, even if no menu was opened.
    assert window.menu_actions["commit"].isEnabled()
    window.menu_actions["mode_raw"].trigger()
    assert window._difference_mode() == "raw"
    assert window.panels[0].difference_mode() == "raw"
    window.set_layout_mode("focus", 2)
    window._sync_menu_actions()
    assert window.menu_actions["layout_focus:2"].isChecked()
    assert window.menu_actions["commit"].isEnabled()


def test_navigation_mode_prevents_accidental_front_picks(window):
    window.panels[0].set_frames(_sequence(0.0))
    window.pick_points_check.setChecked(False)
    window._on_canvas_click("A", 3000.0, 1000.0, "left")
    assert window._clicks["A"] == []
    window.pick_points_check.setChecked(True)
    window._on_canvas_click("A", 3000.0, 1000.0, "left")
    assert window._clicks["A"] == [(3000.0, 1000.0)]


def test_hiding_shell_does_not_hide_the_underlying_image(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0))
    assert panel.canvas.has_gcs_overlay()
    window.wireframe_check.setChecked(False)
    assert not panel.canvas.has_gcs_overlay()
    assert panel.canvas.has_plot_content()
    window.wireframe_check.setChecked(True)
    assert panel.canvas.has_gcs_overlay()


def test_export_records_actual_times_and_freezes_commit_provenance(window, tmp_path):
    window.panels[0].set_frames(_sequence(0.0))
    window.panels[1].set_frames(_sequence(60.0, start_min=3))
    window._on_canvas_click("A", 3000.0, 1000.0, "left")
    when = window._shared_time
    original_height = window.parameters().height_rsun
    window._on_commit()
    window.next_frame()
    window._on_parameters(window.parameters().replace_values(height_rsun=original_height + 2.0))
    path = tmp_path / "analysis.json"
    window._write_analysis(path)
    document = json.loads(path.read_text(), parse_constant=lambda value: pytest.fail(f"Invalid JSON {value}"))
    assert document["format"] == "e-callisto-gcs-analysis"
    fit = document["recorded_fits"][0]
    assert fit["parameters"]["when"] == when.isoformat() + "Z"
    assert fit["parameters"]["rms_arcsec"] is None
    assert fit["parameters"]["apex_height_rsun"] == original_height
    frames = fit["observations"]["frames"]
    assert frames[0]["front_points_arcsec"] == [[3000.0, 1000.0]]
    assert frames[1]["offset_seconds"] == 180.0
    assert frames[1]["included_in_fit"]
    assert frames[0]["display_mode"] == "raw"
    assert frames[0]["difference_reference_utc"] is None
    assert document["current_model"]["height_rsun"] == original_height + 2.0


def test_difference_export_records_reference_image_time(window):
    window.panels[0].set_frames(_sequence(0.0))
    first = window._shared_time
    window.next_frame()
    frame = window._analysis_document()["current_observations"]["frames"][0]
    assert frame["display_mode"] == "running"
    assert frame["difference_reference_utc"] == first.isoformat() + "Z"


def test_restore_and_delete_current_record_leave_other_times_unchanged(window):
    window.panels[0].set_frames(_sequence(0.0))
    window._on_commit()
    first = window._shared_time
    expected = window.parameters()
    window.next_frame()
    window._on_parameters(expected.replace_values(height_rsun=12.0))
    window._on_commit()
    second = window._shared_time
    window._seek_time(first)
    window._restore_recorded_model()
    assert window.parameters() == expected
    assert window._last_refinement is None
    window._delete_recorded_fit()
    assert first not in window._fits
    assert first not in window._fit_provenance
    assert second in window._fits


def test_go_to_time_converts_aware_timestamp_to_utc(window):
    window.panels[0].set_frames(_sequence(0.0))
    expected = window._time_axis[-1]
    local = expected.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    window._seek_time(local)
    assert window._shared_time == expected


def test_export_includes_archived_event_work(window):
    window.panels[0].set_frames(_sequence(0.0))
    window._on_commit()
    when = window._shared_time
    window._archived_fits[when] = window._fits.pop(when)
    window._archived_provenance[when] = window._fit_provenance.pop(when)
    document = window._analysis_document()
    assert document["recorded_fits"] == []
    assert len(document["archived_fits"]) == 1
    assert document["archived_fits"][0]["observations"]["frames"]


def test_kinematic_controls_enable_only_when_selected_order_has_enough_samples(window):
    window.panels[0].set_frames(_sequence(0.0))
    window._on_commit()
    assert window.tracking_panel.clear_btn.isEnabled()
    assert not window.tracking_panel.fit_btn.isEnabled()
    window.next_frame()
    window._on_commit()
    assert window.tracking_panel.fit_btn.isEnabled()
    lower, upper = window.tracking_panel.plot.viewRange()[1]
    assert upper - lower >= 0.1  # constant heights must not magnify floating-point noise
    combo = window.tracking_panel.fit_order_combo
    combo.setCurrentIndex(combo.findData(2))
    assert not window.tracking_panel.fit_btn.isEnabled()
    window.next_frame()
    window._on_commit()
    assert window.tracking_panel.fit_btn.isEnabled()
    window.tracking_panel.clear_btn.click()
    assert not window._fits
    assert not window.tracking_panel.fit_btn.isEnabled()
    assert not window.tracking_panel.clear_btn.isEnabled()


def test_recorded_row_restores_parameters_and_pauses_playback(window):
    window.panels[0].set_frames(_sequence(0.0))
    first = window._shared_time
    expected = window.parameters()
    window._on_commit()
    window.next_frame()
    window._on_parameters(expected.replace_values(height_rsun=10.0))
    window._on_commit()
    window.play()
    window._restore_fit_row(0, 2)
    assert not window._play_timer.isActive()
    assert window._shared_time == first
    assert window.parameters() == expected


def test_export_labels_failed_difference_as_raw(window, monkeypatch):
    window.panels[0].set_frames(_sequence(0.0))
    def fail(*args, **kwargs):
        raise ValueError("synthetic failed difference")
    monkeypatch.setattr("src.Backend.helioviewer_jp2.difference_image", fail)
    window.next_frame()
    frame = window._analysis_document()["current_observations"]["frames"][0]
    assert frame["display_mode"] == "raw"
    assert frame["difference_reference_utc"] is None
