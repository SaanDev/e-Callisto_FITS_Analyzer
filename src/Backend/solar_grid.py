"""
e-CALLISTO FITS Analyzer
Solar coordinate graticule helpers (src/Backend/solar_grid.py).

Turns a displayed solar frame's helioprojective coordinate system into a true
curvilinear coordinate graticule — iso-longitude meridians and iso-latitude
parallels projected onto the disk — for a selectable reference frame:

  * HCI  — Heliocentric Inertial (inertial, tied to the solar rotation axis and
           the ascending node of the solar equator on the ecliptic)
  * HGS  — Heliographic Stonyhurst (Earth/observer central-meridian based)
  * HGC  — Heliographic Carrington (rotating Carrington longitude)

The maths follows the same recipe already proven for the AIA/EUVI limb overlay
in ``src/UI/sunpy_plot_window.py`` (``_compute_aia_limb_arcsec``): sample points
on the solar surface in the target frame, transform them into the map's own
helioprojective frame, keep the near-side hemisphere, and read the resulting
helioprojective ``Tx``/``Ty`` in arcsec (the view coordinates the canvases plot
in). No Qt here so the module stays unit-testable.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np

from src.Backend.solar_data_analysis import frame_observation_time


# Ordered keys and their user-facing names (shared by the UI combo box).
FRAME_KEYS: tuple[str, ...] = ("HCI", "HGS", "HGC")
FRAME_LABELS: dict[str, str] = {"HCI": "HCI", "HGS": "HGS", "HGC": "HGC"}
FRAME_DISPLAY_NAMES: dict[str, str] = {
    "HCI": "Heliocentric Inertial (HCI)",
    "HGS": "Heliographic Stonyhurst",
    "HGC": "Heliographic Carrington",
}

# A single graticule polyline: matched arcsec arrays with NaN where the point is
# on the far hemisphere, so ``connect="finite"`` breaks the curve cleanly.
Polyline = tuple[np.ndarray, np.ndarray]
# A label anchor: (text, x_arcsec, y_arcsec).
Label = tuple[str, float, float]


def frame_key_from_display(text: str) -> str:
    """Map a combo-box display string (or bare key) back to a frame key."""
    value = str(text or "").strip()
    if value in FRAME_KEYS:
        return value
    for key, name in FRAME_DISPLAY_NAMES.items():
        if value == name:
            return key
    upper = value.upper()
    for key in FRAME_KEYS:
        if key in upper:
            return key
    return "HCI"


def _target_obstime(frame: Any, frame_coord: Any):
    obstime = getattr(frame_coord, "obstime", None)
    if obstime is not None:
        return obstime
    # Fall back to a naive datetime parsed from the frame metadata.
    return frame_observation_time(frame)


def build_target_frame(frame_coord: Any, obstime: Any, frame_key: str):
    """Construct the sunpy reference frame the graticule/readout is measured in.

    ``frame_coord`` is the map's own helioprojective coordinate frame (it carries
    the observer, which Carrington needs). Raises if the frame cannot be built.
    """
    from sunpy.coordinates import (
        HeliocentricInertial,
        HeliographicCarrington,
        HeliographicStonyhurst,
    )

    key = str(frame_key or "HCI").upper()
    if obstime is None:
        raise ValueError("An observation time is required to build a solar frame.")
    if key == "HGS":
        return HeliographicStonyhurst(obstime=obstime)
    if key == "HGC":
        observer = getattr(frame_coord, "observer", None)
        return HeliographicCarrington(obstime=obstime, observer=observer)
    return HeliocentricInertial(obstime=obstime)


def _surface_coords(target: Any, lon_deg: np.ndarray, lat_deg: np.ndarray, rsun_length, frame_key: str):
    """A SkyCoord of solar-surface points (radius = R_sun) in the target frame."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    lon = np.asarray(lon_deg, dtype=float) * u.deg
    lat = np.asarray(lat_deg, dtype=float) * u.deg
    if str(frame_key or "HCI").upper() == "HCI":
        # HeliocentricInertial is a full 3-D frame; pin the points to the surface.
        return SkyCoord(lon, lat, distance=rsun_length, frame=target)
    # Heliographic frames take a spherical radius; default is already R_sun but
    # we set it explicitly for parity with the HCI branch.
    return SkyCoord(lon, lat, radius=rsun_length, frame=target)


def _project_to_arcsec(surface: Any, frame_coord: Any) -> Polyline | None:
    """Transform surface points into the map frame and return NaN-masked arcsec."""
    import astropy.units as u

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hpc = surface.transform_to(frame_coord)
            tx = np.asarray(hpc.Tx.to_value(u.arcsec), dtype=float)
            ty = np.asarray(hpc.Ty.to_value(u.arcsec), dtype=float)
            visible = np.asarray(hpc.is_visible(), dtype=bool)
    except Exception:
        return None
    tx = np.where(visible, tx, np.nan)
    ty = np.where(visible, ty, np.nan)
    if not np.any(np.isfinite(tx) & np.isfinite(ty)):
        return None
    return tx, ty


def _label_anchor(tx: np.ndarray, ty: np.ndarray, prefer_index: int, text: str) -> Label | None:
    """Pick a visible point near ``prefer_index`` to anchor a line's label."""
    finite = np.isfinite(tx) & np.isfinite(ty)
    if not np.any(finite):
        return None
    prefer_index = int(np.clip(prefer_index, 0, tx.size - 1))
    if finite[prefer_index]:
        idx = prefer_index
    else:
        candidates = np.nonzero(finite)[0]
        idx = int(candidates[np.argmin(np.abs(candidates - prefer_index))])
    return (text, float(tx[idx]), float(ty[idx]))


def graticule_arcsec(
    frame: Any,
    *,
    frame_key: str = "HCI",
    lon_step: float = 15.0,
    lat_step: float = 15.0,
    resolution: int = 181,
) -> tuple[list[Polyline], list[Label]]:
    """Meridians + parallels for ``frame`` in the given reference frame.

    Returns ``(polylines, labels)`` in helioprojective arcsec (the view
    coordinates the canvases plot in). Returns empty lists when the frame has no
    usable coordinate system (e.g. a non-solar FITS or a derived array without a
    preserved ``coordinate_frame``).
    """
    frame_coord = getattr(frame, "coordinate_frame", None)
    if frame_coord is None:
        return [], []
    obstime = _target_obstime(frame, frame_coord)
    try:
        target = build_target_frame(frame_coord, obstime, frame_key)
    except Exception:
        return [], []

    rsun_length = getattr(frame_coord, "rsun", None)
    if rsun_length is None:
        try:
            from astropy.constants import R_sun

            rsun_length = R_sun
        except Exception:
            return [], []

    resolution = max(int(resolution), 9)
    key = str(frame_key or "HCI").upper()
    polylines: list[Polyline] = []
    labels: list[Label] = []

    # Meridians: fixed longitude, latitude sweeping (avoid the exact poles).
    lat_sweep = np.linspace(-89.0, 89.0, resolution)
    equator_index = int(np.argmin(np.abs(lat_sweep)))
    for lon in np.arange(0.0, 360.0, float(lon_step)):
        surface = _surface_coords(target, np.full_like(lat_sweep, lon), lat_sweep, rsun_length, key)
        projected = _project_to_arcsec(surface, frame_coord)
        if projected is None:
            continue
        tx, ty = projected
        polylines.append((tx, ty))
        anchor = _label_anchor(tx, ty, equator_index, f"{lon:g}°")
        if anchor is not None:
            labels.append(anchor)

    # Parallels: fixed latitude, longitude sweeping the full circle.
    lon_sweep = np.linspace(0.0, 360.0, resolution)
    lat_lines = np.arange(-90.0 + float(lat_step), 90.0, float(lat_step))
    for lat in lat_lines:
        surface = _surface_coords(target, lon_sweep, np.full_like(lon_sweep, lat), rsun_length, key)
        projected = _project_to_arcsec(surface, frame_coord)
        if projected is None:
            continue
        tx, ty = projected
        polylines.append((tx, ty))
        # Anchor parallels near the frame's central meridian (lon = 0).
        anchor = _label_anchor(tx, ty, 0, f"{lat:g}°")
        if anchor is not None:
            labels.append(anchor)

    return polylines, labels


def point_lonlat(
    x_arcsec: float,
    y_arcsec: float,
    frame: Any,
    *,
    frame_key: str = "HCI",
) -> tuple[float, float] | None:
    """Longitude/latitude of the on-disk point under an arcsec cursor position.

    Builds a 2-D helioprojective coordinate at ``(Tx, Ty)`` and transforms it to
    the target frame; sunpy back-projects onto the R_sun sphere. Off-disk sight
    lines miss the sphere and yield NaN → ``None`` (blank readout past the limb).
    """
    frame_coord = getattr(frame, "coordinate_frame", None)
    if frame_coord is None:
        return None
    obstime = _target_obstime(frame, frame_coord)
    try:
        target = build_target_frame(frame_coord, obstime, frame_key)
    except Exception:
        return None

    import astropy.units as u
    from astropy.coordinates import SkyCoord

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hpc = SkyCoord(float(x_arcsec) * u.arcsec, float(y_arcsec) * u.arcsec, frame=frame_coord)
            out = hpc.transform_to(target)
            lon = float(out.lon.to_value(u.deg))
            lat = float(out.lat.to_value(u.deg))
    except Exception:
        return None
    if not (np.isfinite(lon) and np.isfinite(lat)):
        return None
    # Normalise longitude to a signed [-180, 180) range for a compact readout.
    lon = (lon + 180.0) % 360.0 - 180.0
    return lon, lat
