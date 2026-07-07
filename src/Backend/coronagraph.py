"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

White-light coronagraph processing shared by STEREO/SECCHI COR1 & COR2 and
SOHO/LASCO C2 & C3.

Two capabilities live here, both plain numpy so they unit-test without SunPy or
network access:

* Normalizing-Radial-Graded Filter (NRGF; Morgan, Habbal & Woo 2006). A
  coronagraph image is dominated by the steep radial fall-off of the K/F-corona,
  which buries faint CME fronts. The NRGF subtracts the azimuthal mean and
  divides by the azimuthal standard deviation within each thin annulus, so every
  radius is stretched to unit contrast and CME structure becomes visible at all
  heights.

* CME height-time analysis. Given the leading-edge height picked (or detected)
  in successive frames, fit a plane-of-sky height-time curve and report the CME
  speed and acceleration. Helpers convert picked pixels to solar radii and sample
  intensity along a radial cut at a chosen position angle so a leading edge can
  be located.

Pixel convention: images are indexed ``[row, col]`` with ``row`` increasing
downward (numpy/display order). ``center`` is ``(col, row)`` of Sun centre.
Position/​cut angles are measured counter-clockwise from the +x (column, rightward)
axis in the displayed orientation, i.e. ``col = cx + r*cos(theta)`` and
``row = cy - r*sin(theta)`` so ``theta = 90 deg`` points visually up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

import numpy as np


# Solar radius in kilometres (IAU 2015 nominal).
RSUN_KM = 695_700.0


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def solar_center_from_meta(meta: Any, data_shape: tuple[int, int] | None = None) -> tuple[float, float]:
    """Return Sun-centre ``(col, row)`` in 0-based pixels from FITS metadata.

    Uses ``CRPIX1``/``CRPIX2`` (which are 1-based and reference ``CRVAL`` = 0,0 =
    disk centre for a Helioprojective WCS). Falls back to the array centre.
    """
    def _get(*keys: str) -> float | None:
        for key in keys:
            try:
                if key in meta:
                    return float(meta[key])
            except Exception:
                continue
            try:
                value = getattr(meta, key.lower(), None)
                if value is not None:
                    return float(value)
            except Exception:
                continue
        return None

    crpix1 = _get("CRPIX1", "crpix1")
    crpix2 = _get("CRPIX2", "crpix2")
    if crpix1 is not None and crpix2 is not None:
        # FITS CRPIX is 1-based; convert to 0-based array index.
        return (crpix1 - 1.0, crpix2 - 1.0)
    if data_shape is not None:
        ny, nx = data_shape
        return ((nx - 1) / 2.0, (ny - 1) / 2.0)
    raise ValueError("Cannot determine solar centre: no CRPIX and no data_shape.")


def radial_distance_grid(shape: tuple[int, int], center: tuple[float, float]) -> np.ndarray:
    """Return an array of radial distance (pixels) from ``center`` for every pixel."""
    ny, nx = shape
    cx, cy = center
    yy, xx = np.mgrid[0:ny, 0:nx]
    return np.hypot(xx - cx, yy - cy)


# --------------------------------------------------------------------------- #
# Radial profile + NRGF
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RadialProfile:
    """Azimuthal statistics of an image as a function of radius (pixels)."""

    r_centers: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    r_edges: np.ndarray


def radial_profile(
    image: np.ndarray,
    center: tuple[float, float],
    *,
    n_bins: int = 100,
    r_min: float | None = None,
    r_max: float | None = None,
) -> RadialProfile:
    """Compute the azimuthal mean and std of ``image`` in ``n_bins`` annuli.

    NaNs are ignored. Empty annuli get ``mean=nan``/``std=nan``.
    """
    image = np.asarray(image, dtype=float)
    if image.ndim != 2:
        raise ValueError(f"radial_profile expects a 2-D image, got shape {image.shape}.")
    radius = radial_distance_grid(image.shape, center)

    lo = 0.0 if r_min is None else float(r_min)
    hi = float(radius.max()) if r_max is None else float(r_max)
    if hi <= lo:
        raise ValueError("r_max must be greater than r_min.")
    edges = np.linspace(lo, hi, int(n_bins) + 1)

    flat_r = radius.ravel()
    flat_i = image.ravel()
    finite = np.isfinite(flat_i) & (flat_r >= lo) & (flat_r <= hi)
    flat_r = flat_r[finite]
    flat_i = flat_i[finite]

    # Bin index per pixel (clip the top edge into the last bin).
    idx = np.clip(np.digitize(flat_r, edges) - 1, 0, n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins).astype(float)
    sums = np.bincount(idx, weights=flat_i, minlength=n_bins)
    sums_sq = np.bincount(idx, weights=flat_i * flat_i, minlength=n_bins)

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(counts > 0, sums / counts, np.nan)
        var = np.where(counts > 0, sums_sq / counts - mean**2, np.nan)
    var = np.where(np.isfinite(var), np.clip(var, 0.0, None), np.nan)
    std = np.sqrt(var)
    centers = 0.5 * (edges[:-1] + edges[1:])
    return RadialProfile(r_centers=centers, mean=mean, std=std, r_edges=edges)


def nrgf(
    image: np.ndarray,
    center: tuple[float, float],
    *,
    n_bins: int = 100,
    r_min: float | None = None,
    r_max: float | None = None,
    fill: float = np.nan,
) -> np.ndarray:
    """Apply the Normalizing-Radial-Graded Filter.

    For each pixel at radius ``r`` the output is ``(I - mean(r)) / std(r)`` using
    the azimuthal statistics of its annulus, which flattens the radial brightness
    gradient and reveals faint CME fronts. Pixels outside ``[r_min, r_max]`` or in
    annuli with zero spread are set to ``fill``.
    """
    image = np.asarray(image, dtype=float)
    profile = radial_profile(image, center, n_bins=n_bins, r_min=r_min, r_max=r_max)
    radius = radial_distance_grid(image.shape, center)

    edges = profile.r_edges
    idx = np.digitize(radius, edges) - 1
    inside = (idx >= 0) & (idx < n_bins)
    safe_idx = np.where(inside, idx, 0)

    mean_map = profile.mean[safe_idx]
    std_map = profile.std[safe_idx]

    out = np.full(image.shape, float(fill), dtype=float)
    valid = inside & np.isfinite(image) & np.isfinite(std_map) & (std_map > 0)
    out[valid] = (image[valid] - mean_map[valid]) / std_map[valid]
    return out


def radial_graded_normalize(
    image: np.ndarray,
    center: tuple[float, float],
    *,
    n_bins: int = 100,
    r_min: float | None = None,
    r_max: float | None = None,
    fill: float = np.nan,
) -> np.ndarray:
    """Simpler cousin of :func:`nrgf`: divide each pixel by its annulus mean.

    Removes the radial fall-off (so structure at all heights is comparable) while
    preserving the sign/relative amplitude of intensity, which some users prefer
    over the fully contrast-stretched NRGF.
    """
    image = np.asarray(image, dtype=float)
    profile = radial_profile(image, center, n_bins=n_bins, r_min=r_min, r_max=r_max)
    radius = radial_distance_grid(image.shape, center)

    idx = np.digitize(radius, profile.r_edges) - 1
    inside = (idx >= 0) & (idx < n_bins)
    safe_idx = np.where(inside, idx, 0)
    mean_map = profile.mean[safe_idx]

    out = np.full(image.shape, float(fill), dtype=float)
    valid = inside & np.isfinite(image) & np.isfinite(mean_map) & (mean_map != 0)
    out[valid] = image[valid] / mean_map[valid]
    return out


# --------------------------------------------------------------------------- #
# Radial cut (for locating a CME leading edge along a position angle)
# --------------------------------------------------------------------------- #
def radial_cut(
    image: np.ndarray,
    center: tuple[float, float],
    angle_deg: float,
    *,
    r_max: float | None = None,
    n_samples: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample intensity along a ray from Sun centre at ``angle_deg``.

    Returns ``(radii_pixels, intensity)`` sampled with nearest-neighbour lookup.
    ``angle_deg`` is measured counter-clockwise from the +x axis (see module
    docstring), so 90 deg points visually up.
    """
    image = np.asarray(image, dtype=float)
    ny, nx = image.shape
    cx, cy = center
    if r_max is None:
        r_max = float(min(cx, nx - 1 - cx, cy, ny - 1 - cy))
        if r_max <= 0:
            r_max = float(min(nx, ny)) / 2.0
    n = int(n_samples) if n_samples else int(max(2, round(r_max)))
    radii = np.linspace(0.0, float(r_max), n)

    theta = np.deg2rad(float(angle_deg))
    cols = np.rint(cx + radii * np.cos(theta)).astype(int)
    rows = np.rint(cy - radii * np.sin(theta)).astype(int)

    valid = (rows >= 0) & (rows < ny) & (cols >= 0) & (cols < nx)
    intensity = np.full(radii.shape, np.nan, dtype=float)
    intensity[valid] = image[rows[valid], cols[valid]]
    return radii, intensity


# --------------------------------------------------------------------------- #
# Pixel -> solar radii, and height-time fitting
# --------------------------------------------------------------------------- #
def pixel_radius_to_rsun(pixel_radius: float, cdelt_arcsec: float, rsun_arcsec: float) -> float:
    """Convert a plane-of-sky radius in pixels to solar radii.

    ``cdelt_arcsec`` is the plate scale ("/pixel) and ``rsun_arcsec`` the apparent
    solar radius (") for the observer, both available from the map header
    (``CDELT1`` and ``RSUN_OBS``).
    """
    if rsun_arcsec <= 0:
        raise ValueError("rsun_arcsec must be positive.")
    return float(pixel_radius) * float(cdelt_arcsec) / float(rsun_arcsec)


@dataclass(frozen=True)
class HeightTimeFit:
    """Result of a CME height-time fit (plane-of-sky)."""

    times_s: np.ndarray
    heights_km: np.ndarray
    speed_km_s: float
    acceleration_km_s2: float
    intercept_km: float
    # Instantaneous speed between successive picked points (len = n-1).
    segment_speeds_km_s: np.ndarray
    segment_times_s: np.ndarray


def fit_height_time(
    times: Sequence[float] | Sequence[datetime],
    heights_km: Sequence[float],
) -> HeightTimeFit:
    """Fit height(t) for a CME leading edge and derive speed & acceleration.

    ``times`` may be seconds (relative to any epoch) or ``datetime`` objects; the
    first sample defines t=0. A linear fit gives the mean plane-of-sky speed and a
    quadratic fit ``h = h0 + v t + 0.5 a t^2`` gives the constant acceleration
    (reported as ``2 * quadratic_coeff``). Needs at least two points for speed and
    three for acceleration.
    """
    times_s = _to_seconds(times)
    heights = np.asarray(heights_km, dtype=float)
    if times_s.shape != heights.shape:
        raise ValueError("times and heights_km must have the same length.")
    order = np.argsort(times_s)
    times_s = times_s[order]
    heights = heights[order]
    if times_s.size < 2:
        raise ValueError("At least two points are required for a height-time fit.")

    # Linear fit -> mean speed.
    lin = np.polyfit(times_s, heights, 1)
    speed = float(lin[0])
    intercept = float(lin[1])

    # Quadratic fit -> acceleration (only if enough points).
    if times_s.size >= 3:
        quad = np.polyfit(times_s, heights, 2)
        acceleration = float(2.0 * quad[0])
    else:
        acceleration = float("nan")

    dt = np.diff(times_s)
    with np.errstate(invalid="ignore", divide="ignore"):
        seg_speeds = np.where(dt != 0, np.diff(heights) / dt, np.nan)
    seg_times = 0.5 * (times_s[:-1] + times_s[1:])

    return HeightTimeFit(
        times_s=times_s,
        heights_km=heights,
        speed_km_s=speed,
        acceleration_km_s2=acceleration,
        intercept_km=intercept,
        segment_speeds_km_s=seg_speeds,
        segment_times_s=seg_times,
    )


def _to_seconds(times: Sequence[float] | Sequence[datetime]) -> np.ndarray:
    values = list(times)
    if not values:
        return np.asarray([], dtype=float)
    if isinstance(values[0], datetime):
        t0 = values[0]
        return np.asarray([(t - t0).total_seconds() for t in values], dtype=float)
    return np.asarray(values, dtype=float)
