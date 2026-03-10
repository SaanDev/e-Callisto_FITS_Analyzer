"""
e-CALLISTO FITS Analyzer
Version 2.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionLabel")
    return label


def _spin_row(label_text: str, spin: QSpinBox) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    label = QLabel(label_text)
    label.setWordWrap(False)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    spin.setMinimumWidth(90)
    spin.setMaximumWidth(110)
    spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    row.addWidget(label, 1)
    row.addWidget(spin, 0)
    return widget


def _style_row(label_text: str, cb_bold: QCheckBox, cb_italic: QCheckBox) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    label = QLabel(label_text)
    label.setWordWrap(False)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    row.addWidget(label, 1)
    row.addWidget(cb_bold, 0)
    row.addWidget(cb_italic, 0)
    return widget


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.resize(420, 580)
        self._allow_close = False

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.graph_group = QGroupBox("Graph Properties")
        self.graph_group.setEnabled(False)
        self.graph_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        graph_layout = QVBoxLayout(self.graph_group)
        graph_layout.setContentsMargins(12, 12, 12, 12)
        graph_layout.setSpacing(8)

        graph_layout.addWidget(_section_label("Appearance"))

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(
            [
                "Custom",
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "turbo",
                "RdYlBu",
                "jet",
                "cubehelix",
            ]
        )
        graph_layout.addWidget(QLabel("Colormap"))
        graph_layout.addWidget(self.cmap_combo)

        self.font_combo = QComboBox()
        self.font_combo.addItem("Default")
        for family in sorted(QFontDatabase.families()):
            self.font_combo.addItem(family)

        graph_layout.addWidget(QLabel("Font family"))
        graph_layout.addWidget(self.font_combo)

        graph_layout.addSpacing(8)
        graph_layout.addWidget(_section_label("Text"))

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Custom title (leave empty for default)")
        self.title_edit.setMinimumHeight(20)
        graph_layout.addWidget(QLabel("Graph title"))
        graph_layout.addWidget(self.title_edit)

        self.remove_titles_chk = QCheckBox("Remove Titles")
        graph_layout.addWidget(self.remove_titles_chk)

        graph_layout.addSpacing(8)
        graph_layout.addWidget(_section_label("Font sizes"))

        self.tick_font_spin = QSpinBox()
        self.tick_font_spin.setRange(6, 60)
        self.tick_font_spin.setValue(11)
        graph_layout.addWidget(_spin_row("Tick labels (px)", self.tick_font_spin))

        self.axis_font_spin = QSpinBox()
        self.axis_font_spin.setRange(6, 60)
        self.axis_font_spin.setValue(12)
        graph_layout.addWidget(_spin_row("Axis labels (px)", self.axis_font_spin))

        self.title_font_spin = QSpinBox()
        self.title_font_spin.setRange(6, 80)
        self.title_font_spin.setValue(14)
        graph_layout.addWidget(_spin_row("Title (px)", self.title_font_spin))

        graph_layout.addSpacing(8)
        graph_layout.addWidget(_section_label("Text style"))

        self.title_bold_chk = QCheckBox("Bold")
        self.title_italic_chk = QCheckBox("Italic")
        graph_layout.addWidget(_style_row("Title", self.title_bold_chk, self.title_italic_chk))

        self.axis_bold_chk = QCheckBox("Bold")
        self.axis_italic_chk = QCheckBox("Italic")
        graph_layout.addWidget(_style_row("Axis labels", self.axis_bold_chk, self.axis_italic_chk))

        self.ticks_bold_chk = QCheckBox("Bold")
        self.ticks_italic_chk = QCheckBox("Italic")
        graph_layout.addWidget(_style_row("Tick labels", self.ticks_bold_chk, self.ticks_italic_chk))

        root.addWidget(self.graph_group, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.hide)
        buttons.addWidget(self.close_btn)
        root.addLayout(buttons)

    def closeEvent(self, event):
        if self._allow_close:
            super().closeEvent(event)
            return
        self.hide()
        event.ignore()

    def shutdown(self):
        self._allow_close = True
        self.close()
