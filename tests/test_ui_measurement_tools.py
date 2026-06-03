"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.UI.gui_main import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_main_window_ruler_action_and_classic_capture_do_not_mutate_project_state():
    _app()
    window = MainWindow(theme=None)
    window.use_hw_live_preview = False
    window.raw_data = np.arange(12, dtype=np.float32).reshape(3, 4)
    window.freqs = np.array([90.0, 70.0, 50.0], dtype=float)
    window.time = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
    window.filename = "demo.fit"
    window.plot_data(window.raw_data, title="Raw")
    window._project_dirty = False
    before = np.array(window.raw_data, copy=True)

    window._sync_toolbar_enabled_states()
    assert window.ruler_measurement_action.isEnabled() is True
    assert window.tb_ruler.isEnabled() is True

    window.activate_ruler_tool()
    window._on_measurement_mpl_click(SimpleNamespace(inaxes=window.canvas.ax, button=1, xdata=10.0, ydata=90.0))
    window._on_measurement_mpl_click(SimpleNamespace(inaxes=window.canvas.ax, button=1, xdata=30.0, ydata=50.0))

    assert window._measurement_result is not None
    assert window._measurement_result.duration_s == pytest.approx(20.0)
    assert window._measurement_result.frequency_delta_mhz == pytest.approx(-40.0)
    assert window._measurement_result.slope_mhz_s == pytest.approx(-2.0)
    assert "Slope:" in window.measurement_readout.text()
    assert len(window._measurement_artists) >= 2
    assert np.array_equal(window.raw_data, before)
    assert window._project_dirty is False

    window.clear_ruler_measurement()
    assert window._measurement_result is None
    assert window._measurement_artists == []
    window.close()


def test_main_window_ruler_rejects_equal_time_bounds():
    _app()
    window = MainWindow(theme=None)
    window.use_hw_live_preview = False
    window.raw_data = np.zeros((2, 2), dtype=np.float32)
    window.freqs = np.array([20.0, 10.0], dtype=float)
    window.time = np.array([0.0, 1.0], dtype=float)
    window.filename = "demo.fit"
    window.plot_data(window.raw_data, title="Raw")

    window.activate_ruler_tool()
    window._on_measurement_mpl_click(SimpleNamespace(inaxes=window.canvas.ax, button=1, xdata=1.0, ydata=20.0))
    window._on_measurement_mpl_click(SimpleNamespace(inaxes=window.canvas.ax, button=1, xdata=1.0, ydata=10.0))

    assert window._measurement_result is None
    assert "different time" in window.measurement_readout.text()
    window.close()
