"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

UI tests for the PFSS sidebar section (src/ui/solar/solar_pfss_controls.py).

The solve itself is covered in tests/backend/test_backend_pfss_model.py. These
tests are about the wiring that is easy to get wrong: the accordion card, the
instrument gate, the optional-dependency gate, and the rule that a visibility
toggle must never trigger a recompute.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QGroupBox

from src.backend.radio.instrument_profiles import (
    CORONAGRAPH,
    DISK_EUV,
    HELIOSPHERIC,
    MAGNETOGRAPH,
    UNKNOWN,
)
from src.backend.solar.pfss_magnetograms import MAGNETOGRAM_SOURCES, SOURCE_ADAPT, SOURCE_LOCAL
from src.backend.solar.pfss_model import (
    DEFAULT_NRHO,
    DEFAULT_RSS,
    SEED_CLICK,
    SEED_MODES,
    PfssOverlay,
    TracedField,
)
from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow
from src.ui.widgets.collapsible_sections import collapsible_sections, section_for


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 4):
    for _ in range(times):
        QApplication.processEvents()


@pytest.fixture
def window():
    _app()
    win = SolarDataAnalysisWindow()
    _flush()
    yield win
    win.close()
    _flush()


@pytest.fixture
def expanded(window):
    """The window with the PFSS card expanded, as a user click leaves it."""
    window.show()
    section = section_for(window.pfss_group)
    if section is not None and not section.isExpanded():
        section.toggle()
    _flush(5)
    return window


def _traced(lon_deg: float = 0.0, n: int = 6) -> TracedField:
    """A one-line traced field, enough to exercise the overlay plumbing."""
    return TracedField(
        lon_deg=np.full(n, lon_deg),
        lat_deg=np.zeros(n),
        radius_rsun=np.linspace(1.0, 1.4, n),
        offsets=np.asarray([0, n], dtype=np.int64),
        polarity=np.asarray([1.0]),
        open_mask=np.asarray([True]),
        obstime="2020-09-01T13:00:00",
    )


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #

def test_the_card_exists_and_is_collapsible(window):
    assert hasattr(window, "pfss_group")
    sections = collapsible_sections(window.controls_panel)
    assert any(s.findChild(QGroupBox) is window.pfss_group for s in sections), (
        "the group must be a direct-child QGroupBox of the controls panel, or "
        "make_groups_collapsible will not wrap it into an accordion card"
    )


def test_the_card_sits_between_the_vector_field_and_active_regions(window):
    layout = window.controls_panel.layout()
    positions: dict[int, int] = {}
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if widget is None:
            continue
        group = widget if isinstance(widget, QGroupBox) else widget.findChild(QGroupBox)
        if group is not None:
            positions[id(group)] = index
    assert (
        positions[id(window.vector_group)]
        < positions[id(window.pfss_group)]
        < positions[id(window.region_group)]
    )


def test_every_control_is_present(window):
    for name in (
        "pfss_source_combo",
        "pfss_browse_btn",
        "pfss_realization_spin",
        "pfss_magnetogram_label",
        "pfss_rss_spin",
        "pfss_nrho_spin",
        "pfss_seed_combo",
        "pfss_density_spin",
        "pfss_compute_btn",
        "pfss_clear_btn",
        "pfss_open_check",
        "pfss_closed_check",
        "pfss_regions_check",
        "pfss_near_side_check",
        "pfss_diagnostics_btn",
        "pfss_status_label",
    ):
        assert hasattr(window, name), f"missing {name}"


def test_defaults_are_the_conventional_model(window):
    assert window.pfss_rss_spin.value() == pytest.approx(DEFAULT_RSS)
    assert window.pfss_nrho_spin.value() == DEFAULT_NRHO
    assert window.pfss_open_check.isChecked()
    assert window.pfss_closed_check.isChecked()
    assert window.pfss_near_side_check.isChecked(), (
        "near-side masking defaults on: far-side lines would otherwise be drawn "
        "beyond the limb with no footpoint"
    )


def test_all_sources_and_seed_modes_are_offered(window):
    sources = [window.pfss_source_combo.itemData(i) for i in range(window.pfss_source_combo.count())]
    assert sources == list(MAGNETOGRAM_SOURCES)
    modes = [window.pfss_seed_combo.itemData(i) for i in range(window.pfss_seed_combo.count())]
    assert modes == list(SEED_MODES)


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "instrument_class,visible",
    [
        (DISK_EUV, True),
        (MAGNETOGRAPH, True),
        (UNKNOWN, True),
        (CORONAGRAPH, False),
        (HELIOSPHERIC, False),
    ],
)
def test_the_card_is_gated_by_instrument(window, monkeypatch, instrument_class, visible):
    """UNKNOWN is included on purpose.

    A synoptic magnetogram loaded from disk classifies as UNKNOWN, and that is
    exactly the case where the model is wanted -- the same reasoning the Active
    Regions card already uses.
    """
    monkeypatch.setattr(window, "_effective_instrument_class", lambda: instrument_class)
    window._apply_instrument_visibility()
    _flush(2)
    assert window.pfss_group.isHidden() is (not visible)


def test_compute_is_disabled_until_frames_are_loaded(window):
    assert not window.pfss_compute_btn.isEnabled()


def test_computing_without_frames_explains_itself(window):
    window._on_pfss_compute()
    _flush()
    assert "Load solar frames" in window.pfss_status_label.text()


def test_a_missing_optional_dependency_disables_the_card_with_a_hint(window, monkeypatch):
    """The section stays visible but inert, so the feature is discoverable."""
    import src.ui.solar.solar_pfss_controls as controls

    monkeypatch.setattr(controls, "pfss_available", lambda: False)
    window._apply_pfss_availability()
    _flush()

    assert window._pfss_available is False
    assert not window.pfss_compute_btn.isEnabled()
    assert not window.pfss_source_combo.isEnabled()
    assert "sunkit-magex" in window.pfss_status_label.text()
    assert "pip install" in window.pfss_status_label.text()


def test_the_install_hint_is_not_overwritten_by_the_source_description(window, monkeypatch):
    """The hint is the only thing explaining why the card is inert, and
    switching source (or expanding the card) re-derives the status text."""
    import src.ui.solar.solar_pfss_controls as controls

    monkeypatch.setattr(controls, "pfss_available", lambda: False)
    window._apply_pfss_availability()
    window._on_pfss_source_changed()
    window._reapply_gating()
    _flush(2)
    assert "sunkit-magex" in window.pfss_status_label.text()


def test_the_dependency_gate_survives_frames_being_loaded(window, monkeypatch):
    """_set_loaded_state enables its whole list; that must not undo the gate."""
    import src.ui.solar.solar_pfss_controls as controls

    monkeypatch.setattr(controls, "pfss_available", lambda: False)
    window._apply_pfss_availability()
    window._set_loaded_state(True)
    _flush()
    assert not window.pfss_compute_btn.isEnabled()


# --------------------------------------------------------------------------- #
# Per-source widgets
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "source,browse,realization",
    [("gong", False, False), ("hmi", False, False), (SOURCE_ADAPT, False, True), (SOURCE_LOCAL, True, False)],
)
def test_per_source_widgets_appear_only_where_they_apply(expanded, source, browse, realization):
    window = expanded
    index = [
        window.pfss_source_combo.itemData(i) for i in range(window.pfss_source_combo.count())
    ].index(source)
    window.pfss_source_combo.setCurrentIndex(index)
    _flush(3)
    assert window.pfss_browse_btn.isVisible() is browse
    assert window.pfss_realization_spin.isVisible() is realization


def test_expanding_the_card_does_not_reveal_inapplicable_widgets(expanded):
    """Expanding a card re-shows every direct child, undoing the builder's hiding.

    The card can also come up already expanded from saved settings, in which case
    the on_expand callback never fires at all -- so the per-source state has to be
    re-derived after the accordion wrap too.
    """
    window = expanded
    section = section_for(window.pfss_group)
    window.pfss_source_combo.setCurrentIndex(0)   # GONG: neither extra applies
    _flush(2)
    section.toggle()   # collapse
    _flush(2)
    section.toggle()   # re-expand
    _flush(4)
    assert not window.pfss_browse_btn.isVisible()
    assert not window.pfss_realization_spin.isVisible()


def test_the_density_hint_tracks_the_spinbox(expanded):
    window = expanded
    window.pfss_density_spin.setValue(12)
    _flush(2)
    first = window.pfss_density_hint.text()
    window.pfss_density_spin.setValue(36)
    _flush(2)
    assert window.pfss_density_hint.text() != first
    assert "lines" in window.pfss_density_hint.text()


def test_click_to_seed_is_disabled_on_the_renderer_that_cannot_report_clicks(window):
    """Only the pyqtgraph canvas plumbs clicks; matplotlib has none at all."""
    index = window.pfss_seed_combo.findData(SEED_CLICK)
    assert index >= 0

    for position in range(window.renderer_combo.count()):
        if "matplot" in window.renderer_combo.itemText(position).lower():
            window.renderer_combo.setCurrentIndex(position)
            break
    _flush(4)
    assert not window.pfss_seed_combo.model().item(index).isEnabled()

    for position in range(window.renderer_combo.count()):
        if "pyqtgraph" in window.renderer_combo.itemText(position).lower():
            window.renderer_combo.setCurrentIndex(position)
            break
    _flush(4)
    assert window.pfss_seed_combo.model().item(index).isEnabled()


# --------------------------------------------------------------------------- #
# Overlay plumbing
# --------------------------------------------------------------------------- #

def test_both_canvases_expose_the_same_overlay_api(window):
    for canvas in window._all_plot_canvases():
        assert hasattr(canvas, "set_pfss_overlay")
        assert hasattr(canvas, "clear_pfss_overlay")
        assert hasattr(canvas, "has_pfss_overlay")


def test_overlay_setters_tolerate_degenerate_geometry(window):
    for canvas in window._all_plot_canvases():
        canvas.set_pfss_overlay(PfssOverlay())
        canvas.set_pfss_overlay(PfssOverlay(closed=(np.zeros(1), np.zeros(1))))
        canvas.set_pfss_overlay(PfssOverlay(closed=(np.array([np.nan]), np.array([np.nan]))))
        canvas.set_pfss_overlay(None)
        assert not canvas.has_pfss_overlay()


def test_refresh_hides_the_overlay_when_there_is_no_solution(window):
    window._refresh_pfss_overlay()
    _flush()
    assert not window._active_canvas().has_pfss_overlay()


def test_a_visibility_toggle_never_recomputes(window, monkeypatch):
    """Ticking a checkbox re-projects an existing solution; it must not solve."""
    import src.ui.solar.solar_pfss_controls as controls

    calls: list[int] = []
    monkeypatch.setattr(controls, "solve_pfss", lambda *a, **k: calls.append(1))
    window._pfss_traced = _traced()

    for check in (
        window.pfss_open_check,
        window.pfss_closed_check,
        window.pfss_regions_check,
        window.pfss_near_side_check,
    ):
        check.setChecked(not check.isChecked())
        _flush(2)
    assert calls == []


def test_clearing_drops_the_solution_and_both_overlays(window):
    window._pfss_solution = object()
    window._pfss_traced = _traced()
    for canvas in window._all_plot_canvases():
        canvas.set_pfss_overlay(PfssOverlay(closed=(np.zeros(4), np.zeros(4))))

    window._on_pfss_clear()
    _flush()

    assert window._pfss_solution is None
    assert window._pfss_traced is None
    for canvas in window._all_plot_canvases():
        assert not canvas.has_pfss_overlay()


def test_diagnostics_needs_a_solution_first(window):
    window._pfss_solution = None
    window._on_pfss_diagnostics()
    _flush()
    assert "Compute a PFSS solution first" in window.pfss_status_label.text()


# --------------------------------------------------------------------------- #
# Click-to-seed
# --------------------------------------------------------------------------- #

def test_clicks_are_only_collected_in_the_click_seed_mode(window):
    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData(SEED_MODES[0]))
    window._on_pfss_canvas_click(100.0, 50.0)
    assert window._pfss_clicked == []

    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData(SEED_CLICK))
    window._on_pfss_canvas_click(100.0, 50.0)
    window._on_pfss_canvas_click(-200.0, 75.0)
    assert window._pfss_clicked == [(100.0, 50.0), (-200.0, 75.0)]


def test_non_finite_clicks_are_ignored(window):
    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData(SEED_CLICK))
    window._on_pfss_canvas_click(float("nan"), 10.0)
    window._on_pfss_canvas_click(10.0, float("inf"))
    assert window._pfss_clicked == []


def test_computing_in_click_mode_with_no_seeds_says_so(window):
    # Needs a frame carrying a coordinate frame, or the earlier WCS check fires
    # first -- which is the correct order, since without one nothing can be
    # projected regardless of the seed mode.
    class FrameWithWcs:
        coordinate_frame = object()
        data = np.zeros((4, 4))
        meta: dict = {}

    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData(SEED_CLICK))
    window._pfss_clicked = []
    window._map_frames = [FrameWithWcs()]
    window._on_pfss_compute()
    _flush()
    assert "Click the solar disk" in window.pfss_status_label.text()
    window._map_frames = []


def test_a_frame_without_a_coordinate_frame_is_refused_before_anything_else(window):
    """Cropped frames become AiaArrayMap and can lose their WCS entirely."""
    class NoWcs:
        data = np.zeros((4, 4))
        meta: dict = {}

    window._map_frames = [NoWcs()]
    window._on_pfss_compute()
    _flush()
    assert "no usable solar coordinate system" in window.pfss_status_label.text()
    window._map_frames = []


def test_a_right_click_clears_collected_seeds(window):
    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData(SEED_CLICK))
    window._pfss_clicked = [(1.0, 2.0)]
    window._on_plot_canvas_click(0.0, 0.0, "right")
    _flush()
    assert window._pfss_clicked == []


def test_an_armed_measurement_tool_keeps_the_click(window):
    """The ruler and PFSS seeding must not both consume one click."""
    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData(SEED_CLICK))
    window._pfss_clicked = []
    if hasattr(window, "_measure"):
        window._measure.mode = "ruler"
        window._on_plot_canvas_click(10.0, 20.0, "left")
        _flush()
        assert window._pfss_clicked == []
        window._measure.mode = None


# --------------------------------------------------------------------------- #
# Worker wiring
# --------------------------------------------------------------------------- #

def test_the_solve_worker_is_dispatched_by_start_worker():
    """A worker class absent from _start_worker's isinstance chain never quits
    its thread, leaving _active_thread set forever: every later operation is
    refused and the window can no longer be closed."""
    import inspect

    from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow as Window

    source = inspect.getsource(Window._start_worker)
    assert "PfssSolveWorker" in source
    assert source.count("thread.quit") >= 3


def test_the_solve_worker_exposes_the_cancel_contract():
    from src.ui.solar.solar_pfss_controls import PfssSolveWorker
    from src.backend.solar.pfss_model import PfssParameters

    worker = PfssSolveWorker(PfssParameters())
    assert hasattr(worker, "cancel")
    assert hasattr(worker, "run")
    for signal_name in ("progress", "finished", "failed", "cancelled"):
        assert hasattr(worker, signal_name)
    worker.cancel()   # must not raise


def test_close_event_cancels_any_worker_not_just_the_search_one():
    """Previously only SunPyWorker was cancelled, so closing the window during a
    calibration, derotation or PFSS solve waited for it to finish."""
    import inspect

    from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow as Window

    source = inspect.getsource(Window.closeEvent)
    assert 'hasattr(worker, "cancel")' in source


# --------------------------------------------------------------------------- #
# Session persistence through the window
# --------------------------------------------------------------------------- #

def test_the_window_collects_and_restores_its_pfss_settings(window):
    from src.backend.session.solar_session import deserialize_pfss_state, serialize_pfss_state

    window.pfss_nrho_spin.setValue(44)
    window.pfss_rss_spin.setValue(3.5)
    window.pfss_density_spin.setValue(28)
    window.pfss_seed_combo.setCurrentIndex(window.pfss_seed_combo.findData("open_field"))
    window.pfss_closed_check.setChecked(False)
    _flush(2)

    payload = serialize_pfss_state(window._collect_pfss_state())

    window.pfss_nrho_spin.setValue(DEFAULT_NRHO)
    window.pfss_rss_spin.setValue(DEFAULT_RSS)
    window.pfss_closed_check.setChecked(True)
    _flush(2)

    window._restore_pfss_state(deserialize_pfss_state(payload))
    _flush(2)

    assert window.pfss_nrho_spin.value() == 44
    assert window.pfss_rss_spin.value() == pytest.approx(3.5)
    assert window.pfss_density_spin.value() == 28
    assert window.pfss_seed_combo.currentData() == "open_field"
    assert window.pfss_closed_check.isChecked() is False


def test_restoring_tells_the_user_the_overlay_needs_one_click(window):
    """Re-solving needs a background worker the restore path cannot chain, so
    the settings come back and the user is told -- the same contract the level
    and derotation restore uses."""
    from src.backend.session.solar_session import serialize_pfss_state

    window._restore_pfss_state(serialize_pfss_state(window._collect_pfss_state()))
    _flush()
    assert "Compute PFSS" in window.pfss_status_label.text()


def test_an_older_session_without_a_pfss_key_restores_cleanly(window):
    window._restore_pfss_state({})
    window._restore_pfss_state(None)
    _flush()


def test_the_session_snapshot_is_json_serialisable(window):
    """write_solar_session calls json.dumps with no default= handler."""
    import json

    from src.backend.session.solar_session import serialize_pfss_state

    window._pfss_provenance = {"path": "/tmp/m.fits", "carrington_rotation": 2272}
    json.dumps(serialize_pfss_state(window._collect_pfss_state()))


def test_the_diagnostics_window_is_reused_not_stacked(window, monkeypatch):
    import src.ui.solar.solar_pfss_controls as controls

    created: list[int] = []

    class FakeDiagnostics:
        def __init__(self, *a, **k):
            created.append(1)
            self.updates = 0

        def set_solution(self, *a, **k):
            self.updates += 1

        def show(self):
            pass

        def raise_(self):
            pass

    monkeypatch.setattr(
        controls,
        "PfssDiagnosticsWindow",
        FakeDiagnostics,
        raising=False,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.ui.solar.pfss_diagnostics_window",
        type("M", (), {"PfssDiagnosticsWindow": FakeDiagnostics}),
    )
    window._pfss_solution = object()

    window._on_pfss_diagnostics()
    window._on_pfss_diagnostics()
    _flush()
    assert len(created) == 1, "a second click must reuse the open window"


# --------------------------------------------------------------------------- #
# Overlay legibility
# --------------------------------------------------------------------------- #

def test_closed_loops_are_drawn_quieter_than_open_field(window):
    """The overlay exists to be compared against the EUV image beneath it.

    A solve returns several times more closed loops than open lines, so at equal
    weight they paint over the disk and hide what is being compared. Checked on a
    real AIA 193 frame: 550 lines at full brightness obscured the image almost
    completely.
    """
    for canvas in window._all_plot_canvases():
        styles = canvas.PFSS_STYLES
        closed_width, closed_alpha = styles["closed"][1], styles["closed"][2]
        for name in ("open_positive", "open_negative"):
            open_width, open_alpha = styles[name][1], styles[name][2]
            assert closed_alpha < open_alpha, f"{name} vs closed on {type(canvas).__name__}"
            assert closed_width <= open_width


def test_both_renderers_agree_on_the_overlay_classes(window):
    """A renderer switch must not change what a colour means."""
    pyqt, mpl = window.pyqt_canvas, window.matplotlib_canvas
    assert set(pyqt.PFSS_STYLES) == set(mpl.PFSS_STYLES)
    for name in pyqt.PFSS_STYLES:
        assert len(pyqt.PFSS_STYLES[name]) == len(mpl.PFSS_STYLES[name]) == 3


def test_the_default_density_stays_legible(window):
    """Tracing cost is linear in this, and so is how much of the disk is hidden.

    ~12 gives roughly 150 field lines, which shows the topology and still lets
    the image read through; 24 gave ~550 and did not.
    """
    from src.backend.solar.pfss_model import DEFAULT_SEED_DENSITY

    assert DEFAULT_SEED_DENSITY <= 16
    assert window.pfss_density_spin.value() == DEFAULT_SEED_DENSITY
