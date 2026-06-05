"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

from src.Backend.measurements import MeasurementPoint, calculate_two_point_measurement


def test_two_point_measurement_orders_by_time_for_signed_values():
    result = calculate_two_point_measurement((30.0, 45.0), (10.0, 85.0))

    assert result.point1 == MeasurementPoint(30.0, 45.0)
    assert result.point2 == MeasurementPoint(10.0, 85.0)
    assert result.start_point == MeasurementPoint(10.0, 85.0)
    assert result.stop_point == MeasurementPoint(30.0, 45.0)
    assert result.duration_s == pytest.approx(20.0)
    assert result.frequency_delta_mhz == pytest.approx(-40.0)
    assert result.slope_mhz_s == pytest.approx(-2.0)


def test_two_point_measurement_rejects_zero_time_span():
    with pytest.raises(ValueError, match="different time"):
        calculate_two_point_measurement((10.0, 50.0), (10.0, 60.0))


def test_two_point_measurement_rejects_nonfinite_points():
    with pytest.raises(ValueError, match="finite"):
        calculate_two_point_measurement((10.0, 50.0), (float("nan"), 60.0))
