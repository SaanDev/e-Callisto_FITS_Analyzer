"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

class RFIControlDialog(QDialog):
    previewRequested = Signal(object)
    applyRequested = Signal(object)
    resetRequested = Signal()

    def __init__(self, initial: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RFI Cleaning")
        self.setModal(False)
        self.resize(420, 280)

        cfg = dict(initial or {})

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.enabled_chk = QCheckBox("Enable RFI cleaning")
        self.enabled_chk.setChecked(bool(cfg.get("enabled", True)))

        self.kernel_time_spin = QSpinBox()
        self.kernel_time_spin.setRange(1, 31)
        self.kernel_time_spin.setSingleStep(2)
        self.kernel_time_spin.setValue(int(cfg.get("kernel_time", 3)))

        self.kernel_freq_spin = QSpinBox()
        self.kernel_freq_spin.setRange(1, 31)
        self.kernel_freq_spin.setSingleStep(2)
        self.kernel_freq_spin.setValue(int(cfg.get("kernel_freq", 3)))

        self.z_thresh_spin = QDoubleSpinBox()
        self.z_thresh_spin.setRange(0.5, 20.0)
        self.z_thresh_spin.setSingleStep(0.5)
        self.z_thresh_spin.setDecimals(2)
        self.z_thresh_spin.setValue(float(cfg.get("channel_z_threshold", 6.0)))

        self.percentile_spin = QDoubleSpinBox()
        self.percentile_spin.setRange(90.0, 99.99)
        self.percentile_spin.setSingleStep(0.1)
        self.percentile_spin.setDecimals(2)
        self.percentile_spin.setValue(float(cfg.get("percentile_clip", 99.5)))

        self.masked_label = QLabel("Masked channels: 0")
        self.masked_label.setWordWrap(True)

        form.addRow(self.enabled_chk)
        form.addRow("Kernel (time)", self.kernel_time_spin)
        form.addRow("Kernel (freq)", self.kernel_freq_spin)
        form.addRow("Channel Z threshold", self.z_thresh_spin)
        form.addRow("Percentile clip", self.percentile_spin)
        form.addRow(self.masked_label)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.preview_btn = QPushButton("Preview")
        self.apply_btn = QPushButton("Apply")
        self.reset_btn = QPushButton("Reset")
        self.close_btn = QPushButton("Close")
        buttons.addWidget(self.preview_btn)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.preview_btn.clicked.connect(self._emit_preview)
        self.apply_btn.clicked.connect(self._emit_apply)
        self.reset_btn.clicked.connect(self.resetRequested.emit)
        self.close_btn.clicked.connect(self.close)

    def values(self) -> dict:
        return {
            "enabled": bool(self.enabled_chk.isChecked()),
            "kernel_time": int(self.kernel_time_spin.value()),
            "kernel_freq": int(self.kernel_freq_spin.value()),
            "channel_z_threshold": float(self.z_thresh_spin.value()),
            "percentile_clip": float(self.percentile_spin.value()),
        }

    def set_masked_channels(self, indices: list[int] | None) -> None:
        count = len(indices or [])
        if count == 0:
            self.masked_label.setText("Masked channels: 0")
        else:
            show = ", ".join(str(i) for i in (indices or [])[:8])
            if count > 8:
                show += ", ..."
            self.masked_label.setText(f"Masked channels: {count} ({show})")

    def _emit_preview(self):
        self.previewRequested.emit(self.values())

    def _emit_apply(self):
        self.applyRequested.emit(self.values())
