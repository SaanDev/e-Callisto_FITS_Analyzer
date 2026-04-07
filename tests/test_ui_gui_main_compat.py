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

from PySide6.QtCore import QObject, QPointF, Signal
from PySide6.QtGui import QImage
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
    assert dlg.save_plot_button is not None
    assert dlg.bvr_button is not None
    assert dlg.settings_button is not None
    assert dlg.add_points_button.icon().isNull() is False
    assert dlg.undo_button.icon().isNull() is False
    assert dlg.clear_button.icon().isNull() is False
    assert dlg.fit_active_button.icon().isNull() is False
    assert dlg.fit_both_button.icon().isNull() is False
    assert dlg.save_plot_button.icon().isNull() is False
    assert dlg.bvr_button.icon().isNull() is False
    assert dlg.settings_button.icon().isNull() is False

    dlg.close()


def test_type_ii_dialog_maps_image_to_time_and_frequency_edges():
    _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    data = np.arange(12, dtype=float).reshape(3, 4)
    freqs = np.array([100.0, 90.0, 80.0], dtype=float)
    times = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
    dlg = TypeIIBandSplittingDialog(data, freqs, times, "demo.fit")
    dlg.show()
    QApplication.processEvents()

    top_left = dlg.image_item.mapToParent(QPointF(0.0, 0.0))
    bottom_right = dlg.image_item.mapToParent(QPointF(float(data.shape[1]), float(data.shape[0])))

    assert np.isclose(top_left.x(), 0.5)
    assert np.isclose(top_left.y(), 105.0)
    assert np.isclose(bottom_right.x(), 4.5)
    assert np.isclose(bottom_right.y(), 75.0)

    dlg.close()


def test_type_ii_dialog_plot_background_follows_theme():
    app = _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    class _Theme(QObject):
        themeChanged = Signal(bool)

        def __init__(self, dark: bool):
            super().__init__()
            self._dark = bool(dark)

        def is_dark(self) -> bool:
            return self._dark

        def set_dark(self, dark: bool) -> None:
            self._dark = bool(dark)
            self.themeChanged.emit(self._dark)

    previous = app.property("theme_manager")
    theme = _Theme(False)
    app.setProperty("theme_manager", theme)
    try:
        dlg = TypeIIBandSplittingDialog(
            np.arange(12, dtype=float).reshape(3, 4),
            np.array([100.0, 90.0, 80.0], dtype=float),
            np.array([1.0, 2.0, 3.0, 4.0], dtype=float),
            "demo.fit",
        )
        dlg.show()
        QApplication.processEvents()

        lightness_light = dlg.plot_widget.backgroundBrush().color().lightness()
        assert lightness_light > 200

        theme.set_dark(True)
        QApplication.processEvents()
        lightness_dark = dlg.plot_widget.backgroundBrush().color().lightness()
        assert lightness_dark < 80

        dlg.close()
    finally:
        app.setProperty("theme_manager", previous)


def test_type_ii_dialog_save_plot_exports_png(tmp_path, monkeypatch):
    _app()

    dlg = TypeIIBandSplittingDialog(
        np.arange(12, dtype=float).reshape(3, 4),
        np.array([100.0, 90.0, 80.0], dtype=float),
        np.array([1.0, 2.0, 3.0, 4.0], dtype=float),
        "demo.fit",
    )

    out_path = tmp_path / "type_ii_plot.png"
    monkeypatch.setattr(
        "src.UI.dialogs.type_ii_band_splitting_dialog.pick_export_path",
        lambda *_a, **_k: (str(out_path), "png"),
    )
    monkeypatch.setattr(
        "src.UI.dialogs.type_ii_band_splitting_dialog.QMessageBox.information",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "src.UI.dialogs.type_ii_band_splitting_dialog.QMessageBox.critical",
        lambda *_a, **_k: None,
    )

    dlg._save_plot()

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    image = QImage(str(out_path))
    assert image.isNull() is False
    assert image.width() >= 2000

    dlg.close()
