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

from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea, QToolBar

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


def test_main_window_restores_sidebar_and_analysis_summary():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()

    assert isinstance(win.side_scroll, QScrollArea)
    assert win.sidebar_toggle_btn is not None
    assert win.slider_group.title() == "Noise Clipping Thresholds"
    assert win.units_group_box.title() == "Units"
    assert win.graph_group.title() == "Graph Properties"
    assert win.analysis_summary_group.title() == "Analysis Summary"
    assert hasattr(win, "display_toolbar_widget") is False
    assert hasattr(win, "_settings_dialog") is False

    win._analysis_session = {
        "analyzer": {
            "fit_params": {"a": 12.5, "b": -0.45, "r2": 0.98},
            "fold": 2,
            "shock_summary": {"fold": 2, "avg_shock_speed_km_s": 850.0},
        }
    }
    win._refresh_analysis_summary_panel()

    assert "Fit:" in win.analysis_summary_label.text()
    assert "R2:" in win.analysis_summary_label.text()
    win.close()


def test_sidebar_toggle_and_controls_still_work():
    _app()
    win = MainWindow(theme=None)
    win.show()
    _load_demo_plot(win)
    QApplication.processEvents()

    toolbars = win.findChildren(QToolBar)
    assert len(toolbars) == 1
    assert toolbars[0].iconSize().width() == 36
    assert toolbars[0].iconSize().height() == 36

    section_titles = {group.title() for group in win.side_scroll.findChildren(QGroupBox) if group.title()}
    assert {"Noise Clipping Thresholds", "Units", "Graph Properties", "Analysis Summary"}.issubset(section_titles)

    assert win.side_scroll.isVisible() is True
    win.toggle_left_sidebar()
    QApplication.processEvents()
    assert win.side_scroll.isVisible() is False
    assert win.sidebar_toggle_btn.text() == "▶"

    win.toggle_left_sidebar()
    QApplication.processEvents()
    assert win.side_scroll.isVisible() is True
    assert win.sidebar_toggle_btn.text() == "◀"

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


def test_apply_preset_updates_restored_sidebar_controls():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    preset = build_preset(
        "Sidebar UI",
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
