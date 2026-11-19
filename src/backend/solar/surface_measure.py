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
    Pixel ``(col, row)`` integer coordinates are pixel centres. Those are the
    *image-frame* arcsec the canvas plots and the user clicks in.

    ``rotation_deg`` is the image's rotation from solar north (FITS ``CROTA2``,
    or the angle of the map's PC matrix): the true helioprojective position is
    the image-frame offset from the reference point rotated by it. Raw HMI
    frames are stored about 180 deg rotated; AIA level 1 a fraction of a degree.
    Every function here accepts image-frame arcsec and applies the rotation
    before going onto the sphere, so lengths, areas *and* positions are right.
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
    rotation_deg: float = 0.0
    #: Observer-to-Sun-centre distance from the header (DSUN_OBS), km; None
    #: when the header has none and the distance follows from the apparent radius.
    dsun_km: float | None = None
    #: The header's RSUN_OBS (arcsec), kept for the consistency check and export.
    rsun_obs_header_arcsec: float | None = None
    #: Problems or fallbacks met while reading the header, for the result to show.
    metadata_notes: tuple[str, ...] = ()
    #: False when the header geometry is inconsistent beyond tolerance: precision
    #: measurement is then refused until it is resolved (plan section 3).
    metadata_valid: bool = True
    #: HMI QUALITY word (or None when the header has none).
    quality_flag: int | None = None
    #: The load-time pixel-geometry check of this frame (size class, plate scale, limb).
    image_check: str | None = None

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
        if not math.isfinite(float(self.rotation_deg)):
            raise ValueError(f"rotation_deg must be finite, got {self.rotation_deg!r}.")

    @classmethod
    def from_axis_transform(
        cls,
        axis_transform: Mapping[str, float],
        *,
        rsun_arcsec: float,
        b0_deg: float = 0.0,
        observer_lon_deg: float = 0.0,
        rsun_km: float = RSUN_KM,
        rotation_deg: float = 0.0,
        dsun_km: float | None = None,
        rsun_obs_header_arcsec: float | None = None,
        metadata_notes: tuple[str, ...] = (),
        metadata_valid: bool = True,
        quality_flag: int | None = None,
        image_check: str | None = None,
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
            rotation_deg=float(rotation_deg),
            dsun_km=None if dsun_km is None else float(dsun_km),
            rsun_obs_header_arcsec=None if rsun_obs_header_arcsec is None else float(rsun_obs_header_arcsec),
            metadata_notes=tuple(metadata_notes),
            metadata_valid=bool(metadata_valid),
            quality_flag=quality_flag,
            image_check=image_check,
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

    def to_helioprojective(self, tx: Any, ty: Any) -> tuple[np.ndarray, np.ndarray]:
        """Image-frame arcsec (as plotted) -> true helioprojective ``(Tx, Ty)``.

        The FITS convention: the world offset from the reference point is the
        PC matrix ``[[cos r, -sin r], [sin r, cos r]]`` times the image offset.
        """
        tx = np.asarray(tx, dtype=float)
        ty = np.asarray(ty, dtype=float)
        r = math.radians(float(self.rotation_deg))
        if r == 0.0:
            return tx, ty
        dx = tx - self.x_ref_arcsec
        dy = ty - self.y_ref_arcsec
        c, s_ = math.cos(r), math.sin(r)
        return self.x_ref_arcsec + c * dx - s_ * dy, self.y_ref_arcsec + s_ * dx + c * dy

    def from_helioprojective(self, tx: Any, ty: Any) -> tuple[np.ndarray, np.ndarray]:
        """True helioprojective ``(Tx, Ty)`` -> image-frame arcsec (as plotted)."""
        tx = np.asarray(tx, dtype=float)
        ty = np.asarray(ty, dtype=float)
        r = math.radians(float(self.rotation_deg))
        if r == 0.0:
            return tx, ty
        dx = tx - self.x_ref_arcsec
        dy = ty - self.y_ref_arcsec
        c, s_ = math.cos(r), math.sin(r)
        return self.x_ref_arcsec + c * dx + s_ * dy, self.y_ref_arcsec - s_ * dx + c * dy


def solar_radius_arcsec_from_meta(meta: Mapping[str, Any] | None) -> float | None:
    """Apparent solar radius from a header, as SunPy derives it.

    ``RSUN_OBS`` (or ``SOLAR_R``) when present; otherwise computed from the
    observer distance ``DSUN_OBS`` and ``RSUN_REF`` (default the IAU radius),
    ``asin(R / D)``. STEREO/SECCHI, GONG and others often carry only the
    distance, and a cropped frame keeps only its header, so without this a
    measurement silently falls back to an Earth-like 960 arcsec.
    """
    meta = dict(meta or {})
    direct = _meta_float(meta, "rsun_obs", "solar_r", "rsun")
    if direct is not None and 1.0 < direct < 324_000.0:
        return direct
    dsun_m = _meta_float(meta, "dsun_obs")
    if dsun_m is None or dsun_m <= 0.0:
        return None
    rsun_ref_m = _meta_float(meta, "rsun_ref") or RSUN_KM * 1000.0
    if rsun_ref_m >= dsun_m:
        return None
    return math.degrees(math.asin(rsun_ref_m / dsun_m)) * 3600.0


def frame_rotation_deg(frame: Any) -> float:
    """The image's rotation from solar north, in degrees (0 when unknown).

    The map's own ``rotation_matrix`` comes first: SunPy's instrument classes
    know when a header's ``CROTA`` must be ignored (Helioviewer LASCO JP2s are
    already rotated but keep it). Plain frames fall back to the PC matrix or
    ``CROTA2`` in the header.
    """
    matrix = getattr(frame, "rotation_matrix", None)
    if matrix is not None:
        try:
            m = np.asarray(matrix, dtype=float)
            angle = math.degrees(math.atan2(float(m[1, 0]), float(m[0, 0])))
            if math.isfinite(angle):
                return angle
        except Exception:
            pass
    meta = dict(getattr(frame, "meta", None) or {})
    pc11 = _meta_float(meta, "pc1_1")
    pc21 = _meta_float(meta, "pc2_1")
    if pc11 is not None and pc21 is not None:
        return math.degrees(math.atan2(pc21, pc11))
    crota = _meta_float(meta, "crota2", "crota")
    return float(crota) if crota is not None else 0.0


def frame_observer_deg(frame: Any) -> tuple[float, float] | None:
    """Stonyhurst ``(lat, lon)`` of the observer of ``frame`` (B0 and longitude).

    Reads the map's ``observer_coordinate`` (SunPy fills it from the header, or
    from Earth's position at the observation time when the header has none),
    then the coordinate frame a cropped frame still carries. None when neither
    is available; the caller then reads header keys.
    """
    getters = (
        lambda: frame.observer_coordinate,
        lambda: frame.coordinate_frame.observer,
    )
    for getter in getters:
        try:
            observer = getter()
            lat = float(observer.lat.to_value("deg"))
            lon = float(observer.lon.to_value("deg"))
        except Exception:
            continue
        if math.isfinite(lat) and math.isfinite(lon):
            return lat, lon
    return None


#: RSUN_OBS and asin(RSUN_REF / DSUN_OBS) must agree to this fraction; a larger
#: disagreement means inconsistent radius conventions or metadata.
RADIUS_CONSISTENCY_TOLERANCE = 0.01


def _frame_km(frame: Any, attr: str) -> float | None:
    try:
        value = float(getattr(frame, attr).to_value("km"))
    except Exception:
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def geometry_for_frame(
    frame: Any,
    axis_transform: Mapping[str, float],
    *,
    rsun_arcsec: float | None = None,
) -> SurfaceGeometry:
    """The geometry of a loaded frame: radius, distance, observer, rotation and scale.

    Following the measurement plan (section 3): the physical radius R comes
    from the map (``RSUN_REF``) and the observer distance D from ``DSUN_OBS``;
    with both, the limb is placed at ``asin(R / D)`` and ``RSUN_OBS`` is only a
    cross-check -- a disagreement beyond :data:`RADIUS_CONSISTENCY_TOLERANCE`
    marks the geometry invalid instead of silently picking one. Without D the
    apparent radius (``rsun_arcsec``, else the map's) sets the distance, and a
    missing R falls back to the nominal 695,700 km; both fallbacks are recorded
    in ``metadata_notes``. The observer comes from the map's coordinates when it
    has them, else from the header, and the rotation from :func:`frame_rotation_deg`.
    """
    meta = dict(getattr(frame, "meta", None) or {})
    notes: list[str] = []
    valid = True

    rsun_km = _frame_km(frame, "rsun_meters")
    if rsun_km is None:
        rsun_ref_m = _meta_float(meta, "rsun_ref")
        rsun_km = rsun_ref_m / 1000.0 if rsun_ref_m and rsun_ref_m > 0 else None
    if rsun_km is None:
        rsun_km = RSUN_KM
        notes.append("RSUN_REF missing: nominal solar radius 695,700 km used")

    dsun_km = _meta_float(meta, "dsun_obs")
    dsun_km = dsun_km / 1000.0 if dsun_km and dsun_km > 0 else _frame_km(frame, "dsun")

    header_radius = _meta_float(meta, "rsun_obs", "solar_r")
    if header_radius is None:
        try:
            header_radius = float(frame.rsun_obs.to_value("arcsec"))
        except Exception:
            header_radius = None

    if dsun_km is not None and dsun_km > rsun_km:
        radius = math.degrees(math.asin(rsun_km / dsun_km)) * 3600.0
        if header_radius is not None and header_radius > 0:
            mismatch = abs(header_radius - radius) / radius
            if mismatch > RADIUS_CONSISTENCY_TOLERANCE:
                valid = False
                notes.append(
                    f"inconsistent metadata: RSUN_OBS {header_radius:.1f}\" but asin(R/D) gives "
                    f"{radius:.1f}\" ({100 * mismatch:.1f}% apart)"
                )
    else:
        if dsun_km is not None:
            valid = False
            notes.append("invalid metadata: DSUN_OBS is not larger than the solar radius")
        radius = rsun_arcsec if rsun_arcsec is not None else header_radius
        if radius is None:
            radius = solar_radius_arcsec_from_meta(meta)
        if radius is None or not math.isfinite(float(radius)) or float(radius) <= 0.0:
            raise ValueError("This map has no apparent solar radius (RSUN_OBS or DSUN_OBS), so its pixels cannot be placed on the Sun.")
        dsun_km = None
        notes.append("DSUN_OBS missing: observer distance taken from the apparent radius")

    base = geometry_from_meta(meta, axis_transform, rsun_arcsec=float(radius))
    observer = frame_observer_deg(frame)
    b0, lon = (observer if observer is not None else (base.b0_deg, base.observer_lon_deg))
    quality = _meta_float(meta, "quality")
    quality_flag = int(quality) if quality is not None else None
    # The load-time pixel check (image_check.py) leaves its verdict in the meta.
    pixel_error = meta.get("ECA_PIXERR") or meta.get("eca_pixerr")
    pixel_fix = meta.get("ECA_PIXFIX") or meta.get("eca_pixfix")
    if pixel_error:
        valid = False
        notes.append(f"pixel geometry: {pixel_error}")
    if pixel_fix:
        notes.append(f"pixel geometry corrected at load: {pixel_fix}")
    if quality_flag:
        notes.append(f"HMI QUALITY flag 0x{quality_flag & 0xFFFFFFFF:08X} is set: check the data quality")
    return SurfaceGeometry.from_axis_transform(
        axis_transform,
        rsun_arcsec=float(radius),
        b0_deg=b0,
        observer_lon_deg=lon,
        rsun_km=rsun_km,
        rotation_deg=frame_rotation_deg(frame),
        dsun_km=dsun_km,
        rsun_obs_header_arcsec=header_radius,
        metadata_notes=tuple(notes),
        metadata_valid=valid,
        quality_flag=quality_flag,
        image_check=meta.get("ECA_PIXCHK") or meta.get("eca_pixchk"),
    )


def geometry_from_map(frame: Any, axis_transform: Mapping[str, float]) -> SurfaceGeometry:
    """:func:`geometry_for_frame` with the radius read from the map itself."""
    return geometry_for_frame(frame, axis_transform)


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
    """Intersect lines of sight (image-frame arcsec) with the solar sphere.

    Uses the helioprojective -> heliocentric relations of Thompson (2006, A&A
    449, 791): a point at distance ``d`` along ``(Tx, Ty)`` sits at
    ``x = d cos Ty sin Tx``, ``y = d sin Ty``, ``z = D - d cos Ty cos Tx``; the
    near root of ``|P| = 1`` picks the visible hemisphere.
    """
    hpc_x, hpc_y = geometry.to_helioprojective(tx_arcsec, ty_arcsec)
    tx = hpc_x * _ARCSEC_TO_RAD
    ty = hpc_y * _ARCSEC_TO_RAD
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
    #: "ok", "partial" (some selected samples excluded: off the disc or below the
    #: mu cutoff), "invalid" (selection made but no valid sample) or "empty".
    status: str = "ok"
    selected_px: int = 0  # selected pixels (weight > 0)
    selected_weight: float = 0.0  # sum of mask weights (fractional coverage)
    valid_px: float = 0.0  # pixel-equivalents that were integrated
    off_disc_px: float = 0.0  # excluded: the ray misses the photosphere
    near_limb_px: float = 0.0  # excluded: on the disc but mu below the cutoff
    mu_cutoff: float = 0.0
    method: str = "finite_distance"  # or "orthographic"
    subsamples: int = 1  # per pixel side
    #: Sum of local line-of-sight projected elements d^2 dOmega over the valid
    #: samples (plan section 4.4), km^2. ``on_disk_plane_of_sky_km2`` is the
    #: legacy convention (pixel count x one image-plane area at Sun centre).
    projected_km2: float | None = None

    @property
    def correction_factor(self) -> float | None:
        """Surface / projected area, when the projected area is positive."""
        if self.projected_km2 and self.projected_km2 > 0.0 and self.status in ("ok", "partial"):
            return self.on_disk_km2 / self.projected_km2
        return None

    @property
    def is_valid(self) -> bool:
        return self.status in ("ok", "partial")

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
    # Distances from Sun centre need the true (rotated) helioprojective position.
    tx, ty = geometry.to_helioprojective(tx, ty)
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
        stx, sty = geometry.to_helioprojective(stx, sty)
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


def _legacy_mask_area(
    mask: np.ndarray,
    geometry: SurfaceGeometry,
    *,
    image: np.ndarray | None = None,
) -> SurfaceArea:
    """No-cutoff integration with the exact limb-strip refinement (mu_min = 0).

    Kept for full-disc and limb-aware validation: it integrates right up to the
    limb (sub-pixels near it are exact annular sectors), which the routine,
    cutoff-limited measurement deliberately does not.
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

    los_km2 = _los_projected_km2(rows_all, cols_all, geometry)
    status = "empty" if n_pixels == 0 else ("invalid" if disk_px <= 0.0 else ("partial" if off_px > 1e-9 else "ok"))
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
        status=status,
        selected_px=n_pixels,
        selected_weight=float(n_pixels),
        valid_px=disk_px,
        off_disc_px=max(0.0, off_px),
        near_limb_px=0.0,
        mu_cutoff=0.0,
        method="finite_distance",
        subsamples=1,
        projected_km2=los_km2 if disk_px > 0.0 else None,
    )


METHODS = ("finite_distance", "orthographic")
#: Proposed routine limb cutoff (plan section 6.2): incidence ~72.5 deg, at most x3.33.
DEFAULT_MU_MIN = 0.30


def mask_surface_area(
    mask: np.ndarray,
    geometry: SurfaceGeometry,
    *,
    image: np.ndarray | None = None,
    mu_min: float = 0.0,
    method: str = "finite_distance",
    subsamples: int = 1,
) -> SurfaceArea:
    """Photospheric area of a mask, corrected for foreshortening.

    ``mask`` is boolean or fractional coverage weights in [0, 1] (plan section
    5.2). Each selected pixel is split into ``subsamples`` x ``subsamples``
    samples; every sample is one integration element with its own ray, solid
    angle and validity (plan section 6.1):

    * ``finite_distance`` (default): the ray meets the spherical photosphere at
      distance d with local incidence cosine mu; it contributes
      ``d^2 dOmega / mu`` of surface and ``d^2 dOmega`` of projected area;
    * ``orthographic``: the legacy parallel-ray approximation, ``A_p / mu``
      with ``A_p = R^2 / R_p^2`` per pixel -- a labelled comparison only.

    Samples whose ray misses the disc, or whose mu is below ``mu_min``, are
    excluded and counted by cause; the status is then "partial". With no valid
    sample it is "invalid" (never a silent zero), and "empty" with nothing
    selected. ``mu_min = 0`` with no subsampling keeps the exact limb-strip
    integration used for full-disc validation.

    ``image`` (same shape) adds the mean and summed intensity of the valid part.
    """
    weights = np.asarray(mask)
    if weights.ndim != 2:
        raise ValueError(f"The area mask must be 2-D, got shape {weights.shape}.")
    if image is not None:
        image = np.asarray(image)
        if image.shape != weights.shape:
            raise ValueError(f"Image shape {image.shape} does not match mask shape {weights.shape}.")
    if method not in METHODS:
        raise ValueError(f"Unknown area method {method!r}; use one of {METHODS}.")
    mu_min = float(mu_min)
    if not 0.0 <= mu_min < 1.0:
        raise ValueError("mu_min must be in [0, 1).")
    n = max(1, int(subsamples))
    is_bool = weights.dtype == bool
    if not is_bool:
        weights = weights.astype(float)
        if not np.all(np.isfinite(weights) & (weights >= 0.0) & (weights <= 1.0)):
            raise ValueError("Mask weights must be finite and between 0 and 1.")
    if mu_min <= 0.0 and method == "finite_distance" and n == 1 and (is_bool or np.all((weights == 0) | (weights == 1))):
        return _legacy_mask_area(weights.astype(bool), geometry, image=image)
    return _sampled_area(weights.astype(float), geometry, image=image, mu_min=mu_min, method=method, subsamples=n)


def _los_projected_km2(rows: np.ndarray, cols: np.ndarray, geometry: SurfaceGeometry) -> float:
    """Sum of d^2 dOmega over on-disc pixel centres (line-of-sight projected area), km^2."""
    omega = geometry.pixel_area_arcsec2 * _ARCSEC_TO_RAD * _ARCSEC_TO_RAD
    total = 0.0
    for start in range(0, rows.size, _CHUNK):
        tx, ty = geometry.pixel_to_arcsec(cols[start : start + _CHUNK].astype(float), rows[start : start + _CHUNK].astype(float))
        proj = project_to_sphere(tx, ty, geometry)
        total += float(np.where(proj.on_disk, omega * proj.distance**2, 0.0).sum())
    return total * float(geometry.rsun_km) ** 2


def _sampled_area(
    weights: np.ndarray,
    geometry: SurfaceGeometry,
    *,
    image: np.ndarray | None,
    mu_min: float,
    method: str,
    subsamples: int,
) -> SurfaceArea:
    rows_all, cols_all = np.nonzero(weights > 0.0)
    n_pixels = int(rows_all.size)
    pix_arcsec2 = geometry.pixel_area_arcsec2
    km2_per_arcsec2 = geometry.km_per_arcsec**2
    omega = pix_arcsec2 * _ARCSEC_TO_RAD * _ARCSEC_TO_RAD
    rho = float(geometry.rsun_arcsec) * _ARCSEC_TO_RAD
    n = int(subsamples)
    offsets = (np.arange(n, dtype=float) + 0.5) / n - 0.5
    oc, orow = np.meshgrid(offsets, offsets)
    oc = oc.ravel()
    orow = orow.ravel()
    per_sample = omega / (n * n)

    selected_weight = 0.0
    valid_w = off_w = near_w = 0.0
    area_r2 = proj_r2 = 0.0
    mu_proj_sum = 0.0
    min_mu = math.inf
    sum_tx = sum_ty = 0.0
    surf_vec = np.zeros(3)
    intensity_sum = 0.0
    intensity_w = 0.0
    per_chunk = max(1, _CHUNK // (n * n))

    for start in range(0, n_pixels, per_chunk):
        r = rows_all[start : start + per_chunk]
        c = cols_all[start : start + per_chunk]
        w = weights[r, c]
        selected_weight += float(w.sum())
        ctx, cty = geometry.pixel_to_arcsec(c.astype(float), r.astype(float))
        sum_tx += float((w * ctx).sum())
        sum_ty += float((w * cty).sum())
        stx, sty = geometry.pixel_to_arcsec(c[:, None] + oc[None, :], r[:, None] + orow[None, :])
        proj = project_to_sphere(stx, sty, geometry)
        if method == "finite_distance":
            on = proj.on_disk
            mu = proj.mu
            with np.errstate(invalid="ignore", divide="ignore"):
                valid = on & (mu >= mu_min) & (mu > 0.0)
                d2 = np.where(valid, proj.distance**2, 0.0)
                proj_el = per_sample * d2
                surf_el = np.where(valid, proj_el / np.where(valid, mu, 1.0), 0.0)
        else:
            hx, hy = geometry.to_helioprojective(stx, sty)
            theta = np.arccos(np.clip(np.cos(hx * _ARCSEC_TO_RAD) * np.cos(hy * _ARCSEC_TO_RAD), -1.0, 1.0))
            q = theta / rho
            on = q < 1.0
            with np.errstate(invalid="ignore"):
                mu = np.where(on, np.sqrt(np.clip(1.0 - q * q, 0.0, 1.0)), np.nan)
                valid = on & (mu >= mu_min) & (mu > 0.0)
            # Parallel rays: every pixel covers R^2 / R_p^2 of the disc plane.
            proj_el = np.where(valid, per_sample / (rho * rho), 0.0)
            surf_el = np.where(valid, proj_el / np.where(valid, mu, 1.0), 0.0)
        wv = w[:, None] * valid / (n * n)
        valid_w += float(wv.sum())
        off_w += float((w[:, None] * (~on) / (n * n)).sum())
        near_w += float((w[:, None] * (on & ~valid) / (n * n)).sum())
        surf_w = w[:, None] * surf_el
        area_r2 += float(surf_w.sum())
        proj_r2 += float((w[:, None] * proj_el).sum())
        if valid.any():
            mu_proj_sum += float((w[:, None] * proj_el * np.where(valid, mu, 0.0)).sum())
            min_mu = min(min_mu, float(np.nanmin(np.where(valid, mu, np.nan))))
            pos = np.stack((proj.x, proj.y, proj.z), axis=-1)
            surf_vec += np.nansum(np.where(valid[..., None], pos * surf_w[..., None], 0.0), axis=(0, 1))
        if image is not None:
            vals = np.asarray(image[r, c], dtype=float)
            frac = wv.sum(axis=1)
            finite = np.isfinite(vals) & (frac > 0)
            intensity_sum += float((vals[finite] * frac[finite]).sum())
            intensity_w += float(frac[finite].sum())

    if n_pixels == 0:
        status = "empty"
    elif valid_w <= 0.0:
        status = "invalid"
    elif off_w + near_w > 1e-9:
        status = "partial"
    else:
        status = "ok"
    centroid_lonlat: tuple[float, float] | None = None
    norm = float(np.linalg.norm(surf_vec))
    if norm > 0.0:
        lon, lat = stonyhurst_from_cartesian(*(surf_vec / norm), geometry)
        centroid_lonlat = (float(lon), float(lat))
    rsun_km2 = float(geometry.rsun_km) ** 2
    return SurfaceArea(
        n_pixels=n_pixels,
        on_disk_pixels=valid_w,
        off_limb_pixels=off_w,
        on_disk_msh=area_r2 * _MSH_PER_R2,
        on_disk_km2=area_r2 * rsun_km2,
        on_disk_plane_of_sky_km2=valid_w * pix_arcsec2 * km2_per_arcsec2,
        off_limb_km2=off_w * pix_arcsec2 * km2_per_arcsec2,
        total_arcsec2=selected_weight * pix_arcsec2,
        mean_mu=(mu_proj_sum / proj_r2) if proj_r2 > 0 else None,
        min_mu=None if not math.isfinite(min_mu) else min_mu,
        centroid_arcsec=(sum_tx / selected_weight, sum_ty / selected_weight) if selected_weight > 0 else None,
        centroid_lonlat=centroid_lonlat,
        mean_intensity=(intensity_sum / intensity_w) if intensity_w > 0 else None,
        total_intensity=intensity_sum if image is not None else None,
        status=status,
        selected_px=n_pixels,
        selected_weight=selected_weight,
        valid_px=valid_w,
        off_disc_px=off_w,
        near_limb_px=near_w,
        mu_cutoff=mu_min,
        method=method,
        subsamples=n,
        projected_km2=proj_r2 * rsun_km2 if valid_w > 0 else None,
    )


def excluded_pixel_mask(
    mask: np.ndarray,
    geometry: SurfaceGeometry,
    *,
    mu_min: float = DEFAULT_MU_MIN,
    method: str = "finite_distance",
) -> np.ndarray:
    """Selected pixels whose centre sample is not integrated (off the disc or mu < cutoff).

    For the overlay: measured and excluded pixels are tinted differently.
    """
    selected = np.asarray(mask) > 0
    out = np.zeros(selected.shape, dtype=bool)
    rows, cols = np.nonzero(selected)
    rho = float(geometry.rsun_arcsec) * _ARCSEC_TO_RAD
    for start in range(0, rows.size, _CHUNK):
        r = rows[start : start + _CHUNK]
        c = cols[start : start + _CHUNK]
        tx, ty = geometry.pixel_to_arcsec(c.astype(float), r.astype(float))
        if method == "orthographic":
            hx, hy = geometry.to_helioprojective(tx, ty)
            q = np.arccos(np.clip(np.cos(hx * _ARCSEC_TO_RAD) * np.cos(hy * _ARCSEC_TO_RAD), -1.0, 1.0)) / rho
            with np.errstate(invalid="ignore"):
                mu = np.where(q < 1.0, np.sqrt(np.clip(1.0 - q * q, 0.0, 1.0)), np.nan)
            ok = (q < 1.0) & (mu >= mu_min) & (mu > 0.0)
        else:
            proj = project_to_sphere(tx, ty, geometry)
            with np.errstate(invalid="ignore"):
                ok = proj.on_disk & (proj.mu >= mu_min) & (proj.mu > 0.0)
        out[r, c] = ~ok
    return out


# ------------------------------------------------------------- solid angles
def _unit_vectors(tx_arcsec: np.ndarray, ty_arcsec: np.ndarray) -> np.ndarray:
    """Observer-centred unit vectors (cos Ty cos Tx, cos Ty sin Tx, sin Ty)."""
    tx = np.asarray(tx_arcsec, dtype=float) * _ARCSEC_TO_RAD
    ty = np.asarray(ty_arcsec, dtype=float) * _ARCSEC_TO_RAD
    return np.stack((np.cos(ty) * np.cos(tx), np.cos(ty) * np.sin(tx), np.sin(ty)), axis=-1)


def _triangle_solid_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Van Oosterom & Strackee: 2 atan2(|a.(b x c)|, 1 + a.b + b.c + c.a)."""
    triple = np.abs(np.einsum("...i,...i->...", a, np.cross(b, c)))
    denom = 1.0 + np.einsum("...i,...i->...", a, b) + np.einsum("...i,...i->...", b, c) + np.einsum("...i,...i->...", c, a)
    return 2.0 * np.arctan2(triple, denom)


def corner_solid_angles(corners_tx: np.ndarray, corners_ty: np.ndarray) -> np.ndarray:
    """Solid angle (sr) of quadrilateral pixels from their four corner directions.

    ``corners_tx/ty`` have a last axis of 4 corners in boundary order (arcsec).
    The quadrilateral is split into two spherical triangles (plan section 4.3).
    """
    u = _unit_vectors(corners_tx, corners_ty)
    a, b, c, d = u[..., 0, :], u[..., 1, :], u[..., 2, :], u[..., 3, :]
    return _triangle_solid_angle(a, b, c) + _triangle_solid_angle(a, c, d)


def pixel_solid_angles(geometry: SurfaceGeometry, cols: Any, rows: Any) -> np.ndarray:
    """Corner-method solid angle of each pixel (sr) under the geometry's transform.

    The routine measurement uses the documented fast path
    ``|s_x s_y| (pi / 648000)^2``; this is the reference it is checked against.
    """
    cols = np.asarray(cols, dtype=float)
    rows = np.asarray(rows, dtype=float)
    dc = np.array([-0.5, 0.5, 0.5, -0.5])
    dr = np.array([-0.5, -0.5, 0.5, 0.5])
    tx, ty = geometry.pixel_to_arcsec(cols[..., None] + dc, rows[..., None] + dr)
    tx, ty = geometry.to_helioprojective(tx, ty)
    return corner_solid_angles(tx, ty)


def wcs_pixel_solid_angles(wcs: Any, cols: Any, rows: Any) -> np.ndarray:
    """Corner-method solid angles (sr) from an astropy WCS (full projection, any skew)."""
    cols = np.asarray(cols, dtype=float)
    rows = np.asarray(rows, dtype=float)
    dc = np.array([-0.5, 0.5, 0.5, -0.5])
    dr = np.array([-0.5, -0.5, 0.5, 0.5])
    cc = cols[..., None] + dc
    rr = rows[..., None] + dr
    lon, lat = wcs.pixel_to_world_values(cc, rr)
    lon = (np.asarray(lon, dtype=float) + 180.0) % 360.0 - 180.0
    return corner_solid_angles(lon * 3600.0, np.asarray(lat, dtype=float) * 3600.0)


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


#: Largest acceptable spread (p95 - p5) of the quiet-Sun-normalised disk
#: background, block by block. The intensity thresholds assume the background
#: is radially symmetric, so that dividing by I_qs(mu) leaves it flat: HMI
#: continuum comes out flat to ~1.7%; AIA 4500 (scattered light, streaks) to
#: ~29% and AIA 1700 (chromospheric plage) to ~15%, where a fixed fraction of
#: the quiet Sun no longer separates spots from background.
MAX_BACKGROUND_SPREAD = 0.05


def background_spread(
    image: np.ndarray,
    geometry: SurfaceGeometry,
    profile: QuietSunProfile,
    *,
    mu_min: float = DEFAULT_MU_MIN,
    grid: int = 32,
    max_side: int = 1024,
) -> float | None:
    """How far the normalised disk background departs from flat (p95 - p5).

    The image is cut into ``grid`` x ``grid`` blocks; each block lying (90%)
    on the disk at ``mu >= mu_min`` contributes the median of ``I / I_qs``.
    Medians and the 5-95% range keep real sunspots and plage, which cover few
    blocks, from counting. Pixels are sampled on a stride so a 4096^2 frame
    costs no more than a 1024^2 one. None when too few blocks are on the disk.
    """
    image = np.asarray(image, dtype=float)
    if image.ndim != 2:
        return None
    ny, nx = image.shape
    stride = max(1, int(math.ceil(max(ny, nx) / float(max_side))))
    sample = np.zeros(image.shape, dtype=bool)
    sample[::stride, ::stride] = True
    sample = disk_interior_mask(sample, geometry, mu_min=mu_min)
    normalized = normalize_to_quiet_sun(image, geometry, profile, region=sample)
    by, bx = max(1, ny // grid), max(1, nx // grid)
    medians = []
    for i in range(0, ny - by + 1, by):
        for j in range(0, nx - bx + 1, bx):
            block_sample = sample[i : i + by, j : j + bx]
            expected = block_sample.size / float(stride * stride)
            values = normalized[i : i + by, j : j + bx][block_sample]
            values = values[np.isfinite(values)]
            if expected > 0 and values.size >= 0.9 * expected and values.size >= 4:
                medians.append(float(np.median(values)))
    if len(medians) < 20:
        return None
    medians_arr = np.asarray(medians)
    return float(np.percentile(medians_arr, 95) - np.percentile(medians_arr, 5))


# ------------------------------------------------------------------- thresholds
def disk_interior_mask(region: np.ndarray, geometry: SurfaceGeometry, *, mu_min: float) -> np.ndarray:
    """``region`` restricted to on-disk pixels with ``mu >= mu_min``.

    Dark features (sunspots, filaments) can only be measured on the disk, and
    only where the quiet-Sun profile was actually fitted: below its ``mu_min``
    the profile is held at the limb value while real limb darkening keeps
    falling, so ordinary photosphere in the outermost limb, and the dark sky
    beyond it, would read as "darker than the quiet Sun" and be counted as
    spot. On a full-disk HMI frame that ring alone came to ~65,000 MSH.
    """
    region = np.asarray(region, dtype=bool)
    out = np.zeros_like(region)
    rows, cols = np.nonzero(region)
    for start in range(0, rows.size, _CHUNK):
        r = rows[start : start + _CHUNK]
        c = cols[start : start + _CHUNK]
        tx, ty = geometry.pixel_to_arcsec(c.astype(float), r.astype(float))
        proj = project_to_sphere(tx, ty, geometry)
        out[r, c] = proj.on_disk & (proj.mu >= float(mu_min))
    return out


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
#: A dark patch counts as a sunspot (or pore) only if its darkest pixel is at
#: least this far below the penumbra boundary, in units of I_qs. Quiet-Sun
#: granulation at HMI resolution has sigma ~0.03, so with the 0.9 boundary a
#: core below 0.8 is ~7 sigma: no granulation dip reaches it, every real pore
#: and spot does. This is the "real depression" test of automated detections
#: (e.g. Hanaoka 2022, Sol. Phys.); without it a full-disk HMI image on a
#: spotless day picks up ~300 MSH of granulation (measured 2019-04-03).
DEFAULT_CORE_DEPTH = 0.10


def reject_shallow_patches(mask: np.ndarray, normalized: np.ndarray, *, core_below: float) -> np.ndarray:
    """Keep only 8-connected patches of ``mask`` whose darkest pixel is ``< core_below``."""
    from scipy import ndimage

    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return mask.copy()
    labels, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    values = np.where(mask, np.nan_to_num(np.asarray(normalized, dtype=float), nan=np.inf), np.inf)
    darkest = ndimage.minimum(values, labels, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, dtype=bool)
    keep[1:] = np.asarray(darkest) < float(core_below)
    return keep[labels]


def sunspot_masks(
    normalized: np.ndarray,
    region: np.ndarray | None = None,
    *,
    umbra_level: float = DEFAULT_UMBRA_LEVEL,
    penumbra_level: float = DEFAULT_PENUMBRA_LEVEL,
    min_cluster_px: int = 0,
    core_depth: float | None = DEFAULT_CORE_DEPTH,
) -> SunspotMasks:
    """Split a region into umbra (``I/I_qs < umbra_level``) and penumbra.

    The standard intensity-threshold method on a limb-darkening-normalised
    image: the whole spot is ``I/I_qs < penumbra_level`` (0.9, the usual
    penumbra/quiet-Sun boundary for HMI continuum), the umbra the part below
    ``umbra_level`` (0.6). Patches that are not a real depression -- darkest
    pixel not at least ``core_depth`` below the penumbra boundary -- are
    granulation, not spots, and are dropped (``None`` keeps every patch).
    Small clusters are removed first, then the umbra is taken inside what is
    left, so umbra + penumbra always equals the spot.
    """
    umbra_level = float(umbra_level)
    penumbra_level = float(penumbra_level)
    if not umbra_level < penumbra_level:
        raise ValueError("The umbra level must be below the penumbra level.")
    spot = threshold_mask(normalized, region, below=penumbra_level, min_cluster_px=min_cluster_px)
    if core_depth is not None and float(core_depth) > 0.0:
        spot = reject_shallow_patches(spot, normalized, core_below=penumbra_level - float(core_depth))
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
    """Stonyhurst -> image-frame arcsec ``(Tx, Ty, visible)`` for this observer.

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
    tx, ty = geometry.from_helioprojective(tx, ty)
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
