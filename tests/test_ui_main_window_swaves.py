"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("cdflib")
pytest.importorskip("astropy")

from astropy.io import fits
from PySide6.QtWidgets import QApplication

from src.Backend.project_session import read_project, write_project
from src.Backend.swaves import SPACECRAFT_AHEAD, SwavesPayload
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(monkeypatch):
    _app()
    # The crash-recovery prompt is modal and would block a headless run.
    monkeypatch.setattr(MainWindow, "_prompt_recovery_if_needed", lambda self: None)
    win = MainWindow(theme=None)
    yield win
    win.close()


def _find_menu_action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def _load_callisto(win, *, ut_start_sec: float = 45 * 60.0):
    """Install a small synthetic CALLISTO spectrum starting 2012-03-07 00:45 UT."""
    n_time, n_freq = 240, 64
    time = np.linspace(0.0, 900.0, n_time)
    freqs = np.linspace(870.0, 45.0, n_freq)
    data = np.random.default_rng(0).normal(120.0, 3.0, (n_freq, n_time))

    header = fits.Header()
    header["DATE-OBS"] = "2012-03-07"
    header["TIME-OBS"] = "00:45:00"

    win._apply_loaded_dataset(
        data=data,
        freqs=freqs,
        time=time,
        filename="test_20120307_004500.fit",
        header0=header,
        source_path=None,
        ut_start_sec=ut_start_sec,
        plot_title="Raw",
    )
    QApplication.processEvents()
    return time


def _payload(base_utc: datetime, *, minutes: int = 120, start_offset_min: int = -30) -> SwavesPayload:
    """A synthetic SWAVES window padded around the CALLISTO interval."""
    start = base_utc + timedelta(minutes=start_offset_min)
    x_seconds = np.arange(minutes, dtype=float) * 60.0 + start_offset_min * 60.0
    log_rows = np.linspace(np.log10(16025.0), np.log10(2.61), 256)
    intensity = np.random.default_rng(1).normal(5.0, 1.0, (256, minutes))
    return SwavesPayload(
        spacecraft=SPACECRAFT_AHEAD,
        start_utc=start,
        end_utc=start + timedelta(minutes=minutes),
        base_utc=base_utc,
        x_seconds=x_seconds,
        log_freq_rows=log_rows,
        intensity_db=intensity,
        source_files=("stereo_level2_swaves_20120307_v02.cdf",),
    )


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


def test_swaves_action_sits_in_the_radio_bursts_submenu(window):
    solar_action = _find_menu_action(window.menuBar(), "Solar Events")
    solar_menu = solar_action.menu()
    radio_action = _find_menu_action(solar_menu, "Radio Bursts")
    radio_menu = radio_action.menu()

    labels = [action.text() for action in radio_menu.actions() if not action.isSeparator()]
    assert labels == ["e-CALLISTO", "Learmonth", "SWAVES"]


def test_panel_toggle_starts_disabled_until_data_arrive(window):
    assert window.swaves_panel_action.isEnabled() is False
    assert window.swaves_panel_action.isChecked() is True

    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()

    assert window.swaves_panel_action.isEnabled() is True


# ---------------------------------------------------------------------------
# Split view
# ---------------------------------------------------------------------------


def test_base_epoch_matches_the_callisto_x_axis_origin(window):
    _load_callisto(window)
    assert window._swaves_base_utc() == datetime(2012, 3, 7, 0, 45, tzinfo=timezone.utc)


def test_payload_is_rebased_onto_the_callisto_origin(window):
    _load_callisto(window)
    base = window._swaves_base_utc()

    # Arrives measured against midnight rather than the file start.
    stale = _payload(base).rebase(datetime(2012, 3, 7, tzinfo=timezone.utc))
    window._on_swaves_payload_ready(stale)
    QApplication.processEvents()

    assert window._swaves_payload.base_utc == base
    assert window._swaves_payload.x_seconds[0] == pytest.approx(-1800.0)


def test_loading_swaves_splits_the_figure_and_shares_the_time_axis(window):
    time = _load_callisto(window)
    payload = _payload(window._swaves_base_utc())
    window._on_swaves_payload_ready(payload)
    QApplication.processEvents()

    # Two spectrogram panels plus their two colorbars.
    assert len(window.canvas.figure.axes) == 4
    assert window._swaves_ax is not None
    assert window._swaves_ax is not window.canvas.ax

    assert window.canvas.ax.get_xlim() == window._swaves_ax.get_xlim()

    # The shared range spans both instruments.
    x0, x1 = window.canvas.ax.get_xlim()
    assert x0 <= min(float(np.min(time)), float(payload.x_seconds[0]))
    assert x1 >= max(float(np.max(time)), float(payload.x_seconds[-1]))


def test_zooming_the_callisto_panel_moves_the_swaves_panel(window):
    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()

    window.canvas.ax.set_xlim(100.0, 400.0)
    assert window._swaves_ax.get_xlim() == pytest.approx((100.0, 400.0))


def test_only_the_lower_panel_carries_the_time_axis(window):
    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()

    assert window.canvas.ax.get_xlabel() == ""
    assert window._swaves_ax.get_xlabel() != ""
    assert not any(label.get_visible() for label in window.canvas.ax.get_xticklabels())


def test_the_callisto_interval_is_marked_on_the_swaves_panel(window):
    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()

    assert len(window._swaves_ax.patches) >= 1


def test_hiding_the_panel_returns_to_a_single_spectrum(window):
    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()
    assert len(window.canvas.figure.axes) == 4

    window.swaves_panel_action.setChecked(False)
    QApplication.processEvents()

    assert len(window.canvas.figure.axes) == 2
    assert window._swaves_ax is None
    # Hiding must not discard the data.
    assert window._swaves_payload is not None

    window.swaves_panel_action.setChecked(True)
    QApplication.processEvents()
    assert len(window.canvas.figure.axes) == 4


def test_clearing_the_panel_drops_the_payload(window):
    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()

    window.clear_swaves_panel()
    QApplication.processEvents()

    assert window._swaves_payload is None
    assert len(window.canvas.figure.axes) == 2
    assert window.swaves_panel_action.isEnabled() is False


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------


def test_swaves_plots_full_area_without_a_callisto_file(window):
    assert window.raw_data is None

    base = datetime(2012, 3, 7, tzinfo=timezone.utc)
    window._on_swaves_payload_ready(_payload(base))
    QApplication.processEvents()

    assert window._swaves_standalone is True
    # One panel plus its colorbar.
    assert len(window.canvas.figure.axes) == 2
    assert window._swaves_ax is window.canvas.ax
    assert window.canvas.ax.get_xlabel() == "Time [UT]"


def test_loading_callisto_afterwards_collapses_into_the_split_view(window):
    window._on_swaves_payload_ready(_payload(datetime(2012, 3, 7, tzinfo=timezone.utc)))
    QApplication.processEvents()
    assert window._swaves_standalone is True

    _load_callisto(window)

    assert window._swaves_standalone is False
    assert len(window.canvas.figure.axes) == 4
    assert window._swaves_ax is not window.canvas.ax


# ---------------------------------------------------------------------------
# Persistence and reporting
# ---------------------------------------------------------------------------


def test_project_round_trip_restores_the_panel_without_downloading(window, tmp_path, monkeypatch):
    _load_callisto(window)
    payload = _payload(window._swaves_base_utc())
    window._on_swaves_payload_ready(payload)
    QApplication.processEvents()

    meta, arrays = window._capture_project_payload()
    assert meta["swaves"] is not None
    assert {"swaves_x_seconds", "swaves_log_freq", "swaves_intensity"} <= set(arrays)

    project_path = tmp_path / "swaves.ecproj"
    write_project(str(project_path), meta=meta, arrays=arrays)

    monkeypatch.setattr(MainWindow, "_prompt_recovery_if_needed", lambda self: None)
    reopened = MainWindow(theme=None)
    try:
        loaded = read_project(str(project_path))
        reopened._apply_project_payload(loaded.meta, loaded.arrays)
        QApplication.processEvents()

        restored = reopened._swaves_payload
        assert restored is not None
        assert restored.spacecraft == payload.spacecraft
        assert restored.base_utc == payload.base_utc
        assert np.allclose(restored.x_seconds, payload.x_seconds)
        assert len(reopened.canvas.figure.axes) == 4
        assert reopened.swaves_panel_action.isEnabled() is True
    finally:
        reopened.close()


def test_project_without_swaves_leaves_the_panel_off(window, tmp_path, monkeypatch):
    _load_callisto(window)
    meta, arrays = window._capture_project_payload()
    assert meta["swaves"] is None

    project_path = tmp_path / "plain.ecproj"
    write_project(str(project_path), meta=meta, arrays=arrays)

    loaded = read_project(str(project_path))
    window._apply_project_payload(loaded.meta, loaded.arrays)
    QApplication.processEvents()

    assert window._swaves_payload is None
    assert window.swaves_panel_action.isEnabled() is False
    assert len(window.canvas.figure.axes) == 2


def test_report_includes_the_split_view_figure(window):
    _load_callisto(window)
    window._on_swaves_payload_ready(_payload(window._swaves_base_utc()))
    QApplication.processEvents()

    figures = window._build_project_report_figures(None)
    swaves_figures = [fig for fig in figures if "SWAVES" in fig.title]

    assert len(swaves_figures) == 1
    assert swaves_figures[0].png_bytes
    assert swaves_figures[0].availability_note == ""


def test_report_omits_the_figure_when_no_swaves_data_are_loaded(window):
    _load_callisto(window)

    figures = window._build_project_report_figures(None)
    assert not [fig for fig in figures if "SWAVES" in fig.title]


# ---------------------------------------------------------------------------
# Cursor readout
# ---------------------------------------------------------------------------


def test_cursor_readout_reports_swaves_values(window):
    _load_callisto(window)
    payload = _payload(window._swaves_base_utc())
    window._on_swaves_payload_ready(payload)
    QApplication.processEvents()

    handled = window._update_cursor_label_from_swaves(
        float(payload.x_seconds[5]), float(payload.log_freq_rows[10])
    )

    assert handled is True
    text = window.cursor_label.text()
    assert text.startswith("SWAVES")
    assert "dB>bgnd" in text
