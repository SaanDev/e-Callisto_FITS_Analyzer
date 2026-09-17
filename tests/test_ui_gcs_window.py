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

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from src.Backend.gcs_model import GCSParameters, apex_arcsec
from src.UI.gcs_fitting_window import PANEL_LABELS, GCSFittingWindow
from src.UI.gcs_viewpoint_panel import DIFFERENCE_MODES, GCSViewpointPanel
from src.UI.gcs_viewpoint_panel import _qdatetime_utc as QDateTime


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
        # A ring that moves outward frame to frame (the "CME front"), on a
        # background that brightens uniformly by 1 per frame (the kind of global
        # offset two JP2s of the same corona really show), plus noise. Every
        # pixel stays positive, as inside a JP2's field.
        yy, xx = np.mgrid[0:48, 0:48]
        radius = np.hypot(xx - 23.5, yy - 23.5)
        front = 6.0 * np.exp(-((radius - (8.0 + 3.0 * index)) ** 2) / 2.0)
        data = 10.0 + index + front + rng.random((48, 48))
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
        assert panel.source_combo.count() > 0
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
    for index, frame in enumerate(panel.frames):
        assert panel.display_array(index).shape == frame.data.shape
    assert panel.canvas.has_plot_content()


def test_running_difference_actually_differences(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=3))
    panel.set_difference_mode("raw")
    raw = np.asarray(panel.display_array(1), dtype=float).copy()
    panel.set_difference_mode("running")
    differenced = np.asarray(panel.display_array(1), dtype=float)
    assert not np.allclose(raw, differenced)
    inside = differenced != 0
    # The uniform +1 brightening is removed, so "no change" sits at zero…
    assert abs(float(np.median(differenced[inside]))) < 0.2
    # …while the moving front survives as real signal.
    assert float(np.percentile(np.abs(differenced[inside]), 99)) > 1.0


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
    panel.select_source("COR2-A")  # what would be fetched next
    panel.set_frames(_sequence(0.0, detector="C3"))
    _flush()
    title = panel.title_label.text()
    assert "C3" in title
    assert "COR2" not in title


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
    assert checked == ["Running diff"]


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
        projection = project_to_arcsec(gcs_mesh(truth), truth, panel.observer, fov_rsun=(3.7, 30.0))
        usable = np.nonzero(np.isfinite(projection.tx_arcsec))[0]
        radius = np.hypot(projection.tx_arcsec[usable], projection.ty_arcsec[usable])
        outer = usable[radius > np.percentile(radius, 70.0)]
        picked = rng.choice(outer, 20, replace=False)
        window._clicks[panel.label] = [
            (float(projection.tx_arcsec[i]), float(projection.ty_arcsec[i])) for i in picked
        ]

    # Refinement is local: begin with a manually aligned shell. A distant seed
    # can find a different shell with a small residual, especially behind an
    # occulter; that does not establish reconstruction accuracy.
    window._params = GCSParameters(36.0, -13.0, 26.0, 8.8, 33.0, 0.31)
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


def test_every_load_resets_the_colormap_to_gray(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, detector="C2"))
    assert panel.colormap_combo.currentText() == "gray"
    panel.colormap_combo.setCurrentText("viridis")
    panel.set_frames(_sequence(0.0, detector="C2"))
    assert panel.colormap_combo.currentText() == "gray"


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


def test_the_event_range_sets_every_channel_before_anything_is_loaded(window):
    start, end = datetime(2012, 7, 12, 15, 30), datetime(2012, 7, 12, 18, 45)
    window.event_start_edit.setDateTime(QDateTime(start))
    window.event_end_edit.setDateTime(QDateTime(end))
    assert window.event_range() == (start, end)
    assert all(panel.date_range() == (start, end) for panel in window.panels)


def test_load_all_loads_each_channel_with_its_own_range(window, monkeypatch):
    window.event_start_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 16, 0)))
    window.event_end_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 18, 0)))
    # One channel narrowed on its own after the event range was set.
    window.panels[1].set_date_range(datetime(2012, 7, 12, 16, 30), datetime(2012, 7, 12, 17, 30))

    seen: list = []
    for panel in window.panels:
        monkeypatch.setattr(panel, "start_fetch", lambda p=panel: seen.append((p.label, p.date_range())) or True)
    window._fetch_all()
    assert seen == [
        ("A", (datetime(2012, 7, 12, 16, 0), datetime(2012, 7, 12, 18, 0))),
        ("B", (datetime(2012, 7, 12, 16, 30), datetime(2012, 7, 12, 17, 30))),
        ("C", (datetime(2012, 7, 12, 16, 0), datetime(2012, 7, 12, 18, 0))),
    ]


def test_the_time_slider_steps_every_loaded_frame(window):
    window.panels[0].set_frames(_sequence(0.0, n=3, cadence_min=10))
    _flush()
    window.time_slider.setValue(window.time_slider.maximum())
    _flush()
    assert window._shared_time == window._time_axis[-1]
    assert window.time_label.text() == f"{window._time_axis[-1]:%Y-%m-%d %H:%M:%S}"


# --- Layout: images first --------------------------------------------------


def test_a_panel_is_only_its_image(window):
    """Every control a panel owns is mounted in the deck, not around the image."""
    for panel in window.panels:
        for widget in (panel.source_combo, panel.start_edit, panel.end_edit, panel.fetch_btn,
                       panel.colormap_combo, panel.low_slider, panel.high_slider):
            assert widget.parent() is not panel
            assert window.deck.isAncestorOf(widget)
        assert not panel.canvas.map_chrome_visible()


def test_the_splitter_hands_the_images_exactly_the_space_they_can_use(window):
    window.resize(1470, 900)
    window.show()
    _flush(20)
    try:
        ideal = window.stage.ideal_height(window.splitter.width())
        assert abs(window.stage.height() - ideal) <= 2
        # …and the cards get the rest without the deck growing a scrollbar.
        assert not window.deck.verticalScrollBar().isVisible()
    finally:
        window.hide()


def test_each_image_is_a_square_the_data_fills_edge_to_edge(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=2))
    window.resize(1470, 900)
    window.show()
    _flush(20)
    try:
        for panel in window.panels:
            assert panel.width() == panel.height()
            viewbox = panel.canvas.map_plot.getViewBox().screenGeometry()
            assert abs(viewbox.width() - panel.width()) <= 2
    finally:
        window.hide()


# --- Running difference applies to every panel -----------------------------


def test_running_difference_reaches_all_three_panels(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=4))
    _flush()
    window._on_difference_mode("running")
    _flush()
    assert window._difference_mode() == "running"
    for panel in window.panels:
        assert panel.can_difference()
        assert panel.difference_mode() == "running"
        # The moving front shows up in every panel, so this really is differenced.
        differenced = np.asarray(panel.display_array(1))
        assert float(np.percentile(np.abs(differenced[differenced != 0]), 99)) > 1.0


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


def test_focus_enlarges_the_chosen_image_and_equal_leaves_no_space_unused(window):
    window.resize(1470, 900)
    window.show()
    _flush(20)
    try:
        rects = [panel.geometry() for panel in window.panels]
        equal_side = rects[0].width()
        used = max(r.right() for r in rects) - min(r.left() for r in rects) + 1
        assert window.stage.width() - used <= 2  # the row spans the stage

        window.set_layout_mode("focus", 1)
        _flush(20)
        sizes = [panel.width() for panel in window.panels]
        assert sizes[1] > max(sizes[0], sizes[2])
        assert sizes[1] >= 1.4 * equal_side
        assert window.splitter.orientation() == Qt.Horizontal
        assert window.deck.orientation() == "column"
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


# --- Thread lifecycle: the crash on close ----------------------------------


class _SlowWorker(QObject):
    """Stands in for SunPyWorker: long-running, cancellable, same signals."""

    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()
    search_finished = Signal(object)
    load_finished = Signal(object, object)

    def __init__(self):
        super().__init__()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        import time

        for _ in range(200):  # 20 s if nothing cancels it
            if self._cancelled:
                break
            time.sleep(0.1)
        self.finished.emit()


def test_closing_mid_download_cancels_instead_of_aborting(window):
    """Qt calls qFatal if a QThread is destroyed while running, and a real
    download outlasts any wait() worth having — so close must cancel, and must
    never delete a thread that has not returned."""
    import time

    import src.UI.gcs_viewpoint_panel as panel_module

    for panel in window.panels:
        panel._launch(_SlowWorker())
    _flush()
    assert sum(1 for p in window.panels if p._thread and p._thread.isRunning()) == 3
    assert len(panel_module._LIVE_THREADS) >= 3

    started = time.perf_counter()
    window.close()
    _flush(6)
    elapsed = time.perf_counter() - started
    # Cancelled, not waited out: 3 x 20 s of downloads would be 60 s.
    assert elapsed < 5.0
    for panel in window.panels:
        assert panel._thread is None


def test_finished_threads_are_released_from_the_registry(window):
    import time

    import src.UI.gcs_viewpoint_panel as panel_module

    before = len(panel_module._LIVE_THREADS)
    panel = window.panels[0]
    worker = _SlowWorker()
    panel._launch(worker)
    _flush()
    assert len(panel_module._LIVE_THREADS) == before + 1

    worker.cancel()
    deadline = time.time() + 5.0
    while time.time() < deadline and len(panel_module._LIVE_THREADS) > before:
        _flush(2)
        time.sleep(0.05)
    assert len(panel_module._LIVE_THREADS) == before


def test_a_worker_thread_is_not_parented_to_the_widget(window):
    """A QThread parented to a widget is destroyed with it — while still running."""
    panel = window.panels[0]
    panel._launch(_SlowWorker())
    _flush()
    try:
        assert panel._thread.parent() is None
    finally:
        panel.shutdown()


# --- Running difference: the failures that showed raw pixels ---------------


def test_a_frame_that_cannot_share_the_grid_is_dropped_so_differencing_works(window):
    """A frame smaller than the rest cannot be honestly resampled up; it is
    dropped, and said so, rather than breaking every difference."""
    panel = window.panels[0]
    messages: list[str] = []
    panel.statusChanged.connect(messages.append)

    class Odd:
        data = np.zeros((16, 16))
        meta: dict = {}
        date = "2012-07-12T16:05:00"
        detector = "C3"
        instrument = "LASCO"
        observatory = "SOHO"
        wavelength = None
        coordinate_frame = None

    panel.set_frames(_sequence(0.0, n=4) + [Odd()])
    _flush()
    assert len(panel.frames) == 4
    assert any("could not share" in message for message in messages)

    panel.set_difference_mode("running")
    assert panel.difference_mode() == "running"
    assert "failed" not in panel.title_label.text()


def test_a_differencing_failure_is_reported_not_swallowed(window, monkeypatch):
    panel = window.panels[0]
    messages: list[str] = []
    panel.statusChanged.connect(messages.append)
    monkeypatch.setattr(
        "src.Backend.helioviewer_jp2.difference_image",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")),
    )
    panel.set_frames(_sequence(0.0, n=4))  # loads straight into running difference
    window.next_frame()  # first observation has no earlier difference reference
    _flush()
    assert any("differencing failed" in message for message in messages)
    assert "failed" in panel.title_label.text()
    # Raw is a fine fallback; a false label is not.
    assert np.array_equal(panel.display_array(1), np.asarray(panel.frames[1].data, dtype=np.float32))


def test_a_difference_image_is_stretched_symmetrically_about_zero(window):
    """Mid-scale has to mean "no change", or a brightening and a dimming look
    identical and the whole frame takes a colour cast."""
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=4))
    panel.set_difference_mode("raw")
    _flush()
    raw_low, _raw_high = panel.canvas._last_map_levels
    assert raw_low >= 0  # the raw ramp is positive

    panel.set_difference_mode("running")
    window.next_frame()  # test the first genuine difference, not its raw reference
    _flush()
    low, high = panel.canvas._last_map_levels
    assert low == pytest.approx(-high)
    assert low < 0 < high


def test_an_explicit_colormap_survives_switching_view_modes(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=4))
    panel.colormap_combo.setCurrentText("viridis")
    panel.set_difference_mode("raw")
    panel.set_difference_mode("base")
    assert panel.colormap_combo.currentText() == "viridis"


def test_loading_shows_a_busy_state_rather_than_looking_broken(window):
    assert window.load_all_btn.isEnabled() and window.load_all_btn.text() == "Load all"

    window.panels[0]._launch(_SlowWorker())
    window._sync_busy()
    assert not window.load_all_btn.isEnabled()
    assert window.load_all_btn.text() == "Loading…"
    assert window.panels[0].is_fetching()

    window.panels[0].shutdown()
    window._sync_busy()
    assert window.load_all_btn.isEnabled() and window.load_all_btn.text() == "Load all"


def test_the_view_modes_explain_themselves(window):
    tips = {str(b.property("segment_key")): b.toolTip() for b in window._difference_group.buttons()}
    assert set(tips) == {"raw", "running", "base"}
    assert all(tip.strip() for tip in tips.values())
    assert "default" in tips["running"].lower()


# --- JP2 loading ---------------------------------------------------------------


def test_only_jp2_coronagraph_sources_are_offered(window):
    from src.Backend.helioviewer_jp2 import GCS_JP2_SOURCES

    keys = [window.panels[0].source_combo.itemData(i) for i in range(window.panels[0].source_combo.count())]
    assert keys == [source.key for source in GCS_JP2_SOURCES]


def test_the_default_triad_follows_the_target_date():
    _app()
    early = GCSFittingWindow(target_time=datetime(2012, 7, 12, 17))
    late = GCSFittingWindow(target_time=datetime(2021, 10, 28, 15))
    try:
        # Left to right: STEREO-B COR2, LASCO C2, STEREO-A COR2.
        assert [p.source().key for p in early.panels] == ["COR2-B", "LASCO C2", "COR2-A"]
        # STEREO-B was lost in 2014, so the left panel cannot stay on it.
        assert [p.source().key for p in late.panels] == ["LASCO C3", "LASCO C2", "COR2-A"]
    finally:
        early.close()
        late.close()


def test_moving_the_event_past_2014_takes_panel_a_off_stereo_b(window):
    """A still-valid choice is left alone; one the spacecraft could not have made
    is replaced and greyed out."""
    window.event_start_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 16)))
    window.event_end_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 18)))
    window.panels[0].select_source("COR2-B")  # a valid choice in 2012
    window.panels[2].select_source("LASCO C2")
    assert window.panels[0].source().key == "COR2-B"
    window.event_start_edit.setDateTime(QDateTime(datetime(2021, 10, 28, 14)))
    window.event_end_edit.setDateTime(QDateTime(datetime(2021, 10, 28, 16)))
    assert window.panels[0].source().key == "LASCO C3"
    assert window.panels[2].source().key == "LASCO C2"  # still valid, so left alone
    index = window.panels[0].source_combo.findData("COR2-B")
    item = window.panels[0].source_combo.model().item(index)
    assert not (item.flags() & Qt.ItemIsEnabled)


def test_changing_the_event_after_loading_retargets_every_channel(window, monkeypatch):
    """Typing a new event must never quietly re-load the previous one."""
    window.panels[0].set_frames(_sequence(0.0, n=3))
    _flush()
    window.time_slider.setValue(1)
    _flush()

    new_start, new_end = datetime(2013, 4, 11, 6, 30), datetime(2013, 4, 11, 8, 30)
    window.event_start_edit.setDateTime(QDateTime(new_start))
    window.event_end_edit.setDateTime(QDateTime(new_end))
    seen: list = []
    for panel in window.panels:
        monkeypatch.setattr(panel, "start_fetch", lambda p=panel: seen.append(p.date_range()) or True)
    window._fetch_all()
    assert seen == [(new_start, new_end)] * 3


def test_a_source_that_did_not_exist_then_is_refused_with_a_reason(window):
    panel = window.panels[2]
    messages: list[str] = []
    panel.statusChanged.connect(messages.append)
    panel.select_source("COR2-B")
    panel.set_date_range(datetime(2021, 1, 1, 0), datetime(2021, 1, 1, 2))
    assert panel.start_fetch() is False
    assert not panel.is_fetching()
    assert any("no data for 2021-01-01" in message for message in messages)


def test_the_fetch_worker_hands_back_the_sequence(monkeypatch):
    from src.Backend import helioviewer_jp2 as hvjp2
    from src.UI.gcs_viewpoint_panel import JP2FetchWorker

    _app()
    calls: dict = {}

    def fake_fetch(source, start, end, **kwargs):
        calls.update(kwargs, source=source, start=start, end=end)
        kwargs["progress_cb"]("Listing…")
        return hvjp2.JP2Sequence(source=source, frames=(), listed=5, from_cache=2)

    monkeypatch.setattr(hvjp2, "fetch_range", fake_fetch)
    source = hvjp2.source_by_key("LASCO C2")
    start, end = datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 18)
    worker = JP2FetchWorker(source, start, end, max_frames=25, cache_dir=None)
    progress, finished = [], []
    worker.progress.connect(progress.append)
    worker.finished.connect(finished.append)
    worker.run()
    assert progress == ["Listing…"]
    assert finished and finished[0].listed == 5
    assert (calls["start"], calls["end"], calls["max_frames"], calls["source"]) == (start, end, 25, source)


def test_a_cancelled_fetch_worker_says_so(monkeypatch):
    from src.Backend import helioviewer_jp2 as hvjp2
    from src.UI.gcs_viewpoint_panel import JP2FetchWorker

    _app()

    def fake_fetch(*args, **kwargs):
        raise hvjp2.HelioviewerJP2Cancelled("Cancelled.")

    monkeypatch.setattr(hvjp2, "fetch_range", fake_fetch)
    worker = JP2FetchWorker(
        hvjp2.source_by_key("COR2-A"), datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 18), max_frames=10, cache_dir=None
    )
    cancelled, failed = [], []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.run()
    assert cancelled == [True] and failed == []


def test_a_fetch_failure_is_reported(monkeypatch):
    from src.Backend import helioviewer_jp2 as hvjp2
    from src.UI.gcs_viewpoint_panel import JP2FetchWorker

    _app()
    monkeypatch.setattr(
        hvjp2, "fetch_range", lambda *a, **k: (_ for _ in ()).throw(hvjp2.HelioviewerJP2Error("no frames"))
    )
    worker = JP2FetchWorker(
        hvjp2.source_by_key("COR2-A"), datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 18), max_frames=10, cache_dir=None
    )
    failed: list[str] = []
    worker.failed.connect(failed.append)
    worker.run()
    assert failed == ["no frames"]


def test_a_loaded_sequence_reports_cache_hits_and_drops(window):
    from src.Backend import helioviewer_jp2 as hvjp2

    panel = window.panels[1]
    messages: list[str] = []
    panel.statusChanged.connect(messages.append)
    sequence = hvjp2.JP2Sequence(
        source=hvjp2.source_by_key("LASCO C2"),
        frames=tuple(_sequence(0.0, n=4, detector="C2")),
        listed=9,
        from_cache=3,
        skipped=("17:24:00: timeout",),
        dropped=1,
    )
    panel._on_fetch_finished(sequence)
    _flush()
    assert panel.frame_count() == 4
    summary = messages[-1]
    assert "4 frame(s)" in summary and "3 from cache" in summary
    assert "1 failed to download" in summary and "1 dropped" in summary


def test_difference_frames_are_built_once_and_cached(window, monkeypatch):
    """Built on first display (~25 ms each) and reused, so playback pays once."""
    from src.Backend import helioviewer_jp2 as hvjp2

    panel = window.panels[0]
    calls = {"n": 0}
    real = hvjp2.difference_image

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(hvjp2, "difference_image", counting)
    panel.set_frames(_sequence(0.0, n=4))  # loads into running difference
    for _ in range(3):
        for index in range(panel.frame_count()):
            panel.display_array(index)
    assert calls["n"] == panel.frame_count() - 1  # first frame is the raw reference


def test_switching_modes_never_serves_a_stale_image(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=4))
    panel.set_difference_mode("raw")
    raw = panel.display_array(2).copy()
    panel.set_difference_mode("running")
    running = panel.display_array(2).copy()
    panel.set_difference_mode("base")
    base = panel.display_array(2).copy()
    panel.set_difference_mode("raw")
    assert np.array_equal(panel.display_array(2), raw)
    assert not np.allclose(running, raw) and not np.allclose(base, running)


def test_the_analyzer_hands_over_its_current_time_not_its_pixels():
    """The GCS window loads JP2 only; what carries across is the moment."""
    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    _app()
    win = SolarDataAnalysisWindow()
    try:
        frames = _sequence(0.0, n=3, cadence_min=10)
        win._map_frames = frames
        win._current_frame_index = 2
        win.open_gcs_fitting_window()
        gcs = win._gcs_window
        moment = datetime(2012, 7, 12, 16, 20, 0)
        assert gcs.event_range() == (moment - timedelta(hours=1), moment + timedelta(hours=1))
        assert all(panel.frame_count() == 0 for panel in gcs.panels)
        gcs.close()
    finally:
        win._map_frames = []
        win.close()




# --- Event and channel date ranges ------------------------------------------------


def test_each_channel_range_can_be_narrowed_without_touching_the_others(window):
    window.event_start_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 16)))
    window.event_end_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 18)))
    window.panels[0].set_date_range(datetime(2012, 7, 12, 16, 30), datetime(2012, 7, 12, 17))
    assert window.panels[1].date_range() == (datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 18))
    assert window.panels[2].date_range() == (datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 18))


@pytest.mark.parametrize(
    "start, end, reason",
    [
        (datetime(2012, 7, 12, 18), datetime(2012, 7, 12, 16), "after its start"),
        (datetime(2012, 7, 1, 0), datetime(2012, 7, 12, 0), "narrow it"),
    ],
)
def test_a_channel_refuses_an_unusable_range_and_says_why(window, start, end, reason):
    panel = window.panels[0]
    messages: list[str] = []
    panel.statusChanged.connect(messages.append)
    panel.set_date_range(start, end)
    assert panel.start_fetch() is False
    assert not panel.is_fetching()
    assert any(reason in message for message in messages)


def test_load_all_refuses_a_backwards_event_range(window, monkeypatch):
    window.event_start_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 18)))
    window.event_end_edit.setDateTime(QDateTime(datetime(2012, 7, 12, 16)))
    started: list = []
    for panel in window.panels:
        monkeypatch.setattr(panel, "start_fetch", lambda p=panel: started.append(p) or True)
    window._fetch_all()
    assert started == []
    assert "after its start" in window.status_label.text()


def test_the_frame_cap_reaches_every_channel(window):
    window.max_frames_spin.setValue(17)
    assert all(panel.max_frames_provider() == 17 for panel in window.panels)


# --- Defaults on load -------------------------------------------------------------


def test_loading_opens_in_gray_running_difference_whatever_the_view_was_left_on(window):
    window.panels[0].set_frames(_sequence(0.0, n=4))
    window._on_difference_mode("raw")
    window.panels[0].colormap_combo.setCurrentText("viridis")
    assert window._difference_mode() == "raw"

    window.panels[1].set_frames(_sequence(62.0, n=4))
    _flush()
    assert window._difference_mode() == "running"
    for panel in window.panels[:2]:
        assert panel.difference_mode() == "running"
    assert window.panels[1].colormap_combo.currentText() == "gray"


def test_a_fresh_window_starts_in_running_difference(window):
    assert window._difference_mode() == "running"
    assert all(panel._difference_mode == "running" for panel in window.panels)


# --- Solar limb and axes ------------------------------------------------------------


def test_the_solar_limb_is_drawn_at_the_observers_apparent_radius(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=2))
    assert window.limb_check.isChecked()
    assert panel.canvas.has_aia_limb_overlay()
    x, y = panel.canvas._aia_limb_curve.getData()
    radii = np.hypot(x, y)
    assert radii == pytest.approx(panel.observer.rsun_arcsec, rel=1e-6)


def test_the_limb_can_be_hidden_and_stays_hidden_through_a_load(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=2))
    window.limb_check.setChecked(False)
    assert not panel.canvas.has_aia_limb_overlay()
    panel.set_frames(_sequence(0.0, n=2))
    assert not panel.canvas.has_aia_limb_overlay()
    window.limb_check.setChecked(True)
    assert panel.canvas.has_aia_limb_overlay()


def test_axes_are_off_by_default_and_cost_image_space_when_on(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=2))
    window.resize(1470, 900)
    window.show()
    _flush(20)
    try:
        assert not window.axes_check.isChecked()
        bare = panel.canvas.map_plot.getViewBox().screenGeometry().width()
        window.axes_check.setChecked(True)
        _flush(20)
        framed = panel.canvas.map_plot.getViewBox().screenGeometry().width()
        assert framed < bare
    finally:
        window.hide()


# --- Stage geometry and layouts -----------------------------------------------------


@pytest.mark.parametrize("width, height", [(1458, 482), (1458, 700), (900, 900), (2400, 500)])
def test_equal_layout_places_three_equal_squares_inside_the_stage(window, width, height):
    rects = window.stage.geometry_for(width, height)
    sides = {(r.width(), r.height()) for r in rects}
    assert len(sides) == 1 and all(r.width() == r.height() for r in rects)
    assert all(r.left() >= 0 and r.top() >= 0 and r.right() < width and r.bottom() < height for r in rects)
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1 :])


@pytest.mark.parametrize("focus", [0, 1, 2])
@pytest.mark.parametrize("width, height", [(1084, 840), (1500, 980), (700, 900)])
def test_focus_layout_enlarges_one_square_beside_two_smaller(window, focus, width, height):
    window.stage.set_layout("focus", focus)
    rects = window.stage.geometry_for(width, height)
    large = rects[focus]
    smalls = [r for i, r in enumerate(rects) if i != focus]
    assert all(r.width() == r.height() for r in rects)
    assert all(r.width() < large.width() for r in smalls)
    assert all(r.left() >= 0 and r.top() >= 0 and r.right() < width and r.bottom() < height for r in rects)
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1 :])


def test_the_layout_buttons_switch_layout_and_stay_in_step(window):
    buttons = {str(b.property("segment_key")): b for b in window._layout_group.buttons()}
    buttons["focus:2"].click()
    assert window.layout_mode() == "focus" and window.stage.focus_index() == 2
    window.set_layout_mode("equal")
    assert buttons["equal"].isChecked()
    assert window.splitter.orientation() == Qt.Vertical and window.deck.orientation() == "row"


def test_keyboard_shortcuts_are_scoped_to_the_images(window):
    keys = {shortcut.key().toString() for shortcut in window._shortcuts}
    assert {"Space", "Left", "Right", "Home", "0", "1", "2", "3"} <= keys
    assert all(shortcut.context() == Qt.WidgetWithChildrenShortcut for shortcut in window._shortcuts)
    assert all(shortcut.parent() is window.stage for shortcut in window._shortcuts)


def test_space_toggles_playback(window):
    window.panels[0].set_frames(_sequence(0.0, n=3))
    window.toggle_play()
    assert window._play_timer.isActive()
    window.toggle_play()
    assert not window._play_timer.isActive()


def test_a_dragged_splitter_is_respected_until_the_layout_changes(window):
    window.resize(1470, 900)
    window.show()
    _flush(20)
    try:
        window._on_splitter_moved(300, 1)
        window.splitter.setSizes([300, window.splitter.height() - 300])
        window._fit_splitter(force=False)
        assert window.stage.height() <= 310
        window.set_layout_mode("equal")  # an explicit layout change re-fits
        _flush(20)
        assert abs(window.stage.height() - window.stage.ideal_height(window.splitter.width())) <= 2
    finally:
        window.hide()


# --- Readouts ---------------------------------------------------------------------------


def test_the_cursor_readout_gives_position_angle_and_solar_radii(window):
    panel = window.panels[0]
    panel.set_frames(_sequence(0.0, n=2))
    radius = panel.observer.rsun_arcsec
    window._on_hover("A", -3.0 * radius, 0.0)  # three radii due east
    text = window.coord_label.text()
    assert "PA 90°" in text and "3.00 R☉" in text
    window._on_hover("A", None, None)
    assert window.coord_label.text() == ""


def test_the_caption_fits_even_the_smallest_panel(window):
    for panel, lon in zip(window.panels, (0.0, 62.0, -70.0)):
        panel.set_frames(_sequence(lon, n=4))
    window.resize(1470, 900)
    window.show()
    window.set_layout_mode("focus", 0)
    _flush(20)
    try:
        for panel in window.panels[1:]:
            caption_width = panel.title_label._item.boundingRect().width()
            assert caption_width < panel.width()
    finally:
        window.hide()


def test_the_window_fits_a_1280_pixel_screen(window):
    """The toolbar once pinned the minimum width at 1290 px."""
    assert window.minimumSizeHint().width() <= 1200
