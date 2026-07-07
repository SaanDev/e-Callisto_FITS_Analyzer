"""
e-CALLISTO FITS Analyzer
Unit tests for STEREO/SECCHI Heliospheric Imager processing (src/Backend/hi_jmap.py):
background subtraction and time-elongation (J-map) construction.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.Backend.hi_jmap import (
    build_jmap,
    pixel_to_elongation_deg,
    slit_profile,
    subtract_background,
)


def test_subtract_background_median_removes_static_starfield():
    shape = (40, 40)
    starfield = np.zeros(shape)
    starfield[10, 10] = 100.0  # a persistent "star"
    starfield[25, 30] = 80.0

    frames = []
    for t in range(6):
        f = starfield.copy()
        f[20, 5 + 5 * t] += 50.0  # a feature moving across the field
        frames.append(f)

    out = subtract_background(frames, method="median")
    assert len(out) == 6
    # The static stars are removed (near zero) in every subtracted frame.
    for sub in out:
        assert abs(sub[10, 10]) < 1e-6
        assert abs(sub[25, 30]) < 1e-6
    # The moving feature survives in the frame where it sits.
    assert out[0][20, 5] == pytest.approx(50.0)
    assert out[3][20, 20] == pytest.approx(50.0)


def test_subtract_background_previous_is_running_difference():
    a = np.full((8, 8), 1.0)
    b = np.full((8, 8), 3.0)
    c = np.full((8, 8), 7.0)
    out = subtract_background([a, b, c], method="previous")
    assert len(out) == 3
    assert np.allclose(out[1], 2.0)  # b - a
    assert np.allclose(out[2], 4.0)  # c - b


def test_subtract_background_validates_shapes():
    with pytest.raises(ValueError):
        subtract_background([np.zeros((4, 4)), np.zeros((5, 5))])
    with pytest.raises(ValueError):
        subtract_background([np.zeros((4, 4))], method="nope")


def test_slit_profile_locates_feature_radius():
    image = np.zeros((61, 61))
    center = (30.0, 30.0)
    # A single bright pixel exactly 17 px to the right of centre (position angle 0).
    image[30, 47] = 5.0
    radii, profile = slit_profile(image, center, 0.0, r_max=28.0, half_width=0)
    peak_r = radii[int(np.nanargmax(profile))]
    assert peak_r == pytest.approx(17.0, abs=2.0)


def test_build_jmap_shows_outward_moving_track():
    shape = (61, 61)
    center = (30.0, 30.0)
    frames = []
    radii_true = []
    for t in range(8):
        f = np.zeros(shape)
        r = 4 + 3 * t  # feature marches outward along position angle 0
        col = int(round(center[0] + r))
        f[29:32, col - 1:col + 2] = 10.0
        frames.append(f)
        radii_true.append(r)

    jmap = build_jmap(frames, center, 0.0, r_max=28.0, half_width=1)
    assert jmap.image.shape[0] == 8  # one row per frame
    assert jmap.image.shape[1] == jmap.radii_pixels.size

    # Peak elongation per time row increases monotonically (the CME track).
    peak_radii = jmap.radii_pixels[np.nanargmax(jmap.image, axis=1)]
    assert np.all(np.diff(peak_radii) > 0)
    assert peak_radii[0] < peak_radii[-1]


def test_pixel_to_elongation_deg():
    # 3600 px at 1"/px -> 1 degree.
    val = pixel_to_elongation_deg(3600.0, 1.0)
    assert float(val) == pytest.approx(1.0)
    arr = pixel_to_elongation_deg(np.array([0.0, 1800.0, 3600.0]), 2.0)
    assert np.allclose(arr, [0.0, 1.0, 2.0])
