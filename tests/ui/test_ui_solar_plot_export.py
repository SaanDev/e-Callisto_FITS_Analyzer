"""
e-CALLISTO FITS Analyzer
Version 3.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Export Plot in Solar Image Analysis saves the frame on screen with its display
adjustments. With hardware acceleration the canvas is an OpenGL widget whose
screen grab is blank, so the export must render the scene, not grab it. The
tests run without OpenGL, so they make grab() blank to stand in for it.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

import pyqtgraph as pg
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow


def _app():
    return QApplication.instance() or QApplication([])


class _Frame:
    observatory = "SDO"
    instrument = "AIA"
    detector = ""
    wavelength = "193 Angstrom"
    nickname = ""
    source = ""

    def __init__(self, data, date):
        self.data = np.asarray(data, dtype=float)
        self.date = date
        self.meta = {
            "instrume": "AIA",
            "cdelt1": 2.0,
            "cdelt2": 2.0,
            "crpix1": 16.5,
            "crpix2": 16.5,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": 20.0,
        }


def _frames():
    """Four frames, each with one bright square in a different corner."""
    frames = []
    corners = [(2, 2), (2, 22), (22, 2), (22, 22)]
    for i, (y, x) in enumerate(corners):
        data = np.full((32, 32), 10.0)
        data[y:y + 8, x:x + 8] = 1000.0
        frames.append(_Frame(data, f"2026-02-10T01:0{i}:00"))
    return frames


def _blank_grab(monkeypatch):
    """What an OpenGL canvas's grab() gives: the background, no image."""
    def grab(self, *args, **kwargs):
        pixmap = QPixmap(self.size())
        pixmap.fill(QColor(51, 51, 51))
        return pixmap

    monkeypatch.setattr(pg.PlotWidget, "grab", grab)
    monkeypatch.setattr(pg.GraphicsView, "grab", grab, raising=False)


def _window(monkeypatch):
    _app()
    _blank_grab(monkeypatch)
    win = SolarDataAnalysisWindow()
    win.resize(1300, 900)
    win.show()
    win._apply_loaded_frames(_frames(), paths=["a.fits"], metadata={})
    for _ in range(5):
        QApplication.processEvents()
    return win


def _pixels(path) -> np.ndarray:
    img = QImage(str(path)).convertToFormat(QImage.Format_RGB888)
    assert not img.isNull()
    ptr = img.constBits()
    row = img.bytesPerLine()
    buf = np.frombuffer(ptr, dtype=np.uint8, count=row * img.height()).reshape(img.height(), row)
    return buf[:, : img.width() * 3].reshape(img.height(), img.width(), 3).copy()


def test_export_plot_saves_the_displayed_frame_not_a_blank_grab(monkeypatch, tmp_path):
    win = _window(monkeypatch)
    win.colormap_combo.setCurrentText("gray")
    win._set_frame_index(2)
    QApplication.processEvents()

    path = tmp_path / "frame.png"
    win._save_canvas_plot(str(path))
    px = _pixels(path)

    assert len(np.unique(px.reshape(-1, 3), axis=0)) > 20  # axes, labels, image: not one flat colour
    bright = px[px.sum(axis=2) > 700]
    assert len(bright) > 100  # the bright square is in the export

    def coloured(pixels):
        spread = pixels.max(axis=2).astype(int) - pixels.min(axis=2).astype(int)
        return float((spread > 40).mean())

    assert coloured(px) < 0.002  # the gray colormap is applied ...
    win.colormap_combo.setCurrentText("inferno")
    QApplication.processEvents()
    inferno = tmp_path / "inferno.png"
    win._save_canvas_plot(str(inferno))
    assert coloured(_pixels(inferno)) > 0.03  # ... and so is another one (the square, the colorbar)
    win.colormap_combo.setCurrentText("gray")
    QApplication.processEvents()

    # Another frame exports another picture: the export follows the frame shown.
    win._set_frame_index(1)
    QApplication.processEvents()
    other = tmp_path / "other.png"
    win._save_canvas_plot(str(other))
    assert not np.array_equal(px, _pixels(other))
    win.close()


@pytest.mark.parametrize("suffix", [".pdf", ".svg", ".jpg", ".tiff"])
def test_export_plot_writes_every_offered_format(monkeypatch, tmp_path, suffix):
    win = _window(monkeypatch)
    path = tmp_path / f"frame{suffix}"
    win._save_canvas_plot(str(path))
    data = path.read_bytes()
    assert len(data) > 2000
    if suffix == ".pdf":
        assert data.startswith(b"%PDF")
    elif suffix == ".svg":
        assert b"<svg" in data and b"<image" in data  # the frame is embedded, axes are vector
    else:
        px = _pixels(path)
        assert len(np.unique(px.reshape(-1, 3), axis=0)) > 20
    win.close()

