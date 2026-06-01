"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTimeEdit,
    QVBoxLayout,
)


def _qtime_from_seconds_of_day(seconds: float) -> QTime:
    total = int(round(float(seconds))) % 86400
    hour = total // 3600
    minute = (total % 3600) // 60
    second = total % 60
    return QTime(hour, minute, second)


def _seconds_of_day(value: QTime) -> int:
    return int(value.hour()) * 3600 + int(value.minute()) * 60 + int(value.second())


class DisplayRangeDialog(QDialog):
    def __init__(
        self,
        *,
        time_min_s: float,
        time_max_s: float,
        freq_min_mhz: float,
        freq_max_mhz: float,
        initial_time_start_s: float,
        initial_time_stop_s: float,
        initial_freq_start_mhz: float,
        initial_freq_stop_mhz: float,
        ut_start_sec: float | None = None,
        initial_mode: str = "seconds",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Set Display Range")
        self.setModal(True)

        self._ut_start_sec = float(ut_start_sec) if ut_start_sec is not None else None

        full_time_lo = float(min(time_min_s, time_max_s))
        full_time_hi = float(max(time_min_s, time_max_s))
        full_freq_lo = float(min(freq_min_mhz, freq_max_mhz))
        full_freq_hi = float(max(freq_min_mhz, freq_max_mhz))

        self.seconds_radio = QRadioButton("Use seconds from file start", self)
        self.ut_radio = QRadioButton("Use UT clock time", self)
        self.ut_radio.setEnabled(self._ut_start_sec is not None)
        self.seconds_radio.setChecked(True)
        if initial_mode == "ut" and self.ut_radio.isEnabled():
            self.ut_radio.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.seconds_radio)
        self.mode_group.addButton(self.ut_radio)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.seconds_radio)
        mode_row.addWidget(self.ut_radio)
        mode_row.addStretch(1)

        self.start_seconds_spin = self._seconds_spin(initial_time_start_s)
        self.stop_seconds_spin = self._seconds_spin(initial_time_stop_s)
        seconds_form = QFormLayout()
        seconds_form.addRow("Start", self.start_seconds_spin)
        seconds_form.addRow("Stop", self.stop_seconds_spin)
        self.seconds_group = QGroupBox("Seconds", self)
        self.seconds_group.setLayout(seconds_form)

        self.start_ut_edit = QTimeEdit(self)
        self.stop_ut_edit = QTimeEdit(self)
        for edit in (self.start_ut_edit, self.stop_ut_edit):
            edit.setDisplayFormat("HH:mm:ss")
            edit.setEnabled(self._ut_start_sec is not None)
        if self._ut_start_sec is not None:
            self.start_ut_edit.setTime(_qtime_from_seconds_of_day(self._ut_start_sec + initial_time_start_s))
            self.stop_ut_edit.setTime(_qtime_from_seconds_of_day(self._ut_start_sec + initial_time_stop_s))
        ut_form = QFormLayout()
        ut_form.addRow("Start", self.start_ut_edit)
        ut_form.addRow("Stop", self.stop_ut_edit)
        self.ut_group = QGroupBox("UT", self)
        self.ut_group.setEnabled(self._ut_start_sec is not None)
        self.ut_group.setLayout(ut_form)

        self.start_freq_spin = self._frequency_spin(initial_freq_start_mhz)
        self.stop_freq_spin = self._frequency_spin(initial_freq_stop_mhz)
        freq_form = QFormLayout()
        freq_form.addRow("Start", self.start_freq_spin)
        freq_form.addRow("Stop", self.stop_freq_spin)
        freq_group = QGroupBox("Frequency", self)
        freq_group.setLayout(freq_form)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.seconds_radio.toggled.connect(lambda _checked: self._sync_mode_enabled())
        self.ut_radio.toggled.connect(lambda _checked: self._sync_mode_enabled())

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Time range: {full_time_lo:.3f} to {full_time_hi:.3f} s", self))
        layout.addWidget(QLabel(f"Frequency range: {full_freq_lo:.3f} to {full_freq_hi:.3f} MHz", self))
        if self._ut_start_sec is None:
            layout.addWidget(QLabel("UT input is unavailable because this file has no TIME-OBS value.", self))
        layout.addLayout(mode_row)
        layout.addWidget(self.seconds_group)
        layout.addWidget(self.ut_group)
        layout.addWidget(freq_group)
        layout.addWidget(self.button_box)
        self._sync_mode_enabled()

    def _sync_mode_enabled(self) -> None:
        seconds_enabled = bool(self.seconds_radio.isChecked())
        ut_enabled = bool(self.ut_radio.isChecked() and self._ut_start_sec is not None)
        self.seconds_group.setEnabled(seconds_enabled)
        self.ut_group.setEnabled(ut_enabled)
        self.start_seconds_spin.setEnabled(seconds_enabled)
        self.stop_seconds_spin.setEnabled(seconds_enabled)
        self.start_ut_edit.setEnabled(ut_enabled)
        self.stop_ut_edit.setEnabled(ut_enabled)

    @staticmethod
    def _seconds_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
        spin.setSingleStep(1.0)
        spin.setSuffix(" s")
        spin.setValue(float(value))
        return spin

    @staticmethod
    def _frequency_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
        spin.setSingleStep(1.0)
        spin.setSuffix(" MHz")
        spin.setValue(float(value))
        return spin

    def uses_ut(self) -> bool:
        return bool(self.ut_radio.isChecked() and self.ut_radio.isEnabled())

    def seconds_range(self) -> tuple[float, float]:
        return float(self.start_seconds_spin.value()), float(self.stop_seconds_spin.value())

    def ut_seconds_of_day_range(self) -> tuple[int, int]:
        return _seconds_of_day(self.start_ut_edit.time()), _seconds_of_day(self.stop_ut_edit.time())

    def frequency_range(self) -> tuple[float, float]:
        return float(self.start_freq_spin.value()), float(self.stop_freq_spin.value())
