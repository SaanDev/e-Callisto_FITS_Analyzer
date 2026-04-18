"""
Type II graph settings dialog.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from src.UI.font_utils import normalize_font_family, preferred_ui_font_family


class _ColorButton(QPushButton):
    valueChanged = Signal(str)

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self._title = str(title or "Choose Color")
        self._color = str(color or "#ffffff")
        self.clicked.connect(self._choose_color)
        self._update_ui()

    def color(self) -> str:
        return str(self._color or "#ffffff")

    def set_color(self, value: str) -> None:
        color = QColor(str(value or ""))
        if not color.isValid():
            color = QColor("#ffffff")
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
            color = QColor("#ffffff")
            self._color = color.name().lower()
        fg = "#101010" if color.lightness() > 150 else "#f5f5f5"
        self.setText(self._color)
        self.setStyleSheet(
            f"background-color: {self._color}; color: {fg}; border: 1px solid #808080; padding: 4px 8px;"
        )


class TypeIIGraphSettingsDialog(QDialog):
    previewStyleChanged = Signal(dict)
    styleApplied = Signal(dict)

    def __init__(self, *, initial_style: dict[str, Any], defaults: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Type II Graph Settings")
        self.setModal(False)
        self.resize(460, 640)

        self._defaults = dict(defaults or {})
        self._applied_style = dict(initial_style or self._defaults)
        self._syncing = False
        self._app_default_font_family = (
            normalize_font_family(QApplication.font().family().strip()) or preferred_ui_font_family()
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_text_group())
        root.addWidget(
            self._build_series_group(
                "Upper Band",
                prefix="upper",
                line_label="Fit Line",
                marker_label="Picked Points",
            )
        )
        root.addWidget(
            self._build_series_group(
                "Lower Band",
                prefix="lower",
                line_label="Fit Line",
                marker_label="Picked Points",
            )
        )
        root.addWidget(
            self._build_series_group(
                "B vs R",
                prefix="bvr",
                line_label="Fit Line",
                marker_label="Markers",
            )
        )
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
        self._set_controls_from_style(self._applied_style)

    def _build_text_group(self) -> QGroupBox:
        box = QGroupBox("Text Formatting")
        layout = QFormLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.font_combo = QFontComboBox()
        layout.addRow("Font Family", self.font_combo)

        self.title_font_spin, self.title_bold_chk, self.title_italic_chk = self._font_style_row()
        layout.addRow("Title", self._pack_row(self.title_font_spin, self.title_bold_chk, self.title_italic_chk))

        self.axis_font_spin, self.axis_bold_chk, self.axis_italic_chk = self._font_style_row()
        layout.addRow("Axis Labels", self._pack_row(self.axis_font_spin, self.axis_bold_chk, self.axis_italic_chk))

        self.tick_font_spin, self.ticks_bold_chk, self.ticks_italic_chk = self._font_style_row()
        layout.addRow("Tick Labels", self._pack_row(self.tick_font_spin, self.ticks_bold_chk, self.ticks_italic_chk))
        return box

    def _build_series_group(self, title: str, *, prefix: str, line_label: str, marker_label: str) -> QGroupBox:
        box = QGroupBox(title)
        layout = QFormLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        line_color = _ColorButton(f"Choose {title} line color", "#ffffff")
        line_width = self._spin_box(1, 12)
        layout.addRow(
            line_label,
            self._pack_row(line_color, self._value_tag("Width"), line_width),
        )

        marker_color = _ColorButton(f"Choose {title} marker color", "#ffffff")
        marker_size = self._spin_box(2, 32)
        layout.addRow(
            marker_label,
            self._pack_row(marker_color, self._value_tag("Size"), marker_size),
        )

        setattr(self, f"{prefix}_line_color_btn", line_color)
        setattr(self, f"{prefix}_line_width_spin", line_width)
        setattr(self, f"{prefix}_marker_color_btn", marker_color)
        setattr(self, f"{prefix}_marker_size_spin", marker_size)
        return box

    @staticmethod
    def _spin_box(minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(int(minimum), int(maximum))
        return spin

    def _font_style_row(self) -> tuple[QSpinBox, QCheckBox, QCheckBox]:
        spin = self._spin_box(6, 48)
        bold = QCheckBox("Bold")
        italic = QCheckBox("Italic")
        return spin, bold, italic

    @staticmethod
    def _value_tag(text: str) -> QLabel:
        label = QLabel(str(text))
        label.setFrameStyle(QFrame.Shape.NoFrame)
        return label

    @staticmethod
    def _pack_row(*widgets: QWidget) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        return holder

    def _connect_signals(self) -> None:
        controls = [
            self.font_combo,
            self.title_font_spin,
            self.title_bold_chk,
            self.title_italic_chk,
            self.axis_font_spin,
            self.axis_bold_chk,
            self.axis_italic_chk,
            self.tick_font_spin,
            self.ticks_bold_chk,
            self.ticks_italic_chk,
            self.upper_line_width_spin,
            self.upper_marker_size_spin,
            self.lower_line_width_spin,
            self.lower_marker_size_spin,
            self.bvr_line_width_spin,
            self.bvr_marker_size_spin,
        ]
        for control in controls:
            if hasattr(control, "valueChanged"):
                control.valueChanged.connect(self._on_control_changed)
            elif hasattr(control, "toggled"):
                control.toggled.connect(self._on_control_changed)
        self.font_combo.currentFontChanged.connect(self._on_control_changed)
        for control in (
            self.upper_line_color_btn,
            self.upper_marker_color_btn,
            self.lower_line_color_btn,
            self.lower_marker_color_btn,
            self.bvr_line_color_btn,
            self.bvr_marker_color_btn,
        ):
            control.valueChanged.connect(self._on_control_changed)

    def _style_from_controls(self) -> dict[str, Any]:
        font_family = normalize_font_family(self.font_combo.currentFont().family().strip())
        if font_family == self._app_default_font_family and not str(self._applied_style.get("font_family") or "").strip():
            font_family = ""
        return {
            "font_family": font_family,
            "title_font_px": int(self.title_font_spin.value()),
            "axis_label_font_px": int(self.axis_font_spin.value()),
            "tick_font_px": int(self.tick_font_spin.value()),
            "title_bold": bool(self.title_bold_chk.isChecked()),
            "title_italic": bool(self.title_italic_chk.isChecked()),
            "axis_bold": bool(self.axis_bold_chk.isChecked()),
            "axis_italic": bool(self.axis_italic_chk.isChecked()),
            "ticks_bold": bool(self.ticks_bold_chk.isChecked()),
            "ticks_italic": bool(self.ticks_italic_chk.isChecked()),
            "upper_line_color": self.upper_line_color_btn.color(),
            "upper_marker_color": self.upper_marker_color_btn.color(),
            "upper_line_width": int(self.upper_line_width_spin.value()),
            "upper_marker_size": int(self.upper_marker_size_spin.value()),
            "lower_line_color": self.lower_line_color_btn.color(),
            "lower_marker_color": self.lower_marker_color_btn.color(),
            "lower_line_width": int(self.lower_line_width_spin.value()),
            "lower_marker_size": int(self.lower_marker_size_spin.value()),
            "bvr_line_color": self.bvr_line_color_btn.color(),
            "bvr_marker_color": self.bvr_marker_color_btn.color(),
            "bvr_line_width": int(self.bvr_line_width_spin.value()),
            "bvr_marker_size": int(self.bvr_marker_size_spin.value()),
        }

    def current_style(self) -> dict[str, Any]:
        return dict(self._style_from_controls())

    def load_applied_style(self, style: dict[str, Any]) -> None:
        normalized = dict(style or self._defaults)
        self._applied_style = normalized
        self._set_controls_from_style(normalized)

    def _set_controls_from_style(self, style: dict[str, Any]) -> None:
        src = dict(style or {})
        self._syncing = True
        try:
            family = str(src.get("font_family") or "").strip()
            if family:
                self.font_combo.setCurrentFont(QFont(family))
            else:
                self.font_combo.setCurrentFont(QFont(self._app_default_font_family))
            self.title_font_spin.setValue(int(src.get("title_font_px", 14) or 14))
            self.axis_font_spin.setValue(int(src.get("axis_label_font_px", 12) or 12))
            self.tick_font_spin.setValue(int(src.get("tick_font_px", 11) or 11))
            self.title_bold_chk.setChecked(bool(src.get("title_bold", False)))
            self.title_italic_chk.setChecked(bool(src.get("title_italic", False)))
            self.axis_bold_chk.setChecked(bool(src.get("axis_bold", False)))
            self.axis_italic_chk.setChecked(bool(src.get("axis_italic", False)))
            self.ticks_bold_chk.setChecked(bool(src.get("ticks_bold", False)))
            self.ticks_italic_chk.setChecked(bool(src.get("ticks_italic", False)))
            self.upper_line_color_btn.set_color(str(src.get("upper_line_color", "#ff5a1f")))
            self.upper_marker_color_btn.set_color(str(src.get("upper_marker_color", "#ff8c42")))
            self.upper_line_width_spin.setValue(int(src.get("upper_line_width", 2) or 2))
            self.upper_marker_size_spin.setValue(int(src.get("upper_marker_size", 9) or 9))
            self.lower_line_color_btn.set_color(str(src.get("lower_line_color", "#0ea5e9")))
            self.lower_marker_color_btn.set_color(str(src.get("lower_marker_color", "#38bdf8")))
            self.lower_line_width_spin.setValue(int(src.get("lower_line_width", 2) or 2))
            self.lower_marker_size_spin.setValue(int(src.get("lower_marker_size", 9) or 9))
            self.bvr_line_color_btn.set_color(str(src.get("bvr_line_color", "#f59e0b")))
            self.bvr_marker_color_btn.set_color(str(src.get("bvr_marker_color", "#34d399")))
            self.bvr_line_width_spin.setValue(int(src.get("bvr_line_width", 2) or 2))
            self.bvr_marker_size_spin.setValue(int(src.get("bvr_marker_size", 8) or 8))
        finally:
            self._syncing = False

    def _on_control_changed(self, *_args) -> None:
        if self._syncing:
            return
        self.previewStyleChanged.emit(self.current_style())

    def _apply(self) -> None:
        style = self.current_style()
        self._applied_style = dict(style)
        self.styleApplied.emit(style)

    def _reset_to_defaults(self) -> None:
        self._set_controls_from_style(self._defaults)
        self.previewStyleChanged.emit(self.current_style())

    def closeEvent(self, event) -> None:
        if self.current_style() != self._applied_style:
            self.previewStyleChanged.emit(dict(self._applied_style))
        event.accept()
