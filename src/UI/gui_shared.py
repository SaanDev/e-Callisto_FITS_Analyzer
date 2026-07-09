"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import re
import sys

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
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


def screen_available_geometry(widget=None):
    """Available geometry of the screen the widget is (or will be) shown on."""
    screen = None
    try:
        if widget is not None:
            screen = widget.screen()
    except Exception:
        screen = None
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def clamp_minimum_size_to_screen(window, min_width: int, min_height: int, *, fraction: float = 0.90) -> None:
    """Apply a minimum size that never exceeds the current screen.

    Tool windows were designed against large desktop monitors; on laptop
    displays (a MacBook Air is ~1280x832 logical) the designed minimums can be
    bigger than the screen itself, leaving windows that cannot be shrunk or
    even fully seen. Cap the minimum at a fraction of the available screen so
    the user always keeps control of the window.
    """
    avail = screen_available_geometry(window)
    if avail is not None:
        min_width = min(int(min_width), max(320, int(avail.width() * fraction)))
        min_height = min(int(min_height), max(240, int(avail.height() * fraction)))
    window.setMinimumSize(int(min_width), int(min_height))


def fit_window_to_screen(
    window,
    width: int,
    height: int,
    *,
    min_width: int = 0,
    min_height: int = 0,
    width_fraction: float = 0.94,
    height_fraction: float = 0.90,
) -> None:
    """Open *window* at (width, height), clamped to its screen.

    The requested size is the design size for a large monitor; small displays
    get the largest size that still fits with a margin for the menu bar/dock.
    Optional minimums are clamped the same way so the window stays resizable.
    """
    avail = screen_available_geometry(window)
    if avail is not None:
        width = min(int(width), max(320, int(avail.width() * width_fraction)))
        height = min(int(height), max(240, int(avail.height() * height_fraction)))
    if min_width or min_height:
        clamp_minimum_size_to_screen(
            window,
            min(int(min_width or width), int(width)),
            min(int(min_height or height), int(height)),
        )
    window.resize(int(width), int(height))


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
