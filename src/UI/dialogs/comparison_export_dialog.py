"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)


EXPORT_LAYOUT_VISIBLE = "visible"
EXPORT_LAYOUT_GRID = "grid"


@dataclass(frozen=True)
class ComparisonExportOptions:
    layout: str = EXPORT_LAYOUT_VISIBLE
    columns: int | None = None
    title: str = "e-CALLISTO Multi-Station Comparison"
    dpi: int = 300


class ComparisonExportDialog(QDialog):
    def __init__(
        self,
        *,
        initial_options: ComparisonExportOptions | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Export Comparison")
        self.setModal(True)
        self.setMinimumWidth(440)

        options = initial_options or ComparisonExportOptions()

        self.visible_radio = QRadioButton("Visible View", self)
        self.visible_radio.setToolTip("Export the comparison exactly as it appears in the current window.")
        self.grid_radio = QRadioButton("Grid / Table Layout", self)
        self.grid_radio.setToolTip("Export a compact publication-style matrix of all comparison panels.")
        self.layout_group = QButtonGroup(self)
        self.layout_group.addButton(self.visible_radio)
        self.layout_group.addButton(self.grid_radio)
        if options.layout == EXPORT_LAYOUT_GRID:
            self.grid_radio.setChecked(True)
        else:
            self.visible_radio.setChecked(True)

        layout_group = QGroupBox("Layout", self)
        layout_box = QVBoxLayout(layout_group)
        layout_box.addWidget(self.visible_radio)
        layout_box.addWidget(self.grid_radio)

        self.columns_combo = QComboBox(self)
        self.columns_combo.addItem("Auto", None)
        for value in (2, 3, 4):
            self.columns_combo.addItem(str(value), value)
        index = self.columns_combo.findData(options.columns)
        self.columns_combo.setCurrentIndex(max(0, index))

        self.title_edit = QLineEdit(str(options.title or "e-CALLISTO Multi-Station Comparison"), self)
        self.dpi_spin = QSpinBox(self)
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setSuffix(" DPI")
        self.dpi_spin.setValue(int(options.dpi))

        self.grid_group = QGroupBox("Grid / Table Options", self)
        grid_form = QFormLayout(self.grid_group)
        grid_form.addRow("Columns", self.columns_combo)
        grid_form.addRow("Overall title", self.title_edit)
        grid_form.addRow("Raster resolution", self.dpi_spin)

        self.note_label = QLabel(
            "Grid exports use shared time and frequency axes and a white publication-style background.",
            self,
        )
        self.note_label.setWordWrap(True)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.visible_radio.toggled.connect(self._sync_enabled)
        self.grid_radio.toggled.connect(self._sync_enabled)

        outer = QVBoxLayout(self)
        outer.addWidget(layout_group)
        outer.addWidget(self.grid_group)
        outer.addWidget(self.note_label)
        outer.addWidget(self.button_box)
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        self.grid_group.setEnabled(self.grid_radio.isChecked())
        self.note_label.setVisible(self.grid_radio.isChecked())

    def selected_options(self) -> ComparisonExportOptions:
        return ComparisonExportOptions(
            layout=EXPORT_LAYOUT_GRID if self.grid_radio.isChecked() else EXPORT_LAYOUT_VISIBLE,
            columns=self.columns_combo.currentData(),
            title=str(self.title_edit.text() or "").strip() or "e-CALLISTO Multi-Station Comparison",
            dpi=int(self.dpi_spin.value()),
        )
