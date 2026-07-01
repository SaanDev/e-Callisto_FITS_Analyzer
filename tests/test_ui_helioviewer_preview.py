"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import src.UI.helioviewer_preview_dialog as hvd
from src.Backend.helioviewer import HelioviewerImageInfo, HelioviewerPreview
from src.UI.helioviewer_preview_dialog import HelioviewerPreviewDialog


def _app():
    return QApplication.instance() or QApplication([])


def _qt_png_bytes() -> bytes:
    """A guaranteed Qt-decodable PNG for the preview image label."""
    pixmap = QPixmap(16, 16)
    pixmap.fill()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _fake_preview(detector: str) -> HelioviewerPreview:
    info = HelioviewerImageInfo(
        detector=detector, source_id=4 if detector == "C2" else 5,
        date=datetime(2026, 7, 1, 5, 24, 23), name=f"LASCO {detector}",
        scale=11.9, width=1024, height=1024,
    )
    return HelioviewerPreview(
        info=info, png_bytes=_qt_png_bytes(), image_scale=23.8, size_px=512,
        image_url="https://api.helioviewer.org/v2/takeScreenshot/?display=true",
    )


def _wait(dialog, timeout_s: float = 3.0):
    deadline = time.monotonic() + timeout_s
    while dialog._is_loading() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


def test_helioviewer_dialog_renders_preview(monkeypatch):
    _app()
    monkeypatch.setattr(hvd, "fetch_preview", lambda detector, **kw: _fake_preview(detector))
    dialog = HelioviewerPreviewDialog(detector="C2")
    _wait(dialog)

    assert dialog._preview is not None
    assert dialog.open_browser_btn.isEnabled()
    pixmap = dialog.image_label.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    text = dialog.status_label.text()
    assert "LASCO C2" in text and "min ago" in text
    dialog.close()


def test_helioviewer_dialog_reports_errors(monkeypatch):
    _app()

    def _boom(detector, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(hvd, "fetch_preview", _boom)
    dialog = HelioviewerPreviewDialog(detector="C3")
    _wait(dialog)

    assert dialog._preview is None
    assert not dialog.open_browser_btn.isEnabled()
    assert "network down" in dialog.status_label.text()
    dialog.close()


def test_helioviewer_dialog_defaults_to_requested_detector(monkeypatch):
    _app()
    monkeypatch.setattr(hvd, "fetch_preview", lambda detector, **kw: _fake_preview(detector))
    dialog = HelioviewerPreviewDialog(detector="C3")
    _wait(dialog)
    assert dialog._current_detector() == "C3"
    dialog.close()
