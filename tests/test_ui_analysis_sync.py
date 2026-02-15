from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.UI.gui_main import AnalyzeDialog, MainWindow, MaxIntensityPlotDialog


EXPECTED_SHOCK_KEYS = {
    "avg_freq_mhz",
    "avg_freq_err_mhz",
    "avg_drift_mhz_s",
    "avg_drift_err_mhz_s",
    "start_freq_mhz",
    "start_freq_err_mhz",
    "initial_shock_speed_km_s",
    "initial_shock_speed_err_km_s",
    "initial_shock_height_rs",
    "initial_shock_height_err_rs",
    "avg_shock_speed_km_s",
    "avg_shock_speed_err_km_s",
    "avg_shock_height_rs",
    "avg_shock_height_err_rs",
    "fold",
    "fundamental",
    "harmonic",
}


def _app():
    return QApplication.instance() or QApplication([])


def test_analyze_dialog_session_state_contains_canonical_shock_summary():
    _app()
    time_channels = np.arange(1, 30, dtype=float)
    time_s = time_channels * 0.25
    freqs = 90.0 * np.power(time_s, -0.45)

    dlg = AnalyzeDialog(time_channels, freqs, "demo.fit", fundamental=True, harmonic=False)
    dlg.plot_fit()

    state = dlg.session_state()
    analyzer = dict(state.get("analyzer") or {})
    shock = dict(analyzer.get("shock_summary") or {})

    assert "fit_params" in analyzer
    assert EXPECTED_SHOCK_KEYS.issubset(set(shock.keys()))

    dlg.close()


def test_max_dialog_emits_session_changed_on_mode_toggle():
    _app()
    dlg = MaxIntensityPlotDialog(
        np.arange(8, dtype=float),
        np.linspace(80.0, 70.0, 8),
        "demo.fit",
    )

    seen = {"n": 0}

    def _on(_payload):
        seen["n"] += 1

    dlg.sessionChanged.connect(_on)
    dlg.harmonic_radio.setChecked(True)

    assert seen["n"] >= 1
    dlg.close()


def test_analysis_seed_uses_current_canvas_spectrum_not_cached_session():
    _app()
    win = MainWindow(theme=None)
    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 90.0, 80.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 1.0, 1.0, 1.0],
            [2.0, 2.0, 2.0, 2.0],
            [3.0, 9.0, 3.0, 3.0],
        ],
        dtype=float,
    )

    # stale previous session with different shape must not be reused for manual max plot
    win._analysis_session = {
        "source": {"filename": "old.fit", "shape": [3, 2]},
        "max_intensity": {
            "time_channels": [0.0, 1.0],
            "freqs": [80.0, 80.0],
            "fundamental": True,
            "harmonic": False,
        },
    }

    win.plot_data(win.raw_data, title="Raw")
    QApplication.processEvents()
    win.plot_max_intensities()
    QApplication.processEvents()

    session = dict(win._analysis_session or {})
    max_block = dict(session.get("max_intensity") or {})
    t = np.asarray(max_block.get("time_channels"), dtype=float)
    f = np.asarray(max_block.get("freqs"), dtype=float)

    assert t.shape[0] == 4
    assert f.shape[0] == 4
    assert np.array_equal(f, np.array([80.0, 80.0, 80.0, 80.0], dtype=float))

    if win._max_intensity_dialog is not None:
        win._max_intensity_dialog.close()
    win.close()


def test_open_restored_analysis_uses_legacy_fallback_state():
    _app()
    win = MainWindow(theme=None)
    win._analysis_session = None
    win._max_intensity_state = {
        "time_channels": np.array([0.0, 1.0, 2.0], dtype=float),
        "freqs": np.array([80.0, 79.0, 78.0], dtype=float),
        "fundamental": True,
        "harmonic": False,
        "analyzer": {"fold": 1},
    }

    seen = {"called": False}

    def _fake_open(session=None, auto_open_analyzer=False, **_kwargs):
        seen["called"] = session is not None and bool(auto_open_analyzer)
        return None

    win._open_or_focus_max_dialog = _fake_open
    win.open_restored_analysis_windows()
    assert seen["called"] is True
    win.close()


def test_isolated_seed_auto_filters_zero_columns():
    _app()
    win = MainWindow(theme=None)
    win.set_max_auto_clean_isolated_enabled(True)
    win.filename = "demo.fit"
    win.current_plot_type = "Isolated Burst"
    win.freqs = np.array([100.0, 90.0, 80.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=float)
    win._current_plot_source_data = np.array(
        [
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 4.0, 6.0, 5.0, 0.0],
            [0.0, 8.0, 7.0, 9.0, 0.0],
        ],
        dtype=float,
    )

    session = win._build_analysis_seed_from_current_data()
    assert session is not None
    max_block = dict(session.get("max_intensity") or {})
    ui_block = dict(session.get("ui") or {})
    t = np.asarray(max_block.get("time_channels"), dtype=float)
    f = np.asarray(max_block.get("freqs"), dtype=float)

    assert np.array_equal(t, np.array([1.0, 2.0, 3.0], dtype=float))
    assert np.array_equal(f, np.array([80.0, 80.0, 80.0], dtype=float))
    assert bool(ui_block.get("auto_outlier_cleaned")) is True
    assert int(ui_block.get("auto_removed_count", 0)) == 2
    win.close()


def test_max_dialog_hides_manual_outlier_buttons_in_auto_mode():
    _app()
    dlg = MaxIntensityPlotDialog(
        np.arange(8, dtype=float),
        np.linspace(80.0, 70.0, 8),
        "demo.fit",
        auto_outlier_mode=True,
    )
    assert dlg.select_button.isHidden() is True
    assert dlg.remove_button.isHidden() is True
    dlg.close()


def test_isolated_seed_respects_auto_clean_toggle():
    _app()
    win = MainWindow(theme=None)
    win.filename = "demo.fit"
    win.current_plot_type = "Isolated Burst"
    win.freqs = np.array([100.0, 90.0, 80.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=float)
    win._current_plot_source_data = np.array(
        [
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 4.0, 6.0, 5.0, 0.0],
            [0.0, 8.0, 7.0, 9.0, 0.0],
        ],
        dtype=float,
    )

    win.set_max_auto_clean_isolated_enabled(False)
    session = win._build_analysis_seed_from_current_data()
    assert session is not None
    max_block = dict(session.get("max_intensity") or {})
    ui_block = dict(session.get("ui") or {})
    t = np.asarray(max_block.get("time_channels"), dtype=float)
    assert t.shape[0] == 5
    assert bool(ui_block.get("auto_outlier_cleaned")) is False
    assert int(ui_block.get("auto_removed_count", 0)) == 0
    win.close()
