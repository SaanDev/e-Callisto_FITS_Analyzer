"""
e-CALLISTO FITS Analyzer
Version 2.3.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")
pytest.importorskip("openpyxl")
pytest.importorskip("requests")

from PySide6.QtWidgets import QApplication

from src.UI.gui_main import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_main_window_hw_annotation_actions_keep_accel_canvas(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    if not window.accel_canvas.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    window.raw_data = np.zeros((2, 2), dtype=np.float32)
    window.use_hw_live_preview = True

    window._show_plot_canvas()
    window.start_annotation_line()
    assert window.plot_stack.currentWidget() is window.accel_canvas
    assert window._annotation_mode == "line"

    monkeypatch.setattr("src.UI.main_window.QInputDialog.getText", lambda *_a, **_k: ("Label", True))
    window._show_plot_canvas()
    window.start_annotation_text()
    assert window.plot_stack.currentWidget() is window.accel_canvas
    assert window._annotation_mode == "text"
    assert window._annotation_pending_text == "Label"

    window.close()


def test_main_window_hw_annotation_finish_adds_annotation():
    _app()
    window = MainWindow(theme=None)
    if not window.accel_canvas.is_available:
        pytest.skip("pyqtgraph not available in test environment")

    window.raw_data = np.zeros((2, 2), dtype=np.float32)
    window.use_hw_live_preview = True
    window._annotation_mode = "line"

    window._on_accel_annotation_capture_finished("line", [(1.0, 2.0), (3.0, 4.0)])

    assert len(window._annotations) == 1
    assert window._annotations[0]["kind"] == "line"
    assert window._annotations[0]["points"] == [[1.0, 2.0], [3.0, 4.0]]
    assert window._annotation_mode is None

    window.close()
