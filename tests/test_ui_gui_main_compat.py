"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.UI.accelerated_plot_widget import pg
from src.UI.dialogs.analyze_dialog import AnalyzeDialog
from src.UI.dialogs.max_intensity_dialog import MaxIntensityPlotDialog
from src.UI.dialogs.type_ii_band_splitting_dialog import TypeIIBandSplittingDialog
from src.UI.gui_main import (
    AnalyzeDialog as LegacyAnalyzeDialog,
    MainWindow as LegacyMainWindow,
    MaxIntensityPlotDialog as LegacyMaxIntensityPlotDialog,
    TypeIIBandSplittingDialog as LegacyTypeIIBandSplittingDialog,
)
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def test_legacy_gui_main_exports_match_new_modules():
    assert LegacyMainWindow is MainWindow
    assert LegacyAnalyzeDialog is AnalyzeDialog
    assert LegacyMaxIntensityPlotDialog is MaxIntensityPlotDialog
    assert LegacyTypeIIBandSplittingDialog is TypeIIBandSplittingDialog


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

    type_ii_dlg = TypeIIBandSplittingDialog(
        np.arange(12, dtype=float).reshape(3, 4),
        np.array([100.0, 90.0, 80.0], dtype=float),
        np.array([1.0, 2.0, 3.0, 4.0], dtype=float),
        "demo.fit",
    )
    type_ii_dlg.close()


def test_type_ii_dialog_uses_pyqtgraph_plot_widget():
    _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    dlg = TypeIIBandSplittingDialog(
        np.arange(12, dtype=float).reshape(3, 4),
        np.array([100.0, 90.0, 80.0], dtype=float),
        np.array([1.0, 2.0, 3.0, 4.0], dtype=float),
        "demo.fit",
    )

    assert isinstance(dlg.plot_widget, pg.GraphicsLayoutWidget)
    assert dlg.plot_item is not None
    assert dlg.image_item is not None
    assert dlg.bvr_button is not None
    assert dlg.settings_button is not None
    assert dlg.add_points_button.icon().isNull() is False
    assert dlg.undo_button.icon().isNull() is False
    assert dlg.clear_button.icon().isNull() is False
    assert dlg.fit_active_button.icon().isNull() is False
    assert dlg.fit_both_button.icon().isNull() is False
    assert dlg.bvr_button.icon().isNull() is False
    assert dlg.settings_button.icon().isNull() is False

    dlg.close()
