"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.UI.dialogs.display_range_dialog import DisplayRangeDialog
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _flush_events():
    app = _app()
    for _ in range(3):
        app.processEvents()


def _load_demo_plot(win: MainWindow):
    win.set_hardware_live_preview_enabled(False)
    win.filename = "demo.fit"
    win.freqs = np.array([100.0, 95.0, 90.0], dtype=float)
    win.time = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=float)
    win.raw_data = np.array(
        [
            [1.0, 2.0, 3.0, 4.0, 5.0],
            [2.0, 3.0, 4.0, 5.0, 6.0],
            [3.0, 4.0, 5.0, 6.0, 7.0],
        ],
        dtype=np.float32,
    )
    win.ut_start_sec = 12 * 3600
    win.plot_data(win.raw_data, title="Raw")
    _flush_events()


def test_display_range_action_enables_after_data_load():
    _app()
    win = MainWindow(theme=None)

    assert win.set_display_range_action.isEnabled() is False
    assert win.save_display_range_preset_action.isEnabled() is False
    assert win.export_view_config_action.isEnabled() is False

    _load_demo_plot(win)

    assert win.set_display_range_action.isEnabled() is True
    assert win.save_display_range_preset_action.isEnabled() is True
    assert win.export_view_config_action.isEnabled() is True
    win.close()


def test_apply_display_range_sets_mpl_limits_and_undo_restores_previous_view():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    previous_xlim = win.canvas.ax.get_xlim()
    previous_ylim = win.canvas.ax.get_ylim()

    ok = win._apply_display_range(1.0, 3.0, 99.0, 91.0, show_errors=False)
    _flush_events()

    assert ok is True
    assert win.canvas.ax.get_xlim() == pytest.approx((1.0, 3.0))
    assert win.canvas.ax.get_ylim() == pytest.approx((91.0, 99.0))
    captured = win._capture_view()
    assert captured["xlim"] == pytest.approx((1.0, 3.0))
    assert captured["ylim"] == pytest.approx((91.0, 99.0))

    win.undo()
    _flush_events()

    assert win.canvas.ax.get_xlim() == pytest.approx(previous_xlim)
    assert win.canvas.ax.get_ylim() == pytest.approx(previous_ylim)
    win.close()


def test_apply_display_range_rejects_invalid_ranges_without_changing_view():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)
    original_xlim = win.canvas.ax.get_xlim()
    original_ylim = win.canvas.ax.get_ylim()

    assert win._apply_display_range(3.0, 1.0, 91.0, 99.0, show_errors=False) is False
    assert win.canvas.ax.get_xlim() == pytest.approx(original_xlim)
    assert win.canvas.ax.get_ylim() == pytest.approx(original_ylim)

    assert win._apply_display_range(1.0, 3.0, 95.0, 95.0, show_errors=False) is False
    assert win.canvas.ax.get_xlim() == pytest.approx(original_xlim)
    assert win.canvas.ax.get_ylim() == pytest.approx(original_ylim)
    win.close()


def test_ut_display_range_conversion_handles_same_day_and_midnight_crossing():
    _app()
    win = MainWindow(theme=None)

    win.ut_start_sec = 12 * 3600
    assert win._ut_seconds_of_day_range_to_relative(
        12 * 3600 + 60,
        12 * 3600 + 180,
        (0.0, 300.0),
    ) == pytest.approx((60.0, 180.0))

    win.ut_start_sec = 23 * 3600 + 59 * 60
    assert win._ut_seconds_of_day_range_to_relative(
        30,
        120,
        (0.0, 300.0),
    ) == pytest.approx((90.0, 180.0))
    win.close()


def test_display_range_dialog_disables_ut_when_time_obs_missing():
    _app()
    dlg = DisplayRangeDialog(
        time_min_s=0.0,
        time_max_s=10.0,
        freq_min_mhz=20.0,
        freq_max_mhz=80.0,
        initial_time_start_s=0.0,
        initial_time_stop_s=10.0,
        initial_freq_start_mhz=20.0,
        initial_freq_stop_mhz=80.0,
        ut_start_sec=None,
    )

    assert dlg.ut_radio.isEnabled() is False
    assert dlg.uses_ut() is False
    dlg.close()


def test_apply_display_range_sets_hardware_view(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    class FakeAccelCanvas:
        is_available = True

        def __init__(self):
            self.view = {"xlim": (0.0, 4.0), "ylim": (87.5, 102.5)}

        def get_view(self):
            return self.view

        def set_view(self, view):
            self.view = {"xlim": tuple(view["xlim"]), "ylim": tuple(view["ylim"])}

        def set_goes_overlay(self, *_args, **_kwargs):
            return None

    fake = FakeAccelCanvas()
    win.accel_canvas = fake
    win.use_hw_live_preview = True
    monkeypatch.setattr(win, "_hardware_mode_enabled", lambda: True)

    ok = win._apply_display_range(1.0, 3.0, 91.0, 99.0, show_errors=False)

    assert ok is True
    assert fake.view["xlim"] == pytest.approx((1.0, 3.0))
    assert fake.view["ylim"] == pytest.approx((91.0, 99.0))
    win.close()


def test_display_range_survives_noise_redraw():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    assert win._apply_display_range(1.0, 3.0, 91.0, 99.0, show_errors=False) is True
    win.lower_slider.setValue(win._noise_threshold_to_slider(-1.0))
    win.upper_slider.setValue(win._noise_threshold_to_slider(2.0))
    win.update_noise_live()
    _flush_events()

    assert win.canvas.ax.get_xlim() == pytest.approx((1.0, 3.0))
    assert win.canvas.ax.get_ylim() == pytest.approx((91.0, 99.0))
    win.close()


def test_display_range_preset_save_apply_delete(monkeypatch):
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)
    storage = []

    def fake_load():
        return list(storage)

    def fake_save(items):
        storage[:] = list(items)

    monkeypatch.setattr(win, "_load_display_range_presets", fake_load)
    monkeypatch.setattr(win, "_save_display_range_presets", fake_save)
    monkeypatch.setattr("src.UI.main_window.QInputDialog.getText", lambda *_a, **_k: ("Event Window", True))

    assert win._apply_display_range(1.0, 3.0, 91.0, 99.0, show_errors=False) is True
    win.save_display_range_preset()

    assert len(storage) == 1
    assert storage[0]["name"] == "Event Window"

    assert win._apply_display_range(0.0, 2.0, 90.0, 100.0, show_errors=False) is True
    monkeypatch.setattr("src.UI.main_window.QInputDialog.getItem", lambda *_a, **_k: ("Event Window", True))
    win.apply_display_range_preset()

    assert win.canvas.ax.get_xlim() == pytest.approx((1.0, 3.0))
    assert win.canvas.ax.get_ylim() == pytest.approx((91.0, 99.0))

    win.delete_display_range_preset()
    assert storage == []
    win.close()


def test_apply_view_config_payload_applies_visuals_then_range():
    _app()
    win = MainWindow(theme=None)
    _load_demo_plot(win)

    config = {
        "range": {"time_start_s": 1.0, "time_stop_s": 3.0, "freq_min_mhz": 91.0, "freq_max_mhz": 99.0},
        "visual": {
            "use_db": True,
            "use_utc": True,
            "noise_clip_low": -1.0,
            "noise_clip_high": 2.0,
            "noise_clip_scale": "linear",
            "cmap": "inferno",
            "graph": {"title_override": "Configured View"},
        },
    }

    assert win._apply_view_config_payload(config, show_errors=False) is True
    _flush_events()

    assert win.use_db is True
    assert win.use_utc is True
    assert win.current_cmap_name == "inferno"
    assert win.title_edit.text() == "Configured View"
    assert win.canvas.ax.get_xlim() == pytest.approx((1.0, 3.0))
    assert win.canvas.ax.get_ylim() == pytest.approx((91.0, 99.0))
    win.close()
