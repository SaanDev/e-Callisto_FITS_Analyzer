"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Plane-of-sky measurements on solar images (ruler, circle fit, line profile,
region stats).

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


@dataclass(frozen=True)
class CircleFit:
    """Least-squares circle through points sampled along an arc.

    Units follow the inputs: feed arcsec, get arcsec back. ``rms_residual`` is
    the geometric residual ``sqrt(mean((d_i - R)^2))`` rather than the algebraic
    one, so it reads directly as "how far the clicked points sit off the fitted
    front". ``arc_span_deg`` is how much of the circle those points actually
    cover: a short arc pins the radius very weakly, and callers should say so
    rather than quietly reporting a confident-looking number.
    """

    center_x: float
    center_y: float
    radius: float
    rms_residual: float
    n_points: int
    arc_span_deg: float
    center_fixed: bool


def fit_circle(
    points: Sequence[Sequence[float]] | np.ndarray,
    *,
    center: tuple[float, float] | None = None,
) -> CircleFit:
    """Fit a circle to points sampled along an arc (e.g. a CME dome front).

    ``center=None`` runs the full three-parameter fit and needs at least three
    points; passing ``center`` freezes it and only the radius is solved, which
    needs a single point.

    The three-parameter fit is Taubin's, not the naive Kasa/algebraic one: a CME
    front is usually a *partial* arc, often well under 90 deg, where Kasa is
    strongly biased toward small radii while Taubin is essentially unbiased. It
    is one SVD — no iteration, no initial guess.

    Fit in helioprojective arcsec rather than pixels: the sky is isotropic in
    angle, so a spherical bubble projects to a circle in ``(Tx, Ty)`` even when
    ``CDELT1 != CDELT2``, where the same front would be an ellipse in pixels.

    Raises ``ValueError`` when the points are too few, coincident, or so nearly
    collinear that no circle is determined.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    # Drop non-finite rows rather than letting one bad click poison the fit.
    pts = pts[np.isfinite(pts).all(axis=1)]
    n = int(pts.shape[0])
    x = pts[:, 0]
    y = pts[:, 1]

    if center is not None:
        if n < 1:
            raise ValueError("A fixed-centre circle fit needs at least one point.")
        cx = float(center[0])
        cy = float(center[1])
        distances = np.hypot(x - cx, y - cy)
        # The mean distance is the exact least-squares minimiser of sum((d - R)^2).
        radius = float(distances.mean())
        rms = float(np.sqrt(np.mean((distances - radius) ** 2)))
        return CircleFit(cx, cy, radius, rms, n, _arc_span_deg(x, y, cx, cy), True)

    if n < 3:
        raise ValueError("A circle fit needs at least three points.")

    # Centring the data is what keeps the normal equations well conditioned.
    mx = float(x.mean())
    my = float(y.mean())
    xc = x - mx
    yc = y - my
    z = xc * xc + yc * yc
    z_mean = float(z.mean())
    if z_mean <= 0.0:
        raise ValueError("Circle fit is degenerate: all points coincide.")
    z0 = (z - z_mean) / (2.0 * math.sqrt(z_mean))

    _, _, vt = np.linalg.svd(np.column_stack((z0, xc, yc)), full_matrices=False)
    coeffs = vt[-1]  # right singular vector of the smallest singular value
    a0 = float(coeffs[0]) / (2.0 * math.sqrt(z_mean))
    a1 = float(coeffs[1])
    a2 = float(coeffs[2])
    a3 = -z_mean * a0

    collinear = "Circle fit is degenerate: the points are (nearly) collinear."
    discriminant = a1 * a1 + a2 * a2 - 4.0 * a0 * a3
    if not math.isfinite(discriminant) or discriminant <= 0.0 or abs(a0) < 1e-300:
        raise ValueError(collinear)

    cx = -a1 / (2.0 * a0) + mx
    cy = -a2 / (2.0 * a0) + my
    radius = math.sqrt(discriminant) / (2.0 * abs(a0))
    # a0 -> 0 is the collinear limit, where the radius runs away from the data.
    span = float(np.hypot(xc, yc).max())
    if not math.isfinite(radius) or radius > 1e6 * max(span, 1e-12):
        raise ValueError(collinear)

    distances = np.hypot(x - cx, y - cy)
    rms = float(np.sqrt(np.mean((distances - radius) ** 2)))
    return CircleFit(cx, cy, radius, rms, n, _arc_span_deg(x, y, cx, cy), False)


def _arc_span_deg(x: np.ndarray, y: np.ndarray, cx: float, cy: float) -> float:
    """Angular extent the points cover about ``(cx, cy)``: 360 minus the largest gap."""
    if x.size < 2:
        return 0.0
    angles = np.sort(np.arctan2(y - cy, x - cx))
    gaps = np.diff(np.concatenate([angles, [angles[0] + 2.0 * np.pi]]))
    return float(360.0 - np.degrees(float(gaps.max())))


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
