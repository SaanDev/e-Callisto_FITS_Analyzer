"""
UI tests for regularized gapped-frequency rendering.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _flush_events():
    app = _app()
    for _ in range(3):
        app.processEvents()


def _regularized_gapped_dataset():
    freqs = np.array([300.0, 290.0, 280.0, 270.0, 260.0, 250.0, 240.0, 230.0, 220.0], dtype=float)
    time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    data = np.zeros((freqs.size, time.size), dtype=np.float32)
    data[:3] = np.arange(12, dtype=np.float32).reshape(3, 4)
    data[-3:] = np.arange(12, 24, dtype=np.float32).reshape(3, 4)
    gap_rows = np.array([False, False, False, True, True, True, False, False, False], dtype=bool)
    return data, freqs, time, gap_rows


def test_regularized_gap_plot_uses_single_image_and_keeps_hw_preview_enabled():
    _app()
    window = MainWindow(theme=None)
    if window.accel_canvas.is_available:
        window.set_hardware_live_preview_enabled(True)

    data, freqs, time, _gap_rows = _regularized_gapped_dataset()
    window._apply_loaded_dataset(
        data=data,
        freqs=freqs,
        time=time,
        filename="demo.fit",
        header0=None,
        source_path=None,
        ut_start_sec=0.0,
        combined_mode="frequency",
        combined_sources=["A.fit", "B.fit"],
        gap_row_mask=None,
        frequency_step_mhz=10.0,
        plot_title="Raw",
    )
    _flush_events()

    assert len(window.canvas.ax.images) == 1
    if window.accel_canvas.is_available:
        assert window._hardware_mode_enabled() is True
        assert window.hw_live_preview_action.isEnabled() is True

    window.close()


def test_lasso_selection_inside_zero_filled_gap_selects_only_gap_rows():
    _app()
    window = MainWindow(theme=None)
    data, freqs, time, gap_rows = _regularized_gapped_dataset()

    window._apply_loaded_dataset(
        data=data,
        freqs=freqs,
        time=time,
        filename="demo.fit",
        header0=None,
        source_path=None,
        ut_start_sec=0.0,
        combined_mode="frequency",
        combined_sources=["A.fit", "B.fit"],
        gap_row_mask=None,
        frequency_step_mhz=10.0,
        plot_title="Raw",
    )
    _flush_events()

    window.noise_reduced_data = data.copy()
    window.noise_vmin = 0.0
    window.noise_vmax = 23.0

    window.on_lasso_select(
        [
            (0.5, 268.0),
            (2.5, 268.0),
            (2.5, 252.0),
            (0.5, 252.0),
        ]
    )
    _flush_events()

    assert window.lasso_mask is not None
    assert bool(np.any(window.lasso_mask[gap_rows]))
    assert not bool(np.any(window.lasso_mask[~gap_rows]))
    window.close()
