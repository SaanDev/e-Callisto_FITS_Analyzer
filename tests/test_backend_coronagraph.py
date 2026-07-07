"""
e-CALLISTO FITS Analyzer
Unit tests for white-light coronagraph processing (src/Backend/coronagraph.py):
NRGF, radial-graded normalization, radial cuts and CME height-time fitting.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from src.Backend.coronagraph import (
    RSUN_KM,
    fit_height_time,
    nrgf,
    pixel_radius_to_rsun,
    radial_cut,
    radial_distance_grid,
    radial_graded_normalize,
    radial_profile,
    solar_center_from_meta,
)


def test_radial_distance_grid_center_is_zero():
    grid = radial_distance_grid((11, 11), (5.0, 5.0))
    assert grid[5, 5] == pytest.approx(0.0)
    assert grid[5, 0] == pytest.approx(5.0)
    assert grid[0, 5] == pytest.approx(5.0)


def test_radial_profile_recovers_linear_profile():
    center = (50.0, 50.0)
    r = radial_distance_grid((101, 101), center)
    image = 3.0 * r + 5.0  # intensity depends only on radius
    prof = radial_profile(image, center, n_bins=50, r_min=2.0, r_max=40.0)
    expected = 3.0 * prof.r_centers + 5.0
    # Middle bins are well-sampled; compare where counts are meaningful.
    good = np.isfinite(prof.mean)
    assert np.allclose(prof.mean[good], expected[good], rtol=0.05, atol=1.0)


def test_nrgf_reveals_blob_against_radial_background():
    rng = np.random.default_rng(0)
    center = (50.0, 50.0)
    r = radial_distance_grid((101, 101), center)
    background = 1000.0 * np.exp(-r / 15.0)
    # Azimuthal texture so each annulus has non-zero spread (std > 0).
    texture = background * 0.02 * rng.standard_normal(r.shape)
    field = background + texture

    blob_rc = (30, 50)  # a compact bright CME-like feature
    with_blob = field.copy()
    with_blob[28:33, 48:53] += 300.0

    out_plain = nrgf(field, center, n_bins=40, r_min=5.0, r_max=45.0)
    out_blob = nrgf(with_blob, center, n_bins=40, r_min=5.0, r_max=45.0)

    # Background NRGF is contrast-normalised ~ N(0,1): typical |value| is small.
    valid = np.isfinite(out_plain)
    assert np.nanmedian(np.abs(out_plain[valid])) < 2.0

    # The blob is revealed as a clear outlier: it beats the background's 99.5th
    # percentile and is far brighter than the same pixel without the blob.
    assert out_blob[blob_rc] > np.nanpercentile(out_plain[valid], 99.5)
    assert out_blob[blob_rc] > out_plain[blob_rc] + 2.0


def test_radial_graded_normalize_flattens_to_unity():
    center = (40.0, 40.0)
    r = radial_distance_grid((81, 81), center)
    image = 500.0 * np.exp(-r / 10.0) + 1.0  # purely radial, strictly positive
    out = radial_graded_normalize(image, center, n_bins=60, r_min=3.0, r_max=35.0)
    valid = np.isfinite(out)
    # Dividing a purely-radial image by its annulus mean yields ~1 everywhere.
    assert np.allclose(out[valid], 1.0, atol=0.05)


def test_radial_cut_locates_spoke_direction():
    center = (50.0, 50.0)
    image = np.zeros((101, 101), dtype=float)
    image[0:50, 50] = 1.0  # bright ray pointing straight up from centre

    r_up, i_up = radial_cut(image, center, 90.0, r_max=40.0)
    r_right, i_right = radial_cut(image, center, 0.0, r_max=40.0)

    assert np.nanmax(i_up) == pytest.approx(1.0)
    assert np.nanmax(i_up) > np.nanmax(i_right) + 0.5
    assert np.nanmax(i_right) == pytest.approx(0.0)


def test_pixel_radius_to_rsun():
    # 100 px at 14.7"/px with Rsun = 960" -> ~1.53 Rsun.
    val = pixel_radius_to_rsun(100.0, 14.7, 960.0)
    assert val == pytest.approx(100.0 * 14.7 / 960.0, rel=1e-9)
    with pytest.raises(ValueError):
        pixel_radius_to_rsun(10.0, 1.0, 0.0)


def test_fit_height_time_linear_speed():
    speed = 800.0  # km/s
    times = np.array([0, 600, 1200, 1800, 2400], dtype=float)
    heights = 2.0 * RSUN_KM + speed * times  # start at 2 Rsun, constant speed
    fit = fit_height_time(times, heights)
    assert fit.speed_km_s == pytest.approx(speed, rel=1e-6)
    assert abs(fit.acceleration_km_s2) < 1e-3
    assert fit.segment_speeds_km_s.shape == (4,)
    assert np.allclose(fit.segment_speeds_km_s, speed, rtol=1e-6)


def test_fit_height_time_recovers_acceleration():
    v0, accel = 300.0, 20.0  # km/s, km/s^2
    times = np.linspace(0, 3000, 11)
    heights = 1.5 * RSUN_KM + v0 * times + 0.5 * accel * times**2
    fit = fit_height_time(times, heights)
    assert fit.acceleration_km_s2 == pytest.approx(accel, rel=1e-6)


def test_fit_height_time_accepts_datetimes():
    t0 = datetime(2012, 7, 12, 16, 0, 0)
    times = [t0 + timedelta(minutes=10 * i) for i in range(4)]
    speed = 500.0
    heights = [2.0 * RSUN_KM + speed * (600 * i) for i in range(4)]
    fit = fit_height_time(times, heights)
    assert fit.speed_km_s == pytest.approx(speed, rel=1e-6)
    assert fit.times_s[-1] == pytest.approx(1800.0)


def test_fit_height_time_requires_two_points():
    with pytest.raises(ValueError):
        fit_height_time([0.0], [1.0])


def test_solar_center_from_meta_uses_crpix():
    meta = {"CRPIX1": 512.5, "CRPIX2": 256.5}
    cx, cy = solar_center_from_meta(meta)
    assert (cx, cy) == pytest.approx((511.5, 255.5))  # 1-based -> 0-based


def test_solar_center_from_meta_falls_back_to_shape():
    cx, cy = solar_center_from_meta({}, data_shape=(101, 201))
    assert (cx, cy) == pytest.approx((100.0, 50.0))
    with pytest.raises(ValueError):
        solar_center_from_meta({})
