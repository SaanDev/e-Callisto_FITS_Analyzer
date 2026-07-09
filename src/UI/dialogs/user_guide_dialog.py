"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from src.UI.user_guide_content import USER_GUIDE_HTML, build_default_stylesheet
from src.version import APP_NAME


def _get_theme():
    app = QApplication.instance()
    if app is None:
        return None
    return app.property("theme_manager")


class UserGuideDialog(QDialog):
    """Modeless window that renders the in-app user guide.

    The guide HTML comes from ``src/UI/user_guide_content.py`` and is displayed
    in a ``QTextBrowser``. Colors follow the app theme and update live when the
    user switches between light and dark modes.
    """

    def __init__(self, parent=None, anchor: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} User Guide")
        self.resize(880, 720)

        self._theme = _get_theme()
        self._initial_anchor = str(anchor or "").strip()

        self._build_ui()
        self._apply_theme()

        if self._theme is not None:
            try:
                self._theme.themeChanged.connect(self._on_theme_changed)
            except Exception:
                pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        self.home_btn = QPushButton("Home")
        self.back_btn = QPushButton("Back")
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find in guide...")
        self.find_edit.setClearButtonEnabled(True)
        self.find_btn = QPushButton("Find Next")

        toolbar.addWidget(self.home_btn)
        toolbar.addWidget(self.back_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.find_edit, 1)
        toolbar.addWidget(self.find_btn)
        root.addLayout(toolbar)

        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setOpenLinks(True)
        root.addWidget(self.browser, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = QPushButton("Close")
        buttons.addWidget(self.close_btn)
        root.addLayout(buttons)

        self.home_btn.clicked.connect(self.go_home)
        self.back_btn.clicked.connect(self.browser.backward)
        self.find_btn.clicked.connect(self.find_next)
        self.find_edit.returnPressed.connect(self.find_next)
        self.close_btn.clicked.connect(self.close)

    # --------------------------------------------------------------- theme
    def _apply_theme(self):
        dark = bool(self._theme.is_dark()) if self._theme is not None else False
        # Set the document CSS first, then (re)load the HTML so the stylesheet
        # applies to the rendered content.
        self.browser.document().setDefaultStyleSheet(build_default_stylesheet(dark))
        self.browser.setHtml(USER_GUIDE_HTML)
        if self._initial_anchor:
            self.browser.scrollToAnchor(self._initial_anchor)

    def _on_theme_changed(self, _dark: bool = False):
        self._apply_theme()

    # ------------------------------------------------------------- actions
    def go_home(self):
        self.browser.scrollToAnchor("top")

    def find_next(self):
        text = self.find_edit.text().strip()
        if not text:
            return
        if not self.browser.find(text):
            # Wrap around to the top and try once more.
            cursor = self.browser.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.browser.setTextCursor(cursor)
            if not self.browser.find(text):
                QMessageBox.information(self, "Find", f'"{text}" was not found.')

    # ------------------------------------------------------------ lifecycle
    def show_anchor(self, anchor: str):
        """Bring the window forward and scroll to a named section."""

        anchor = str(anchor or "").strip()
        if anchor:
            self.browser.scrollToAnchor(anchor)
        self.show()
        self.raise_()
        self.activateWindow()
