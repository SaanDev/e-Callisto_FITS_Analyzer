"""
Simple FITS header viewer + export-to-txt dialog.
"""

from __future__ import annotations

import os

from astropy.io import fits
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)


class FitsHeaderViewerDialog(QDialog):
    def __init__(self, header: fits.Header, *, title: str = "FITS Header", default_name: str = "fits_header.txt", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 650)

        self._header = header if header is not None else fits.Header()
        self._default_name = default_name or "fits_header.txt"

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setPlainText(self._header.tostring(sep="\n", endcard=True, padding=False))

        self.save_btn = QPushButton("Save as .txt")
        self.save_btn.clicked.connect(self.save_as_txt)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(self.save_btn)
        btns.addWidget(self.close_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.text)
        layout.addLayout(btns)
        self.setLayout(layout)

        self.setWindowModality(Qt.WindowModality.ApplicationModal)

    def save_as_txt(self):
        start = self._default_name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save FITS Header",
            start,
            "Text files (*.txt)",
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"

        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText())
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", f"Could not save header:\n{e}")
            return

        QMessageBox.information(self, "Saved", f"Header saved:\n{path}")

