"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from src.backend.radio.batch_processing import PLOTUTIL_DB_SCALE
from src.backend.session.presets import build_preset
from src.ui.app.main_window import MainWindow

NROWS = 12
NCOLS = 30


def _app():
    return QApplication.instance() or QApplication([])


def _dispose(win) -> None:
    # Delete the pyqtgraph canvases now: left for interpreter shutdown, their
    # axis labels can be torn down out of order and crash the process.
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _raw_spectrum() -> np.ndarray:
    rng = np.random.default_rng(7)
    gain = np.linspace(60.0, 180.0, NROWS)[:, None]
    data = gain + rng.normal(0.0, 3.0, (NROWS, NCOLS))
    data[4:8, 10:16] += 60.0  # a burst that pulls the mean above the median
    return data.astype(np.float32)


@pytest.fixture
def window(monkeypatch):
    _app()
    monkeypatch.setattr(MainWindow, "_prompt_recovery_if_needed", lambda self: None, raising=False)
    win = MainWindow(theme=None)
    monkeypatch.setattr(win, "_default_preset_name", lambda: "")
    data = _raw_spectrum()
    win._apply_loaded_dataset(
        data=data,
        freqs=np.linspace(80.0, 45.0, NROWS),
        time=np.arange(NCOLS, dtype=float),
        filename="bg.fit",
        header0=None,
        source_path=None,
        ut_start_sec=0.0,
        plot_title="Raw",
    )
    QApplication.processEvents()
    yield win
    _dispose(win)


def _subtract(win, method: str) -> None:
    win.background_method_combo.setCurrentIndex(win.background_method_combo.findData(method))
    win.background_subtract_btn.click()
    QApplication.processEvents()


def _image_clim(win):
    return win.canvas.ax.images[-1].get_clim()


def test_section_offers_the_three_methods(window):
    combo = window.background_method_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Mean", "Median", "Median (dB)"]
    assert [combo.itemData(i) for i in range(combo.count())] == ["mean", "median", "plotutil_median_db"]
    assert window.background_subtract_btn.isEnabled() is True
    assert "raw data" in window.background_status_label.text()


def test_button_is_disabled_without_data():
    _app()
    win = MainWindow(theme=None)
    try:
        assert win.background_subtract_btn.isEnabled() is False
        assert "Load a FITS file" in win.background_status_label.text()
    finally:
        _dispose(win)


@pytest.mark.parametrize(
    "method, reference",
    [
        ("mean", lambda raw: raw - raw.mean(axis=1, keepdims=True)),
        ("median", lambda raw: raw - np.median(raw, axis=1, keepdims=True)),
        (
            "plotutil_median_db",
            lambda raw: (raw - np.median(raw, axis=1, keepdims=True)) * PLOTUTIL_DB_SCALE,
        ),
    ],
)
def test_each_method_is_computed_from_the_raw_data(window, method, reference):
    raw_before = np.array(window.raw_data, copy=True)

    _subtract(window, method)

    assert window.current_plot_type == "Background Subtracted"
    assert window._background_applied_method == method
    assert np.allclose(window.noise_reduced_data, reference(raw_before.astype(np.float64)), atol=1e-3)
    assert np.array_equal(window.raw_data, raw_before)


def test_reapplying_replaces_the_result_instead_of_stacking(window):
    _subtract(window, "mean")
    _subtract(window, "median")

    raw = np.asarray(window.raw_data, dtype=np.float64)
    assert np.allclose(window.noise_reduced_data, raw - np.median(raw, axis=1, keepdims=True), atol=1e-3)
    assert window.background_status_label.text() == "Applied: Median"


def test_resubtracting_discards_products_derived_from_the_old_one(window):
    _subtract(window, "mean")
    window.apply_rfi_now()
    QApplication.processEvents()
    assert window.current_plot_type == "RFI Cleaned"

    _subtract(window, "median")

    assert window.current_plot_type == "Background Subtracted"
    assert window._rfi_config["applied"] is False


def test_thresholds_set_the_color_scale_without_touching_the_data(window):
    _subtract(window, "median")
    product = np.array(window.noise_reduced_data, copy=True)

    window._set_noise_clip_state(-2.0, 10.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    window.update_noise_live()
    QApplication.processEvents()

    assert np.array_equal(window.noise_reduced_data, product)
    assert float(np.nanmin(window.noise_reduced_data)) < -2.0
    assert float(np.nanmax(window.noise_reduced_data)) > 10.0
    assert window.current_plot_type == "Background Subtracted"
    assert _image_clim(window) == pytest.approx((-2.0, 10.0))


def test_hardware_canvas_receives_the_thresholds_as_levels(window, monkeypatch):
    calls = []

    class FakeAccel:
        is_available = True

        def get_view(self):
            return None

        def set_text_style(self, **_kwargs):
            pass

        def update_image(self, data, **kwargs):
            calls.append(kwargs.get("levels"))

        def set_time_mode(self, *_args):
            pass

        def set_goes_overlay(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(window, "accel_canvas", FakeAccel())
    monkeypatch.setattr(window, "_hardware_mode_enabled", lambda: True)
    monkeypatch.setattr(window, "_refresh_accel_swaves_panel", lambda: None)
    monkeypatch.setattr(window, "_render_light_curve_accel_overlay", lambda: None)

    window._set_noise_clip_state(70.0, 150.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    assert window._refresh_accel_plot(data=window.raw_data, title="Raw") is True
    window._set_noise_clip_state(0.0, 0.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    assert window._refresh_accel_plot(data=window.raw_data, title="Raw") is True

    assert calls[0] == pytest.approx((70.0, 150.0))
    assert calls[1] is None


def test_thresholds_on_the_raw_view_do_not_subtract_anything(window):
    window._set_noise_clip_state(70.0, 150.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    window.update_noise_live()
    QApplication.processEvents()

    assert window.noise_reduced_data is None
    assert window.current_plot_type == "Raw"
    assert _image_clim(window) == pytest.approx((70.0, 150.0))
    # The sliders have to reach the raw digits, not just +/-100.
    assert window._noise_clip_bounds()[1] >= float(np.nanmax(window.raw_data))


def test_switching_intensity_scale_resets_thresholds_but_a_method_change_keeps_them(window):
    window._set_noise_clip_state(70.0, 150.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    _subtract(window, "mean")
    # Raw-digit limits would saturate a background-subtracted plot.
    assert (window.noise_clip_low, window.noise_clip_high) == (0.0, 0.0)

    window._set_noise_clip_state(-3.0, 12.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    _subtract(window, "median")
    assert (window.noise_clip_low, window.noise_clip_high) == pytest.approx((-3.0, 12.0))

    _subtract(window, "plotutil_median_db")
    assert (window.noise_clip_low, window.noise_clip_high) == (0.0, 0.0)


def test_median_db_product_is_labelled_and_displayed_in_db(window):
    window.set_units_mode(True)
    _subtract(window, "plotutil_median_db")

    assert window._intensity_unit_label() == "dB"
    # Already dB: no second Digits-to-dB conversion on top.
    assert window._intensity_for_display(window.noise_reduced_data) is window.noise_reduced_data
    assert window.units_db_radio.isChecked() is True
    assert window.units_db_radio.isEnabled() is False
    assert window.units_digits_radio.isEnabled() is False

    window._set_noise_clip_state(-1.0, 8.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    assert window._noise_clip_display_values() == pytest.approx((-1.0, 8.0, "dB"))
    assert window.lower_value_label.text() == "-1.00 dB"
    assert window.lower_value_sub_label.isHidden() is True


def test_reset_to_raw_clears_the_subtraction_and_restores_units(window):
    _subtract(window, "plotutil_median_db")

    window.reset_to_raw()
    QApplication.processEvents()

    assert window.noise_reduced_data is None
    assert window._background_applied_method is None
    assert window.current_plot_type == "Raw"
    assert window.units_digits_radio.isEnabled() is True
    assert window.units_digits_radio.isChecked() is True
    assert window._intensity_unit_label() == "Digits"
    assert "raw data" in window.background_status_label.text()


def test_undo_returns_to_the_raw_view(window):
    _subtract(window, "median")

    window.undo()
    QApplication.processEvents()

    assert window.noise_reduced_data is None
    assert window._background_applied_method is None
    assert window.current_plot_type == "Raw"


def test_preset_records_and_restores_the_background(window):
    _subtract(window, "median")
    settings = window._preset_settings_payload()
    assert settings["background_method"] == "median"
    assert settings["background_subtracted"] is True
    preset = build_preset("Median view", settings)

    window.reset_to_raw()
    QApplication.processEvents()
    assert window._apply_preset_payload(preset) is True
    QApplication.processEvents()

    assert window._background_applied_method == "median"
    assert window.current_plot_type == "Background Subtracted"
    assert window.background_method_combo.currentData() == "median"


def test_legacy_preset_with_thresholds_still_opens_subtracted(window):
    legacy = build_preset("Old", {"noise_clip_low": -5.0, "noise_clip_high": 20.0})
    assert "background_subtracted" not in legacy["settings"]

    assert window._apply_preset_payload(legacy) is True
    QApplication.processEvents()

    assert window.current_plot_type == "Background Subtracted"
    assert window._background_applied_method == "mean"
    assert (window.noise_clip_low, window.noise_clip_high) == pytest.approx((-5.0, 20.0))


def test_project_payload_carries_the_applied_method(window):
    _subtract(window, "plotutil_median_db")
    meta, arrays = window._capture_project_payload()

    assert meta["background_applied_method"] == "plotutil_median_db"
    assert meta["background_method"] == "plotutil_median_db"

    window.reset_to_raw()
    window._apply_project_payload(meta, arrays)
    QApplication.processEvents()

    assert window._background_applied_method == "plotutil_median_db"
    assert window._intensity_unit_label() == "dB"


def _close_dialog(dlg) -> None:
    dlg.close()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_type_ii_window_shows_the_noise_reduced_spectrum(window):
    _subtract(window, "median")
    window._set_noise_clip_state(-2.0, 10.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    window.update_noise_live()
    QApplication.processEvents()

    dlg = window._open_or_focus_type_ii_dialog()
    assert dlg is not None
    try:
        assert np.array_equal(dlg.spectrum_data, window.noise_reduced_data)
        assert not np.allclose(dlg.spectrum_data, window.raw_data)
        # The thresholds are the main plot's color limits; the window must use
        # them too, or the subtracted data show at full range and look raw.
        assert tuple(dlg.image_item.getLevels()) == pytest.approx((-2.0, 10.0))
        images = [im for ax in dlg.origin_figure().axes for im in ax.images]
        assert images and images[0].get_clim() == pytest.approx((-2.0, 10.0))
    finally:
        _close_dialog(dlg)


def test_type_ii_window_fits_the_data_without_thresholds(window):
    _subtract(window, "median")

    dlg = window._open_or_focus_type_ii_dialog()
    try:
        low, high = float(np.nanmin(window.noise_reduced_data)), float(np.nanmax(window.noise_reduced_data))
        assert tuple(dlg.image_item.getLevels()) == pytest.approx((low, high))
    finally:
        _close_dialog(dlg)


def test_reopening_type_ii_window_loads_the_isolated_burst(window):
    _subtract(window, "median")
    window._set_noise_clip_state(-2.0, 10.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    dlg = window._open_or_focus_type_ii_dialog()
    try:
        dlg._upper_points = [(12.0, 70.0), (13.0, 65.0)]
        dlg._emit_session_changed()

        mask = np.zeros(window.noise_reduced_data.shape, dtype=bool)
        mask[4:8, 10:16] = True
        window._plot_isolated_burst(mask)
        window._set_noise_clip_state(0.0, 30.0, scale=window.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
        QApplication.processEvents()

        assert window._open_or_focus_type_ii_dialog() is dlg
        np.testing.assert_array_equal(dlg.spectrum_data, window.noise_reduced_data)
        assert np.all(dlg.spectrum_data[~mask] == 0.0)
        assert tuple(dlg.image_item.getLevels()) == pytest.approx((0.0, 30.0))
        assert dlg._upper_points == [(12.0, 70.0), (13.0, 65.0)]
    finally:
        _close_dialog(dlg)
