"""
e-CALLISTO FITS Analyzer
Version 2.3.0
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


def test_accelerated_widget_enables_hover_mouse_tracking():
    _app()
    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    assert widget.hasMouseTracking() is True
    assert widget._graphics.hasMouseTracking() is True
    assert widget._graphics.viewport().hasMouseTracking() is True


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


def test_accelerated_widget_gap_rows_render_with_gray_hatch_pattern():
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

    data = np.array(
        [
            [1.0, 2.0, 3.0],
            [np.nan, np.nan, np.nan],
            [4.0, 5.0, 6.0],
        ],
        dtype=np.float32,
    )
    widget.update_image(
        data,
        extent=[0.0, 3.0, 30.0, 0.0],
        cmap=_DummyCmap(),
        gap_row_mask=np.array([False, True, False], dtype=bool),
        title="Preview",
        x_label="Time [s]",
        y_label="Frequency [MHz]",
    )

    rendered = np.asarray(widget._image.image)
    assert rendered.ndim == 3
    assert rendered.shape[2] == 4
    assert np.all(rendered[1, :, 3] > 0)
    gap_colors = np.unique(rendered[1, :, :3].reshape(-1, 3), axis=0)
    assert gap_colors.shape[0] >= 2


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


def test_accelerated_widget_annotation_capture_api_no_crash():
    _app()
    widget = AcceleratedPlotWidget()
    if not widget.is_available:
        pytest.skip("pyqtgraph not available in test environment")
    widget.begin_annotation_capture("polygon")
    widget.begin_annotation_capture("line")
    widget.begin_annotation_capture("text")
    widget.stop_interaction_capture()


def test_accelerated_widget_goes_overlay_lifecycle():
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

    widget.update_image(
        np.random.rand(8, 16).astype(np.float32),
        extent=[0.0, 10.0, 20.0, 80.0],
        cmap=_DummyCmap(),
        title="Preview",
        x_label="Time [s]",
        y_label="Frequency [MHz]",
    )

    payload = {
        "satellite_number": 18,
        "satellite_numbers": (17, 18),
        "series": {
            "xrsa": {
                "x_seconds": np.array([0.0, 2.0, 4.0, 6.0], dtype=float),
                "flux_wm2": np.array([6e-9, 8e-9, 1e-8, 2e-8], dtype=float),
                "channel_label": "xrsa",
            },
            "xrsb": {
                "x_seconds": np.array([0.0, 2.0, 4.0, 6.0], dtype=float),
                "flux_wm2": np.array([1e-8, 2e-8, 4e-8, 6e-8], dtype=float),
                "channel_label": "xrsb",
            },
        },
        "x_seconds": np.array([0.0, 2.0, 4.0, 6.0], dtype=float),
        "flux_wm2": np.array([1e-8, 2e-8, 4e-8, 6e-8], dtype=float),
        "channel_label": "xrsb",
    }
    widget.set_goes_overlay(payload, visible_channels=("xrsa", "xrsb"))

    assert widget._goes_overlay_payload is payload
    assert widget._goes_curve_items

    x_b, y_b = widget._goes_curve_items["xrsb"].getData()
    x_a, y_a = widget._goes_curve_items["xrsa"].getData()
    assert np.allclose(x_b, payload["series"]["xrsb"]["x_seconds"])
    assert np.allclose(y_b, np.log10(payload["series"]["xrsb"]["flux_wm2"]))
    assert np.allclose(x_a, payload["series"]["xrsa"]["x_seconds"])
    assert np.allclose(y_a, np.log10(payload["series"]["xrsa"]["flux_wm2"]))

    widget.clear_goes_overlay()
    x_data, y_data = widget._goes_curve_items["xrsb"].getData()
    assert widget._goes_overlay_payload is None
    assert len(x_data) == 0
    assert len(y_data) == 0


def test_accelerated_widget_rect_zoom_hides_and_restores_goes_overlay():
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

    widget.update_image(
        np.random.rand(8, 16).astype(np.float32),
        extent=[0.0, 10.0, 20.0, 80.0],
        cmap=_DummyCmap(),
        title="Preview",
        x_label="Time [s]",
        y_label="Frequency [MHz]",
    )

    payload = {
        "satellite_number": 18,
        "satellite_numbers": (18,),
        "series": {
            "xrsb": {
                "x_seconds": np.array([0.0, 2.0, 4.0, 6.0], dtype=float),
                "flux_wm2": np.array([1e-8, 2e-8, 4e-8, 6e-8], dtype=float),
                "channel_label": "xrsb",
            },
        },
    }
    widget.set_goes_overlay(payload, visible_channels=("xrsb",))
    assert widget._goes_overlay_rect_zoom_hidden is False

    widget.start_rect_zoom_once()
    assert widget._rect_zoom_once is True
    assert widget._goes_overlay_rect_zoom_hidden is True
    assert widget._goes_axis is None or widget._goes_axis.isVisible() is False

    widget.cancel_rect_zoom()
    assert widget._rect_zoom_once is False
    assert widget._goes_overlay_rect_zoom_hidden is False
    assert widget._goes_axis is None or widget._goes_axis.isVisible() is True

    x_data, y_data = widget._goes_curve_items["xrsb"].getData()
    assert np.allclose(x_data, payload["series"]["xrsb"]["x_seconds"])
    assert np.allclose(y_data, np.log10(payload["series"]["xrsb"]["flux_wm2"]))
