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

    monkeypatch.setattr(
        window,
        "_open_text_annotation_dialog",
        lambda **_k: {
            "text": "Label",
            "color": "#ffaa00",
            "font_family": "Helvetica",
            "font_size": 15,
            "font_bold": True,
            "font_italic": False,
        },
    )
    window._show_plot_canvas()
    window.start_annotation_text()
    assert window.plot_stack.currentWidget() is window.accel_canvas
    assert window._annotation_mode == "text"
    assert window._annotation_pending_text == "Label"
    assert window._annotation_pending_text_style["font_family"] == "Helvetica"

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


def test_edit_text_label_updates_style(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    window._annotations = [
        {
            "id": "ann1",
            "kind": "text",
            "points": [[4.0, 5.0]],
            "text": "Old",
            "color": "#00d4ff",
            "font_family": "",
            "font_size": 12,
            "font_bold": False,
            "font_italic": False,
            "line_width": 1.5,
            "visible": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    monkeypatch.setattr(window, "_choose_text_annotation_index", lambda **_k: 0)
    monkeypatch.setattr(
        window,
        "_open_text_annotation_dialog",
        lambda **_k: {
            "text": "New",
            "color": "#ff5500",
            "font_family": "Arial",
            "font_size": 18,
            "font_bold": True,
            "font_italic": True,
        },
    )

    window.edit_text_label()

    ann = window._annotations[0]
    assert ann["text"] == "New"
    assert ann["color"] == "#ff5500"
    assert ann["font_family"] == "Arial"
    assert ann["font_size"] == 18
    assert ann["font_bold"] is True
    assert ann["font_italic"] is True
    window.close()


def test_move_text_label_repositions_selected_label():
    _app()
    window = MainWindow(theme=None)
    window._annotations = [
        {
            "id": "ann1",
            "kind": "text",
            "points": [[1.0, 2.0]],
            "text": "Label",
            "color": "#00d4ff",
            "font_family": "",
            "font_size": 12,
            "font_bold": False,
            "font_italic": False,
            "line_width": 1.5,
            "visible": True,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    window._annotation_target_index = 0

    window._move_selected_text_annotation_to((8.0, 9.0))

    assert window._annotations[0]["points"] == [[8.0, 9.0]]
    assert window._annotation_mode is None
    window.close()
