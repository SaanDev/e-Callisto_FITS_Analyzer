"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from astropy.io import fits
from PySide6.QtWidgets import QApplication

from src.Backend.multi_station_comparison import COLOR_SCALE_MANUAL, TIME_ALIGNMENT_SECONDS, TIME_ALIGNMENT_UT
from src.UI.dialogs.multi_station_comparison_dialog import MultiStationComparisonDialog
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _flush_events():
    app = _app()
    for _ in range(3):
        app.processEvents()


def _write_fit(path: Path, *, label: str, time_obs: str | None = "12:00:00", base: float = 0.0) -> None:
    data = (np.arange(12, dtype=np.float32).reshape(3, 4) + float(base)).astype(np.float32)
    hdu = fits.PrimaryHDU(data=data)
    hdr = hdu.header
    hdr["CRVAL1"] = 0.0
    hdr["CDELT1"] = 1.0
    hdr["CRPIX1"] = 1.0
    hdr["CRVAL2"] = 100.0
    hdr["CDELT2"] = -5.0
    hdr["CRPIX2"] = 1.0
    hdr["INSTRUME"] = label
    if time_obs is not None:
        hdr["TIME-OBS"] = time_obs
    hdu.writeto(path, overwrite=True)


def test_multi_station_action_opens_and_reuses_dialog():
    _app()
    win = MainWindow(theme=None)

    assert win.multi_station_comparison_action.text() == "Multi-Station Comparison..."
    win.multi_station_comparison_action.trigger()
    _flush_events()
    first = win._multi_station_comparison_dialog

    assert first is not None
    assert first.isVisible() is True

    win.multi_station_comparison_action.trigger()
    _flush_events()
    assert win._multi_station_comparison_dialog is first

    first.close()
    win.close()


def test_add_remove_reorder_files_updates_station_list(tmp_path: Path):
    _app()
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A")
    _write_fit(b, label="B")
    dialog = MultiStationComparisonDialog()

    dialog.add_files([str(a), str(b)])
    dialog._render_now()

    assert dialog.file_list.count() == 2
    assert [item.label for item in dialog._datasets] == ["A", "B"]

    dialog.file_list.setCurrentRow(1)
    dialog._move_selected(-1)
    assert [item.label for item in dialog._datasets] == ["B", "A"]

    dialog.file_list.setCurrentRow(0)
    dialog.remove_selected_files()
    assert dialog.file_list.count() == 1
    assert [item.label for item in dialog._datasets] == ["A"]
    dialog.close()


def test_load_view_config_applies_visual_settings_and_seconds_range():
    _app()
    dialog = MultiStationComparisonDialog()
    dialog._set_alignment_mode(TIME_ALIGNMENT_SECONDS)

    ok = dialog._apply_view_config_payload(
        {
            "range": {"time_start_s": 1.0, "time_stop_s": 2.0, "freq_min_mhz": 40.0, "freq_max_mhz": 80.0},
            "visual": {"use_db": True, "use_utc": False, "cmap": "plasma", "noise_clip_low": -3.0, "noise_clip_high": 7.0},
        },
        apply_range=True,
    )

    assert ok is True
    assert dialog.units_combo.currentText() == "dB"
    assert dialog.colormap_combo.currentText() == "plasma"
    assert dialog.current_color_scale_mode() == COLOR_SCALE_MANUAL
    assert dialog._display_range["time_start_s"] == pytest.approx(1.0)
    dialog.close()


def test_seconds_display_range_applies_to_all_comparison_panels(tmp_path: Path):
    _app()
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A")
    _write_fit(b, label="B", base=100.0)
    dialog = MultiStationComparisonDialog()
    dialog.add_files([str(a), str(b)])
    dialog._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
    dialog._display_range = {"time_start_s": 1.0, "time_stop_s": 3.0, "freq_min_mhz": 80.0, "freq_max_mhz": 110.0}

    dialog._render_now()

    assert dialog.canvas.fig.axes[0].get_xlim() == pytest.approx((1.0, 3.0))
    assert dialog.canvas.fig.axes[0].get_ylim() == pytest.approx((80.0, 110.0))
    assert dialog.canvas.fig.axes[1].get_xlim() == pytest.approx((1.0, 3.0))
    assert dialog.canvas.fig.axes[1].get_ylim() == pytest.approx((80.0, 110.0))
    dialog.close()


def test_ut_mode_downgrades_when_a_file_has_no_time_obs(tmp_path: Path):
    _app()
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A", time_obs="12:00:00")
    _write_fit(b, label="B", time_obs=None)
    dialog = MultiStationComparisonDialog()

    dialog.add_files([str(a), str(b)])
    dialog._set_alignment_mode(TIME_ALIGNMENT_UT)
    dialog._on_alignment_changed()

    assert dialog.current_alignment_mode() == TIME_ALIGNMENT_SECONDS
    dialog.close()


def test_export_is_disabled_until_two_valid_files_are_loaded(tmp_path: Path):
    _app()
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A")
    _write_fit(b, label="B")
    dialog = MultiStationComparisonDialog()

    assert dialog.export_btn.isEnabled() is False
    dialog.add_files([str(a)])
    assert dialog.export_btn.isEnabled() is False
    dialog.add_files([str(b)])
    assert dialog.export_btn.isEnabled() is True
    dialog.close()


def test_opening_comparison_dialog_does_not_mutate_main_window_data_or_view(tmp_path: Path):
    _app()
    path = tmp_path / "main.fit"
    _write_fit(path, label="Main", time_obs="12:00:00")
    win = MainWindow(theme=None)
    win.load_fits_into_main(str(path))
    _flush_events()

    original_data = win.raw_data.copy()
    original_freqs = win.freqs.copy()
    original_time = win.time.copy()
    original_view = win._capture_view()
    original_dirty = win._project_dirty

    win.open_multi_station_comparison_dialog()
    _flush_events()

    assert np.array_equal(win.raw_data, original_data)
    assert np.array_equal(win.freqs, original_freqs)
    assert np.array_equal(win.time, original_time)
    assert win._capture_view()["xlim"] == pytest.approx(original_view["xlim"])
    assert win._capture_view()["ylim"] == pytest.approx(original_view["ylim"])
    assert win._project_dirty is original_dirty

    win._multi_station_comparison_dialog.close()
    win.close()
