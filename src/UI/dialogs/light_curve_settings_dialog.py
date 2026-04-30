"""
Light-curve overlay settings dialog.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _ColorButton(QPushButton):
    valueChanged = Signal(str)

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self._title = str(title or "Choose Color")
        self._color = str(color or "#00e5ff")
        self.clicked.connect(self._choose_color)
        self._update_ui()

    def color(self) -> str:
        return str(self._color or "#00e5ff")

    def set_color(self, value: str) -> None:
        color = QColor(str(value or ""))
        if not color.isValid():
            color = QColor("#00e5ff")
        self._color = color.name().lower()
        self._update_ui()

    def _choose_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, self._title)
        if not chosen.isValid():
            return
        self._color = chosen.name().lower()
        self._update_ui()
        self.valueChanged.emit(self._color)

    def _update_ui(self) -> None:
        color = QColor(self._color)
        if not color.isValid():
            color = QColor("#00e5ff")
            self._color = color.name().lower()
        fg = "#101010" if color.lightness() > 150 else "#f5f5f5"
        self.setText(self._color)
        self.setStyleSheet(
            f"background-color: {self._color}; color: {fg}; border: 1px solid #808080; padding: 4px 8px;"
        )


class LightCurveSettingsDialog(QDialog):
    settingsChanged = Signal(dict)
    settingsApplied = Signal(dict)

    def __init__(self, *, initial_settings: dict[str, Any], defaults: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Light Curve Settings")
        self.setModal(False)
        self.resize(420, 280)

        self._defaults = dict(defaults or {})
        self._settings = self._normalize_settings(initial_settings or self._defaults)
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.single_curve_chk = QCheckBox("Single light curve")
        self.multi_curve_chk = QCheckBox("Multiple light curves")
        form.addRow("Mode", self._pack_row(self.single_curve_chk, self.multi_curve_chk))

        self.color_btn = _ColorButton("Choose light curve color", self._settings.get("line_color", "#00e5ff"))
        form.addRow("Color", self.color_btn)

        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setRange(0.5, 12.0)
        self.line_width_spin.setSingleStep(0.5)
        self.line_width_spin.setDecimals(1)
        form.addRow("Thickness", self.line_width_spin)

        self.vertical_scale_spin = QDoubleSpinBox()
        self.vertical_scale_spin.setRange(0.1, 5.0)
        self.vertical_scale_spin.setSingleStep(0.1)
        self.vertical_scale_spin.setDecimals(1)
        form.addRow("Vertical Scale", self.vertical_scale_spin)

        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.1, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)
        form.addRow("Opacity", self.opacity_spin)

        self.line_style_combo = QComboBox()
        self.line_style_combo.addItems(["solid", "dashed", "dotted"])
        form.addRow("Line Style", self.line_style_combo)

        self.show_frequency_label_chk = QCheckBox("Show frequency labels")
        form.addRow("Labels", self.show_frequency_label_chk)

        root.addLayout(form)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.apply_button = QPushButton("Apply")
        self.reset_button = QPushButton("Reset to Defaults")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.reset_button)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self.apply_button.clicked.connect(self._apply)
        self.reset_button.clicked.connect(self._reset_to_defaults)
        self.close_button.clicked.connect(self.close)
        self._connect_signals()
        self.load_settings(self._settings)

    @staticmethod
    def _pack_row(*widgets: QWidget) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        return holder

    def _connect_signals(self) -> None:
        self.single_curve_chk.toggled.connect(self._on_mode_toggled)
        self.multi_curve_chk.toggled.connect(self._on_mode_toggled)
        self.color_btn.valueChanged.connect(self._on_control_changed)
        self.line_width_spin.valueChanged.connect(self._on_control_changed)
        self.vertical_scale_spin.valueChanged.connect(self._on_control_changed)
        self.opacity_spin.valueChanged.connect(self._on_control_changed)
        self.line_style_combo.currentTextChanged.connect(self._on_control_changed)
        self.show_frequency_label_chk.toggled.connect(self._on_control_changed)

    def _normalize_settings(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        defaults = {
            "mode": "single",
            "line_color": "#00e5ff",
            "line_width": 2.0,
            "show_frequency_label": True,
            "vertical_scale": 1.0,
            "opacity": 0.95,
            "line_style": "solid",
        }
        defaults.update(self._defaults or {})
        out = dict(defaults)
        if isinstance(raw, dict):
            out.update(raw)

        out["mode"] = "multiple" if str(out.get("mode", "")).lower() == "multiple" else "single"
        color = QColor(str(out.get("line_color") or ""))
        out["line_color"] = color.name().lower() if color.isValid() else "#00e5ff"
        try:
            out["line_width"] = min(max(float(out.get("line_width", 2.0)), 0.5), 12.0)
        except Exception:
            out["line_width"] = 2.0
        try:
            out["vertical_scale"] = min(max(float(out.get("vertical_scale", 1.0)), 0.1), 5.0)
        except Exception:
            out["vertical_scale"] = 1.0
        try:
            out["opacity"] = min(max(float(out.get("opacity", 0.95)), 0.1), 1.0)
        except Exception:
            out["opacity"] = 0.95
        style = str(out.get("line_style") or "solid").lower()
        out["line_style"] = style if style in {"solid", "dashed", "dotted"} else "solid"
        out["show_frequency_label"] = bool(out.get("show_frequency_label", True))
        return out

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = self._normalize_settings(settings)
        self._syncing = True
        try:
            multiple = self._settings["mode"] == "multiple"
            self.single_curve_chk.setChecked(not multiple)
            self.multi_curve_chk.setChecked(multiple)
            self.color_btn.set_color(self._settings["line_color"])
            self.line_width_spin.setValue(float(self._settings["line_width"]))
            self.vertical_scale_spin.setValue(float(self._settings["vertical_scale"]))
            self.opacity_spin.setValue(float(self._settings["opacity"]))
            self.line_style_combo.setCurrentText(str(self._settings["line_style"]))
            self.show_frequency_label_chk.setChecked(bool(self._settings["show_frequency_label"]))
        finally:
            self._syncing = False

    def settings(self) -> dict[str, Any]:
        return self._settings_from_controls()

    def _settings_from_controls(self) -> dict[str, Any]:
        return self._normalize_settings(
            {
                "mode": "multiple" if self.multi_curve_chk.isChecked() else "single",
                "line_color": self.color_btn.color(),
                "line_width": float(self.line_width_spin.value()),
                "show_frequency_label": bool(self.show_frequency_label_chk.isChecked()),
                "vertical_scale": float(self.vertical_scale_spin.value()),
                "opacity": float(self.opacity_spin.value()),
                "line_style": str(self.line_style_combo.currentText() or "solid"),
            }
        )

    def _on_mode_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        sender = self.sender()
        if not checked:
            if not self.single_curve_chk.isChecked() and not self.multi_curve_chk.isChecked():
                self._syncing = True
                try:
                    if sender is self.multi_curve_chk:
                        self.multi_curve_chk.setChecked(True)
                    else:
                        self.single_curve_chk.setChecked(True)
                finally:
                    self._syncing = False
            return
        self._syncing = True
        try:
            if sender is self.single_curve_chk:
                self.multi_curve_chk.setChecked(False)
            else:
                self.single_curve_chk.setChecked(False)
        finally:
            self._syncing = False
        self._on_control_changed()

    def _on_control_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._settings = self._settings_from_controls()
        self.settingsChanged.emit(dict(self._settings))

    def _apply(self) -> None:
        self._settings = self._settings_from_controls()
        self.settingsApplied.emit(dict(self._settings))
        self.settingsChanged.emit(dict(self._settings))

    def _reset_to_defaults(self) -> None:
        self.load_settings(dict(self._defaults))
        self._settings = self._settings_from_controls()
        self.settingsChanged.emit(dict(self._settings))
