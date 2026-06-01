"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout


class LightCurveFrequencyDialog(QDialog):
    def __init__(
        self,
        frequency_min_mhz: float,
        frequency_max_mhz: float,
        *,
        initial_frequency_mhz: float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Plot Light Curve")
        self.setModal(True)

        lo = float(min(frequency_min_mhz, frequency_max_mhz))
        hi = float(max(frequency_min_mhz, frequency_max_mhz))
        if not hi > lo:
            hi = lo + 1e-6

        if initial_frequency_mhz is None:
            initial = (lo + hi) / 2.0
        else:
            initial = min(max(float(initial_frequency_mhz), lo), hi)

        self.frequency_spin = QDoubleSpinBox(self)
        self.frequency_spin.setDecimals(3)
        self.frequency_spin.setRange(lo, hi)
        self.frequency_spin.setSingleStep(max((hi - lo) / 100.0, 0.001))
        self.frequency_spin.setSuffix(" MHz")
        self.frequency_spin.setValue(initial)
        self.frequency_spin.selectAll()

        form = QFormLayout()
        form.addRow("Frequency", self.frequency_spin)

        self.button_box = QDialogButtonBox(self)
        self.plot_button = self.button_box.addButton("Plot", QDialogButtonBox.AcceptRole)
        self.button_box.addButton(QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Available range: {lo:.3f} to {hi:.3f} MHz", self))
        layout.addLayout(form)
        layout.addWidget(self.button_box)

    def frequency_mhz(self) -> float:
        return float(self.frequency_spin.value())
