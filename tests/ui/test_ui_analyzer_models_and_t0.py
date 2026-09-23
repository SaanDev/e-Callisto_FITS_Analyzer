"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("scipy")

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication

from src.backend.radio.density_models import DENSITY_MODEL_ORDER, shock_parameters
from src.backend.session.analysis_session import normalize_session
from src.ui.radio.dialogs.analyze_dialog import AnalyzeDialog


def _app():
    return QApplication.instance() or QApplication([])


def _dialog(**kwargs):
    # A burst that started 20 s before the file: f = 90 (t + 20)^-0.45.
    time_s = np.arange(4.0, 60.0, 1.0)
    freqs = 90.0 * np.power(time_s + 20.0, -0.45) * 4.0
    return AnalyzeDialog(time_s / 0.25, freqs, "demo.fit", time_seconds=time_s, **kwargs)


def _select_model(dlg, key):
    dlg.density_model_combo.setCurrentIndex(dlg.density_model_combo.findData(key))


def _select_t0(dlg, mode):
    dlg.t0_combo.setCurrentIndex(dlg.t0_combo.findData(mode))


def test_defaults_are_newkirk_and_file_start():
    _app()
    dlg = _dialog()
    dlg.plot_fit()

    analyzer = dlg.session_state()["analyzer"]
    assert (analyzer["density_model"], analyzer["t0_mode"], analyzer["t0_s"]) == ("newkirk", "file_start", 0.0)
    assert "Newkirk 1-fold" in dlg.shock_header.text()
    assert "(x −" not in dlg.equation_display.text()
    dlg.close()


def test_comparison_table_lists_every_model_and_matches_the_labels():
    _app()
    dlg = _dialog()
    dlg.plot_fit()

    table = dlg.comparison_table
    assert table.rowCount() == len(DENSITY_MODEL_ORDER)
    newkirk_row = DENSITY_MODEL_ORDER.index("newkirk")
    shock = dlg.session_state()["analyzer"]["shock_summary"]
    assert table.item(newkirk_row, 0).text() == f"{shock['initial_shock_speed_km_s']:.1f}"
    assert table.item(newkirk_row, 3).text() == f"{shock['avg_shock_height_rs']:.3f}"
    assert table.item(newkirk_row, 0).font().bold()
    dlg.close()


def test_choosing_a_model_recomputes_the_shock_parameters():
    _app()
    dlg = _dialog()
    dlg.plot_fit()
    emitted = []
    dlg.sessionChanged.connect(emitted.append)

    _select_model(dlg, "leblanc")

    shock = dlg.session_state()["analyzer"]["shock_summary"]
    expected = shock_parameters(
        np.asarray(dlg._shock_freq_values),
        np.asarray(dlg._shock_drift_vals),
        np.asarray(dlg._shock_calc_drift_errs),
        freq_err_mhz=float(dlg.freq_err),
        model="leblanc",
        fold=1,
    )
    assert shock["initial_shock_speed_km_s"] == pytest.approx(expected["initial_shock_speed_km_s"])
    assert shock["avg_shock_height_rs"] == pytest.approx(expected["avg_shock_height_rs"])
    assert "Leblanc 1-fold" in dlg.shock_header.text()
    assert emitted and emitted[-1]["analyzer"]["density_model"] == "leblanc"
    dlg.close()


def test_burst_onset_t0_refits_against_time_since_onset():
    _app()
    dlg = _dialog()
    dlg.plot_fit()
    b_file_start = dlg._fit_params["b"]

    _select_t0(dlg, "burst_onset")

    # One sample (1 s) before the first point at 4 s.
    assert dlg._fit_t0_s == pytest.approx(3.0)
    assert "(x − 3.00)" in dlg.equation_display.text()
    assert dlg._fit_params["b"] != pytest.approx(b_file_start)
    fit_x = np.asarray(dlg._graph["fit_x"])
    assert fit_x.min() == pytest.approx(4.0)  # the curve stays on the data's own time axis
    analyzer = dlg.session_state()["analyzer"]
    assert (analyzer["t0_mode"], analyzer["t0_s"]) == ("burst_onset", pytest.approx(3.0))
    dlg.close()


def test_custom_t0_is_entered_in_ut_when_the_start_time_is_known():
    _app()
    dlg = _dialog()
    dlg.set_ut_start_sec(12 * 3600.0)
    dlg.plot_fit()

    _select_t0(dlg, "custom")
    assert not dlg.t0_time_edit.isHidden() and dlg.t0_seconds_spin.isHidden()
    dlg.t0_time_edit.setTime(QTime(11, 59, 40))  # 20 s before the file starts
    dlg.t0_time_edit.editingFinished.emit()

    assert dlg._fit_t0_s == pytest.approx(-20.0)
    # The synthetic burst follows exactly this law, so the exponent comes back.
    assert dlg._fit_params["b"] == pytest.approx(0.45, rel=1e-3)
    dlg.close()


def test_model_and_t0_survive_a_session_round_trip():
    _app()
    dlg = _dialog()
    dlg.plot_fit()
    _select_model(dlg, "mann")
    _select_t0(dlg, "custom")
    dlg.t0_seconds_spin.setValue(-20.0)
    dlg.t0_seconds_spin.editingFinished.emit()
    saved = normalize_session(dlg.session_state())
    dlg.close()

    restored = _dialog(session=saved)

    assert restored._selected_density_model() == "mann"
    assert restored._t0_mode() == "custom"
    assert restored._fit_t0_s == pytest.approx(-20.0)
    assert "Mann 1-fold" in restored.shock_header.text()
    restored.close()
