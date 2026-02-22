"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.UI.dialogs.analyze_dialog import AnalyzeDialog
from src.UI.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.UI.gui_main import (
    AnalyzeDialog as LegacyAnalyzeDialog,
    MainWindow as LegacyMainWindow,
    MaxIntensityPlotDialog as LegacyMaxIntensityPlotDialog,
)
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_legacy_gui_main_exports_match_new_modules():
    assert LegacyMainWindow is MainWindow
    assert LegacyAnalyzeDialog is AnalyzeDialog
    assert LegacyMaxIntensityPlotDialog is MaxIntensityPlotDialog


def test_new_module_classes_construct_and_close():
    _app()

    win = MainWindow(theme=None)
    win.close()

    max_dlg = MaxIntensityPlotDialog(
        np.arange(8, dtype=float),
        np.linspace(80.0, 70.0, 8),
        "demo.fit",
    )
    max_dlg.close()

    analyze_dlg = AnalyzeDialog(
        np.arange(1, 8, dtype=float),
        np.linspace(90.0, 70.0, 7),
        "demo.fit",
    )
    analyze_dlg.close()
