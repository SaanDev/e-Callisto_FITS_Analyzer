"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QToolBar, QToolButton

from src.Backend.presets import build_preset
from src.UI.main_window import MainWindow
from src.UI.widgets.collapsible_sections import (
    collapsible_groups,
    collapsible_sections,
    group_base_title,
    section_for,
    set_group_expanded,
)


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
    # Sidebar sections are accordion cards, so the visible title carries an
    # arrow; the section's identity is its base title.
    assert group_base_title(win.slider_group) == "Noise Clipping Thresholds"
    assert group_base_title(win.units_group_box) == "Units"
    assert group_base_title(win.graph_group) == "Graph Properties"
    assert group_base_title(win.analysis_summary_group) == "Analysis Summary"
    assert group_base_title(win.timeline_group) == "Timeline"
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

    # The group box's own title is cleared when it is wrapped; the section
    # header carries the name now.
    section_titles = {
        group_base_title(group) for group in collapsible_groups(win.side_scroll.widget())
    }
    assert {
        "Timeline", "Noise Clipping Thresholds", "Units", "Graph Properties", "Analysis Summary"
    }.issubset(section_titles)

    # The sidebar collapses by animating its width to 0 inside a splitter
    # (same method as the Solar Image Analysis window); the scroll area stays
    # "visible" and the arrow handle flips direction to signal the state.
    assert win._sidebar_collapsed is False
    win.toggle_left_sidebar()
    QApplication.processEvents()
    assert win._sidebar_collapsed is True
    assert win.sidebar_toggle_btn.arrowType() == Qt.RightArrow

    win.toggle_left_sidebar()
    QApplication.processEvents()
    assert win._sidebar_collapsed is False
    assert win.sidebar_toggle_btn.arrowType() == Qt.LeftArrow

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


def test_sidebar_colormap_dropdown_includes_bone_r():
    _app()
    win = MainWindow(theme=None)

    options = [win.cmap_combo.itemText(i) for i in range(win.cmap_combo.count())]
    assert "bone_r" in options

    win.close()


def test_loaded_raw_fits_uses_previous_noise_clipping_defaults(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_default_preset_name", lambda: "")
    data = np.arange(200, dtype=float).reshape(10, 20)

    win._apply_loaded_dataset(
        data=data,
        freqs=np.linspace(300.0, 210.0, data.shape[0]),
        time=np.arange(data.shape[1], dtype=float),
        filename="raw.fit",
        header0=None,
        source_path=None,
        ut_start_sec=0.0,
        plot_title="Raw",
    )
    QApplication.processEvents()

    assert win.noise_clip_low == pytest.approx(0.0)
    assert win.noise_clip_high == pytest.approx(0.0)
    assert win.lower_slider.value() == win._noise_threshold_to_slider(0.0)
    assert win.upper_slider.value() == win._noise_threshold_to_slider(0.0)
    assert win.preset_raw_fits_percentile_action.isEnabled() is True
    assert win._active_preset_snapshot is None

    win.close()


def test_loaded_raw_fits_applies_configured_default_preset(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    data = np.arange(200, dtype=float).reshape(10, 20)
    preset = build_preset(
        "Default Background",
        {
            "noise_clip_low": 12.5,
            "noise_clip_high": 175.0,
            "cmap": "inferno",
        },
    )
    monkeypatch.setattr(win, "_load_global_presets", lambda: [preset])
    monkeypatch.setattr(win, "_default_preset_name", lambda: "Default Background")

    win._apply_loaded_dataset(
        data=data,
        freqs=np.linspace(300.0, 210.0, data.shape[0]),
        time=np.arange(data.shape[1], dtype=float),
        filename="raw.fit",
        header0=None,
        source_path=None,
        ut_start_sec=0.0,
        plot_title="Raw",
    )
    QApplication.processEvents()

    assert win.noise_clip_low == pytest.approx(12.5)
    assert win.noise_clip_high == pytest.approx(175.0)
    assert win.upper_slider.value() == win._noise_threshold_to_slider(175.0)
    assert win.current_cmap_name == "inferno"
    assert win._active_preset_snapshot["name"] == "Default Background"
    assert win.noise_reduced_data is not None
    assert win.current_plot_type == "Background Subtracted"
    assert win._current_plot_source_data is not None
    assert np.array_equal(win._current_plot_source_data, win.noise_reduced_data)

    win.close()


def test_raw_fits_percentile_preset_remains_available_manually(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_default_preset_name", lambda: "")
    data = np.arange(200, dtype=float).reshape(10, 20)
    win._apply_loaded_dataset(
        data=data,
        freqs=np.linspace(300.0, 210.0, data.shape[0]),
        time=np.arange(data.shape[1], dtype=float),
        filename="raw.fit",
        header0=None,
        source_path=None,
        ut_start_sec=0.0,
        plot_title="Raw",
    )
    QApplication.processEvents()

    win.apply_raw_fits_percentile_preset()
    QApplication.processEvents()

    expected_low = float(np.percentile(data, MainWindow.RAW_FITS_VMIN_PERCENTILE))
    expected_high = float(np.percentile(data, MainWindow.RAW_FITS_VMAX_PERCENTILE))
    assert win.noise_clip_low == pytest.approx(expected_low)
    assert win.noise_clip_high == pytest.approx(expected_high)
    assert win._active_preset_snapshot["name"] == MainWindow.RAW_FITS_PRESET_NAME

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
    assert win.noise_reduced_data is not None
    assert win.current_plot_type == "Background Subtracted"
    assert win._current_plot_source_data is not None
    assert np.array_equal(win._current_plot_source_data, win.noise_reduced_data)
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
    assert win.title_edit.isEnabled() is False
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


def test_sidebar_sections_are_collapsible_cards():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()
    try:
        sections = collapsible_sections(win.side_scroll.widget())
        assert [section.title() for section in sections] == [
            "Timeline",
            "Noise Clipping Thresholds",
            "Units",
            "Axis",
            "Graph Properties",
            "Analysis Summary",
            "Ruler Measurement",
        ]
        # The header row is the control; there is no check box to hit.
        assert all(isinstance(s.header(), QToolButton) for s in sections)
        assert not any(g.isCheckable() for g in collapsible_groups(win.side_scroll.widget()))

        units = section_for(win.units_group_box)
        assert units is not None
        assert units.isExpanded() is True
        assert units.header().arrowType() == Qt.DownArrow

        expanded_height = units.sizeHint().height()
        units.header().click()
        QApplication.processEvents()

        assert units.isExpanded() is False
        assert units.header().arrowType() == Qt.RightArrow
        assert win.units_digits_radio.isHidden()
        # Collapsing has to actually reclaim the space, not just hide content.
        assert units.sizeHint().height() < expanded_height

        units.header().click()
        QApplication.processEvents()
        assert units.isExpanded() is True
        assert not win.units_digits_radio.isHidden()
    finally:
        win.close()


def test_clicking_a_header_collapses_only_its_own_section():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()
    try:
        units = section_for(win.units_group_box)
        thresholds = section_for(win.slider_group)
        units.setExpanded(True)
        thresholds.setExpanded(True)
        QApplication.processEvents()

        units.header().click()
        QApplication.processEvents()

        assert units.isExpanded() is False
        assert thresholds.isExpanded() is True
        assert not win.lower_slider.isHidden()
    finally:
        win.close()


def test_expanding_a_section_does_not_defeat_its_gating():
    _app()
    win = MainWindow(theme=None)
    try:
        # Nothing loaded, so Graph Properties must stay disabled even though
        # re-opening a card re-shows all of its children.
        assert win.graph_group.isEnabled() is False
        set_group_expanded(win.graph_group, False)
        set_group_expanded(win.graph_group, True)
        QApplication.processEvents()
        assert win.graph_group.isEnabled() is False
    finally:
        win.close()


def test_section_expanded_state_survives_a_restart():
    _app()
    first = MainWindow(theme=None)
    try:
        set_group_expanded(first.units_group_box, False)
        QApplication.processEvents()
    finally:
        first.close()

    second = MainWindow(theme=None)
    try:
        assert section_for(second.units_group_box).isExpanded() is False
        assert second.units_digits_radio.isHidden()
        assert section_for(second.slider_group).isExpanded() is True
    finally:
        second.close()


def test_every_collapsed_card_shrinks_to_its_header_row():
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()
    try:
        sections = collapsible_sections(win.side_scroll.widget())
        for section in sections:
            section.setExpanded(False)
        QApplication.processEvents()

        heights = {section.sizeHint().height() for section in sections}
        # A ragged accordion reads as broken; every collapsed card is one row.
        assert len(heights) == 1
        header_height = sections[0].header().sizeHint().height()
        assert heights.pop() == pytest.approx(header_height, abs=2)
    finally:
        win.close()


def test_collapsed_cards_carry_the_style_property_the_sidebar_qss_keys_on():
    _app()
    win = MainWindow(theme=None)
    try:
        units = section_for(win.units_group_box)
        units.setExpanded(True)
        assert units.property("collapsed") is False
        units.setExpanded(False)
        QApplication.processEvents()
        assert units.property("collapsed") is True
        units.setExpanded(True)
        QApplication.processEvents()
        assert units.property("collapsed") is False
    finally:
        win.close()
