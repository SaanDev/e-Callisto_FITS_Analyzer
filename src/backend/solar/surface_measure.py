"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Length and area of features on the solar disk, corrected for foreshortening.

These back the Solar Image Analysis length/area tools: filament length,
sunspot (umbra/penumbra) area, post-flare arcade and ribbon area. The plane-of-
sky ruler in ``image_measure`` measures what the image shows; this module puts
each pixel back on the solar sphere first, so a feature near the limb measures
the same as it would at disk centre.

Everything is plain numpy over the canvas's linear pixel <-> helioprojective
arcsec mapping (the same ``axis_transform`` dict the analysis window builds), so
it unit-tests without Qt or SunPy.

Geometry
--------
Each pixel's line of sight is intersected with the sphere of radius
``R_sun`` seen from an observer at distance ``D``. Only the apparent radius is
needed: ``R / D = sin(rsun_arcsec)``, so working in solar radii ``D =
1 / sin(rsun)``. This is the full perspective projection, not the orthographic
approximation, so the limb sits where SunPy puts it.

* On-disk pixels: surface area ``= Omega * d^2 / mu``, with ``Omega`` the pixel's
  solid angle, ``d`` the observer-to-surface distance and ``mu`` the cosine
  between the surface normal and the line of sight. Pixels near the limb are
  supersampled, because ``1 / mu`` at the pixel centre is a poor estimate there
  and a pixel straddling the limb is part disk, part sky.
* Off-limb pixels have no surface to project onto. Their area is reported
  separately as plane-of-sky km^2 at the Sun's distance and is *not* corrected;
  callers must label it that way. Nothing is silently dropped.

Areas are given in MSH (millionths of the solar hemisphere, ``2 pi R^2 * 1e-6``),
the unit sunspot catalogues use, as well as km^2.

Coordinates follow ``image_measure``: ``Tx`` grows toward solar west, ``Ty``
toward solar north; heliocentric-Cartesian ``z`` points at the observer.
Stonyhurst latitude/longitude use the observer's ``B0`` (``HGLT_OBS``) and
Stonyhurst longitude (``HGLN_OBS``, zero for Earth and SDO). Image rotation
(``CROTA``) is not modelled, matching the canvas, which shows it unrotated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from src.backend.solar.coronagraph import RSUN_KM


_ARCSEC_TO_RAD = math.pi / (180.0 * 3600.0)

# One MSH is a millionth of the visible hemisphere, 2*pi*R^2 (R = 1 here).
_HEMISPHERE_R2 = 2.0 * math.pi
_MSH_PER_R2 = 1e6 / _HEMISPHERE_R2

# Pixels whose centre sits below this mu (about 0.99 R from disk centre) are
# supersampled, as is any pixel the limb passes through.
_SUPERSAMPLE_MU = 0.15
_SUPERSAMPLE_N = 8

# Masked pixels are processed in chunks so a full-disk mask on a 4k image does
# not allocate a dozen 16M-element temporaries at once.
_CHUNK = 1 << 20

# Below this mu a measurement is close enough to the limb that its correction
# is large and sensitive to the exact limb position; callers should say so.
LIMB_WARNING_MU = 0.3


# --------------------------------------------------------------------- geometry
@dataclass(frozen=True)
class SurfaceGeometry:
    """Everything needed to put an image pixel on the solar sphere.

    The pixel <-> arcsec part mirrors the window's ``axis_transform`` dict:
    ``Tx = x_ref_arcsec + (col - x_ref_pix) * x_scale`` and likewise for rows.
    Pixel ``(col, row)`` integer coordinates are pixel centres.
    """

    rsun_arcsec: float
    x_ref_pix: float = 0.0
    y_ref_pix: float = 0.0
    x_scale_arcsec_per_pix: float = 1.0
    y_scale_arcsec_per_pix: float = 1.0
    x_ref_arcsec: float = 0.0
    y_ref_arcsec: float = 0.0
    b0_deg: float = 0.0
    observer_lon_deg: float = 0.0
    rsun_km: float = RSUN_KM

    def __post_init__(self) -> None:
        rsun = float(self.rsun_arcsec)
        if not math.isfinite(rsun) or rsun <= 0.0:
            raise ValueError(f"The apparent solar radius must be positive, got {self.rsun_arcsec!r}.")
        if rsun * _ARCSEC_TO_RAD >= math.pi / 2.0:
            raise ValueError("The apparent solar radius must be under 90 degrees.")
        for name in ("x_scale_arcsec_per_pix", "y_scale_arcsec_per_pix"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value == 0.0:
                raise ValueError(f"{name} must be finite and non-zero, got {value!r}.")
        if not math.isfinite(float(self.rsun_km)) or float(self.rsun_km) <= 0.0:
            raise ValueError(f"rsun_km must be positive, got {self.rsun_km!r}.")

    @classmethod
    def from_axis_transform(
        cls,
        axis_transform: Mapping[str, float],
        *,
        rsun_arcsec: float,
        b0_deg: float = 0.0,
        observer_lon_deg: float = 0.0,
        rsun_km: float = RSUN_KM,
    ) -> "SurfaceGeometry":
        """Build from the analysis window's ``axis_transform`` dict."""
        return cls(
            rsun_arcsec=float(rsun_arcsec),
            x_ref_pix=float(axis_transform.get("x_ref_pix", 0.0)),
            y_ref_pix=float(axis_transform.get("y_ref_pix", 0.0)),
            x_scale_arcsec_per_pix=float(axis_transform.get("x_scale_arcsec_per_pix", 1.0)),
            y_scale_arcsec_per_pix=float(axis_transform.get("y_scale_arcsec_per_pix", 1.0)),
            x_ref_arcsec=float(axis_transform.get("x_ref_arcsec", 0.0)),
            y_ref_arcsec=float(axis_transform.get("y_ref_arcsec", 0.0)),
            b0_deg=float(b0_deg),
            observer_lon_deg=float(observer_lon_deg),
            rsun_km=float(rsun_km),
        )

    # Observer distance from Sun centre, in solar radii.
    @property
    def observer_distance_rsun(self) -> float:
        return 1.0 / math.sin(float(self.rsun_arcsec) * _ARCSEC_TO_RAD)

    # Plane-of-sky scale at the Sun's distance, the same one the ruler uses.
    @property
    def km_per_arcsec(self) -> float:
        return float(self.rsun_km) / float(self.rsun_arcsec)

    @property
    def pixel_area_arcsec2(self) -> float:
        return abs(float(self.x_scale_arcsec_per_pix) * float(self.y_scale_arcsec_per_pix))

    def pixel_to_arcsec(self, cols: Any, rows: Any) -> tuple[np.ndarray, np.ndarray]:
        cols = np.asarray(cols, dtype=float)
        rows = np.asarray(rows, dtype=float)
        tx = self.x_ref_arcsec + (cols - self.x_ref_pix) * self.x_scale_arcsec_per_pix
        ty = self.y_ref_arcsec + (rows - self.y_ref_pix) * self.y_scale_arcsec_per_pix
        return tx, ty

    def arcsec_to_pixel(self, tx: Any, ty: Any) -> tuple[np.ndarray, np.ndarray]:
        tx = np.asarray(tx, dtype=float)
        ty = np.asarray(ty, dtype=float)
        cols = self.x_ref_pix + (tx - self.x_ref_arcsec) / self.x_scale_arcsec_per_pix
        rows = self.y_ref_pix + (ty - self.y_ref_arcsec) / self.y_scale_arcsec_per_pix
        return cols, rows


def geometry_from_map(frame: Any, axis_transform: Mapping[str, float]) -> SurfaceGeometry:
    """Read the solar radius and observer position from a SunPy map.

    ``axis_transform`` is the window's pixel <-> arcsec mapping for the shown
    data (it already accounts for any crop). Raises ``ValueError`` when the map
    carries no usable apparent solar radius, which is the one value nothing can
    be measured without.
    """
    import astropy.units as u

    rsun_arcsec: float | None = None
    try:
        rsun_arcsec = float(frame.rsun_obs.to_value(u.arcsec))
    except Exception:
        rsun_arcsec = None
    if rsun_arcsec is None or not math.isfinite(rsun_arcsec) or rsun_arcsec <= 0.0:
        raise ValueError("This map has no apparent solar radius (RSUN_OBS), so its pixels cannot be placed on the Sun.")

    b0_deg = 0.0
    lon_deg = 0.0
    try:
        observer = frame.observer_coordinate
        b0_deg = float(observer.lat.to_value(u.deg))
        lon_deg = float(observer.lon.to_value(u.deg))
    except Exception:
        try:
            b0_deg = float(frame.carrington_latitude.to_value(u.deg))
        except Exception:
            b0_deg = 0.0
    if not math.isfinite(b0_deg):
        b0_deg = 0.0
    if not math.isfinite(lon_deg):
        lon_deg = 0.0

    rsun_km = RSUN_KM
    try:
        value = float(frame.rsun_meters.to_value(u.km))
        if math.isfinite(value) and value > 0.0:
            rsun_km = value
    except Exception:
        pass

    return SurfaceGeometry.from_axis_transform(
        axis_transform,
        rsun_arcsec=rsun_arcsec,
        b0_deg=b0_deg,
        observer_lon_deg=lon_deg,
        rsun_km=rsun_km,
    )


def _meta_float(meta: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        for variant in (key.lower(), key.upper()):
            if variant in meta:
                try:
                    value = float(meta[variant])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    return value
    return None


def geometry_from_meta(
    meta: Mapping[str, Any] | None,
    axis_transform: Mapping[str, float],
    *,
    rsun_arcsec: float,
) -> SurfaceGeometry:
    """Build the geometry from FITS header keys, without SunPy.

    ``rsun_arcsec`` is passed in because the window already resolves it (map
    attribute, then ``RSUN_OBS``). ``B0`` comes from ``HGLT_OBS`` (falling back
    to ``CRLT_OBS``, which equals it), the observer longitude from ``HGLN_OBS``
    and the solar radius in km from ``RSUN_REF`` (metres). Missing keys fall
    back to an Earth-like view: ``B0 = 0``, longitude 0, the IAU radius.
    """
    meta = dict(meta or {})
    b0 = _meta_float(meta, "hglt_obs", "crlt_obs", "solar_b0")
    lon = _meta_float(meta, "hgln_obs")
    rsun_ref_m = _meta_float(meta, "rsun_ref")
    rsun_km = rsun_ref_m / 1000.0 if rsun_ref_m and rsun_ref_m > 0 else RSUN_KM
    return SurfaceGeometry.from_axis_transform(
        axis_transform,
        rsun_arcsec=float(rsun_arcsec),
        b0_deg=b0 if b0 is not None else 0.0,
        observer_lon_deg=lon if lon is not None else 0.0,
        rsun_km=rsun_km,
    )


@dataclass(frozen=True)
class SurfaceProjection:
    """Lines of sight intersected with the solar sphere (solar-radius units).

    ``x, y, z`` are heliocentric-Cartesian (``z`` toward the observer) and NaN
    where the line of sight misses the Sun. ``mu`` is the cosine between the
    surface normal and the direction back to the observer (1 at disk centre, 0
    at the limb); ``distance`` is observer-to-surface.
    """

    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    mu: np.ndarray
    distance: np.ndarray
    on_disk: np.ndarray


def project_to_sphere(tx_arcsec: Any, ty_arcsec: Any, geometry: SurfaceGeometry) -> SurfaceProjection:
    """Intersect helioprojective lines of sight with the solar sphere.

    Uses the helioprojective -> heliocentric relations of Thompson (2006, A&A
    449, 791): a point at distance ``d`` along ``(Tx, Ty)`` sits at
    ``x = d cos Ty sin Tx``, ``y = d sin Ty``, ``z = D - d cos Ty cos Tx``; the
    near root of ``|P| = 1`` picks the visible hemisphere.
    """
    tx = np.asarray(tx_arcsec, dtype=float) * _ARCSEC_TO_RAD
    ty = np.asarray(ty_arcsec, dtype=float) * _ARCSEC_TO_RAD
    big_d = geometry.observer_distance_rsun

    cos_ty = np.cos(ty)
    cos_theta = cos_ty * np.cos(tx)
    disc = big_d * big_d * cos_theta * cos_theta - (big_d * big_d - 1.0)
    on_disk = np.isfinite(disc) & (disc >= 0.0) & (cos_theta > 0.0)

    with np.errstate(invalid="ignore"):
        d = np.where(on_disk, big_d * cos_theta - np.sqrt(np.where(on_disk, disc, 0.0)), np.nan)
        x = d * cos_ty * np.sin(tx)
        y = d * np.sin(ty)
        z = big_d - d * cos_theta
        # n . (O - P) / |O - P| with n = P and |P| = 1.
        mu = np.clip((z * big_d - 1.0) / d, 0.0, 1.0)
    return SurfaceProjection(x=x, y=y, z=z, mu=mu, distance=d, on_disk=on_disk)


def stonyhurst_from_cartesian(x: Any, y: Any, z: Any, geometry: SurfaceGeometry) -> tuple[np.ndarray, np.ndarray]:
    """Heliocentric-Cartesian -> Stonyhurst ``(lon_deg, lat_deg)`` (Thompson 2006, eq. 12).

    Longitude is wrapped to ``[-180, 180)``.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    b0 = math.radians(float(geometry.b0_deg))
    r = np.sqrt(x * x + y * y + z * z)
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.degrees(np.arcsin(np.clip((y * math.cos(b0) + z * math.sin(b0)) / r, -1.0, 1.0)))
        lon = float(geometry.observer_lon_deg) + np.degrees(np.arctan2(x, z * math.cos(b0) - y * math.sin(b0)))
    lon = (lon + 180.0) % 360.0 - 180.0
    return lon, lat


def stonyhurst_from_arcsec(tx_arcsec: Any, ty_arcsec: Any, geometry: SurfaceGeometry) -> tuple[np.ndarray, np.ndarray]:
    """Stonyhurst ``(lon_deg, lat_deg)`` under helioprojective points; NaN off the disk."""
    proj = project_to_sphere(tx_arcsec, ty_arcsec, geometry)
    return stonyhurst_from_cartesian(proj.x, proj.y, proj.z, geometry)


def mu_map(shape: tuple[int, int], geometry: SurfaceGeometry) -> np.ndarray:
    """``mu`` for every pixel centre of an image; NaN off the disk."""
    ny, nx = int(shape[0]), int(shape[1])
    tx, _ = geometry.pixel_to_arcsec(np.arange(nx, dtype=float), 0.0)
    _, ty = geometry.pixel_to_arcsec(0.0, np.arange(ny, dtype=float))
    proj = project_to_sphere(tx[None, :], ty[:, None], geometry)
    return np.where(proj.on_disk, proj.mu, np.nan)


# ----------------------------------------------------------------------- length
@dataclass(frozen=True)
class SurfaceLength:
    """Length of a clicked polyline, on the solar surface where it can be.

    ``total_km`` is the corrected on-disk length plus the plane-of-sky length
    of any part that runs off the limb (a prominence leg, say). The flat image-
    plane length of the whole line is kept alongside for comparison.
    """

    n_vertices: int
    total_km: float
    on_disk_km: float  # along the solar surface
    on_disk_plane_of_sky_km: float  # the same stretch, as the image shows it
    off_limb_km: float  # plane of sky, uncorrected
    plane_of_sky_km: float  # whole line, flat
    plane_of_sky_arcsec: float
    segment_km: tuple[float, ...]  # total_km per clicked segment
    min_mu: float | None  # lowest mu along the on-disk part
    crosses_limb: bool
    start_lonlat: tuple[float, float] | None  # Stonyhurst (lon, lat), deg
    end_lonlat: tuple[float, float] | None

    @property
    def total_mm(self) -> float:
        return self.total_km / 1000.0

    @property
    def near_limb(self) -> bool:
        return self.min_mu is not None and self.min_mu < LIMB_WARNING_MU


def _great_circle(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Angle (radians) between rows of unit vectors ``p`` and ``q``, stable for tiny angles."""
    cross = np.linalg.norm(np.cross(p, q), axis=-1)
    dot = np.einsum("ij,ij->i", p, q)
    return np.arctan2(cross, dot)


def polyline_surface_length(
    vertices_arcsec: Sequence[Sequence[float]] | np.ndarray,
    geometry: SurfaceGeometry,
    *,
    step_arcsec: float | None = None,
) -> SurfaceLength:
    """Length of the polyline through ``vertices_arcsec`` (helioprojective ``(Tx, Ty)``).

    Each clicked segment is followed as drawn on the image and resampled every
    ``step_arcsec`` (default ``R_sun / 4000``, about a quarter arcsec for SDO).
    Consecutive on-disk samples contribute the great-circle distance between
    their surface points; anything off the limb contributes its plane-of-sky
    distance. A sub-step that crosses the limb counts as plane of sky, which
    is at most one step of difference.
    """
    pts = np.asarray(vertices_arcsec, dtype=float).reshape(-1, 2)
    pts = pts[np.isfinite(pts).all(axis=1)]
    n_vertices = int(pts.shape[0])
    if n_vertices < 2:
        raise ValueError("A length needs at least two points.")

    step = float(step_arcsec) if step_arcsec else float(geometry.rsun_arcsec) / 4000.0
    if not math.isfinite(step) or step <= 0.0:
        raise ValueError(f"step_arcsec must be positive, got {step_arcsec!r}.")

    km_per_arcsec = geometry.km_per_arcsec
    rsun_km = float(geometry.rsun_km)
    segment_km: list[float] = []
    on_disk_km = 0.0
    on_disk_flat_km = 0.0
    off_limb_km = 0.0
    flat_arcsec = 0.0
    min_mu = math.inf
    crosses = False

    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        seg_arcsec = math.hypot(x1 - x0, y1 - y0)
        flat_arcsec += seg_arcsec
        if seg_arcsec == 0.0:
            segment_km.append(0.0)
            continue
        n = max(2, int(math.ceil(seg_arcsec / step)) + 1)
        t = np.linspace(0.0, 1.0, n)
        tx = x0 + (x1 - x0) * t
        ty = y0 + (y1 - y0) * t
        proj = project_to_sphere(tx, ty, geometry)
        on = proj.on_disk
        if on.any():
            min_mu = min(min_mu, float(np.nanmin(proj.mu[on])))
        if on.any() and not on.all():
            crosses = True

        both = on[:-1] & on[1:]
        sub_flat_km = np.full(n - 1, seg_arcsec / (n - 1) * km_per_arcsec)
        seg_total = 0.0
        if both.any():
            pos = np.column_stack((proj.x, proj.y, proj.z))
            surf = _great_circle(pos[:-1][both], pos[1:][both]) * rsun_km
            surface_sum = float(surf.sum())
            on_disk_km += surface_sum
            on_disk_flat_km += float(sub_flat_km[both].sum())
            seg_total += surface_sum
        sky = float(sub_flat_km[~both].sum())
        off_limb_km += sky
        seg_total += sky
        segment_km.append(seg_total)

    start = stonyhurst_from_arcsec(pts[0, 0], pts[0, 1], geometry)
    end = stonyhurst_from_arcsec(pts[-1, 0], pts[-1, 1], geometry)

    def _lonlat(pair: tuple[np.ndarray, np.ndarray]) -> tuple[float, float] | None:
        lon, lat = float(pair[0]), float(pair[1])
        return (lon, lat) if math.isfinite(lon) and math.isfinite(lat) else None

    return SurfaceLength(
        n_vertices=n_vertices,
        total_km=on_disk_km + off_limb_km,
        on_disk_km=on_disk_km,
        on_disk_plane_of_sky_km=on_disk_flat_km,
        off_limb_km=off_limb_km,
        plane_of_sky_km=flat_arcsec * km_per_arcsec,
        plane_of_sky_arcsec=flat_arcsec,
        segment_km=tuple(segment_km),
        min_mu=None if not math.isfinite(min_mu) else min_mu,
        crosses_limb=crosses,
        start_lonlat=_lonlat(start),
        end_lonlat=_lonlat(end),
    )


# ------------------------------------------------------------------------ masks
def polygon_mask(shape: tuple[int, int], vertices_pix: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    """Pixels whose centre lies inside the polygon (even-odd rule).

    ``vertices_pix`` are ``(col, row)`` pixel coordinates; the polygon closes
    itself. Works for the many-vertex outlines a freehand lasso produces. Only
    the polygon's bounding box is tested, one edge at a time, so the cost is
    ``O(edges * box area)`` with no Python loop over pixels.
    """
    ny, nx = int(shape[0]), int(shape[1])
    mask = np.zeros((ny, nx), dtype=bool)
    verts = np.asarray(vertices_pix, dtype=float).reshape(-1, 2)
    verts = verts[np.isfinite(verts).all(axis=1)]
    if verts.shape[0] < 3:
        return mask

    c0 = max(0, int(math.ceil(verts[:, 0].min())))
    c1 = min(nx - 1, int(math.floor(verts[:, 0].max())))
    r0 = max(0, int(math.ceil(verts[:, 1].min())))
    r1 = min(ny - 1, int(math.floor(verts[:, 1].max())))
    if c1 < c0 or r1 < r0:
        return mask

    cols = np.arange(c0, c1 + 1, dtype=float)[None, :]
    rows = np.arange(r0, r1 + 1, dtype=float)[:, None]
    inside = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    xs = verts[:, 0]
    ys = verts[:, 1]
    xs_next = np.roll(xs, -1)
    ys_next = np.roll(ys, -1)
    for xa, ya, xb, yb in zip(xs, ys, xs_next, ys_next):
        if ya == yb:
            continue  # horizontal edges never cross a horizontal ray
        # Half-open in y so a vertex shared by two edges is counted once.
        spans = (rows >= min(ya, yb)) & (rows < max(ya, yb))
        if not spans.any():
            continue
        x_cross = xa + (rows - ya) * (xb - xa) / (yb - ya)
        inside ^= spans & (cols < x_cross)
    mask[r0 : r1 + 1, c0 : c1 + 1] = inside
    return mask


def ellipse_mask(
    shape: tuple[int, int],
    center_pix: tuple[float, float],
    semi_axes_pix: tuple[float, float],
    *,
    angle_deg: float = 0.0,
) -> np.ndarray:
    """Pixels whose centre lies inside an ellipse.

    ``semi_axes_pix`` are ``(a, b)`` along the column and row directions before
    rotation by ``angle_deg`` (counter-clockwise in pixel space).
    """
    ny, nx = int(shape[0]), int(shape[1])
    mask = np.zeros((ny, nx), dtype=bool)
    a, b = abs(float(semi_axes_pix[0])), abs(float(semi_axes_pix[1]))
    if a == 0.0 or b == 0.0 or not (math.isfinite(a) and math.isfinite(b)):
        return mask
    cx, cy = float(center_pix[0]), float(center_pix[1])
    reach = max(a, b)
    c0, c1 = max(0, int(math.ceil(cx - reach))), min(nx - 1, int(math.floor(cx + reach)))
    r0, r1 = max(0, int(math.ceil(cy - reach))), min(ny - 1, int(math.floor(cy + reach)))
    if c1 < c0 or r1 < r0:
        return mask
    dx = np.arange(c0, c1 + 1, dtype=float)[None, :] - cx
    dy = np.arange(r0, r1 + 1, dtype=float)[:, None] - cy
    theta = math.radians(float(angle_deg))
    u = dx * math.cos(theta) + dy * math.sin(theta)
    v = -dx * math.sin(theta) + dy * math.cos(theta)
    mask[r0 : r1 + 1, c0 : c1 + 1] = (u / a) ** 2 + (v / b) ** 2 <= 1.0
    return mask


def rectangle_mask(
    shape: tuple[int, int],
    corner0_pix: tuple[float, float],
    corner1_pix: tuple[float, float],
) -> np.ndarray:
    """Pixels whose centre lies inside the axis-aligned rectangle spanned by two corners."""
    ny, nx = int(shape[0]), int(shape[1])
    mask = np.zeros((ny, nx), dtype=bool)
    x_lo, x_hi = sorted((float(corner0_pix[0]), float(corner1_pix[0])))
    y_lo, y_hi = sorted((float(corner0_pix[1]), float(corner1_pix[1])))
    c0, c1 = max(0, int(math.ceil(x_lo))), min(nx - 1, int(math.floor(x_hi)))
    r0, r1 = max(0, int(math.ceil(y_lo))), min(ny - 1, int(math.floor(y_hi)))
    if c1 >= c0 and r1 >= r0:
        mask[r0 : r1 + 1, c0 : c1 + 1] = True
    return mask


def polygon_mask_from_arcsec(
    shape: tuple[int, int],
    vertices_arcsec: Sequence[Sequence[float]] | np.ndarray,
    geometry: SurfaceGeometry,
) -> np.ndarray:
    """:func:`polygon_mask` for vertices clicked on the canvas in arcsec."""
    verts = np.asarray(vertices_arcsec, dtype=float).reshape(-1, 2)
    cols, rows = geometry.arcsec_to_pixel(verts[:, 0], verts[:, 1])
    return polygon_mask(shape, np.column_stack((cols, rows)))


# ------------------------------------------------------------------------- area
@dataclass(frozen=True)
class SurfaceArea:
    """Area of a pixel mask, split into a corrected on-disk part and an off-limb part.

    Pixel counts are fractional: a supersampled limb pixel can be part disk,
    part sky. ``off_limb_km2`` is plane-of-sky at the Sun's distance and is not
    a surface area; ``on_disk_plane_of_sky_km2`` is the on-disk part as the
    image shows it, so ``on_disk_km2 / on_disk_plane_of_sky_km2`` is the
    foreshortening factor.
    """

    n_pixels: int
    on_disk_pixels: float
    off_limb_pixels: float
    on_disk_msh: float
    on_disk_km2: float
    on_disk_plane_of_sky_km2: float
    off_limb_km2: float
    total_arcsec2: float
    mean_mu: float | None  # image-area-weighted, on-disk part only
    min_mu: float | None
    centroid_arcsec: tuple[float, float] | None  # image-plane centroid of the whole mask
    centroid_lonlat: tuple[float, float] | None  # Stonyhurst, surface-area-weighted
    mean_intensity: float | None = None
    total_intensity: float | None = None

    @property
    def on_disk_mm2(self) -> float:
        return self.on_disk_km2 / 1e6

    @property
    def off_limb_mm2(self) -> float:
        return self.off_limb_km2 / 1e6

    @property
    def near_limb(self) -> bool:
        return self.min_mu is not None and self.min_mu < LIMB_WARNING_MU

    @property
    def has_off_limb(self) -> bool:
        return self.off_limb_pixels > 0.0


def _central_angle(theta: np.ndarray, big_d: float) -> np.ndarray:
    """Heliocentric angle from the sub-observer point of the surface point seen at ``theta``.

    From the Sun-observer-point triangle (law of sines): ``alpha = asin(D sin
    theta) - theta`` on the visible hemisphere, reaching ``pi/2 - theta_limb``
    at the limb.
    """
    return np.arcsin(np.clip(big_d * np.sin(theta), -1.0, 1.0)) - theta


def _surface_weights(
    cols: np.ndarray,
    rows: np.ndarray,
    geometry: SurfaceGeometry,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-pixel on-disk fraction, surface area (R^2), mu, and surface position.

    Returns ``(disk_fraction, area_r2, mu, xyz)``. ``mu`` is at the pixel
    centre (NaN when the centre is off the disk); ``xyz`` is the surface point
    under the centre, or the nearest limb point for a pixel whose centre is
    just off the disk but that still covers a sliver of it.

    Away from the limb the area is ``Omega d^2 / mu`` at the pixel centre.
    Near the limb (and for pixels the limb passes through) each pixel is split
    into ``N x N`` sub-pixels, and each sub-pixel is treated as a short annular
    sector about disk centre: its surface area is then exact,
    ``dphi * (cos alpha(theta_in) - cos alpha(min(theta_out, theta_limb)))``,
    with no ``1 / mu`` singularity, and the part beyond the limb falls out as
    sky on its own.
    """
    tx, ty = geometry.pixel_to_arcsec(cols, rows)
    proj = project_to_sphere(tx, ty, geometry)
    omega = geometry.pixel_area_arcsec2 * _ARCSEC_TO_RAD * _ARCSEC_TO_RAD

    disk_fraction = proj.on_disk.astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        area = np.where(proj.on_disk, omega * proj.distance**2 / proj.mu, 0.0)
    xyz = np.column_stack((proj.x, proj.y, proj.z))

    rho = float(geometry.rsun_arcsec) * _ARCSEC_TO_RAD
    big_d = geometry.observer_distance_rsun
    with np.errstate(invalid="ignore"):
        theta = np.arccos(np.clip(np.cos(tx * _ARCSEC_TO_RAD) * np.cos(ty * _ARCSEC_TO_RAD), -1.0, 1.0))
    half_diag = 0.5 * math.hypot(geometry.x_scale_arcsec_per_pix, geometry.y_scale_arcsec_per_pix) * _ARCSEC_TO_RAD
    refine = np.abs(theta - rho) < half_diag
    refine |= proj.on_disk & (proj.mu < _SUPERSAMPLE_MU)
    refine &= theta > 2.0 * half_diag  # the sector picture needs to be off disk centre
    idx = np.nonzero(refine)[0]
    if not idx.size:
        return disk_fraction, area, proj.mu, xyz

    n = _SUPERSAMPLE_N
    offsets = (np.arange(n, dtype=float) + 0.5) / n - 0.5
    oc, orow = np.meshgrid(offsets, offsets)
    oc = oc.ravel()
    orow = orow.ravel()
    # Side of a (near-square) sub-pixel, in radians.
    side = math.sqrt(omega) / n
    alpha_limb = math.pi / 2.0 - rho
    per_chunk = max(1, _CHUNK // (n * n))
    for start in range(0, idx.size, per_chunk):
        sel = idx[start : start + per_chunk]
        stx, sty = geometry.pixel_to_arcsec(cols[sel][:, None] + oc[None, :], rows[sel][:, None] + orow[None, :])
        th = np.arccos(np.clip(np.cos(stx * _ARCSEC_TO_RAD) * np.cos(sty * _ARCSEC_TO_RAD), -1.0, 1.0))
        th_in = np.maximum(th - 0.5 * side, 0.0)
        th_out = th + 0.5 * side
        th_cut = np.minimum(th_out, rho)
        valid = th_in < rho
        with np.errstate(invalid="ignore", divide="ignore"):
            strip = (side / th) * (np.cos(_central_angle(th_in, big_d)) - np.cos(_central_angle(th_cut, big_d)))
            covered = (th_cut - th_in) / (th_out - th_in)
        area[sel] = np.where(valid, strip, 0.0).sum(axis=1)
        disk_fraction[sel] = np.where(valid, np.clip(covered, 0.0, 1.0), 0.0).mean(axis=1)

        # A centre just past the limb has no surface point of its own; use the
        # limb point in the same direction so the centroid still counts it.
        sliver = ~proj.on_disk[sel] & (disk_fraction[sel] > 0.0)
        if sliver.any():
            fill = sel[sliver]
            phi = np.arctan2(ty[fill], tx[fill])
            xyz[fill, 0] = math.sin(alpha_limb) * np.cos(phi)
            xyz[fill, 1] = math.sin(alpha_limb) * np.sin(phi)
            xyz[fill, 2] = math.cos(alpha_limb)
    return disk_fraction, area, proj.mu, xyz


def mask_surface_area(
    mask: np.ndarray,
    geometry: SurfaceGeometry,
    *,
    image: np.ndarray | None = None,
) -> SurfaceArea:
    """Area of the ``True`` pixels of ``mask``, corrected for foreshortening on the disk.

    ``image`` (same shape) adds the mean and summed intensity of the masked
    pixels, ignoring non-finite values.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2:
        raise ValueError(f"The area mask must be 2-D, got shape {mask.shape}.")
    if image is not None:
        image = np.asarray(image)
        if image.shape != mask.shape:
            raise ValueError(f"Image shape {image.shape} does not match mask shape {mask.shape}.")

    rows_all, cols_all = np.nonzero(mask)
    n_pixels = int(rows_all.size)
    pix_arcsec2 = geometry.pixel_area_arcsec2
    km2_per_arcsec2 = geometry.km_per_arcsec**2

    disk_px = 0.0
    area_r2 = 0.0
    mu_weighted = 0.0
    min_mu = math.inf
    sum_tx = 0.0
    sum_ty = 0.0
    surf_vec = np.zeros(3)
    intensity_sum = 0.0
    intensity_n = 0

    for start in range(0, n_pixels, _CHUNK):
        rows = rows_all[start : start + _CHUNK].astype(float)
        cols = cols_all[start : start + _CHUNK].astype(float)
        frac, area, mu, xyz = _surface_weights(cols, rows, geometry)
        tx, ty = geometry.pixel_to_arcsec(cols, rows)
        sum_tx += float(tx.sum())
        sum_ty += float(ty.sum())
        disk_px += float(frac.sum())
        area_r2 += float(area.sum())
        on_centre = np.isfinite(mu)
        if on_centre.any():
            mu_weighted += float((mu[on_centre] * frac[on_centre]).sum())
            min_mu = min(min_mu, float(mu[on_centre].min()))
        has_pos = np.isfinite(xyz).all(axis=1) & (area > 0.0)
        if has_pos.any():
            surf_vec += (xyz[has_pos] * area[has_pos, None]).sum(axis=0)
        if image is not None:
            vals = np.asarray(image[rows_all[start : start + _CHUNK], cols_all[start : start + _CHUNK]], dtype=float)
            finite = np.isfinite(vals)
            intensity_sum += float(vals[finite].sum())
            intensity_n += int(finite.sum())

    off_px = float(n_pixels) - disk_px
    # Pixels whose centre is off the disk but that have a disk sliver carry no
    # centre mu; weight the mean by the on-centre part only.
    on_centre_px = disk_px if disk_px > 0 else 0.0
    mean_mu = mu_weighted / on_centre_px if on_centre_px > 0 and math.isfinite(min_mu) else None

    centroid_lonlat: tuple[float, float] | None = None
    norm = float(np.linalg.norm(surf_vec))
    if norm > 0.0:
        lon, lat = stonyhurst_from_cartesian(*(surf_vec / norm), geometry)
        centroid_lonlat = (float(lon), float(lat))

    return SurfaceArea(
        n_pixels=n_pixels,
        on_disk_pixels=disk_px,
        off_limb_pixels=max(0.0, off_px),
        on_disk_msh=area_r2 * _MSH_PER_R2,
        on_disk_km2=area_r2 * float(geometry.rsun_km) ** 2,
        on_disk_plane_of_sky_km2=disk_px * pix_arcsec2 * km2_per_arcsec2,
        off_limb_km2=max(0.0, off_px) * pix_arcsec2 * km2_per_arcsec2,
        total_arcsec2=n_pixels * pix_arcsec2,
        mean_mu=mean_mu,
        min_mu=None if not math.isfinite(min_mu) else min_mu,
        centroid_arcsec=(sum_tx / n_pixels, sum_ty / n_pixels) if n_pixels else None,
        centroid_lonlat=centroid_lonlat,
        mean_intensity=(intensity_sum / intensity_n) if intensity_n else None,
        total_intensity=intensity_sum if image is not None else None,
    )


def msh_to_km2(msh: float, *, rsun_km: float = RSUN_KM) -> float:
    return float(msh) / _MSH_PER_R2 * float(rsun_km) ** 2


def km2_to_msh(km2: float, *, rsun_km: float = RSUN_KM) -> float:
    return float(km2) / float(rsun_km) ** 2 * _MSH_PER_R2


# -------------------------------------------------------------------- quiet Sun
@dataclass(frozen=True)
class QuietSunProfile:
    """Quiet-Sun intensity as a function of ``mu`` (centre-to-limb variation).

    ``coefficients`` are a ``numpy.polynomial.Polynomial`` in ``mu`` fitted to
    the per-bin medians ``bin_levels`` at ``bin_mu``. Below ``mu_min`` the
    profile is held at its ``mu_min`` value (``limb_level``), which is also the
    reference used for off-limb pixels.
    """

    coefficients: tuple[float, ...]
    mu_min: float
    bin_mu: tuple[float, ...]
    bin_levels: tuple[float, ...]
    disk_centre_level: float
    limb_level: float
    n_samples: int

    def level_at(self, mu: Any) -> np.ndarray:
        mu = np.asarray(mu, dtype=float)
        poly = np.polynomial.Polynomial(self.coefficients)
        held = np.clip(mu, self.mu_min, 1.0)
        level = poly(held)
        return np.where(np.isfinite(mu), level, self.limb_level)


def fit_quiet_sun_profile(
    image: np.ndarray,
    geometry: SurfaceGeometry,
    *,
    degree: int = 5,
    n_bins: int = 40,
    mu_min: float = 0.1,
    exclude: np.ndarray | None = None,
    clip_sigma: float = 3.0,
    iterations: int = 3,
    max_samples: int = 1_000_000,
) -> QuietSunProfile:
    """Fit the quiet-Sun level against ``mu`` over the whole disk.

    The disk is binned in ``mu`` and the median of each bin taken, which already
    shrugs off sunspots and plage because they cover a small fraction of any
    annulus. A polynomial in ``mu`` is then fitted to the bin medians and pixels
    further than ``clip_sigma`` robust standard deviations (relative to the fit)
    are dropped before re-binning, ``iterations`` times.

    The same procedure fits limb darkening in HMI continuum and limb brightening
    in EUV. ``degree=0`` gives a flat disk median. ``exclude`` masks pixels
    (e.g. the region being measured) out of the fit; large images are strided
    down to about ``max_samples`` on-disk pixels.
    """
    image = np.asarray(image, dtype=float)
    if image.ndim != 2:
        raise ValueError(f"The quiet-Sun fit needs a 2-D image, got shape {image.shape}.")
    ny, nx = image.shape
    stride = max(1, int(math.ceil(math.sqrt(ny * nx * 0.8 / max(1, int(max_samples))))))
    rows = np.arange(0, ny, stride)
    cols = np.arange(0, nx, stride)
    sub = image[np.ix_(rows, cols)]
    tx, _ = geometry.pixel_to_arcsec(cols.astype(float), 0.0)
    _, ty = geometry.pixel_to_arcsec(0.0, rows.astype(float))
    proj = project_to_sphere(tx[None, :], ty[:, None], geometry)
    keep = proj.on_disk & np.isfinite(sub) & (proj.mu >= float(mu_min))
    if exclude is not None:
        exclude = np.asarray(exclude, dtype=bool)
        if exclude.shape != image.shape:
            raise ValueError(f"exclude shape {exclude.shape} does not match image shape {image.shape}.")
        keep &= ~exclude[np.ix_(rows, cols)]
    mu = proj.mu[keep]
    vals = sub[keep]
    if mu.size < max(50, 4 * (degree + 1)):
        raise ValueError("Too little of the solar disk is in view to fit a quiet-Sun level.")

    edges = np.linspace(float(mu_min), 1.0, int(n_bins) + 1)
    active = np.ones(mu.size, dtype=bool)
    coeffs = np.array([float(np.median(vals))])
    bin_mu: np.ndarray = np.array([])
    bin_levels: np.ndarray = np.array([])
    for _ in range(max(1, int(iterations))):
        which = np.clip(np.digitize(mu[active], edges) - 1, 0, int(n_bins) - 1)
        centres, levels, weights = [], [], []
        a_mu = mu[active]
        a_vals = vals[active]
        for b in range(int(n_bins)):
            in_bin = which == b
            count = int(in_bin.sum())
            if count < 10:
                continue
            centres.append(float(np.median(a_mu[in_bin])))
            levels.append(float(np.median(a_vals[in_bin])))
            weights.append(math.sqrt(count))
        if len(centres) == 0:
            raise ValueError("Too little of the solar disk is in view to fit a quiet-Sun level.")
        bin_mu = np.asarray(centres)
        bin_levels = np.asarray(levels)
        deg = int(min(max(0, int(degree)), len(centres) - 1))
        poly = np.polynomial.Polynomial.fit(bin_mu, bin_levels, deg, w=np.asarray(weights))
        coeffs = poly.convert().coef
        model = np.polynomial.Polynomial(coeffs)(mu)
        with np.errstate(invalid="ignore", divide="ignore"):
            rel = vals / model - 1.0
        finite = np.isfinite(rel)
        mad = float(np.median(np.abs(rel[finite & active] - np.median(rel[finite & active])))) if (finite & active).any() else 0.0
        sigma = 1.4826 * mad
        if sigma <= 0.0:
            break
        new_active = finite & (np.abs(rel) <= float(clip_sigma) * sigma)
        if new_active.sum() < max(50, 4 * (deg + 1)) or np.array_equal(new_active, active):
            break
        active = new_active

    poly = np.polynomial.Polynomial(coeffs)
    centre = float(poly(1.0))
    limb = float(poly(float(mu_min)))
    if not (math.isfinite(centre) and centre != 0.0):
        raise ValueError("The quiet-Sun fit did not converge to a usable level.")
    return QuietSunProfile(
        coefficients=tuple(float(c) for c in coeffs),
        mu_min=float(mu_min),
        bin_mu=tuple(float(v) for v in bin_mu),
        bin_levels=tuple(float(v) for v in bin_levels),
        disk_centre_level=centre,
        limb_level=limb,
        n_samples=int(active.sum()),
    )


def normalize_to_quiet_sun(
    image: np.ndarray,
    geometry: SurfaceGeometry,
    profile: QuietSunProfile,
    *,
    region: np.ndarray | None = None,
) -> np.ndarray:
    """``I / I_qs(mu)`` per pixel; NaN outside ``region`` when one is given.

    Off-limb pixels have no ``mu`` and are divided by the profile's limb level,
    so a bright arcade above the limb is compared with the quiet Sun at the
    limb rather than at disk centre.
    """
    image = np.asarray(image, dtype=float)
    out = np.full(image.shape, np.nan)
    if region is None:
        target = np.ones(image.shape, dtype=bool)
    else:
        target = np.asarray(region, dtype=bool)
        if target.shape != image.shape:
            raise ValueError(f"region shape {target.shape} does not match image shape {image.shape}.")
    rows, cols = np.nonzero(target)
    for start in range(0, rows.size, _CHUNK):
        r = rows[start : start + _CHUNK]
        c = cols[start : start + _CHUNK]
        tx, ty = geometry.pixel_to_arcsec(c.astype(float), r.astype(float))
        proj = project_to_sphere(tx, ty, geometry)
        level = profile.level_at(np.where(proj.on_disk, proj.mu, np.nan))
        with np.errstate(invalid="ignore", divide="ignore"):
            out[r, c] = image[r, c] / level
    return out


# ------------------------------------------------------------------- thresholds
def threshold_mask(
    values: np.ndarray,
    region: np.ndarray | None = None,
    *,
    below: float | None = None,
    above: float | None = None,
    min_cluster_px: int = 0,
) -> np.ndarray:
    """Pixels of ``region`` with ``above <= value < below`` (either bound optional).

    Typical use is on a quiet-Sun-normalised image: ``below=0.9`` for a whole
    sunspot in HMI continuum, ``above=2.0`` for a bright arcade or ribbon.
    ``min_cluster_px`` drops 8-connected clusters smaller than that, which keeps
    isolated noise pixels out of the area.
    """
    if below is None and above is None:
        raise ValueError("Give a lower bound, an upper bound, or both.")
    values = np.asarray(values, dtype=float)
    with np.errstate(invalid="ignore"):
        mask = np.isfinite(values)
        if below is not None:
            mask &= values < float(below)
        if above is not None:
            mask &= values >= float(above)
    if region is not None:
        region = np.asarray(region, dtype=bool)
        if region.shape != values.shape:
            raise ValueError(f"region shape {region.shape} does not match values shape {values.shape}.")
        mask &= region
    if int(min_cluster_px) > 1:
        mask = remove_small_clusters(mask, int(min_cluster_px))
    return mask


def remove_small_clusters(mask: np.ndarray, min_px: int) -> np.ndarray:
    """Drop 8-connected clusters of fewer than ``min_px`` pixels."""
    from scipy import ndimage

    mask = np.asarray(mask, dtype=bool)
    if int(min_px) <= 1 or not mask.any():
        return mask.copy()
    labels, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return mask.copy()
    sizes = np.bincount(labels.ravel())
    keep = sizes >= int(min_px)
    keep[0] = False
    return keep[labels]


@dataclass(frozen=True)
class SunspotMasks:
    """Umbra / penumbra split of a sunspot region on a quiet-Sun-normalised image."""

    umbra: np.ndarray
    penumbra: np.ndarray
    spot: np.ndarray
    umbra_level: float
    penumbra_level: float


DEFAULT_UMBRA_LEVEL = 0.6
DEFAULT_PENUMBRA_LEVEL = 0.9


def sunspot_masks(
    normalized: np.ndarray,
    region: np.ndarray | None = None,
    *,
    umbra_level: float = DEFAULT_UMBRA_LEVEL,
    penumbra_level: float = DEFAULT_PENUMBRA_LEVEL,
    min_cluster_px: int = 0,
) -> SunspotMasks:
    """Split a region into umbra (``I/I_qs < umbra_level``) and penumbra.

    The defaults, 0.6 and 0.9 of the quiet Sun, are the usual cuts for HMI
    continuum. Small clusters are removed from the whole spot first, then the
    umbra is taken inside what is left, so the two always add up to the spot.
    """
    umbra_level = float(umbra_level)
    penumbra_level = float(penumbra_level)
    if not umbra_level < penumbra_level:
        raise ValueError("The umbra level must be below the penumbra level.")
    spot = threshold_mask(normalized, region, below=penumbra_level, min_cluster_px=min_cluster_px)
    with np.errstate(invalid="ignore"):
        umbra = spot & (np.asarray(normalized, dtype=float) < umbra_level)
    penumbra = spot & ~umbra
    return SunspotMasks(
        umbra=umbra,
        penumbra=penumbra,
        spot=spot,
        umbra_level=umbra_level,
        penumbra_level=penumbra_level,
    )


# ---------------------------------------------------------- solar rotation
#: Howard, Harvey & Forgach (1990) sidereal rates, microradians per second:
#: ``omega = A + B sin^2(lat) + C sin^4(lat)``. SunPy's default model.
HOWARD_RATES_URAD_PER_S = (2.894, -0.428, -0.370)
#: Earth's mean orbital motion; subtracting it gives the synodic rate seen from
#: Earth (and SDO), exactly as ``sunpy.sun.models.differential_rotation``
#: does with ``frame_time="synodic"``.
EARTH_ORBIT_DEG_PER_DAY = 0.9856


def synodic_rotation_deg(latitude_deg: Any, seconds: float) -> np.ndarray:
    """Stonyhurst longitude a surface point moves through in ``seconds``.

    Howard et al. (1990) differential rotation, made synodic by removing
    Earth's mean orbital motion. Negative ``seconds`` rotates backwards.
    """
    lat = np.radians(np.asarray(latitude_deg, dtype=float))
    a, b, c = HOWARD_RATES_URAD_PER_S
    s2 = np.sin(lat) ** 2
    sidereal_rad = (a + b * s2 + c * s2 * s2) * 1e-6 * float(seconds)
    return np.degrees(sidereal_rad) - EARTH_ORBIT_DEG_PER_DAY * float(seconds) / 86400.0


def arcsec_from_stonyhurst(
    lon_deg: Any, lat_deg: Any, geometry: SurfaceGeometry
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stonyhurst -> helioprojective ``(Tx, Ty, visible)`` for this observer.

    ``visible`` is False for points on the far side of the limb, whose
    helioprojective position would land on the disk in front of them.
    """
    lon = np.radians(np.asarray(lon_deg, dtype=float) - float(geometry.observer_lon_deg))
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    b0 = math.radians(float(geometry.b0_deg))
    x = np.cos(lat) * np.sin(lon)
    y = np.sin(lat) * math.cos(b0) - np.cos(lat) * np.cos(lon) * math.sin(b0)
    z = np.sin(lat) * math.sin(b0) + np.cos(lat) * np.cos(lon) * math.cos(b0)
    big_d = geometry.observer_distance_rsun
    zeta = big_d - z
    dist = np.sqrt(x * x + y * y + zeta * zeta)
    tx = np.degrees(np.arctan2(x, zeta)) * 3600.0
    ty = np.degrees(np.arcsin(y / dist)) * 3600.0
    visible = z * big_d - 1.0 > 0.0
    return tx, ty, visible


@dataclass(frozen=True)
class RotatedPoints:
    """Points carried from one frame to another by solar rotation."""

    points: list[tuple[float, float]]
    #: Each point was on the disk and moved with the Sun (False: off-limb, kept fixed).
    rotated: tuple[bool, ...]
    #: Each point rotated onto the far side and could not be placed (its old position is kept).
    hidden: tuple[bool, ...]

    @property
    def any_hidden(self) -> bool:
        return any(self.hidden)

    @property
    def any_rotated(self) -> bool:
        return any(self.rotated)


def rotate_points(
    points_arcsec: Sequence[Sequence[float]],
    source: SurfaceGeometry,
    target: SurfaceGeometry,
    seconds: float,
) -> RotatedPoints:
    """Carry helioprojective points from ``source``'s view to ``target``'s ``seconds`` later.

    On-disk points are put on the surface, rotated by :func:`synodic_rotation_deg`
    at their own latitude, and projected for the target observer (whose B0 and
    distance may differ). Off-limb points have no surface position and stay where
    they are, which suits limb features over short sequences.
    """
    pts = np.asarray(points_arcsec, dtype=float).reshape(-1, 2)
    lon, lat = stonyhurst_from_arcsec(pts[:, 0], pts[:, 1], source)
    on_disk = np.isfinite(lon) & np.isfinite(lat)
    new_lon = lon + synodic_rotation_deg(lat, seconds)
    tx, ty, visible = arcsec_from_stonyhurst(new_lon, lat, target)
    out: list[tuple[float, float]] = []
    rotated: list[bool] = []
    hidden: list[bool] = []
    for i in range(pts.shape[0]):
        if not on_disk[i]:
            out.append((float(pts[i, 0]), float(pts[i, 1])))
            rotated.append(False)
            hidden.append(False)
        elif not bool(visible[i]):
            out.append((float(pts[i, 0]), float(pts[i, 1])))
            rotated.append(False)
            hidden.append(True)
        else:
            out.append((float(tx[i]), float(ty[i])))
            rotated.append(True)
            hidden.append(False)
    return RotatedPoints(points=out, rotated=tuple(rotated), hidden=tuple(hidden))
