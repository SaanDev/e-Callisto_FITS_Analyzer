"""
e-CALLISTO FITS Analyzer
Version 2.3.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ArrowAnnotationDialog(QDialog):
    def __init__(
        self,
        *,
        title: str,
        initial_style: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(380, 240)

        style = dict(initial_style or {})
        self._color = str(style.get("color") or "#00d4ff")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setRange(0.5, 20.0)
        self.line_width_spin.setDecimals(1)
        self.line_width_spin.setSingleStep(0.5)
        self.line_width_spin.setValue(float(style.get("line_width", 1.5) or 1.5))
        form.addRow("Line Thickness", self.line_width_spin)

        self.head_size_spin = QDoubleSpinBox()
        self.head_size_spin.setRange(4.0, 64.0)
        self.head_size_spin.setDecimals(1)
        self.head_size_spin.setSingleStep(1.0)
        self.head_size_spin.setValue(float(style.get("arrow_head_size", 14.0) or 14.0))
        form.addRow("Arrow Head Size", self.head_size_spin)

        heads_row = QWidget()
        heads_layout = QHBoxLayout(heads_row)
        heads_layout.setContentsMargins(0, 0, 0, 0)
        heads_layout.setSpacing(10)
        self.start_head_chk = QCheckBox("Start")
        self.start_head_chk.setChecked(bool(style.get("arrow_start", False)))
        self.end_head_chk = QCheckBox("End")
        self.end_head_chk.setChecked(bool(style.get("arrow_end", True)))
        heads_layout.addWidget(self.start_head_chk)
        heads_layout.addWidget(self.end_head_chk)
        heads_layout.addStretch(1)
        form.addRow("Arrow Heads", heads_row)

        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        self.color_preview = QLabel()
        self.color_preview.setFixedWidth(54)
        self.color_preview.setMinimumHeight(24)
        self.color_preview.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Plain)
        self.color_code_label = QLabel()
        self.color_btn = QPushButton("Choose...")
        self.color_btn.clicked.connect(self._choose_color)
        color_layout.addWidget(self.color_preview, 0)
        color_layout.addWidget(self.color_code_label, 1)
        color_layout.addWidget(self.color_btn, 0)
        form.addRow("Color", color_row)

        root.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._update_color_preview()

    def _update_color_preview(self) -> None:
        color = QColor(self._color)
        if not color.isValid():
            color = QColor("#00d4ff")
            self._color = color.name()
        text_color = "#101010" if color.lightness() > 140 else "#f5f5f5"
        self.color_preview.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #888;"
        )
        self.color_code_label.setText(
            f"<span style='color:{text_color}; background:{color.name()}; padding:2px 6px;'>{color.name()}</span>"
        )

    def _choose_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Choose Arrow Color")
        if not chosen.isValid():
            return
        self._color = chosen.name()
        self._update_color_preview()

    def payload(self) -> dict[str, object]:
        return {
            "color": str(self._color or "#00d4ff"),
            "line_width": float(self.line_width_spin.value()),
            "arrow_head_size": float(self.head_size_spin.value()),
            "arrow_start": bool(self.start_head_chk.isChecked()),
            "arrow_end": bool(self.end_head_chk.isChecked()),
        }

    def accept(self) -> None:
        if not self.start_head_chk.isChecked() and not self.end_head_chk.isChecked():
            QMessageBox.information(self, "Arrow Annotation", "Enable at least one arrow head.")
            return
        super().accept()
