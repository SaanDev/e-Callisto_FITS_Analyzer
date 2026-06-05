"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.Backend.measurements import MeasurementResult


class MeasurementReadout(QGroupBox):
    clearRequested = Signal()

    def __init__(self, title: str = "Measurement", parent=None):
        super().__init__(title, parent)
        self._plain_text = "No measurement."

        self.result_label = QLabel(self._plain_text, self)
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(self.result_label.textInteractionFlags())

        self.copy_btn = QPushButton("Copy", self)
        self.clear_btn = QPushButton("Clear", self)
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        self.clear_btn.clicked.connect(self.clearRequested.emit)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        button_row.addWidget(self.copy_btn)
        button_row.addWidget(self.clear_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(self.result_label)
        layout.addLayout(button_row)

        self.set_empty()

    def set_empty(self, text: str = "No measurement.") -> None:
        self._plain_text = str(text or "No measurement.")
        self.result_label.setText(self._plain_text)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)

    def set_pending(self, text: str) -> None:
        self._plain_text = str(text or "")
        self.result_label.setText(self._plain_text)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(True)

    def set_error(self, text: str) -> None:
        self._plain_text = str(text or "Measurement failed.")
        self.result_label.setText(self._plain_text)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(True)

    def set_measurement(
        self,
        result: MeasurementResult,
        *,
        title: str | None = None,
        time_formatter: Callable[[float], str] | None = None,
    ) -> None:
        fmt_time = time_formatter or (lambda value: f"{float(value):.3f} s")
        prefix = f"{str(title).strip()}\n" if str(title or "").strip() else ""
        text = (
            f"{prefix}"
            f"P1: {fmt_time(result.point1.time_s)}, {result.point1.frequency_mhz:.3f} MHz\n"
            f"P2: {fmt_time(result.point2.time_s)}, {result.point2.frequency_mhz:.3f} MHz\n"
            f"Duration: {result.duration_s:.3f} s\n"
            f"Frequency drift: {result.frequency_delta_mhz:.3f} MHz\n"
            f"Slope: {result.slope_mhz_s:.6f} MHz/s"
        )
        self._plain_text = text
        self.result_label.setText(text.replace("\n", "<br>"))
        self.copy_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

    def copy_to_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._plain_text)

    def text(self) -> str:
        return self._plain_text
