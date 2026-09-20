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

from astropy.io import fits
from PySide6.QtWidgets import QApplication

from src.ui.app.main_window import MainWindow
from tests.backend.test_backend_artemis import CHANNEL_COUNT, SAMPLE_COUNT, artemis_header


CALLISTO_DB_SCALE = 2500.0 / 256.0 / 25.4


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 6):
    app = _app()
    for _ in range(times):
        app.processEvents()


@pytest.fixture
def artemis_path(tmp_path):
    rng = np.random.default_rng(11)
    gains = np.linspace(20.0, 160.0, CHANNEL_COUNT).reshape(-1, 1)
    data = gains + rng.normal(0.0, 3.0, size=(CHANNEL_COUNT, SAMPLE_COUNT))
    data[40:80, 200:260] += 900.0
    path = tmp_path / "00714.FITS"
    fits.PrimaryHDU(data=data.astype(np.int16), header=artemis_header()).writeto(
        path, overwrite=True, output_verify="ignore"
    )
    return str(path)


@pytest.fixture
def window(artemis_path):
    _app()
    win = MainWindow(theme=None)
    win.set_hardware_live_preview_enabled(False)
    win.load_fits_into_main(artemis_path)
    _flush()
    yield win
    win.close()


def test_window_recognises_the_receiver(window):
    profile = window._artemis_profile

    assert profile is not None
    assert profile.receiver == "ASG"
    assert "ARTEMIS-IV" in profile.summary()


def test_time_axis_is_seconds_and_ut_start_is_the_observation_time(window):
    assert window.time[0] == pytest.approx(0.0)
    assert window.time[-1] == pytest.approx(SAMPLE_COUNT - 1, abs=1e-2)
    assert window.ut_start_sec == pytest.approx(32399.9, abs=1e-2)


def test_intensity_unit_is_counts_not_digits(window):
    assert window._intensity_linear_unit() == "Counts"
    assert window._intensity_unit_label() == "Counts"
    assert window.units_digits_radio.text() == "Counts"

    window.set_units_mode(True)
    assert window._intensity_unit_label() == "dB"


def test_db_scale_is_the_receivers_own_not_callistos(window):
    scale = window._intensity_db_scale()

    assert scale == pytest.approx(70.0 / 4096.0)
    assert scale != pytest.approx(CALLISTO_DB_SCALE)


def test_clip_sliders_reach_the_receivers_real_range(window):
    low, high = window._noise_clip_bounds()

    # The CALLISTO default is +/-100, far short of counts in the hundreds.
    assert high > 500.0
    assert low < -500.0


def test_an_artemis_file_opens_background_subtracted(window):
    assert window.current_plot_type == "Background Subtracted"
    assert window.noise_reduced_data is not None
    assert window.noise_clip_high > 0.0


def test_reset_to_raw_still_shows_the_stored_counts(window):
    window.reset_to_raw()
    _flush()

    assert window.current_plot_type == "Raw"
    assert window.noise_clip_low == pytest.approx(0.0)
    assert window.noise_clip_high == pytest.approx(0.0)
    assert float(np.nanmax(window.raw_data)) > 800.0


def test_db_readout_is_referenced_to_the_clipping_threshold(window):
    window.set_units_mode(True)
    low_db, high_db, unit = window._noise_clip_display_values()

    assert unit == "dB"
    cold = min(window.noise_clip_low, window.noise_clip_high)
    hot = max(window.noise_clip_low, window.noise_clip_high)
    assert high_db == pytest.approx((hot - cold) * (70.0 / 4096.0))
    assert low_db == pytest.approx(0.0)


def test_loading_logs_the_calibration_and_the_header_notes(window):
    messages = " ".join(entry["msg"] for entry in window._processing_log)

    assert "counts/dB" in messages
    assert "not flux calibrated" in messages
    assert "hours" in messages


def test_a_callisto_dataset_keeps_digits_and_the_callisto_scale(window):
    window.filename = "demo.fit"
    window._assign_dataset_arrays(
        data=np.zeros((3, 5), dtype=np.float32),
        freqs=np.array([100.0, 95.0, 90.0]),
        time=np.arange(5, dtype=float),
        filename="demo.fit",
        header0=fits.Header({"INSTRUME": "CALLISTO"}),
    )

    assert window._artemis_profile is None
    assert window._intensity_linear_unit() == "Digits"
    assert window.units_digits_radio.text() == "Digits"
    assert window._intensity_db_scale() == pytest.approx(CALLISTO_DB_SCALE)


def test_header_viewer_explains_the_artemis_quirks(window):
    from src.ui.radio.fits_header_viewer import FitsHeaderViewerDialog

    dialog = FitsHeaderViewerDialog(window._fits_header0, parent=window)
    try:
        text = dialog.text.toPlainText()
    finally:
        dialog.close()

    assert "ARTEMIS-IV" in text
    assert "counts/dB" in text
    assert "YYYY-DD-MM" in text
    # The raw cards are still there, verbatim, below the preamble.
    assert "THERMOPYLAE" in text
    assert "CRVAL1" in text
    assert "CDELT2" in text
