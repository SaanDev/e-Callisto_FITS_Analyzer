"""
e-CALLISTO FITS Analyzer
Offscreen tests for the three-viewpoint GCS fitting window
(src/UI/gcs_fitting_window.py, src/UI/gcs_viewpoint_panel.py) and its entry
point in the Solar Image Analysis window.

The load-bearing tests are the ones that pin what makes three viewpoints worth
having: one parameter set drives every panel, and the panels stay simultaneous
in *time* rather than in frame index.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QApplication

from src.Backend.gcs_model import GCSParameters, apex_arcsec
from src.UI.gcs_fitting_window import PANEL_LABELS, GCSFittingWindow
from src.UI.gcs_viewpoint_panel import DIFFERENCE_MODES, GCSViewpointPanel


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 4):
    app = _app()
    for _ in range(times):
        app.processEvents()


def _sequence(observer_lon_deg: float, *, n=4, cadence_min=12, start_min=0, detector="C3"):
    """A synthetic frame sequence seen from a chosen heliographic longitude."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import frames
    from sunpy.map.header_helper import make_fitswcs_header

    frames_out = []
    base = datetime(2012, 7, 12, 16, 0, 0)
    rng = np.random.default_rng(4)
    for index in range(n):
        when = base + timedelta(minutes=start_min + index * cadence_min)
        stamp = when.isoformat()
        observer = SkyCoord(
            observer_lon_deg * u.deg,
            0 * u.deg,
            1.0 * u.AU,
            frame=frames.HeliographicStonyhurst,
            obstime=stamp,
        )
        data = rng.random((48, 48)) + index
        ref = SkyCoord(
            0 * u.arcsec, 0 * u.arcsec, obstime=stamp, observer=observer, frame="helioprojective"
        )
        header = make_fitswcs_header(
            data,
            ref,
            scale=[112, 112] * u.arcsec / u.pix,
            instrument="LASCO",
            detector=detector,
            exposure=1.0 * u.s,
        )
        frames_out.append(sunpy.map.Map(data, header))
    return frames_out


@pytest.fixture
def window():
    _app()
    widget = GCSFittingWindow()
    yield widget
    widget.close()


# --- Structure -------------------------------------------------------------


def test_it_is_a_standalone_window_with_three_panels(window):
    from PySide6.QtWidgets import QMainWindow

    assert isinstance(window, QMainWindow)
    assert [panel.label for panel in window.panels] == list(PANEL_LABELS)
    assert len(window.panels) == 3


def test_the_inline_tool_is_gone_from_the_analysis_window():
    """GCS moved out; the analyzer's measure toolbar must not still offer it."""
    from src.UI.solar_measure_tools import MeasurementController
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    assert "gcs" not in MeasurementController.MODES
    _app()
    win = SolarDataAnalysisWindow()
    try:
        assert not hasattr(win, "gcs_tool_btn")
        # …but the menu entry that opens the standalone window is there.
        assert win.gcs_fitting_action.text() == "GCS CME Fitting…"
        assert hasattr(win, "open_gcs_fitting_window")
    finally:
        win.close()


def test_each_panel_has_its_own_source_colormap_and_contrast(window):
    for panel in window.panels:
        assert panel.observable_combo.count() > 0
        assert panel.colormap_combo.count() > 0
        assert panel.low_slider.value() < panel.high_slider.value()
        assert panel.fetch_btn.isEnabled()


# --- One shared fit --------------------------------------------------------


def test_one_parameter_set_drives_every_panel(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()
    for panel in window.panels:
        assert panel.canvas.has_gcs_overlay()

    # Same shell, three vantages: the projected apex must differ per panel while
    # the parameters stay single-valued.
    apexes = [apex_arcsec(window.parameters(), panel.observer) for panel in window.panels]
    assert apexes[0] != pytest.approx(apexes[1])
    assert apexes[1] != pytest.approx(apexes[2])


def test_a_single_slider_moves_all_three_panels(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()
    before = window.parameters().height_rsun
    slider = window.gcs_panel.sliders["height_rsun"]
    slider.slider.setValue(slider.slider.value() + 60)
    _flush()
    assert window.parameters().height_rsun > before
    assert all(panel.canvas.has_gcs_overlay() for panel in window.panels)


def test_there_is_exactly_one_parameter_panel(window):
    """Per-panel sliders would let the three drift into three different CMEs."""
    from src.UI.solar_measure_tools import GCSParameterPanel

    panels = window.findChildren(GCSParameterPanel)
    assert len(panels) == 1


def test_dragging_on_any_panel_moves_the_shared_fit(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()
    before = window.parameters().height_rsun
    third = window.panels[2]
    apex = apex_arcsec(window.parameters(), third.observer)
    window._on_handle("C", "apex", apex[0] * 1.35, apex[1] * 1.35, True)
    assert window.parameters().height_rsun > before


# --- Shared time -----------------------------------------------------------


def test_panels_are_synced_by_timestamp_not_by_index(window):
    """Three spacecraft rarely share a cadence, so index-syncing would silently
    show three different moments side by side."""
    window.panels[0].set_frames(_sequence(0.0, n=6, cadence_min=10))
    window.panels[1].set_frames(_sequence(62.0, n=3, cadence_min=20, start_min=5))
    _flush()
    window.time_slider.setValue(window.time_slider.maximum())
    _flush()

    shared = window._shared_time
    assert shared is not None
    for panel in window.panels[:2]:
        stamp = panel.current_time()
        if stamp is None:
            continue
        gaps = [abs((t - shared).total_seconds()) for t in panel.times()]
        assert abs((stamp - shared).total_seconds()) == pytest.approx(min(gaps))


def test_the_time_axis_spans_every_panel(window):
    window.panels[0].set_frames(_sequence(0.0, n=3, cadence_min=10))
    window.panels[1].set_frames(_sequence(62.0, n=4, cadence_min=10, start_min=5))
    _flush()
    # Distinct timestamps across both panels.
    assert window.time_slider.maximum() == 6


# --- Difference and rendering ---------------------------------------------


@pytest.mark.parametrize("mode", [key for key, _ in DIFFERENCE_MODES])
def test_every_difference_mode_renders(window, mode):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    window._on_difference_mode(mode)
    _flush()
    panel = window.panels[0]
    assert panel._difference_mode == mode
    assert len(panel._display) == len(panel.frames)
    assert panel.canvas.has_plot_content()


def test_running_difference_actually_differences(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=3))
    raw_first = np.asarray(panel._display[1], dtype=float).copy()
    panel.set_difference_mode("running")
    differenced = np.asarray(panel._display[1], dtype=float)
    assert not np.allclose(raw_first, differenced)
    # Frame N minus N-1 of a +1-per-frame ramp is ~1 everywhere.
    assert float(np.nanmedian(differenced)) == pytest.approx(1.0, abs=0.6)


def test_contrast_sliders_change_the_display_levels(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0))
    _flush()
    before = panel.canvas._last_map_levels
    panel.low_slider.setValue(10.0)
    panel.high_slider.setValue(90.0)
    panel.render()
    _flush()
    assert panel.canvas._last_map_levels != before


def test_the_colormap_is_per_panel(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()
    window.panels[0].colormap_combo.setCurrentText("gray")
    window.panels[1].colormap_combo.setCurrentText("viridis")
    _flush()
    assert window.panels[0].colormap_combo.currentText() == "gray"
    assert window.panels[1].colormap_combo.currentText() == "viridis"


# --- Wireframe styling -----------------------------------------------------


def test_a_panel_is_named_after_the_frame_it_holds(window):
    """The combo says what will be fetched next; the title must say what is shown."""
    panel = window.panels[0]
    panel.observable_combo.setCurrentIndex(0)  # an AIA entry
    panel.set_frames(_sequence(0.0, detector="C3"))
    _flush()
    title = panel.title_label.text()
    assert "C3" in title
    assert "AIA" not in title


def test_setting_the_difference_mode_in_code_keeps_the_radios_honest(window):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    window._on_difference_mode("running")
    _flush()
    checked = [
        window._difference_group.button(index).text()
        for index, _ in enumerate(DIFFERENCE_MODES)
        if window._difference_group.button(index).isChecked()
    ]
    assert checked == ["Running difference"]


def test_the_default_wireframe_is_heavy_enough_to_see(window):
    assert window.width_slider.value() == pytest.approx(2.6, abs=0.05)
    for panel in window.panels:
        assert panel.canvas.gcs_style()["width"] >= 2.0


def test_thickness_colour_and_opacity_reach_every_panel(window):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    window.width_slider.setValue(6.0)
    window.opacity_slider.setValue(40.0)
    window._wireframe_colour = (0, 255, 120)
    window._apply_style()
    for panel in window.panels:
        style = panel.canvas.gcs_style()
        assert style["width"] == pytest.approx(6.0, abs=0.05)
        assert style["opacity"] == pytest.approx(0.4, abs=0.01)
        assert style["color"] == (0, 255, 120)


def test_density_changes_how_much_mesh_is_drawn(window):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    # Drive the underlying QSlider: setValue() on the wrapper is deliberately
    # silent so echoing a value back cannot feed back into a redraw.
    #
    # Higher must mean denser — the control is labelled "Density", and it reads
    # a ring *stride* underneath, so the inversion is easy to reintroduce.
    window.density_slider.slider.setValue(window.density_slider.slider.maximum())
    _flush()
    dense = window.panels[0].canvas._gcs_curve.getData()[0].size
    window.density_slider.slider.setValue(0)
    _flush()
    coarse = window.panels[0].canvas._gcs_curve.getData()[0].size
    assert dense > coarse


# --- Fitting, commits and kinematics ---------------------------------------


def test_clicks_are_collected_per_panel_and_undone_on_right_click(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()
    window._on_canvas_click("A", 4000.0, 500.0, "left")
    window._on_canvas_click("A", 4200.0, 900.0, "left")
    window._on_canvas_click("C", 3000.0, 100.0, "left")
    assert len(window._clicks["A"]) == 2 and len(window._clicks["C"]) == 1
    window._on_canvas_click("A", 0.0, 0.0, "right")
    assert len(window._clicks["A"]) == 1
    window._clear_points()
    assert all(not points for points in window._clicks.values())


def test_refine_uses_every_fetched_viewpoint(window):
    from src.Backend.gcs_model import gcs_mesh, project_to_arcsec

    truth = GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32)
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()

    rng = np.random.default_rng(13)
    for panel in window.panels:
        projection = project_to_arcsec(gcs_mesh(truth), truth, panel.observer)
        usable = np.nonzero(np.isfinite(projection.tx_arcsec))[0]
        radius = np.hypot(projection.tx_arcsec[usable], projection.ty_arcsec[usable])
        outer = usable[radius > np.percentile(radius, 70.0)]
        picked = rng.choice(outer, 20, replace=False)
        window._clicks[panel.label] = [
            (float(projection.tx_arcsec[i]), float(projection.ty_arcsec[i])) for i in picked
        ]

    window._params = GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27)
    window._on_refine()
    _flush()
    result = window._last_refinement
    assert result is not None
    assert result.n_viewpoints == 3
    assert result.rms_arcsec < result.seed_rms_arcsec
    assert abs(window.parameters().lon_deg - truth.lon_deg) < 5.0


def test_refine_without_points_reports_rather_than_raising(window):
    window.panels[0].set_frames(_sequence(0.0))
    window._on_refine()
    assert "refine" in window.status_label.text().lower()


def test_commits_build_a_three_d_height_time_series(window):
    window.panels[0].set_frames(_sequence(0.0, n=4, cadence_min=15))
    _flush()
    for index in range(3):
        window.time_slider.setValue(index)
        _flush()
        window._params = window.parameters().replace_values(height_rsun=6.0 + index)
        window._on_commit()
    assert len(window._fits) == 3
    assert window.tracking_panel.table.rowCount() == 3

    window._on_fit_kinematics()
    _flush()
    text = window.status_label.text()
    assert "3-D" in text and "de-projected" in text
    assert "plane-of-sky" not in text


def test_a_single_commit_cannot_yield_a_speed(window):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    window._on_commit()
    window._on_fit_kinematics()
    assert "two or more" in window.status_label.text()


def test_clearing_fits_empties_the_table(window):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    window._on_commit()
    window._on_clear_fits()
    assert window._fits == {}
    assert window.tracking_panel.table.rowCount() == 0


# --- Status and degradation ------------------------------------------------


def test_the_status_warns_while_only_one_viewpoint_is_loaded(window):
    window.panels[0].set_frames(_sequence(0.0))
    _flush()
    text = window.status_label.text()
    assert "1 viewpoint" in text and "held fixed" in text


def test_the_status_reports_every_pairwise_separation(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon))
    _flush()
    text = window.status_label.text()
    assert "3 viewpoints" in text
    assert text.count("°") >= 3  # A-B, A-C, B-C


def test_a_frame_without_coordinates_leaves_its_panel_dark(window):
    class Bare:
        data = np.zeros((8, 8))

    window.panels[0].set_frames([Bare()])
    _flush()
    assert window.panels[0].observer is None
    assert not window.panels[0].canvas.has_gcs_overlay()


def test_send_to_analyzer_emits_the_parameters(window):
    seen = []
    window.parametersCommitted.connect(seen.append)
    window._on_send()
    assert seen and seen[0] == window.parameters()


def test_the_analyzer_opens_and_reuses_one_window(monkeypatch):
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    win = SolarDataAnalysisWindow()
    try:
        win.open_gcs_fitting_window()
        first = win._gcs_window
        assert isinstance(first, GCSFittingWindow)
        win.open_gcs_fitting_window()
        assert win._gcs_window is first  # reused, not stacked
        first.close()
    finally:
        win.close()


def test_the_colormap_defaults_from_the_frame_not_the_combo(window):
    """An AIA palette on a LASCO frame makes a faint front nearly invisible."""
    panel = window.panels[0]
    panel.observable_combo.setCurrentIndex(0)  # an AIA entry
    panel.set_frames(_sequence(0.0, detector="C2"))
    _flush()
    assert "aia" not in panel.colormap_combo.currentText().lower()
    assert "lasco" in panel.colormap_combo.currentText().lower()


# --- Reachable and usable with nothing loaded ------------------------------


def _menu_items(window, menu_title: str) -> dict[str, bool]:
    """Every item of a named menu as {label: enabled} — what the user actually sees."""
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None and action.text() == menu_title:
            return {a.text(): a.isEnabled() for a in menu.actions() if a.text()}
    return {}


def test_the_entry_is_actually_present_in_the_analysis_menu():
    """Checking the QAction alone is not enough: it can exist and be enabled
    while never having been added to a menu, which is exactly what happened."""
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    win = SolarDataAnalysisWindow()
    try:
        items = _menu_items(win, "Analysis")
        assert "GCS CME Fitting…" in items, f"not in the Analysis menu: {list(items)}"
        assert items["GCS CME Fitting…"] is True
    finally:
        win.close()


def test_the_main_window_offers_it_too():
    """It is standalone, so it should not require opening the analyzer first."""
    from src.UI.main_window import MainWindow

    _app()
    win = MainWindow()
    try:
        items = _menu_items(win, "Analysis")
        assert "GCS CME Fitting…" in items, f"not in the Analysis menu: {list(items)}"
        assert items["GCS CME Fitting…"] is True
        assert win._gcs_window is None
        win.open_gcs_fitting_window()
        assert isinstance(win._gcs_window, GCSFittingWindow)
        opened = win._gcs_window
        win.open_gcs_fitting_window()
        assert win._gcs_window is opened  # reused, not stacked
        opened.close()
    finally:
        win.close()


def test_the_menu_entry_is_enabled_with_no_frames_loaded():
    """The window fetches its own three viewpoints, so it must never be gated
    on the analyzer having data."""
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    win = SolarDataAnalysisWindow()
    try:
        assert win._map_frames == []
        assert win.gcs_fitting_action.isEnabled()
        # And it stays enabled through the state syncs that gate everything else.
        win._sync_menu_action_state(loaded=False)
        assert win.gcs_fitting_action.isEnabled()
        assert not win.compare_viewpoint_action.isEnabled()  # this one does need data
    finally:
        win.close()


def test_it_opens_and_is_usable_with_no_frames_loaded():
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    win = SolarDataAnalysisWindow()
    try:
        win.open_gcs_fitting_window()
        gcs = win._gcs_window
        assert isinstance(gcs, GCSFittingWindow)
        # Every panel is present and can be driven, even with nothing to show.
        assert len(gcs.panels) == 3
        assert all(panel.fetch_btn.isEnabled() for panel in gcs.panels)
        assert gcs.parameters().is_physical
        # The sliders still work; there is simply nothing to draw on yet.
        slider = gcs.gcs_panel.sliders["height_rsun"]
        slider.slider.setValue(slider.slider.value() + 40)
        _flush()
        assert gcs.parameters().height_rsun > 0
        gcs.close()
    finally:
        win.close()


def test_a_target_time_can_be_set_before_anything_is_fetched(window):
    """Without this, an unseeded fetch would search around 'now', where the
    archives have nothing."""
    target = datetime(2012, 7, 12, 16, 30, 0)
    window.target_edit.setDateTime(QDateTime(target))
    _flush()
    assert window.target_time() == target


def test_each_panel_fetches_at_the_windows_target(window, monkeypatch):
    target = datetime(2012, 7, 12, 16, 30, 0)
    window.target_edit.setDateTime(QDateTime(target))
    _flush()

    seen: list = []
    for panel in window.panels:
        monkeypatch.setattr(panel, "start_fetch", lambda when=None, p=panel: seen.append((p.label, when)))
    window._fetch_all()
    assert seen == [("A", target), ("B", target), ("C", target)]

    # A panel's own Fetch button asks the window too, so fetching one at a time
    # still centres all three on the same moment.
    assert window.panels[1].target_provider() == target


def test_once_frames_exist_the_slider_owns_the_time(window):
    window.panels[0].set_frames(_sequence(0.0, n=3, cadence_min=10))
    _flush()
    window.time_slider.setValue(window.time_slider.maximum())
    _flush()
    stepped = window._shared_time
    assert stepped is not None
    # The target box follows the slider rather than fighting it.
    assert window.target_edit.dateTime().toPython().replace(tzinfo=None) == stepped
    assert window.target_time() == stepped


# --- Layout: images first --------------------------------------------------


def test_panel_settings_start_collapsed_so_the_image_gets_the_space(window):
    for panel in window.panels:
        assert not panel.settings_section.isExpanded()
        panel.settings_section.setExpanded(True)
        assert panel.settings_section.isExpanded()
        assert panel.observable_combo.isVisibleTo(panel.settings_section)


def test_the_fit_controls_can_be_collapsed_away(window):
    assert window.controls_section.isExpanded()
    window.controls_section.setExpanded(False)
    assert not window.controls_section.isExpanded()
    # The sliders still exist and still drive the fit while hidden.
    slider = window.gcs_panel.sliders["height_rsun"]
    before = window.parameters().height_rsun
    slider.slider.setValue(slider.slider.value() + 40)
    _flush()
    assert window.parameters().height_rsun != pytest.approx(before)


def test_the_canvas_is_the_stretching_widget_in_a_panel(window):
    from PySide6.QtWidgets import QSizePolicy

    panel = window.panels[0]
    assert panel.canvas.sizePolicy().verticalPolicy() == QSizePolicy.Expanding
    # The settings card must not fight the canvas for vertical space.
    assert panel.settings_section.sizePolicy().verticalPolicy() == QSizePolicy.Maximum


# --- Running difference applies to every panel -----------------------------


def test_running_difference_reaches_all_three_panels(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=4))
    _flush()
    window._on_difference_mode("running")
    _flush()
    for panel in window.panels:
        assert panel.can_difference()
        assert panel.difference_mode() == "running"
        assert "Running difference" in panel.title_label.text()
        # A +1-per-frame ramp differences to ~1, so this really is differenced.
        assert float(np.nanmedian(np.asarray(panel._display[1]))) == pytest.approx(1.0, abs=0.6)


def test_a_one_frame_panel_says_so_instead_of_showing_raw_under_a_false_label(window):
    """The bug this guards: difference_sequence hands back the raw array for a
    single-frame sequence, so the panel would claim to be differenced and not be."""
    window.panels[0].set_frames(_sequence(0.0, n=4))
    window.panels[1].set_frames(_sequence(62.0, n=1))
    _flush()
    window._on_difference_mode("running")
    _flush()

    assert window.panels[0].can_difference()
    assert not window.panels[1].can_difference()
    assert window.panels[1].difference_mode() == "raw"
    assert "unavailable" in window.panels[1].title_label.text()
    assert "1 frame" in window.panels[1].title_label.text()


def test_the_status_names_the_panel_that_cannot_difference(window):
    window.panels[0].set_frames(_sequence(0.0, n=4))
    window.panels[1].set_frames(_sequence(62.0, n=1))
    window.panels[2].set_frames(_sequence(-70.0, n=4))
    _flush()
    window._on_difference_mode("running")
    _flush()
    text = window.status_label.text()
    assert "panel B" in text and "cannot difference" in text

    # …and stops complaining once the view is back to raw.
    window._on_difference_mode("raw")
    _flush()
    assert "cannot difference" not in window.status_label.text()


# --- Playback --------------------------------------------------------------


def test_the_transport_is_disabled_until_there_is_a_sequence(window):
    for button in (window.play_btn, window.next_btn, window.prev_btn, window.rewind_btn):
        assert not button.isEnabled()
    assert not window.time_slider.isEnabled()

    window.panels[0].set_frames(_sequence(0.0, n=4))
    _flush()
    for button in (window.play_btn, window.next_btn, window.prev_btn, window.rewind_btn):
        assert button.isEnabled()
    assert window.time_slider.isEnabled()


def test_stepping_moves_one_frame_at_a_time_and_clamps(window):
    window.panels[0].set_frames(_sequence(0.0, n=4))
    _flush()
    window._rewind()
    assert window.time_slider.value() == 0
    window.previous_frame()
    assert window.time_slider.value() == 0  # clamped, not wrapped

    window.next_frame()
    assert window.time_slider.value() == 1
    for _ in range(10):
        window.next_frame()
    assert window.time_slider.value() == window.time_slider.maximum()


def test_play_advances_and_wraps_then_pause_stops_it(window):
    window.panels[0].set_frames(_sequence(0.0, n=3))
    _flush()
    window._rewind()
    window.play()
    assert window._play_timer.isActive()
    assert not window.play_btn.isEnabled() and window.pause_btn.isEnabled()

    for _ in range(3):
        window._advance_frame()
    # Three ticks over three frames wraps back to the start rather than sticking.
    assert window.time_slider.value() == 0

    window.pause()
    assert not window._play_timer.isActive()
    assert window.play_btn.isEnabled() and not window.pause_btn.isEnabled()


def test_playback_steps_every_panel_together(window):
    window.panels[0].set_frames(_sequence(0.0, n=4, cadence_min=10))
    window.panels[1].set_frames(_sequence(62.0, n=4, cadence_min=10))
    _flush()
    window._rewind()
    first = [panel.current_time() for panel in window.panels[:2]]
    window.next_frame()
    _flush()
    second = [panel.current_time() for panel in window.panels[:2]]
    assert second != first
    assert second[0] == second[1]  # same cadence here, so they land together


def test_the_fps_setting_drives_the_timer_interval(window):
    window.panels[0].set_frames(_sequence(0.0, n=4))
    _flush()
    window.fps_spin.setValue(10)
    window.play()
    assert window._play_timer.interval() == 100
    window.pause()


def test_closing_stops_playback(window):
    window.panels[0].set_frames(_sequence(0.0, n=4))
    _flush()
    window.play()
    assert window._play_timer.isActive()
    window.close()
    assert not window._play_timer.isActive()


def test_the_sun_is_never_drawn_stretched(window):
    """Non-negotiable: the canvas pins both axes via setLimits, so switching the
    square map off makes the ViewBox stretch rather than letterbox — an elliptical
    Sun. A smaller correctly-shaped image beats a bigger wrong one."""
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=3))
    window.resize(1780, 1020)
    window.show()
    _flush(8)
    try:
        for panel in window.panels:
            viewbox = panel.canvas.map_plot.getViewBox()
            (x0, x1), (y0, y1) = viewbox.viewRange()
            per_x = (x1 - x0) / max(viewbox.width(), 1)
            per_y = (y1 - y0) / max(viewbox.height(), 1)
            assert per_x == pytest.approx(per_y, rel=0.02), (
                f"{panel.label} is distorted: {per_x:.2f} vs {per_y:.2f} arcsec/px"
            )
    finally:
        window.hide()


def test_collapsing_the_controls_makes_the_images_bigger(window):
    """The point of the collapsible cards — measured, not assumed."""
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=3))
    window.resize(1780, 1020)
    window.show()
    _flush(8)
    try:
        window.controls_section.setExpanded(True)
        _flush(8)
        opened = window.panels[0].canvas.height()
        window.controls_section.setExpanded(False)
        _flush(8)
        collapsed = window.panels[0].canvas.height()
        assert collapsed > opened
    finally:
        window.hide()


def test_the_fit_controls_card_never_crushes_its_own_contents(window):
    """Capping the card below the tracking panel's minimum does not shrink it —
    it makes the panel overlap its own axis labels."""
    window.resize(1780, 1020)
    window.show()
    _flush(8)
    try:
        panel = window.tracking_panel
        assert panel.height() >= panel.minimumSizeHint().height()
    finally:
        window.hide()


def test_a_parameter_slider_row_does_not_stretch(window):
    """Stretching rows are what inflated the card and squeezed the images."""
    from PySide6.QtWidgets import QSizePolicy

    slider = window.gcs_panel.sliders["lon_deg"]
    assert slider.sizePolicy().verticalPolicy() == QSizePolicy.Fixed


def test_axis_titles_are_hidden_to_widen_the_image(window):
    """Three panels side by side make the image width-limited, so the ~60 px
    axis title is width taken straight out of the picture."""
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=2))
    _flush()
    label = panel.canvas.map_plot.getPlotItem().getAxis("left").label
    assert not label.toPlainText().strip()
    # The tick numbers, which are what is actually read, stay.
    assert panel.canvas.map_plot.getPlotItem().getAxis("left").isVisible()
