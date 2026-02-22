"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import re
import sys

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QLayout,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

IS_LINUX = sys.platform.startswith("linux")

_linux_msgbox_fixer = None

class _LinuxMessageBoxFixer(QObject):
    def eventFilter(self, obj, event):
        if IS_LINUX and event.type() == QEvent.Show and isinstance(obj, QMessageBox):
            # Make the main text label wrap and give it room
            label = obj.findChild(QLabel, "qt_msgbox_label")
            if label:
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setMinimumWidth(520)

            # Allow the dialog to grow to its content
            obj.setSizeGripEnabled(True)
            obj.setMinimumWidth(560)

            lay = obj.layout()
            if lay:
                lay.setSizeConstraint(QLayout.SetMinimumSize)

            QTimer.singleShot(0, obj.adjustSize)

        return super().eventFilter(obj, event)


def _install_linux_msgbox_fixer():
    global _linux_msgbox_fixer
    if not IS_LINUX:
        return
    app = QApplication.instance()
    if app is None or _linux_msgbox_fixer is not None:
        return
    _linux_msgbox_fixer = _LinuxMessageBoxFixer(app)
    app.installEventFilter(_linux_msgbox_fixer)


def start_combine(self):
    QTimer.singleShot(100, self.combine_files)  # delays execution and avoids UI freeze


def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        # Packaged app
        return os.path.join(sys._MEIPASS, relative_path)
    # Development mode
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def _ext_from_filter(name_filter: str) -> str:
    m = re.search(r"\*\.(\w+)", name_filter or "")
    return m.group(1).lower() if m else ""


def pick_export_path(parent, caption: str, default_name: str, filters: str, default_filter: str = None):
    """
    Returns (path, ext).
    Linux uses a QFileDialog instance (non-native) so selectedNameFilter is reliable.
    Windows/macOS keep using getSaveFileName.
    """
    if IS_LINUX:
        dlg = QFileDialog(parent, caption)
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setNameFilters(filters.split(";;"))
        if default_filter:
            dlg.selectNameFilter(default_filter)
        dlg.selectFile(default_name)

        # Important for Linux reliability
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)

        if not dlg.exec():
            return "", ""
        path = dlg.selectedFiles()[0]
        chosen_filter = dlg.selectedNameFilter()
    else:
        path, chosen_filter = QFileDialog.getSaveFileName(parent, caption, default_name, filters)
        if not path:
            return "", ""

    ext = os.path.splitext(path)[1].lstrip(".").lower()

    # If user didn’t type an extension, take it from the selected filter
    if not ext:
        ext = _ext_from_filter(chosen_filter) or "png"
        path = f"{path}.{ext}"

    return path, ext


class MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=10, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()
