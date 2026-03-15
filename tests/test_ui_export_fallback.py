"""
e-CALLISTO FITS Analyzer
Version 2.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


class _FramebufferViewport(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(48, 32)

    def grabFramebuffer(self):
        image = QImage(self.size(), QImage.Format_ARGB32)
        image.fill(QColor("#2ca02c"))
        return image


class _DummyGraphics:
    def __init__(self, viewport: QWidget):
        self._viewport = viewport

    def viewport(self):
        return self._viewport


class _DummyAccelCanvas(QWidget):
    def __init__(self, viewport: QWidget):
        super().__init__()
        self._graphics = _DummyGraphics(viewport)
        self.resize(viewport.size())

    def export_plot_item(self):
        return object()


def test_hardware_png_export_falls_back_to_framebuffer_capture(tmp_path: Path):
    _app()
    win = MainWindow(theme=None)
    viewport = _FramebufferViewport()
    dummy_canvas = _DummyAccelCanvas(viewport)
    win.accel_canvas = dummy_canvas

    out = tmp_path / "hardware-export.png"
    win._export_hardware_visible_plot(str(out), "png")

    assert out.exists()
    assert out.stat().st_size > 0

    image = QImage(str(out))
    assert not image.isNull()
    assert image.pixelColor(0, 0) == QColor("#2ca02c")

    dummy_canvas.close()
    viewport.close()
    win.close()
