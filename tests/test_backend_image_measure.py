"""
e-CALLISTO FITS Analyzer
Unit tests for plane-of-sky image measurements (src/Backend/image_measure.py).
"""

from __future__ import annotations

import numpy as np
import pytest

from src.Backend.coronagraph import RSUN_KM
from src.Backend.image_measure import (
    RegionStats,
    RulerResult,
    line_profile,
    region_stats,
    ruler_measurement,
)


@pytest.mark.parametrize(
    "p1, expected_pa",
    [
        ((0.0, 100.0), 0.0),  # solar north (+y)
        ((-100.0, 0.0), 90.0),  # east (-x)
        ((0.0, -100.0), 180.0),  # south (-y)
        ((100.0, 0.0), 270.0),  # west (+x)
        ((-100.0, 100.0), 45.0),  # north-east
    ],
)
def test_ruler_position_angle_cardinals(p1, expected_pa):
    result = ruler_measurement((0.0, 0.0), p1)
    assert result.position_angle_deg == pytest.approx(expected_pa, abs=1e-9)


def test_ruler_distances_with_rsun():
    # 960" apart with Rsun=960" -> exactly 1 solar radius = RSUN_KM kilometres.
    result = ruler_measurement((0.0, 0.0), (0.0, 960.0), rsun_arcsec=960.0)
    assert result.distance_arcsec == pytest.approx(960.0)
    assert result.distance_rsun == pytest.approx(1.0)
    assert result.distance_km == pytest.approx(RSUN_KM)


def test_ruler_without_rsun_leaves_conversions_none():
    result = ruler_measurement((10.0, 20.0), (13.0, 24.0))
    assert result.distance_arcsec == pytest.approx(5.0)  # 3-4-5 triangle
    assert result.distance_rsun is None
    assert result.distance_km is None
    assert result.dx_arcsec == pytest.approx(3.0)
    assert result.dy_arcsec == pytest.approx(4.0)


def test_line_profile_on_horizontal_gradient():
    image = np.tile(np.arange(10, dtype=float), (5, 1))  # value == column index
    distances, intensity = line_profile(image, (0.0, 2.0), (9.0, 2.0))
    assert distances[0] == 0.0
    assert distances[-1] == pytest.approx(9.0)
    assert intensity[0] == pytest.approx(0.0)
    assert intensity[-1] == pytest.approx(9.0)
    assert np.all(np.diff(intensity) >= 0)  # monotonic along the gradient


def test_line_profile_marks_outside_as_nan():
    image = np.ones((4, 4))
    _, intensity = line_profile(image, (0.0, 0.0), (8.0, 0.0))
    assert np.isnan(intensity[-1])  # runs off the right edge
    assert intensity[0] == pytest.approx(1.0)


def test_line_profile_rejects_non_2d():
    with pytest.raises(ValueError):
        line_profile(np.zeros(5), (0, 0), (1, 1))


def test_region_stats_delta_function_centroid():
    image = np.zeros((10, 10))
    image[6, 3] = 50.0
    stats = region_stats(image, (0, 10, 0, 10))
    assert isinstance(stats, RegionStats)
    assert stats.max == pytest.approx(50.0)
    assert stats.centroid_x_pix == pytest.approx(3.0)
    assert stats.centroid_y_pix == pytest.approx(6.0)
    assert stats.n_pixels == 100


def test_region_stats_zero_signal_uses_geometric_centre():
    image = np.zeros((10, 10))
    stats = region_stats(image, (2, 8, 4, 10))
    assert stats.centroid_x_pix == pytest.approx((2 + 7) / 2.0)
    assert stats.centroid_y_pix == pytest.approx((4 + 9) / 2.0)
    assert stats.mean == pytest.approx(0.0)


def test_region_stats_respects_bounds():
    image = np.zeros((10, 10))
    image[0, 0] = 1000.0  # outside the region below
    image[5, 5] = 10.0
    stats = region_stats(image, (4, 8, 4, 8))
    assert stats.max == pytest.approx(10.0)
    assert stats.n_pixels == 16


def test_ruler_result_is_dataclass():
    result = ruler_measurement((0.0, 0.0), (0.0, 1.0))
    assert isinstance(result, RulerResult)
