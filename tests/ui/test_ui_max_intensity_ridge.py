"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.ui.radio.dialogs.max_intensity_dialog import MaxIntensityPlotDialog

FREQS = np.linspace(200.0, 45.0, 120)
TIME = np.arange(0.0, 60.0, 0.25)
BURST = 150.0 * ((TIME + 10.0) / 10.0) ** -0.6


def _app():
    return QApplication.instance() or QApplication([])


#: A second, brighter burst high in the band, later in time.
SECOND = 190.0 - 0.8 * (TIME - 37.5)


def _spectrum(second_burst=False):
    rng = np.random.default_rng(7)
    data = rng.normal(0.0, 1.0, (FREQS.size, TIME.size))
    for column, freq in enumerate(BURST):
        if 20 <= column <= 200:
            data[:, column] += 8.0 * np.exp(-0.5 * ((FREQS - freq) / 2.0) ** 2)
    if second_burst:
        for column in range(150, 230):
            data[:, column] += 20.0 * np.exp(-0.5 * ((FREQS - SECOND[column]) / 2.0) ** 2)
    return data


def _dialog(data):
    columns = np.arange(TIME.size, dtype=float)
    maxima = FREQS[np.argmax(data, axis=0)]
    dlg = MaxIntensityPlotDialog(columns, maxima, "demo.fit", time_seconds=TIME)
    dlg.set_ridge_source(data, FREQS, TIME)
    return dlg


def test_ridge_controls_need_a_spectrum():
    _app()
    dlg = MaxIntensityPlotDialog(np.arange(5.0), np.full(5, 80.0), "demo.fit")

    assert not dlg.track_ridge_button.isEnabled()
    assert not dlg.ridge_start_button.isEnabled()
    dlg.close()


def test_track_ridge_replaces_the_points_and_can_be_undone():
    _app()
    data = _spectrum()
    dlg = _dialog(data)
    before = dlg.freqs.copy()
    emitted = []
    dlg.sessionChanged.connect(emitted.append)

    assert dlg.track_ridge()

    columns = dlg.time_channels.astype(int)
    assert columns[0] == 20 and columns[-1] == 200
    assert np.median(np.abs(dlg.freqs - BURST[columns])) < 1.0
    assert np.array_equal(dlg.time_seconds, TIME[columns])
    assert dlg.restore_maxima_action.isEnabled()
    assert emitted and len(emitted[-1]["max_intensity"]["freqs"]) == columns.size

    dlg.restore_per_column_maxima()
    assert np.array_equal(dlg.freqs, before)
    assert not dlg.restore_maxima_action.isEnabled()
    dlg.close()


def test_a_picked_start_chooses_which_feature_is_followed():
    _app()
    data = _spectrum(second_burst=True)
    dlg = _dialog(data)

    # Brightest-point tracking follows the brighter second burst...
    assert dlg.track_ridge()
    columns = dlg.time_channels.astype(int)
    assert np.median(np.abs(dlg.freqs - SECOND[columns])) < 1.0
    dlg.restore_per_column_maxima()

    # ...a click on the burst follows the burst instead.
    dlg.ridge_start_button.setChecked(True)
    click = SimpleNamespace(inaxes=dlg.canvas.ax, xdata=float(TIME[100]), ydata=float(BURST[100]))
    dlg._on_ridge_seed_click(click)
    assert not dlg.ridge_start_button.isChecked()

    assert dlg.track_ridge()
    columns = dlg.time_channels.astype(int)
    assert np.median(np.abs(dlg.freqs - BURST[columns])) < 1.0
    dlg.close()


def test_a_start_in_empty_sky_is_reported(monkeypatch):
    _app()
    dlg = _dialog(_spectrum())
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    click = SimpleNamespace(inaxes=dlg.canvas.ax, xdata=float(TIME[230]), ydata=60.0)
    dlg._on_ridge_seed_click(click)

    assert not dlg.track_ridge()
    assert warnings
    dlg.close()
