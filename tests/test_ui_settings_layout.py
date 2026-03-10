"""
e-CALLISTO FITS Analyzer
Version 2.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QToolBar

from src.Backend.presets import build_preset
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _load_demo_plot(win: MainWindow):
    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 90.0, 80.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 2.0, 3.0, 4.0],
            [2.0, 4.0, 6.0, 8.0],
            [3.0, 6.0, 9.0, 12.0],
        ],
        dtype=float,
    )
    win.ut_start_sec = 3661
    win.plot_data(win.raw_data, title="Raw")
    QApplication.processEvents()


def test_main_window_has_no_analysis_summary_panel_or_sidebar():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()

    assert hasattr(win, "side_scroll") is False
    assert hasattr(win, "sidebar_toggle_btn") is False
    assert win.analysis_summary_group is None
    assert win.analysis_summary_label is None

    win._analysis_session = {
        "analyzer": {
            "fit_params": {"a": 12.5, "b": -0.45, "r2": 0.98},
            "fold": 2,
            "shock_summary": {"fold": 2, "avg_shock_speed_km_s": 850.0},
        }
    }
    win._refresh_analysis_summary_panel()

    win.close()


def test_display_toolbar_row_controls_update_units_and_time_modes():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    assert win.display_toolbar_widget is not None
    assert hasattr(win, "display_toolbar") is False
    toolbars = win.findChildren(QToolBar)
    assert len(toolbars) == 1
    assert toolbars[0] is win.main_toolbar
    assert win.main_toolbar.iconSize().width() == 34
    assert win.main_toolbar.iconSize().height() == 34
    toolbar_texts = {label.text() for label in win.display_toolbar_widget.findChildren(QLabel) if label.text()}
    section_titles = {group.title() for group in win.display_toolbar_widget.findChildren(QGroupBox) if group.title()}
    assert {"Noise Clipping Thresholds", "Units"}.issubset(section_titles)
    assert {"Lower", "Upper", "Intensity", "Time"}.issubset(toolbar_texts)

    win.units_db_radio.setChecked(True)
    QApplication.processEvents()
    assert win.use_db is True
    assert win._colorbar_label_text == "Intensity [dB]"

    win.time_ut_radio.setChecked(True)
    QApplication.processEvents()
    assert win.use_utc is True
    assert win.canvas.ax.get_xlabel() == "Time [UT]"

    win.time_sec_radio.setChecked(True)
    QApplication.processEvents()
    assert win.use_utc is False
    assert win.canvas.ax.get_xlabel() == "Time [s]"

    win.close()


def test_settings_menu_reuses_non_modal_window_and_applies_live_changes():
    _app()
    win = MainWindow(theme=None)

    dlg = win._settings_dialog
    assert dlg.isVisible() is False
    assert dlg.graph_group.isEnabled() is False

    win.open_settings_action.trigger()
    QApplication.processEvents()
    assert dlg.isVisible() is True

    dlg.close()
    QApplication.processEvents()
    assert dlg.isVisible() is False

    win.open_settings_action.trigger()
    QApplication.processEvents()
    assert win._settings_dialog is dlg
    assert dlg.isVisible() is True

    _load_demo_plot(win)
    assert dlg.graph_group.isEnabled() is True

    dlg.title_edit.setText("Custom Spectrum")
    QApplication.processEvents()
    assert win.canvas.ax.get_title() == "Custom Spectrum"

    dlg.remove_titles_chk.setChecked(True)
    QApplication.processEvents()
    assert win.canvas.ax.get_title() == ""
    assert win.canvas.ax.get_xlabel() == ""

    dlg.close()
    win.close()


def test_apply_preset_updates_rehomed_toolbar_and_settings_controls():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    preset = build_preset(
        "Compact UI",
        {
            "lower_slider": -5,
            "upper_slider": 12,
            "use_db": True,
            "use_utc": True,
            "cmap": "inferno",
            "graph": {
                "title_override": "Preset Title",
                "font_family": "Default",
                "tick_font_px": 15,
                "axis_label_font_px": 16,
                "title_font_px": 18,
                "title_bold": True,
                "title_italic": False,
                "axis_bold": False,
                "axis_italic": False,
                "ticks_bold": False,
                "ticks_italic": False,
                "remove_titles": False,
            },
        },
    )

    assert win._apply_preset_payload(preset) is True
    QApplication.processEvents()

    assert win.lower_slider.value() == -5
    assert win.upper_slider.value() == 12
    assert win.units_db_radio.isChecked() is True
    assert win.time_ut_radio.isChecked() is True
    assert win.cmap_combo.currentText() == "inferno"
    assert win.title_edit.text() == "Preset Title"
    assert win.tick_font_spin.value() == 15
    assert win.canvas.ax.get_title() == "Preset Title"

    win.close()
