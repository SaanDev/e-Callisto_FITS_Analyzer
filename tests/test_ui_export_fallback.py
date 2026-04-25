"""
e-CALLISTO FITS Analyzer
Version 2.4.0
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

import src.UI.main_window as main_window_module
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


def test_hardware_pick_export_path_appends_selected_filter_extension(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: ("/tmp/hardware-export", "PNG (*.png)"),
    )

    path, ext = win._pick_export_path_for_figure(
        "Export Figure",
        "hardware-export",
        "PNG (*.png);;PDF (*.pdf)",
        default_filter="PNG (*.png)",
    )

    assert path == "/tmp/hardware-export.png"
    assert ext == "png"
    win.close()


def test_linux_hardware_pick_export_path_uses_shared_helper(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window_module.sys, "platform", "linux")

    called = {}

    def fake_pick_export_path(parent, caption, default_name, filters, default_filter=None):
        called["args"] = (caption, default_name, filters, default_filter)
        return "/tmp/hardware-export.pdf", "pdf"

    monkeypatch.setattr(main_window_module, "pick_export_path", fake_pick_export_path)
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("native dialog should not be used")),
    )

    path, ext = win._pick_export_path_for_figure(
        "Export Figure",
        "hardware-export",
        "PNG (*.png);;PDF (*.pdf)",
        default_filter="PNG (*.png)",
    )

    assert path == "/tmp/hardware-export.pdf"
    assert ext == "pdf"
    assert called["args"] == (
        "Export Figure",
        "hardware-export",
        "PNG (*.png);;PDF (*.pdf)",
        "PNG (*.png)",
    )
    win.close()
