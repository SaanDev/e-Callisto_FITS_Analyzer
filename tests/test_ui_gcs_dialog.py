"""
e-CALLISTO FITS Analyzer
Offscreen tests for the multi-viewpoint GCS dialog (src/UI/gcs_fitting_dialog.py)
and its entry point in the Solar Image Analysis window.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("sunpy.map")

from PySide6.QtWidgets import QApplication

from src.Backend.gcs_model import GCSParameters, ObserverGeometry, apex_arcsec
from src.UI.gcs_fitting_dialog import MIN_USEFUL_SEPARATION_DEG, GCSFittingDialog


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 4):
    app = _app()
    for _ in range(times):
        app.processEvents()


def _map(observer_lon_deg: float, *, instrument="LASCO", detector="C3"):
    """A synthetic coronagraph frame seen from a chosen heliographic longitude."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import frames
    from sunpy.map.header_helper import make_fitswcs_header

    obstime = "2012-07-12T16:00:00"
    observer = SkyCoord(
        observer_lon_deg * u.deg,
        0 * u.deg,
        1.0 * u.AU,
        frame=frames.HeliographicStonyhurst,
        obstime=obstime,
    )
    data = np.random.default_rng(5).random((64, 64))
    ref = SkyCoord(
        0 * u.arcsec, 0 * u.arcsec, obstime=obstime, observer=observer, frame="helioprojective"
    )
    header = make_fitswcs_header(
        data,
        ref,
        scale=[112, 112] * u.arcsec / u.pix,
        instrument=instrument,
        detector=detector,
    )
    return sunpy.map.Map(data, header)


@pytest.fixture
def dialog():
    _app()
    widget = GCSFittingDialog(
        None,
        reference_frames=[_map(0.0)],
        reference_index=0,
        reference_label="LASCO C3",
    )
    yield widget
    widget.close()


def test_the_dialog_opens_with_a_visible_shell_on_viewpoint_a(dialog):
    assert dialog.canvas_a.has_gcs_overlay()
    params = dialog.parameters()
    assert params.is_physical
    # Seeded on the limb, so it clears the occulter rather than hiding behind it.
    observer = dialog._observer("A")
    offset = abs(((params.lon_deg - observer.lon_deg + 180.0) % 360.0) - 180.0)
    assert offset == pytest.approx(90.0, abs=1.0)


def test_it_carries_an_existing_fit_across_instead_of_reseeding():
    _app()
    initial = GCSParameters(12.0, -30.0, 44.0, 11.0, 25.0, 0.4)
    widget = GCSFittingDialog(
        None, reference_frames=[_map(0.0)], reference_index=0, initial=initial
    )
    try:
        assert widget.parameters() == initial
        assert widget.gcs_panel.sliders["lat_deg"].value() == pytest.approx(-30.0, abs=0.2)
    finally:
        widget.close()


def test_one_parameter_set_drives_every_panel(dialog):
    dialog._map_b = _map(62.0)
    dialog._sync_gcs()
    _flush()
    assert dialog.canvas_a.has_gcs_overlay()
    assert dialog.canvas_b.has_gcs_overlay()

    # The two panels see the *same* shell from different places, so the projected
    # apex must differ while the parameters stay single-valued.
    apex_a = apex_arcsec(dialog.parameters(), dialog._observer("A"))
    apex_b = apex_arcsec(dialog.parameters(), dialog._observer("B"))
    assert apex_a != pytest.approx(apex_b)


def test_a_slider_move_updates_both_panels(dialog):
    dialog._map_b = _map(62.0)
    dialog._sync_gcs()
    before = dialog.parameters().height_rsun
    slider = dialog.gcs_panel.sliders["height_rsun"]
    slider.slider.setValue(slider.slider.value() + 60)
    _flush()
    assert dialog.parameters().height_rsun > before
    assert dialog.canvas_a.has_gcs_overlay() and dialog.canvas_b.has_gcs_overlay()


def test_clicking_collects_points_per_panel_and_right_click_undoes(dialog):
    dialog._map_b = _map(62.0)
    dialog._sync_gcs()
    dialog._on_canvas_click("A", 4000.0, 500.0, "left")
    dialog._on_canvas_click("A", 4200.0, 900.0, "left")
    dialog._on_canvas_click("B", 3000.0, 100.0, "left")
    assert len(dialog._gcs_clicks["A"]) == 2
    assert len(dialog._gcs_clicks["B"]) == 1

    dialog._on_canvas_click("A", 0.0, 0.0, "right")
    assert len(dialog._gcs_clicks["A"]) == 1


def test_a_handle_drag_does_not_also_land_a_point(dialog):
    canvas = dialog.canvas_a
    canvas._gcs_drag_active = True
    dialog._on_canvas_click("A", 4000.0, 500.0, "left")
    assert dialog._gcs_clicks["A"] == []
    canvas._gcs_drag_active = False
    dialog._on_canvas_click("A", 4000.0, 500.0, "left")
    assert len(dialog._gcs_clicks["A"]) == 1


def test_dragging_the_apex_on_either_panel_moves_the_shared_fit(dialog):
    dialog._map_b = _map(62.0)
    dialog._sync_gcs()
    before = dialog.parameters().height_rsun
    apex = apex_arcsec(dialog.parameters(), dialog._observer("B"))
    dialog._on_handle("B", "apex", apex[0] * 1.3, apex[1] * 1.3, True)
    assert dialog.parameters().height_rsun > before


def test_the_status_line_warns_about_a_single_viewpoint(dialog):
    text = dialog.gcs_status.text()
    assert "One viewpoint" in text
    assert "longitude" in text


def test_the_status_line_warns_when_the_baseline_is_too_short(dialog):
    dialog._map_b = _map(MIN_USEFUL_SEPARATION_DEG / 2.0)
    dialog._sync_gcs()
    text = dialog.gcs_status.text()
    assert "too close" in text and "held fixed" in text


def test_a_useful_baseline_is_reported_without_a_warning(dialog):
    dialog._map_b = _map(62.0)
    dialog._sync_gcs()
    text = dialog.gcs_status.text()
    assert "62" in text and "too close" not in text


def test_refine_without_points_reports_rather_than_raising(dialog):
    dialog._on_refine()
    assert "refine" in dialog.gcs_status.text().lower()


def test_refine_across_two_viewpoints_improves_the_fit(dialog):
    from src.Backend.gcs_model import gcs_mesh, project_to_arcsec

    truth = GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32)
    dialog._map_b = _map(62.0)
    dialog._sync_gcs()

    rng = np.random.default_rng(11)
    for panel in ("A", "B"):
        observer = dialog._observer(panel)
        projection = project_to_arcsec(gcs_mesh(truth), truth, observer)
        usable = np.nonzero(np.isfinite(projection.tx_arcsec))[0]
        radius = np.hypot(projection.tx_arcsec[usable], projection.ty_arcsec[usable])
        outer = usable[radius > np.percentile(radius, 70.0)]
        picked = rng.choice(outer, 25, replace=False)
        dialog._gcs_clicks[panel] = [
            (float(projection.tx_arcsec[i]), float(projection.ty_arcsec[i])) for i in picked
        ]

    dialog._gcs_params = GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27)
    dialog._on_refine()
    _flush()
    result = dialog._gcs_last_refinement
    assert result is not None and result.rms_arcsec < result.seed_rms_arcsec
    assert abs(dialog.parameters().lon_deg - truth.lon_deg) < 5.0
    assert abs(dialog.parameters().height_rsun - truth.height_rsun) < 0.6
    assert result.n_viewpoints == 2


def test_viewpoint_b_is_never_reprojected_onto_a(dialog):
    """A reprojected frame carries A's observer, which would silently restore
    the single-view degeneracy the dialog exists to break."""
    original_b = _map(62.0)
    reprojected_b = _map(0.0)  # what reproject_map_to would hand back: A's observer
    dialog._map_b = original_b
    dialog._show_b(reprojected_b, reprojected=True, separation=62.0)
    _flush()

    assert dialog._map_b_reprojected is None
    # The fit — and the picture on screen — must both use B's own vantage.
    observer_b = dialog._observer("B")
    assert observer_b is not None
    assert observer_b.lon_deg == pytest.approx(62.0, abs=1.0)
    assert dialog._frame_for("B") is original_b


def test_send_to_window_pushes_the_fit_back(dialog):
    class FakeMeasure:
        def __init__(self):
            self.received = None

        def set_gcs_parameters(self, params):
            self.received = params

    class FakeWindow:
        pass

    window = FakeWindow()
    window._measure = FakeMeasure()
    dialog._window_ref = window
    dialog._on_commit()
    assert window._measure.received == dialog.parameters()
    assert "Sent to the analysis window" in dialog.gcs_status.text()


def test_send_to_window_degrades_without_a_window(dialog):
    dialog._window_ref = None
    dialog._on_commit()
    assert "No analysis window" in dialog.gcs_status.text()


def test_the_window_exposes_a_menu_entry(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow

    # Modal message boxes block forever offscreen — stub before touching them.
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    _app()
    win = SolarDataAnalysisWindow()
    try:
        assert hasattr(win, "gcs_fitting_action")
        assert win.gcs_fitting_action.text() == "GCS CME Fitting…"
        actions = {action.text() for action in win.analysis_menu.actions()}
        assert "GCS CME Fitting…" in actions
        # Without frames it must inform, not raise.
        win._map_frames = []
        win.open_gcs_fitting_dialog()
    finally:
        win.close()
