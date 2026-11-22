"""
e-CALLISTO FITS Analyzer
Version 3.3.0
Sahan S Liyanage
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Turning solar images north-up.

Some instruments store their images rotated: raw SDO/HMI frames are about
180 deg round (CROTA2 ~ 179.93), so shown as stored the Sun is upside down and
east/west are swapped. The analysis window draws images on an axis-aligned
arcsec grid, so everything placed by solar coordinates -- NOAA/HEK labels, the
lat/lon grid, the cursor readout, CME position angles -- lands on the mirror
image of the right spot unless the frame is first turned north-up.

:func:`north_up` returns a new map with solar north up and the header updated,
so pixel and world coordinates stay consistent:

* a rotation within ``tolerance_deg`` of 0 is left alone (AIA level 1 is a
  fraction of a degree; the measurement geometry already accounts for it);
* within ``tolerance_deg`` of 180 deg (raw HMI) the pixels are flipped exactly
  -- no interpolation, no padding -- and the reference pixel and rotation are
  updated; the small remainder (~0.07 deg for HMI) stays in the header;
* anything else is rotated with SunPy's ``Map.rotate`` (cubic interpolation).

The rotation is read from the map's ``rotation_matrix``, so instrument classes
that know a header CROTA must be ignored (Helioviewer LASCO JP2s are already
rotated) are respected.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

#: Rotations smaller than this (deg) are not worth resampling for.
DEFAULT_TOLERANCE_DEG = 0.5


def rotation_angle_deg(frame: Any) -> float | None:
    """The map's rotation from solar north (deg, in (-180, 180]); None if unknown."""
    matrix = getattr(frame, "rotation_matrix", None)
    if matrix is None:
        return None
    try:
        m = np.asarray(matrix, dtype=float)
        angle = math.degrees(math.atan2(float(m[1, 0]), float(m[0, 0])))
    except Exception:
        return None
    if not math.isfinite(angle):
        return None
    angle = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if angle == -180.0 else angle


def needs_north_up(frame: Any, *, tolerance_deg: float = DEFAULT_TOLERANCE_DEG) -> bool:
    angle = rotation_angle_deg(frame)
    return angle is not None and abs(angle) > float(tolerance_deg)


def north_up(frame: Any, *, tolerance_deg: float = DEFAULT_TOLERANCE_DEG) -> Any:
    """``frame`` turned so solar north is up (the same object when nothing to do)."""
    angle = rotation_angle_deg(frame)
    if angle is None or abs(angle) <= float(tolerance_deg):
        return frame
    if abs(abs(angle) - 180.0) <= float(tolerance_deg):
        flipped = _flip_180(frame)
        if flipped is not None:
            return flipped
    return _rotate_with_sunpy(frame)


def _flip_180(frame: Any) -> Any | None:
    """Exact half-turn: reverse both pixel axes and move the reference pixel.

    With world = R(rho) * (p - crpix), reversing the axes maps the pixel offset
    to its negative, which is R(180); so the new rotation is rho - 180 and the
    new reference pixel is mirrored (FITS pixels are 1-based: n + 1 - crpix).
    """
    try:
        import sunpy.map
    except Exception:
        return None
    data = np.asarray(getattr(frame, "data", None))
    if data.ndim != 2:
        return None
    meta = dict(getattr(frame, "meta", {}) or {})
    ny, nx = data.shape
    lower = {str(k).lower(): k for k in meta}

    def _get(key: str) -> Any:
        return meta.get(lower.get(key, key))

    def _set(key: str, value: Any) -> None:
        meta[lower.get(key, key)] = value

    crpix1, crpix2 = _get("crpix1"), _get("crpix2")
    if crpix1 is None or crpix2 is None:
        return None
    _set("crpix1", float(nx) + 1.0 - float(crpix1))
    _set("crpix2", float(ny) + 1.0 - float(crpix2))

    has_pc = all(lower.get(k) in meta for k in ("pc1_1", "pc1_2", "pc2_1", "pc2_2"))
    if has_pc:
        for key in ("pc1_1", "pc1_2", "pc2_1", "pc2_2"):
            _set(key, -float(_get(key)))
    else:
        rho = _get("crota2")
        if rho is None:
            rho = _get("crota")
        rho = 180.0 if rho is None else float(rho)
        new_rho = (rho - 180.0 + 180.0) % 360.0 - 180.0
        _set("crota2", new_rho)
        if lower.get("crota") in meta:
            _set("crota", new_rho)
    for key in ("cd1_1", "cd1_2", "cd2_1", "cd2_2"):
        if lower.get(key) in meta:
            _set(key, -float(_get(key)))
    # A reversed view, not a copy: the window keeps the stored frames too (so
    # North Up can be switched off), and a sequence of 4k frames must not cost
    # twice the memory.
    new_data = data[::-1, ::-1]
    try:
        rotated = sunpy.map.Map(new_data, meta)
    except Exception:
        return None
    # Only accept the flip if SunPy now reads the frame as (nearly) north-up.
    angle = rotation_angle_deg(rotated)
    if angle is None or abs(angle) > 1.0:
        return None
    return rotated


def _rotate_with_sunpy(frame: Any) -> Any:
    rotate = getattr(frame, "rotate", None)
    if rotate is None:
        return frame
    try:
        return rotate(order=3, missing=np.nan)
    except TypeError:
        return rotate()
    except Exception:
        return frame
