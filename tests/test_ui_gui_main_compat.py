"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import warnings

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


def test_type_ii_dialog_refresh_without_points_emits_no_scatter_warnings():
    _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dlg = TypeIIBandSplittingDialog(
            np.arange(12, dtype=float).reshape(3, 4),
            np.array([100.0, 90.0, 80.0], dtype=float),
            np.array([1.0, 2.0, 3.0, 4.0], dtype=float),
            "demo.fit",
        )
        dlg.show()
        QApplication.processEvents()
        dlg._refresh_plot()
        QApplication.processEvents()
        dlg.close()

    messages = [str(w.message) for w in caught]
    assert all("All-NaN slice encountered" not in message for message in messages)


def test_type_ii_dialog_bvr_button_switches_plot_mode():
    _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    dlg = TypeIIBandSplittingDialog(
        np.arange(20, dtype=float).reshape(4, 5),
        np.array([120.0, 110.0, 100.0, 90.0], dtype=float),
        np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float),
        "demo.fit",
        session={
            "source": {"filename": "demo.fit", "shape": [4, 5]},
            "analyzer": {
                "fold": 2,
                "shock_summary": {
                    "start_freq_mhz": 63.0,
                    "initial_shock_speed_km_s": 920.0,
                    "avg_shock_speed_km_s": 760.0,
                    "initial_shock_height_rs": 1.31,
                    "avg_shock_height_rs": 1.47,
                    "avg_drift_mhz_s": -0.12,
                    "avg_drift_err_mhz_s": 0.01,
                },
            },
        },
    )
    dlg._upper_points = [(1.0, 100.0), (2.0, 76.0), (3.0, 64.0)]
    dlg._lower_points = [(1.0, 82.0), (2.0, 68.0), (3.0, 57.0)]
    dlg._fit_both_bands()

    dlg.bvr_button.setChecked(True)
    QApplication.processEvents()

    assert dlg._plot_mode == "bvr"
    assert dlg.image_item.isVisible() is False
    assert dlg.bvr_scatter_item.isVisible() is True
    assert "Shock Height" in dlg.plot_item.getAxis("bottom").labelText
    assert "Magnetic Field" in dlg.plot_item.getAxis("left").labelText

    dlg.bvr_button.setChecked(False)
    QApplication.processEvents()

    assert dlg._plot_mode == "spectrum"
    assert dlg.image_item.isVisible() is True

    dlg.close()


def test_type_ii_settings_dialog_live_preview_reverts_on_close_without_apply():
    _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    dlg = TypeIIBandSplittingDialog(
        np.arange(12, dtype=float).reshape(3, 4),
        np.array([100.0, 90.0, 80.0], dtype=float),
        np.array([1.0, 2.0, 3.0, 4.0], dtype=float),
        "demo.fit",
    )
    settings = dlg._open_or_focus_settings_dialog()
    assert settings is dlg._open_or_focus_settings_dialog()

    settings.title_font_spin.setValue(20)
    settings.upper_line_width_spin.setValue(5)
    QApplication.processEvents()

    assert dlg.plot_item.titleLabel.item.font().pixelSize() == 20
    assert dlg.upper_curve_item.opts["pen"].widthF() == pytest.approx(5.0)

    settings.close()
    QApplication.processEvents()

    assert dlg.plot_item.titleLabel.item.font().pixelSize() == 14
    assert dlg.upper_curve_item.opts["pen"].widthF() == pytest.approx(2.0)
    assert dlg.session_state()["type_ii"]["plot_style"]["upper_line_width"] == 2

    dlg.close()


def test_type_ii_settings_dialog_apply_persists_style_for_spectrum_and_bvr():
    _app()
    if pg is None:
        pytest.skip("PyQtGraph is unavailable")

    dlg = TypeIIBandSplittingDialog(
        np.arange(20, dtype=float).reshape(4, 5),
        np.array([120.0, 110.0, 100.0, 90.0], dtype=float),
        np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float),
        "demo.fit",
        session={
            "source": {"filename": "demo.fit", "shape": [4, 5]},
            "analyzer": {
                "fold": 2,
                "shock_summary": {
                    "start_freq_mhz": 63.0,
                    "initial_shock_speed_km_s": 920.0,
                    "avg_shock_speed_km_s": 760.0,
                    "initial_shock_height_rs": 1.31,
                    "avg_shock_height_rs": 1.47,
                    "avg_drift_mhz_s": -0.12,
                    "avg_drift_err_mhz_s": 0.01,
                },
            },
        },
    )
    dlg._upper_points = [(1.0, 100.0), (2.0, 76.0), (3.0, 64.0)]
    dlg._lower_points = [(1.0, 82.0), (2.0, 68.0), (3.0, 57.0)]
    dlg._fit_both_bands()

    settings = dlg._open_or_focus_settings_dialog()
    settings.upper_line_width_spin.setValue(4)
    settings.bvr_line_width_spin.setValue(6)
    settings.bvr_marker_size_spin.setValue(13)
    settings.apply_button.click()
    QApplication.processEvents()

    assert dlg.upper_curve_item.opts["pen"].widthF() == pytest.approx(4.0)
    assert dlg.session_state()["type_ii"]["plot_style"]["upper_line_width"] == 4

    dlg.bvr_button.setChecked(True)
    QApplication.processEvents()

    assert dlg.bvr_curve_item.opts["pen"].widthF() == pytest.approx(6.0)
    assert dlg.bvr_scatter_item.opts["size"] == 13

    settings.reset_button.click()
    QApplication.processEvents()
    assert dlg.bvr_curve_item.opts["pen"].widthF() == pytest.approx(2.0)

    settings.apply_button.click()
    QApplication.processEvents()
    assert dlg.session_state()["type_ii"]["plot_style"]["bvr_line_width"] == 2
    assert dlg.session_state()["type_ii"]["plot_style"]["bvr_marker_size"] == 8

    dlg.close()
