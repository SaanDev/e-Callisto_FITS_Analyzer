"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Plane-of-sky measurements on solar images (ruler, line profile, region stats).

These are the pure-math backends for the Solar Image Analysis measurement
tools. Everything is plain numpy over helioprojective-arcsec / pixel inputs so
it unit-tests without Qt or SunPy. CME height–time fitting lives in
``src/Backend/coronagraph.py`` (``fit_height_time``); this module provides the
generic geometry the interactive tools share.

Conventions (matching the analyzer's canvases): helioprojective ``Tx``
(x, arcsec) grows toward solar west, ``Ty`` (y, arcsec) toward solar north.
Position angle follows the solar-physics convention: measured from solar north
(+Y) rotating counter-clockwise through east (−X), in [0, 360).
Pixel arrays are indexed ``[row, col]`` with row 0 at the array origin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.Backend.coronagraph import RSUN_KM


# Arcseconds subtended by one radian.
_ARCSEC_PER_RADIAN = 180.0 / math.pi * 3600.0


@dataclass(frozen=True)
class RulerResult:
    """Two-point plane-of-sky distance and orientation."""

    dx_arcsec: float
    dy_arcsec: float
    distance_arcsec: float
    distance_rsun: float | None  # None when the solar radius is unknown
    distance_km: float | None  # plane-of-sky, small-angle approximation
    position_angle_deg: float  # N -> E (counter-clockwise), [0, 360)


def ruler_measurement(
    p0_arcsec: tuple[float, float],
    p1_arcsec: tuple[float, float],
    *,
    rsun_arcsec: float | None = None,
) -> RulerResult:
    """Measure the segment from ``p0`` to ``p1`` (helioprojective arcsec).

    ``rsun_arcsec`` (the apparent solar radius, e.g. header ``RSUN_OBS``)
    converts the angular distance into solar radii and kilometres; both are
    ``None`` when it is not supplied.
    """
    x0, y0 = float(p0_arcsec[0]), float(p0_arcsec[1])
    x1, y1 = float(p1_arcsec[0]), float(p1_arcsec[1])
    dx = x1 - x0
    dy = y1 - y0
    distance = math.hypot(dx, dy)

    # Position angle N->E: 0 deg at solar north (+y), 90 deg at east (-x).
    pa = math.degrees(math.atan2(-dx, dy)) % 360.0

    distance_rsun: float | None = None
    distance_km: float | None = None
    if rsun_arcsec is not None and float(rsun_arcsec) > 0:
        distance_rsun = distance / float(rsun_arcsec)
        distance_km = distance_rsun * RSUN_KM

    return RulerResult(
        dx_arcsec=dx,
        dy_arcsec=dy,
        distance_arcsec=distance,
        distance_rsun=distance_rsun,
        distance_km=distance_km,
        position_angle_deg=pa,
    )


def line_profile(
    image: np.ndarray,
    p0_pix: tuple[float, float],
    p1_pix: tuple[float, float],
    *,
    n_samples: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample intensity along the segment ``p0 -> p1`` (pixel coordinates).

    Returns ``(distance_px_along_line, intensity)`` with nearest-neighbour
    sampling; samples that fall outside the image are NaN. Generalises
    ``hi_jmap.slit_profile`` (which is anchored at Sun centre) to an arbitrary
    two-point segment, e.g. across a loop, filament or CME front.
    """
    image = np.asarray(image, dtype=float)
    if image.ndim != 2:
        raise ValueError(f"line_profile expects a 2-D image, got shape {image.shape}.")
    ny, nx = image.shape

    x0, y0 = float(p0_pix[0]), float(p0_pix[1])
    x1, y1 = float(p1_pix[0]), float(p1_pix[1])
    length = math.hypot(x1 - x0, y1 - y0)
    n = int(n_samples) if n_samples else int(max(2, round(length) + 1))

    distances = np.linspace(0.0, length, n)
    cols = np.rint(np.linspace(x0, x1, n)).astype(int)
    rows = np.rint(np.linspace(y0, y1, n)).astype(int)

    valid = (rows >= 0) & (rows < ny) & (cols >= 0) & (cols < nx)
    intensity = np.full(n, np.nan, dtype=float)
    intensity[valid] = image[rows[valid], cols[valid]]
    return distances, intensity


@dataclass(frozen=True)
class RegionStats:
    """Statistics of a rectangular region, with an intensity-weighted centroid."""

    n_pixels: int
    min: float
    max: float
    mean: float
    median: float
    std: float
    centroid_x_pix: float  # full-image pixel coordinates
    centroid_y_pix: float


def region_stats(image: np.ndarray, bounds: tuple[int, int, int, int]) -> RegionStats:
    """Summarise the rectangle ``(x0, x1, y0, y1)`` of ``image``.

    Moments come from :func:`sunpy_analysis.summarize_map_roi`; the centroid is
    intensity-weighted (negative weights clipped to zero) and reported in
    full-image pixel coordinates. When the region carries no positive signal the
    geometric centre is returned.
    """
    from src.Backend.sunpy_analysis import summarize_map_roi

    image = np.asarray(image, dtype=float)
    if image.ndim != 2:
        raise ValueError(f"region_stats expects a 2-D image, got shape {image.shape}.")

    summary = summarize_map_roi(image, bounds)

    x0, x1, y0, y1 = bounds
    x_low, x_high = sorted((int(x0), int(x1)))
    y_low, y_high = sorted((int(y0), int(y1)))
    x_low = max(0, x_low)
    y_low = max(0, y_low)
    x_high = min(image.shape[1], x_high)
    y_high = min(image.shape[0], y_high)

    cx = (x_low + max(x_high - 1, x_low)) / 2.0
    cy = (y_low + max(y_high - 1, y_low)) / 2.0
    if x_high > x_low and y_high > y_low:
        region = image[y_low:y_high, x_low:x_high]
        weights = np.where(np.isfinite(region), np.clip(region, 0.0, None), 0.0)
        total = float(weights.sum())
        if total > 0:
            yy, xx = np.mgrid[y_low:y_high, x_low:x_high]
            cx = float((weights * xx).sum() / total)
            cy = float((weights * yy).sum() / total)

    return RegionStats(
        n_pixels=summary.n_pixels,
        min=summary.min,
        max=summary.max,
        mean=summary.mean,
        median=summary.median,
        std=summary.std,
        centroid_x_pix=cx,
        centroid_y_pix=cy,
    )
