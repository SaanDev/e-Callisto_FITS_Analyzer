"""
e-CALLISTO FITS Analyzer
Version 2.3.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class TextAnnotationDialog(QDialog):
    def __init__(
        self,
        *,
        title: str,
        initial_text: str = "",
        initial_style: dict | None = None,
        require_text: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 260)

        style = dict(initial_style or {})
        self._require_text = bool(require_text)
        self._color = str(style.get("color") or "#00d4ff")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self.text_edit = QLineEdit(str(initial_text or ""))
        self.text_edit.setPlaceholderText("Annotation text")
        form.addRow("Text", self.text_edit)

        self.font_combo = QFontComboBox()
        font_family = str(style.get("font_family") or "").strip()
        if font_family:
            self.font_combo.setCurrentFont(QFont(font_family))
        form.addRow("Font", self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(int(style.get("font_size", 12) or 12))
        form.addRow("Font Size", self.font_size_spin)

        style_row = QWidget()
        style_layout = QHBoxLayout(style_row)
        style_layout.setContentsMargins(0, 0, 0, 0)
        style_layout.setSpacing(10)
        self.bold_chk = QCheckBox("Bold")
        self.bold_chk.setChecked(bool(style.get("font_bold", False)))
        self.italic_chk = QCheckBox("Italic")
        self.italic_chk.setChecked(bool(style.get("font_italic", False)))
        style_layout.addWidget(self.bold_chk)
        style_layout.addWidget(self.italic_chk)
        style_layout.addStretch(1)
        form.addRow("Font Style", style_row)

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
        chosen = QColorDialog.getColor(QColor(self._color), self, "Choose Text Color")
        if not chosen.isValid():
            return
        self._color = chosen.name()
        self._update_color_preview()

    def payload(self) -> dict[str, object]:
        return {
            "text": self.text_edit.text().strip(),
            "color": str(self._color or "#00d4ff"),
            "font_family": self.font_combo.currentFont().family().strip(),
            "font_size": int(self.font_size_spin.value()),
            "font_bold": bool(self.bold_chk.isChecked()),
            "font_italic": bool(self.italic_chk.isChecked()),
        }

    def accept(self) -> None:
        if self._require_text and not self.text_edit.text().strip():
            QMessageBox.information(self, "Text Annotation", "Enter text for the label.")
            return
        super().accept()
