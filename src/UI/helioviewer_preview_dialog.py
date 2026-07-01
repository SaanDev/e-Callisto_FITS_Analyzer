"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Near-real-time SOHO/LASCO quicklook preview (Helioviewer). This is a browse
image, not analysis-grade FITS — it answers "what does the corona look like
right now", complementing the calibrated (but months-behind) VSO/SDAC archive
used by the main analysis window.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, QThread, QUrl, QObject, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.Backend.helioviewer import HelioviewerPreview, fetch_preview

_PREVIEW_PX = 512


class _PreviewWorker(QObject):
    finished = Signal(object)   # HelioviewerPreview
    failed = Signal(str)

    def __init__(self, detector: str, size_px: int = _PREVIEW_PX):
        super().__init__()
        self._detector = str(detector)
        self._size_px = int(size_px)

    @Slot()
    def run(self):
        try:
            preview = fetch_preview(self._detector, size_px=self._size_px)
            self.finished.emit(preview)
        except Exception as exc:  # noqa: BLE001 - surface any network/API error to the UI
            self.failed.emit(str(exc))


class HelioviewerPreviewDialog(QDialog):
    """Fetches and displays the newest available LASCO C2/C3 quicklook image."""

    def __init__(self, parent: QWidget | None = None, *, detector: str = "C2", theme: Any | None = None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("SOHO/LASCO Near-Real-Time Preview (Helioviewer)")
        self.resize(600, 660)

        self._preview: HelioviewerPreview | None = None
        self._thread: QThread | None = None
        self._worker: _PreviewWorker | None = None

        self._build_ui()
        det = str(detector or "C2").strip().upper()
        idx = self.detector_combo.findData(det)
        if idx >= 0:
            self.detector_combo.setCurrentIndex(idx)
        self._start_fetch()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Detector:"))
        self.detector_combo = QComboBox()
        self.detector_combo.addItem("SOHO/LASCO C2", userData="C2")
        self.detector_combo.addItem("SOHO/LASCO C3", userData="C3")
        top.addWidget(self.detector_combo)
        top.addStretch(1)
        self.refresh_btn = QPushButton("Refresh")
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)

        self.image_label = QLabel("Loading...")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(_PREVIEW_PX, _PREVIEW_PX)
        self.image_label.setStyleSheet("QLabel { background: #000; color: #ccc; border-radius: 4px; }")
        layout.addWidget(self.image_label, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.status_label)

        bottom = QHBoxLayout()
        self.open_browser_btn = QPushButton("Open in Browser")
        self.open_browser_btn.setEnabled(False)
        bottom.addWidget(self.open_browser_btn)
        bottom.addStretch(1)
        self.close_btn = QPushButton("Close")
        bottom.addWidget(self.close_btn)
        layout.addLayout(bottom)

        self.detector_combo.currentIndexChanged.connect(lambda _i: self._start_fetch())
        self.refresh_btn.clicked.connect(self._start_fetch)
        self.open_browser_btn.clicked.connect(self._open_in_browser)
        self.close_btn.clicked.connect(self.close)

    def _current_detector(self) -> str:
        return str(self.detector_combo.currentData() or "C2")

    def _is_loading(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.refresh_btn.setEnabled(enabled)
        self.detector_combo.setEnabled(enabled)

    def _start_fetch(self) -> None:
        if self._is_loading():
            return
        detector = self._current_detector()
        self._set_controls_enabled(False)
        self.open_browser_btn.setEnabled(False)
        self.image_label.setText(f"Loading latest SOHO/LASCO {detector} from Helioviewer...")
        self.status_label.setText("Contacting Helioviewer near-real-time service...")

        self._thread = QThread(self)
        self._worker = _PreviewWorker(detector)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_controls_enabled(True)

    @Slot(object)
    def _on_finished(self, preview_obj: object) -> None:
        preview = preview_obj if isinstance(preview_obj, HelioviewerPreview) else None
        if preview is None:
            self._on_failed("Unexpected preview payload.")
            return
        self._preview = preview
        pixmap = QPixmap()
        if not pixmap.loadFromData(preview.png_bytes, "PNG"):
            self._on_failed("Could not decode the preview image.")
            return
        scaled = pixmap.scaled(
            self.image_label.width(),
            self.image_label.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.open_browser_btn.setEnabled(True)
        self.status_label.setText(self._status_text(preview))

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._preview = None
        self.image_label.setText("Preview unavailable.")
        self.status_label.setText(
            f"<b>Could not load the Helioviewer preview.</b><br>{message}<br>"
            "Check your internet connection and try Refresh."
        )

    def _status_text(self, preview: HelioviewerPreview) -> str:
        info = preview.info
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age = now - info.date
        age_text = self._format_age(age.total_seconds())
        return (
            f"<b>{info.name}</b> &mdash; latest available frame<br>"
            f"Observed: {info.date:%Y-%m-%d %H:%M:%S} UTC ({age_text})<br>"
            f"Source: Helioviewer near-real-time quicklook (browse image, not analysis-grade FITS)."
        )

    @staticmethod
    def _format_age(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 90:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        rem = minutes % 60
        if hours < 48:
            return f"{hours} h {rem} min ago"
        return f"{hours // 24} day(s) ago"

    def _open_in_browser(self) -> None:
        if self._preview is None:
            return
        QDesktopServices.openUrl(QUrl(self._preview.image_url))

    def closeEvent(self, event):
        thread = self._thread
        if thread is not None:
            try:
                thread.quit()
                thread.wait(1500)
            except Exception:
                pass
        super().closeEvent(event)
