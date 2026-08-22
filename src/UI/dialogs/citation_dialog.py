"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


RECOMMENDED_CITATION_TEXT = (
    "If you use e-CALLISTO FITS Analyzer in your research, please cite this article.\n\n"
    "G. L. S. S. Liyanage, J. Adassuriya, K. P. S. C. Jayaratne, C. Monstein, and "
    "P. K. Manoharan. \"e-CALLISTO FITS analyzer: a software framework for CALLISTO solar "
    "radio data.\" RAS Techniques and Instruments, 5, rzag056, 2026.\n"
    "DOI: 10.1093/rasti/rzag056\n"
    "URL: https://doi.org/10.1093/rasti/rzag056"
)

# Published in RAS Techniques and Instruments; supersedes the arXiv preprint
# entry used before v2.8.0. The citation key is deliberately unchanged so that
# manuscripts already referencing it keep resolving.
CITATION_BIBTEX = """@article{liyanage2026ecallistofitsanalyzersoftware,
      title={e-CALLISTO FITS analyzer: a software framework for CALLISTO solar radio data},
      author={Liyanage, G L S S and Adassuriya, J and Jayaratne, K P S C and Monstein, C and Manoharan, P K},
      journal={RAS Techniques and Instruments},
      volume={5},
      pages={rzag056},
      year={2026},
      month={01},
      issn={2752-8200},
      doi={10.1093/rasti/rzag056},
      url={https://doi.org/10.1093/rasti/rzag056},
}"""


class CitationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cite this Software")
        self.resize(760, 560)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        intro = QLabel(
            "If you use this software for research, please cite the paper below. "
            "You can copy either the formatted citation or the BibTeX entry."
        )
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(intro)

        citation_label = QLabel("Citation")
        citation_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(citation_label)

        self.citation_text_edit = QPlainTextEdit()
        self.citation_text_edit.setReadOnly(True)
        self.citation_text_edit.setPlainText(RECOMMENDED_CITATION_TEXT)
        root.addWidget(self.citation_text_edit)

        bibtex_label = QLabel("BibTeX")
        bibtex_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(bibtex_label)

        self.bibtex_text_edit = QPlainTextEdit()
        self.bibtex_text_edit.setReadOnly(True)
        self.bibtex_text_edit.setPlainText(CITATION_BIBTEX)
        root.addWidget(self.bibtex_text_edit, 1)

        buttons = QHBoxLayout()
        self.copy_citation_btn = QPushButton("Copy Citation")
        self.copy_bibtex_btn = QPushButton("Copy BibTeX")
        self.close_btn = QPushButton("Close")

        self.copy_citation_btn.clicked.connect(self.copy_citation_text)
        self.copy_bibtex_btn.clicked.connect(self.copy_bibtex_text)
        self.close_btn.clicked.connect(self.close)

        buttons.addWidget(self.copy_citation_btn)
        buttons.addWidget(self.copy_bibtex_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        root.addLayout(buttons)

    def _copy_to_clipboard(self, text: str, label: str):
        cb = QGuiApplication.clipboard()
        if cb is None:
            QMessageBox.warning(self, "Clipboard", "Clipboard is not available on this system.")
            return
        cb.setText(text)
        QMessageBox.information(self, "Copied", f"{label} copied to clipboard.")

    def copy_citation_text(self):
        self._copy_to_clipboard(self.citation_text_edit.toPlainText(), "Citation text")

    def copy_bibtex_text(self):
        self._copy_to_clipboard(self.bibtex_text_edit.toPlainText(), "BibTeX entry")
