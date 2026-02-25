"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from src.Backend.sunpy_analysis import classify_goes_flux, summarize_map_roi, summarize_xrs_interval


def test_summarize_map_roi_with_bounds():
    data = np.arange(100, dtype=float).reshape(10, 10)
    summary = summarize_map_roi(data, roi_bounds=(2, 6, 1, 5))
    assert summary.n_pixels == 16
    assert summary.min == 12.0
    assert summary.max == 45.0
    assert summary.mean > 20.0


def test_summarize_map_roi_handles_nonfinite_values():
    data = np.array([[1.0, np.nan], [np.inf, 4.0]], dtype=float)
    summary = summarize_map_roi(data)
    assert summary.n_pixels == 2
    assert summary.min == 1.0
    assert summary.max == 4.0


def test_classify_goes_flux_boundaries():
    assert classify_goes_flux(5e-8).startswith("A")
    assert classify_goes_flux(5e-7).startswith("B")
    assert classify_goes_flux(5e-6).startswith("C")
    assert classify_goes_flux(5e-5).startswith("M")
    assert classify_goes_flux(2e-4).startswith("X")


def test_summarize_xrs_interval():
    t0 = datetime(2026, 2, 10, 1, 0, 0)
    times = [t0 + timedelta(minutes=i) for i in range(5)]
    flux = [1e-7, 2e-7, 8e-6, 3e-6, 1e-6]
    summary = summarize_xrs_interval(flux, times=times)
    assert np.isclose(summary.peak_flux, 8e-6)
    assert summary.peak_time == times[2]
    assert summary.rise_seconds == 120.0
    assert summary.decay_seconds == 120.0
    assert summary.flare_class.startswith("C")

