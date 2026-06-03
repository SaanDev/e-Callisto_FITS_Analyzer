"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MeasurementPoint:
    time_s: float
    frequency_mhz: float


@dataclass(frozen=True)
class MeasurementResult:
    point1: MeasurementPoint
    point2: MeasurementPoint
    start_point: MeasurementPoint
    stop_point: MeasurementPoint
    duration_s: float
    frequency_delta_mhz: float
    slope_mhz_s: float

    @property
    def points(self) -> tuple[MeasurementPoint, MeasurementPoint]:
        return self.point1, self.point2


def _coerce_point(point) -> MeasurementPoint:
    if isinstance(point, MeasurementPoint):
        out = point
    else:
        x, y = point
        out = MeasurementPoint(float(x), float(y))
    if not math.isfinite(out.time_s) or not math.isfinite(out.frequency_mhz):
        raise ValueError("Measurement points must be finite.")
    return out


def calculate_two_point_measurement(
    point_a,
    point_b,
    *,
    epsilon_s: float = 1e-9,
) -> MeasurementResult:
    """Calculate signed time/frequency ruler values for two spectrum points."""
    p1 = _coerce_point(point_a)
    p2 = _coerce_point(point_b)
    if abs(p2.time_s - p1.time_s) <= float(epsilon_s):
        raise ValueError("Measurement needs two points with different time values.")

    start, stop = (p1, p2) if p1.time_s <= p2.time_s else (p2, p1)
    duration = float(stop.time_s - start.time_s)
    frequency_delta = float(stop.frequency_mhz - start.frequency_mhz)
    slope = float(frequency_delta / duration)
    return MeasurementResult(
        point1=p1,
        point2=p2,
        start_point=start,
        stop_point=stop,
        duration_s=duration,
        frequency_delta_mhz=frequency_delta,
        slope_mhz_s=slope,
    )
