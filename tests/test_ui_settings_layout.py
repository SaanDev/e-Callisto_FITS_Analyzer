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
            "noise_clip_low": -5.0,
            "noise_clip_high": 12.0,
            "noise_clip_scale": "signed_log",
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

    assert win.noise_clip_low == pytest.approx(-5.0)
    assert win.noise_clip_high == pytest.approx(12.0)
    assert win.noise_clip_scale == MainWindow.NOISE_CLIP_SCALE_SIGNED_LOG
    assert win.noise_log_scale_chk.isChecked() is True
    assert win.lower_slider.value() == win._noise_threshold_to_slider(-5.0, scale=MainWindow.NOISE_CLIP_SCALE_SIGNED_LOG)
    assert win.upper_slider.value() == win._noise_threshold_to_slider(12.0, scale=MainWindow.NOISE_CLIP_SCALE_SIGNED_LOG)
    assert win.units_db_radio.isChecked() is True
    assert win.time_ut_radio.isChecked() is True
    assert win.cmap_combo.currentText() == "inferno"
    assert win.title_edit.text() == "Preset Title"
    assert win.tick_font_spin.value() == 15
    assert win.canvas.ax.get_title() == "Preset Title"
    low_disp, high_disp, unit = win._noise_clip_display_values()
    assert win.lower_value_label.text() == win._format_noise_clip_threshold_digits(win.noise_clip_low)
    assert win.upper_value_label.text() == win._format_noise_clip_threshold_digits(win.noise_clip_high)
    assert win.lower_value_sub_label.isHidden() is False
    assert win.upper_value_sub_label.isHidden() is False
    assert win.lower_value_sub_label.text() == win._format_noise_clip_value(low_disp, unit)
    assert win.upper_value_sub_label.text() == win._format_noise_clip_value(high_disp, unit)

    win.close()


def test_reset_all_restores_sidebar_controls_to_defaults():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    win._set_noise_clip_state(-7.5, 14.5, scale=MainWindow.NOISE_CLIP_SCALE_SIGNED_LOG, sync_widgets=True)
    win.set_units_mode(True)
    win.set_axis_to_utc()
    win.cmap_combo.setCurrentText("inferno")
    win.remove_titles_chk.setChecked(True)
    win.title_bold_chk.setChecked(True)
    win.title_italic_chk.setChecked(True)
    win.axis_bold_chk.setChecked(True)
    win.axis_italic_chk.setChecked(True)
    win.ticks_bold_chk.setChecked(True)
    win.ticks_italic_chk.setChecked(True)
    win.title_edit.setText("Custom")
    if win.font_combo.count() > 1:
        win.font_combo.setCurrentIndex(1)
    win.tick_font_spin.setValue(17)
    win.axis_font_spin.setValue(18)
    win.title_font_spin.setValue(19)
    QApplication.processEvents()

    win.reset_all()
    QApplication.processEvents()

    assert win.raw_data is None
    assert win.noise_clip_low == pytest.approx(0.0)
    assert win.noise_clip_high == pytest.approx(0.0)
    assert win.noise_clip_scale == MainWindow.NOISE_CLIP_SCALE_LINEAR
    assert win.noise_log_scale_chk.isChecked() is False
    assert win.lower_value_label.text() == "0.00 Digits"
    assert win.upper_value_label.text() == "0.00 Digits"
    assert win.lower_value_sub_label.isHidden() is True
    assert win.upper_value_sub_label.isHidden() is True
    assert win.units_digits_radio.isChecked() is True
    assert win.units_db_radio.isChecked() is False
    assert win.time_sec_radio.isChecked() is True
    assert win.time_ut_radio.isChecked() is False
    assert win.cmap_combo.currentText() == "Custom"
    assert win.title_edit.text() == ""
    assert win.title_edit.isEnabled() is True
    assert win.font_combo.currentText() == "Default"
    assert win.remove_titles_chk.isChecked() is False
    assert win.title_bold_chk.isChecked() is False
    assert win.title_italic_chk.isChecked() is False
    assert win.axis_bold_chk.isChecked() is False
    assert win.axis_italic_chk.isChecked() is False
    assert win.ticks_bold_chk.isChecked() is False
    assert win.ticks_italic_chk.isChecked() is False
    assert win.tick_font_spin.value() == 11
    assert win.axis_font_spin.value() == 12
    assert win.title_font_spin.value() == 14
    assert win.graph_group.isEnabled() is False

    win.close()
