"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import numpy as np
import pytest
import tempfile
from pathlib import Path

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.UI.accelerated_plot_widget import AcceleratedPlotWidget


def _app():
    return QApplication.instance() or QApplication([])


def test_accelerated_widget_constructs():
    _app()
    widget = AcceleratedPlotWidget()
    assert hasattr(widget, "is_available")


def test_accelerated_widget_update_image_no_crash():
    _app()
    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    class _DummyCmap:
        def __call__(self, x):
            arr = np.asarray(x, dtype=float)
            rgba = np.zeros((arr.size, 4), dtype=float)
            rgba[:, 0] = arr
            rgba[:, 1] = 1.0 - arr
            rgba[:, 2] = 0.5
            rgba[:, 3] = 1.0
            return rgba

    data = np.random.rand(8, 16).astype(np.float32)
    widget.update_image(
        data,
        extent=[0.0, 10.0, 20.0, 80.0],
        cmap=_DummyCmap(),
        title="Background Subtracted",
        x_label="Time [s]",
        y_label="Frequency [MHz]",
    )
    widget.set_text_style(
        font_family="Helvetica",
        tick_font_px=10,
        axis_label_font_px=12,
        title_font_px=14,
        title_bold=True,
        axis_italic=True,
        ticks_bold=True,
    )


def test_accelerated_widget_export_item_available():
    _app()
    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph not available in test environment")
    assert widget.export_plot_item() is not None


def test_pyqtgraph_image_export_not_empty():
    _app()
    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    class _DummyCmap:
        def __call__(self, x):
            arr = np.asarray(x, dtype=float)
            rgba = np.zeros((arr.size, 4), dtype=float)
            rgba[:, 0] = arr
            rgba[:, 1] = 1.0 - arr
            rgba[:, 2] = 0.5
            rgba[:, 3] = 1.0
            return rgba

    data = np.random.rand(16, 32).astype(np.float32)
    widget.resize(800, 500)
    widget.update_image(
        data,
        extent=[0.0, 10.0, 20.0, 80.0],
        cmap=_DummyCmap(),
        title="Preview",
        x_label="Time [s]",
        y_label="Frequency [MHz]",
    )

    import pyqtgraph.exporters as pg_exporters

    exporter = pg_exporters.ImageExporter(widget.export_plot_item())
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plot.png"
        exporter.export(str(out))
        assert out.exists()
        assert out.stat().st_size > 0


def test_accelerated_widget_lasso_capture_api_no_crash():
    _app()
    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph not available in test environment")
    widget.begin_lasso_capture()
    widget.stop_interaction_capture()
