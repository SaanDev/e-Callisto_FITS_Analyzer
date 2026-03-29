"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

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


def test_analyze_dialog_fold_combo_reserves_space_for_visible_value():
    _app()
    dlg = AnalyzeDialog(
        np.arange(1, 6, dtype=float),
        np.array([60.0, 55.0, 50.0, 45.0, 40.0], dtype=float),
        "demo.fit",
        fundamental=True,
        harmonic=False,
    )

    assert dlg.fold_combo.minimumContentsLength() == 2
    assert dlg.fold_combo.minimumWidth() >= 70

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


def test_max_dialog_shows_manual_outlier_buttons_in_normal_mode():
    _app()
    dlg = MaxIntensityPlotDialog(
        np.arange(8, dtype=float),
        np.linspace(80.0, 70.0, 8),
        "demo.fit",
        auto_outlier_mode=False,
    )

    assert dlg.select_button.isHidden() is False
    assert dlg.remove_button.isHidden() is False

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


def test_max_dialog_keeps_manual_outlier_buttons_in_auto_mode():
    _app()
    dlg = MaxIntensityPlotDialog(
        np.arange(8, dtype=float),
        np.linspace(80.0, 70.0, 8),
        "demo.fit",
        auto_outlier_mode=True,
    )
    assert dlg.select_button.isHidden() is False
    assert dlg.remove_button.isHidden() is False
    assert dlg.select_button.isEnabled() is True
    assert dlg.remove_button.isEnabled() is True
    dlg.close()


def test_max_dialog_manual_outlier_removal_still_works_in_auto_mode():
    _app()
    dlg = MaxIntensityPlotDialog(
        np.arange(6, dtype=float),
        np.linspace(80.0, 70.0, 6),
        "demo.fit",
        auto_outlier_mode=True,
    )

    dlg.selected_mask = np.array([False, True, False, True, False, False], dtype=bool)
    dlg.remove_selected_outliers()

    assert dlg.time_channels.shape[0] == 4
    assert dlg.freqs.shape[0] == 4
    assert dlg.selected_mask.shape[0] == 4

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


def test_recovery_prompt_is_skipped_during_pytest(monkeypatch, tmp_path):
    _app()
    win = MainWindow(theme=None)
    win._previous_clean_exit = False

    snap = tmp_path / "dummy_snapshot.npz"
    snap.write_bytes(b"x")
    monkeypatch.setattr("src.UI.main_window.latest_snapshot_path", lambda: str(snap))
    monkeypatch.setattr(
        "src.UI.main_window.QMessageBox.question",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Recovery prompt should be skipped under pytest")),
    )

    win._prompt_recovery_if_needed()
    win.close()


def test_reset_selection_restores_pre_rfi_noise_reduced_data():
    _app()
    win = MainWindow(theme=None)

    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 1.0, 1.0, 1.0],
            [2.0, 2.0, 2.0, 2.0],
            [3.0, 3.0, 3.0, 3.0],
        ],
        dtype=np.float32,
    )
    base_noise_reduced = np.array(
        [
            [0.0, 0.5, 0.0, 0.5],
            [1.0, 1.5, 1.0, 1.5],
            [2.0, 2.5, 2.0, 2.5],
        ],
        dtype=np.float32,
    )
    rfi_cleaned = np.array(
        [
            [0.0, 0.2, 0.0, 0.2],
            [0.8, 1.2, 0.8, 1.2],
            [1.6, 2.0, 1.6, 2.0],
        ],
        dtype=np.float32,
    )

    win.noise_reduced_data = base_noise_reduced.copy()
    win.noise_reduced_original = base_noise_reduced.copy()
    win.noise_reduced_original_plot_type = "Background Subtracted"
    win.current_plot_type = "Background Subtracted"

    win._rfi_preview_data = rfi_cleaned.copy()
    win._rfi_preview_masked = [1]
    win.apply_rfi_now()

    assert np.array_equal(win.noise_reduced_data, rfi_cleaned)
    assert np.array_equal(win.noise_reduced_original, base_noise_reduced)
    assert win.current_plot_type == "RFI Cleaned"
    assert bool(win._rfi_config.get("applied", False)) is True

    win.reset_selection()

    assert np.array_equal(win.noise_reduced_data, base_noise_reduced)
    assert win.current_plot_type == "Background Subtracted"
    assert bool(win._rfi_config.get("applied", True)) is False

    win.close()


def test_noise_threshold_live_update_preserves_zoomed_view():
    _app()
    win = MainWindow(theme=None)
    win.set_hardware_live_preview_enabled(False)

    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 4.0, 2.0, 5.0],
            [2.0, 6.0, 3.0, 7.0],
            [3.0, 8.0, 4.0, 9.0],
        ],
        dtype=np.float32,
    )

    win.plot_data(win.raw_data, title="Raw")
    QApplication.processEvents()

    expected_xlim = (0.6, 2.4)
    expected_ylim = (91.5, 98.5)
    win.canvas.ax.set_xlim(*expected_xlim)
    win.canvas.ax.set_ylim(*expected_ylim)

    win.lower_slider.setValue(win._noise_threshold_to_slider(-1.0))
    win.upper_slider.setValue(win._noise_threshold_to_slider(2.0))
    win.update_noise_live()
    QApplication.processEvents()

    assert win.noise_clip_low == pytest.approx(-1.0)
    assert win.noise_clip_high == pytest.approx(2.0)
    assert win.canvas.ax.get_xlim() == pytest.approx(expected_xlim)
    assert win.canvas.ax.get_ylim() == pytest.approx(expected_ylim)
    win.close()


def test_noise_threshold_commit_preserves_zoomed_view():
    _app()
    win = MainWindow(theme=None)
    win.set_hardware_live_preview_enabled(False)

    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 4.0, 2.0, 5.0],
            [2.0, 6.0, 3.0, 7.0],
            [3.0, 8.0, 4.0, 9.0],
        ],
        dtype=np.float32,
    )
    win.noise_reduced_data = np.array(
        [
            [-1.0, 1.0, -1.0, 1.0],
            [0.0, 2.0, 0.0, 2.0],
            [1.0, 3.0, 1.0, 3.0],
        ],
        dtype=np.float32,
    )

    win.plot_data(win.raw_data, title="Raw")
    QApplication.processEvents()

    expected_xlim = (0.8, 2.2)
    expected_ylim = (92.0, 99.0)
    win.canvas.ax.set_xlim(*expected_xlim)
    win.canvas.ax.set_ylim(*expected_ylim)

    win._commit_noise_live_update()
    QApplication.processEvents()

    assert win.canvas.ax.get_xlim() == pytest.approx(expected_xlim)
    assert win.canvas.ax.get_ylim() == pytest.approx(expected_ylim)
    win.close()


def test_noise_scale_mapping_round_trip_and_zero_midpoint():
    _app()
    win = MainWindow(theme=None)

    values = [-100.0, -10.0, -1.0, 0.0, 1.0, 10.0, 100.0]
    positions = [
        win._noise_threshold_to_slider(value, scale=win.NOISE_CLIP_SCALE_SIGNED_LOG)
        for value in values
    ]

    assert positions == sorted(positions)
    assert win._noise_threshold_to_slider(0.0, scale=win.NOISE_CLIP_SCALE_SIGNED_LOG) == win.NOISE_SLIDER_MID
    assert win._noise_slider_to_threshold(win.NOISE_SLIDER_MID, scale=win.NOISE_CLIP_SCALE_SIGNED_LOG) == pytest.approx(0.0)
    for value in values:
        slider_value = win._noise_threshold_to_slider(value, scale=win.NOISE_CLIP_SCALE_SIGNED_LOG)
        assert win._noise_slider_to_threshold(slider_value, scale=win.NOISE_CLIP_SCALE_SIGNED_LOG) == pytest.approx(value, abs=0.06)

    win.close()


def test_noise_scale_toggle_preserves_logical_thresholds():
    _app()
    win = MainWindow(theme=None)
    win._set_noise_clip_state(-12.5, 34.25, scale=win.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)

    win.noise_log_scale_chk.setChecked(True)
    QApplication.processEvents()
    assert win.noise_clip_scale == win.NOISE_CLIP_SCALE_SIGNED_LOG
    assert win.noise_clip_low == pytest.approx(-12.5)
    assert win.noise_clip_high == pytest.approx(34.25)

    win.noise_log_scale_chk.setChecked(False)
    QApplication.processEvents()
    assert win.noise_clip_scale == win.NOISE_CLIP_SCALE_LINEAR
    assert win.noise_clip_low == pytest.approx(-12.5)
    assert win.noise_clip_high == pytest.approx(34.25)

    win.close()


def test_noise_slider_crossing_clamps_threshold_order():
    _app()
    win = MainWindow(theme=None)
    win._set_noise_clip_state(-10.0, 5.0, scale=win.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)

    win.lower_slider.setValue(win._noise_threshold_to_slider(20.0))
    QApplication.processEvents()
    assert win.noise_clip_low == pytest.approx(5.0)
    assert win.noise_clip_high == pytest.approx(5.0)
    assert win.lower_slider.value() == win.upper_slider.value()

    win._set_noise_clip_state(-10.0, 5.0, scale=win.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    win.upper_slider.setValue(win._noise_threshold_to_slider(-20.0))
    QApplication.processEvents()
    assert win.noise_clip_low == pytest.approx(-10.0)
    assert win.noise_clip_high == pytest.approx(-10.0)
    assert win.lower_slider.value() == win.upper_slider.value()

    win.close()


def test_noise_value_labels_follow_units_and_reset_to_zero():
    _app()
    win = MainWindow(theme=None)
    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 4.0, 2.0, 5.0],
            [2.0, 6.0, 3.0, 7.0],
            [3.0, 8.0, 4.0, 9.0],
        ],
        dtype=np.float32,
    )

    win._set_noise_clip_state(-5.0, 12.0, scale=win.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    QApplication.processEvents()

    low_disp, high_disp, unit = win._noise_clip_display_values()
    assert win.lower_value_label.text() == win._format_noise_clip_value(low_disp, unit)
    assert win.upper_value_label.text() == win._format_noise_clip_value(high_disp, unit)
    assert win.lower_value_sub_label.isHidden() is True
    assert win.upper_value_sub_label.isHidden() is True

    win.set_units_mode(True)
    QApplication.processEvents()
    prior_lower_text = win.lower_value_label.text()
    prior_lower_sub_text = win.lower_value_sub_label.text()
    low_disp, high_disp, unit = win._noise_clip_display_values()
    assert unit == "dB"
    assert win.lower_value_label.text() == win._format_noise_clip_threshold_digits(win.noise_clip_low)
    assert win.upper_value_label.text() == win._format_noise_clip_threshold_digits(win.noise_clip_high)
    assert win.lower_value_sub_label.isHidden() is False
    assert win.upper_value_sub_label.isHidden() is False
    assert win.lower_value_sub_label.text() == win._format_noise_clip_value(low_disp, unit)
    assert win.upper_value_sub_label.text() == win._format_noise_clip_value(high_disp, unit)

    win.lower_slider.setValue(win._noise_threshold_to_slider(-8.0))
    QApplication.processEvents()
    assert win.lower_value_label.text() != prior_lower_text
    assert win.lower_value_sub_label.text() != prior_lower_sub_text
    assert win.lower_value_label.text() == win._format_noise_clip_threshold_digits(-8.0)

    win.reset_to_raw()
    QApplication.processEvents()
    assert win.noise_clip_low == pytest.approx(0.0)
    assert win.noise_clip_high == pytest.approx(0.0)
    low_disp, high_disp, unit = win._noise_clip_display_values()
    assert win.lower_value_label.text() == win._format_noise_clip_threshold_digits(win.noise_clip_low)
    assert win.upper_value_label.text() == win._format_noise_clip_threshold_digits(win.noise_clip_high)
    assert win.lower_value_sub_label.isHidden() is False
    assert win.upper_value_sub_label.isHidden() is False
    assert win.lower_value_sub_label.text() == win._format_noise_clip_value(low_disp, unit)
    assert win.upper_value_sub_label.text() == win._format_noise_clip_value(high_disp, unit)

    win.close()
