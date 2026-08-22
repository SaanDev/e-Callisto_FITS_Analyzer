"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Playback trades exactness for frame rate only while the view is moving. These
tests pin that it engages when it should, stops when it should, and never
touches the arrays the analysis tools read.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.Backend.solar_data_analysis import (
    finite_percentiles,
    subsample_for_statistics,
)
from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow, SolarMatplotlibCanvas


def _app():
    return QApplication.instance() or QApplication([])


class _FrameStub:
    def __init__(self, data, meta=None):
        self.data = np.asarray(data)
        self.meta = dict(meta or {})


def _frames(shape=(64, 64), count=4):
    rng = np.random.default_rng(5)
    meta = {
        "cdelt1": 0.6, "cdelt2": 0.6,
        "crpix1": shape[1] / 2, "crpix2": shape[0] / 2,
        "crval1": 0.0, "crval2": 0.0, "exptime": 2.0,
    }
    return [_FrameStub((rng.random(shape) * 3000).astype(np.float32), meta) for _ in range(count)]


@pytest.fixture
def window():
    _app()
    win = SolarDataAnalysisWindow()
    win._map_frames = _frames()
    win._composite_frames = []
    win._exposure_varies = False
    win.frame_slider.setMaximum(len(win._map_frames) - 1)
    try:
        yield win
    finally:
        win.pause_frames()
        win.close()


# --- subsample_for_statistics ---------------------------------------------


def test_subsample_returns_small_arrays_untouched():
    arr = np.zeros((32, 32), dtype=np.float32)
    assert subsample_for_statistics(arr) is arr


def test_subsample_reduces_large_arrays():
    arr = np.zeros((4096, 4096), dtype=np.float32)
    out = subsample_for_statistics(arr, max_samples=1_000_000)
    assert out.size < arr.size
    assert out.size >= 250_000
    assert out.ndim == 2


def test_subsample_ignores_one_dimensional_input():
    arr = np.zeros(5_000_000, dtype=np.float32)
    assert subsample_for_statistics(arr) is arr


def test_subsampled_percentiles_track_the_exact_values():
    rng = np.random.default_rng(11)
    y, x = np.mgrid[0:1200, 0:1200]
    radius = np.hypot(x - 600, y - 600)
    frame = (np.exp(-((radius - 400) ** 2) / (2 * 90.0**2)) * 2500 + rng.random((1200, 1200)) * 80)
    frame = frame.astype(np.float32)

    exact = finite_percentiles(frame, (1.0, 99.5))
    approx = finite_percentiles(subsample_for_statistics(frame, max_samples=100_000), (1.0, 99.5))
    span = float(exact[1] - exact[0])
    # Well inside one of the 256 display levels.
    assert abs(approx[0] - exact[0]) < span / 256.0
    assert abs(approx[1] - exact[1]) < span / 256.0


# --- finite_percentiles ----------------------------------------------------


def test_finite_percentiles_matches_the_compacted_form():
    rng = np.random.default_rng(3)
    arr = (rng.random((80, 90)) * 100).astype(np.float32)
    arr[5, 5] = np.nan
    expected = np.nanpercentile(arr[np.isfinite(arr)], [1.0, 99.5])
    assert np.allclose(finite_percentiles(arr, (1.0, 99.5)), expected, rtol=1e-6)


def test_finite_percentiles_excludes_infinities():
    arr = np.array([[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, np.inf, -np.inf]], dtype=np.float32)
    expected = np.nanpercentile(arr[np.isfinite(arr)], [10.0, 90.0])
    assert np.allclose(finite_percentiles(arr, (10.0, 90.0)), expected, rtol=1e-6)


def test_finite_percentiles_handles_empty_and_all_nan():
    assert np.all(np.isnan(finite_percentiles(np.zeros((0, 0)), (50.0,))))
    assert np.all(np.isnan(finite_percentiles(np.full((4, 4), np.nan), (50.0,))))


# --- canvas decimation -----------------------------------------------------


def test_canvas_decimation_is_off_by_default():
    _app()
    canvas = SolarMatplotlibCanvas()
    arr = np.zeros((2048, 2048), dtype=np.float32)
    out, step = canvas._decimate_for_display(arr)
    assert step == 1
    assert out is arr


def test_canvas_decimates_only_above_the_limit():
    _app()
    canvas = SolarMatplotlibCanvas()
    canvas.set_display_pixel_limit(512)

    small = np.zeros((256, 256), dtype=np.float32)
    out, step = canvas._decimate_for_display(small)
    assert step == 1 and out is small

    big = np.zeros((2048, 2048), dtype=np.float32)
    out, step = canvas._decimate_for_display(big)
    assert step == 4
    assert out.shape == (512, 512)
    assert out.flags["C_CONTIGUOUS"]


def test_canvas_decimation_preserves_extent():
    """A decimated frame still covers the same patch of sky."""
    _app()
    canvas = SolarMatplotlibCanvas()
    transform = {
        "x_ref_pix": 0.0, "y_ref_pix": 0.0,
        "x_scale_arcsec_per_pix": 0.6, "y_scale_arcsec_per_pix": 0.6,
        "x_ref_arcsec": 0.0, "y_ref_arcsec": 0.0,
    }
    arr = (np.random.default_rng(2).random((1024, 1024)) * 100).astype(np.float32)

    canvas.plot_map_data(arr, title="full", vmin=0.0, vmax=100.0, axis_transform=transform)
    full_extent = canvas._image_artist.get_extent()

    canvas.set_display_pixel_limit(256)
    canvas.plot_map_data(arr, title="decimated", vmin=0.0, vmax=100.0, axis_transform=transform)
    assert canvas._image_artist.get_extent() == pytest.approx(full_extent)
    assert max(canvas._image_artist.get_array().shape[:2]) <= 256


def test_canvas_reuses_the_image_artist_between_matching_frames():
    _app()
    canvas = SolarMatplotlibCanvas()
    transform = {
        "x_ref_pix": 0.0, "y_ref_pix": 0.0,
        "x_scale_arcsec_per_pix": 0.6, "y_scale_arcsec_per_pix": 0.6,
        "x_ref_arcsec": 0.0, "y_ref_arcsec": 0.0,
    }
    a = (np.random.default_rng(7).random((64, 64)) * 100).astype(np.float32)
    b = (np.random.default_rng(8).random((64, 64)) * 100).astype(np.float32)

    canvas.plot_map_data(a, title="one", vmin=0.0, vmax=100.0, axis_transform=transform)
    first = canvas._image_artist
    canvas.plot_map_data(b, title="two", vmin=0.0, vmax=100.0, axis_transform=transform)

    assert canvas._image_artist is first
    assert np.allclose(np.asarray(canvas._image_artist.get_array()), b)
    assert canvas.ax.get_title() == "two"


def test_canvas_rebuilds_when_the_shape_changes():
    _app()
    canvas = SolarMatplotlibCanvas()
    a = (np.random.default_rng(7).random((64, 64)) * 100).astype(np.float32)
    canvas.plot_map_data(a, title="one", vmin=0.0, vmax=100.0)
    first = canvas._image_artist

    bigger = (np.random.default_rng(9).random((128, 128)) * 100).astype(np.float32)
    canvas.plot_map_data(bigger, title="two", vmin=0.0, vmax=100.0)
    assert canvas._image_artist is not first


def test_clear_plot_drops_the_reuse_signature():
    _app()
    canvas = SolarMatplotlibCanvas()
    canvas.plot_map_data(np.zeros((32, 32), dtype=np.float32), title="x", vmin=0.0, vmax=1.0)
    canvas.clear_plot()
    assert canvas._image_artist is None
    assert canvas._plot_signature is None


# --- window playback wiring ------------------------------------------------


def test_fast_preview_is_off_when_idle(window):
    assert window._fast_preview_active is False


def test_playing_enables_fast_preview_and_pausing_restores_it(window):
    window.play_frames()
    assert window._fast_preview_active is True
    assert window.matplotlib_canvas._display_pixel_limit is not None

    window.pause_frames()
    assert window._fast_preview_active is False
    assert window.matplotlib_canvas._display_pixel_limit is None


def test_clip_drag_enables_fast_preview_then_settles(window):
    window._schedule_clip_render()
    assert window._fast_preview_active is True

    # No further changes: the settle pass restores full quality.
    window._clip_render_pending = False
    window._flush_clip_render()
    assert window._fast_preview_active is False


def test_clip_settle_does_not_disturb_running_playback(window):
    window.play_frames()
    window._schedule_clip_render()
    window._clip_render_pending = False
    window._flush_clip_render()
    assert window._fast_preview_active is True


def test_playback_keeps_full_resolution_data_for_analysis(window):
    """Decimation is display-only: the analysis array stays full size."""
    window.play_frames()
    window._set_frame_index(1)
    assert window._current_map_data.shape == window._map_frames[1].data.shape


def test_display_pixel_limit_covers_the_widget(window):
    window.matplotlib_canvas.resize(1600, 1200)
    assert window._playback_display_pixel_limit() >= 1024
