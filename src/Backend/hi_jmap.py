"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

STEREO/SECCHI Heliospheric Imager (HI1/HI2) processing.

The Heliospheric Imagers stare into the very faint outer corona and inner
heliosphere, where the signal is buried under a bright, nearly-static F-corona
and star field. Two operations make CMEs visible and trackable:

* Background subtraction. Subtracting a temporal median (or the previous frame)
  removes the static F-corona/starfield and leaves the moving CME material.

* J-map (time-elongation map) construction. Sampling intensity along a fixed
  strip through the image — conventionally the ecliptic — and stacking those
  1-D profiles over time produces a 2-D map of elongation vs time in which a CME
  traces a characteristic slanted bright track. The track's slope gives the
  apparent radial/heliospheric propagation.

Everything is plain numpy so it unit-tests without SunPy or network access. The
slit sampling shares the pixel/angle convention documented in
``src/Backend/coronagraph.py`` (rows increase downward; angle counter-clockwise
from +x, so 90 deg points visually up).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


def subtract_background(
    frames: Sequence[np.ndarray],
    method: str = "median",
) -> list[np.ndarray]:
    """Return background-subtracted HI frames.

    ``method='median'`` subtracts the per-pixel temporal median across the whole
    stack (best for removing the static F-corona and star field). ``'previous'``
    is a plain running difference (frame minus the one before; the first frame is
    differenced against the second so the output length matches the input).
    """
    stack = [np.asarray(f, dtype=float) for f in frames]
    if not stack:
        return []
    shapes = {f.shape for f in stack}
    if len(shapes) != 1:
        raise ValueError(f"All HI frames must share one shape, got {shapes}.")

    method = str(method).lower()
    if method == "median":
        background = np.nanmedian(np.stack(stack, axis=0), axis=0)
        return [f - background for f in stack]
    if method == "previous":
        out = []
        for i, f in enumerate(stack):
            ref = stack[i - 1] if i > 0 else (stack[1] if len(stack) > 1 else f)
            out.append(f - ref)
        return out
    raise ValueError(f"Unknown background method '{method}'. Use 'median' or 'previous'.")


def slit_profile(
    image: np.ndarray,
    center: tuple[float, float],
    position_angle_deg: float,
    *,
    r_max: float | None = None,
    n_samples: int | None = None,
    half_width: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Average intensity along a radial slit at ``position_angle_deg``.

    Samples a ray from ``center`` outward and, when ``half_width > 0``, averages
    over parallel rays offset by up to ``half_width`` pixels perpendicular to the
    slit (a wider, less noisy strip). Returns ``(radii_pixels, intensity)``.
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

    theta = np.deg2rad(float(position_angle_deg))
    # Along-slit unit vector (col, row) with row increasing downward.
    ux, uy = np.cos(theta), -np.sin(theta)
    # Perpendicular unit vector (rotate the along vector by +90 deg).
    px, py = -uy, ux

    offsets = range(-int(half_width), int(half_width) + 1)
    samples = []
    for k in offsets:
        cols = np.rint(cx + radii * ux + k * px).astype(int)
        rows = np.rint(cy + radii * uy + k * py).astype(int)
        valid = (rows >= 0) & (rows < ny) & (cols >= 0) & (cols < nx)
        line = np.full(radii.shape, np.nan, dtype=float)
        line[valid] = image[rows[valid], cols[valid]]
        samples.append(line)

    with np.errstate(invalid="ignore"):
        profile = np.nanmean(np.vstack(samples), axis=0)
    return radii, profile


@dataclass(frozen=True)
class JMap:
    """A time-elongation map and its axes."""

    image: np.ndarray  # shape (n_times, n_elongation)
    radii_pixels: np.ndarray  # elongation axis, in pixels from Sun centre
    frame_index: np.ndarray  # 0..n_times-1


def build_jmap(
    frames: Sequence[np.ndarray],
    center: tuple[float, float],
    position_angle_deg: float,
    *,
    r_max: float | None = None,
    n_samples: int | None = None,
    half_width: int = 1,
) -> JMap:
    """Stack per-frame slit profiles into a (time x elongation) J-map.

    A CME moving outward along the slit appears as a bright track whose
    elongation increases with time (a slanted streak).
    """
    stack = [np.asarray(f, dtype=float) for f in frames]
    if not stack:
        raise ValueError("build_jmap needs at least one frame.")

    rows = []
    radii_ref = None
    for frame in stack:
        radii, profile = slit_profile(
            frame, center, position_angle_deg,
            r_max=r_max, n_samples=n_samples, half_width=half_width,
        )
        if radii_ref is None:
            radii_ref = radii
        rows.append(profile)

    image = np.vstack(rows)
    return JMap(
        image=image,
        radii_pixels=np.asarray(radii_ref),
        frame_index=np.arange(len(stack)),
    )


def pixel_to_elongation_deg(pixel_radius: np.ndarray | float, cdelt_arcsec: float) -> np.ndarray:
    """Convert a plane-of-sky radius in pixels to elongation in degrees.

    Elongation (angle from Sun centre) is ``pixel_radius * cdelt`` arcsec, here
    returned in degrees — the standard vertical axis of an HI J-map.
    """
    return np.asarray(pixel_radius, dtype=float) * float(cdelt_arcsec) / 3600.0
