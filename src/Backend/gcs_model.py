"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Graduated Cylindrical Shell (GCS) flux-rope model for 3-D CME reconstruction.

The plane-of-sky tools in ``image_measure`` and ``coronagraph`` measure a CME as
it appears. A radial plane-of-sky measurement generally underestimates the
heliocentric height when the eruption is away from that plane. GCS estimates
the three-dimensional geometry under a flux-rope shape assumption: a
six-parameter "croissant" is projected onto two or more simultaneous coronagraph
views and adjusted against all of them. Direction, angular width and
de-projected height remain subject to observational and model uncertainty.

Parameters (Thernisien et al. 2006; Thernisien 2011)
---------------------------------------------------
Three positional — ``lon``, ``lat`` (Stonyhurst direction of propagation) and
``tilt`` (rotation of the croissant about that direction) — and three
geometrical: ``height`` (leading-edge distance), ``alpha`` (half angle between
the leg axes) and ``kappa`` (aspect ratio, ``0 < kappa < 1``).

``height`` is the **leading edge** (apex) distance from Sun centre, and the other
radii are derived from it: the leg/junction height ``h`` by
:func:`leg_height_rsun`, the apex cross-section radius by
:func:`apex_cross_section_radius_rsun`, and the apex circle centre ``OC1`` by
:func:`shell_centre_distance_rsun`. This is worth stating because the literature
is loose about it and the sense is easy to invert; it is verified against the
mesh itself in ``tests/test_backend_gcs_model.py``, which asserts that the
largest mesh radius equals ``height``. Being the leading edge is also what lets
``height`` feed :func:`src.Backend.coronagraph.fit_height_time` directly, with no
conversion.

Why the projection is hand-rolled
---------------------------------
Fitting GCS means riding six sliders while watching the wireframe track the
front, so the per-update cost *is* the user experience. Going through
``SkyCoord.transform_to`` costs ~3.2 ms per 5130-point mesh; the matrix form
below costs ~0.07 ms and agrees with sunpy to 2e-10 arcsec, with an occultation
test that matches ``Helioprojective.is_visible()`` exactly for every point above
1.05 R_sun. The mathematical legs extend to Sun centre, so points below this
cutoff are masked before rendering. That is what makes a
full six-parameter update ~0.3 ms and lets the sliders run without a debounce
timer, a level-of-detail fallback or a projection cache.

``tests/test_backend_gcs_model.py`` pins this by projecting the same mesh
through ``solar_grid._project_to_arcsec`` (the ``SkyCoord`` path) and asserting
agreement, so the fast path stays honest.

Provenance and licence
----------------------
The mesh construction (:func:`gcs_skeleton`, :func:`gcs_mesh`) follows
``gcs_python`` by Johan von Forstner, MIT licensed
(https://github.com/johan12345/gcs_python), which is itself a port of the
SolarSoft IDL ``shellskeleton.pro`` and ``cmecloud.pro`` from the SECCHI
``scraytrace`` package. The closed-form relations between ``height``, ``alpha``,
``kappa`` and the derived radii are the published equations cited inline.

PyThea (https://github.com/AthKouloumvakos/PyThea, Kouloumvakos et al. 2022,
doi:10.3389/fspas.2022.974137) is the reference implementation this module is
written against and validated for agreement. PyThea is GPL-3.0 and this project
is MIT, so no PyThea code is reproduced here; the shared lineage is the MIT
``gcs_python`` above and the published papers.

References
----------
* Thernisien, Howard & Vourlidas (2006), ApJ 652, 763 — doi:10.1086/508254
* Thernisien (2011), ApJS 194, 33 — doi:10.1088/0067-0049/194/2/33

Pure numpy and scipy over plain floats and arrays: no Qt, no I/O and no sunpy in
the hot path, so the geometry, the UI gating and the tests all share one
description of the model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Sequence

import numpy as np


# --- Constants and type aliases -------------------------------------------

#: Radians per arcsecond, for the projection's final scaling.
ARCSEC_PER_RADIAN = 180.0 / math.pi * 3600.0

#: Heliocentric radius below which a point is inside the photosphere. The fast
#: occultation test is exact above this; see the module docstring.
MIN_MODEL_RADIUS_RSUN = 1.05

#: Default mesh resolution, matching PyThea's ``nbvertsl`` / ``nbvertcirc`` /
#: ``nbvertcircshell`` so wireframes are directly comparable. Yields 5130 points.
DEFAULT_N_STRAIGHT = 10
DEFAULT_N_FRONT = 20
DEFAULT_N_CIRCLE = 90

#: Ordered parameter names, used for bounds, Jacobians and error reporting.
PARAMETER_NAMES: tuple[str, ...] = (
    "lon_deg",
    "lat_deg",
    "tilt_deg",
    "height_rsun",
    "alpha_deg",
    "kappa",
)

#: The two parameters GCS constrains poorly even from a good seed: alpha and
#: kappa trade off against each other and against height. Reported so the UI can
#: say so rather than presenting six equally trustworthy numbers.
WEAKLY_CONSTRAINED: tuple[str, ...] = ("alpha_deg", "kappa")

# One polyline of the projected wireframe: matched arcsec arrays, NaN where the
# point is hidden, so ``connect="finite"`` breaks the curve cleanly. Same
# convention as ``solar_grid.Polyline``.
Polyline = tuple[np.ndarray, np.ndarray]


# --- Parameters -----------------------------------------------------------


@dataclass(frozen=True)
class GCSParameters:
    """The six GCS parameters, in degrees and solar radii."""

    #: Stonyhurst longitude of the propagation direction.
    lon_deg: float
    #: Stonyhurst latitude of the propagation direction.
    lat_deg: float
    #: Rotation of the croissant about its own propagation axis.
    tilt_deg: float
    #: Heliocentric leading-edge (apex) distance, not the cone-junction ``h``.
    height_rsun: float
    #: Half angle between the two leg axes.
    alpha_deg: float
    #: Aspect ratio, ``0 < kappa < 1``. Singular at 1.
    kappa: float

    def as_array(self) -> np.ndarray:
        """The six values in :data:`PARAMETER_NAMES` order."""
        return np.array(
            [
                self.lon_deg,
                self.lat_deg,
                self.tilt_deg,
                self.height_rsun,
                self.alpha_deg,
                self.kappa,
            ],
            dtype=float,
        )

    @classmethod
    def from_array(cls, values: Sequence[float]) -> "GCSParameters":
        """Rebuild from a six-element sequence in :data:`PARAMETER_NAMES` order."""
        lon, lat, tilt, height, alpha, kappa = (float(v) for v in values)
        return cls(
            lon_deg=lon,
            lat_deg=lat,
            tilt_deg=tilt,
            height_rsun=height,
            alpha_deg=alpha,
            kappa=kappa,
        )

    def replace_values(self, **changes: float) -> "GCSParameters":
        """A copy with named parameters overridden (frozen-dataclass friendly)."""
        return replace(self, **changes)

    @property
    def is_physical(self) -> bool:
        """False for parameter sets the model cannot evaluate at all."""
        return (
            all(math.isfinite(value) for value in self.as_array())
            and 0.0 < self.kappa < 1.0
            and self.height_rsun > 0.0
            and 0.0 < self.alpha_deg < 90.0
            and -90.0 <= self.lat_deg <= 90.0
        )


def interpolate_parameters(first: GCSParameters, second: GCSParameters, fraction: float) -> GCSParameters:
    """The shell ``fraction`` of the way from ``first`` to ``second``, linearly.

    For showing how recorded fits evolve between the times they were made — an
    interpolated shell is a display aid, never a fit. Longitude goes the short
    way round the Sun and tilt the short way round its 180-degree symmetry (see
    :func:`orientation_matrix`), so the shell never swings through a direction
    neither fit had. Angles are continued from ``first`` rather than wrapped, so
    the readout moves smoothly too. The ends are returned exactly.
    """
    fraction = float(fraction)
    if not math.isfinite(fraction) or fraction <= 0.0:
        return first
    if fraction >= 1.0:
        return second

    def periodic(start: float, stop: float, period: float) -> float:
        step = (stop - start + period / 2.0) % period - period / 2.0
        return start + fraction * step

    def linear(start: float, stop: float) -> float:
        return start + fraction * (stop - start)

    return GCSParameters(
        lon_deg=periodic(first.lon_deg, second.lon_deg, 360.0),
        lat_deg=linear(first.lat_deg, second.lat_deg),
        tilt_deg=periodic(first.tilt_deg, second.tilt_deg, 180.0),
        height_rsun=linear(first.height_rsun, second.height_rsun),
        alpha_deg=linear(first.alpha_deg, second.alpha_deg),
        kappa=linear(first.kappa, second.kappa),
    )


# --- Derived radii (closed-form Thernisien relations) ---------------------


def leg_height_rsun(params: GCSParameters) -> float:
    """Height of the cone junction, ``h`` in T2011 eq. 2 / T2006 eq. 3.

    This is the distance along the propagation axis to where the straight legs
    meet the circular front, derived from the GCS ``height`` input.
    """
    alpha = math.radians(params.alpha_deg)
    return float(
        params.height_rsun * (1.0 - params.kappa) * math.cos(alpha) / (1.0 + math.sin(alpha))
    )


def apex_cross_section_radius_rsun(params: GCSParameters) -> float:
    """Cross-section radius of the shell at the apex, T2011 eq. 29."""
    alpha = math.radians(params.alpha_deg)
    kappa = params.kappa
    h = leg_height_rsun(params)
    return float(kappa * (h / math.cos(alpha) + h * math.tan(alpha)) / (1.0 - kappa**2))


def apex_height_rsun(params: GCSParameters) -> float:
    """Heliocentric distance of the leading edge, in solar radii.

    For GCS this *is* ``params.height_rsun`` — the shell is constructed so that
    its outermost point sits at that distance (the test suite asserts it against
    the mesh). Provided as a named function anyway, so call sites read as the
    physical quantity they mean and would keep working if the parameterisation
    ever changed.
    """
    return float(params.height_rsun)


def shell_centre_distance_rsun(params: GCSParameters) -> float:
    """Distance to the apex circle's centre, ``OC1`` in T2011 eq. 20.

    Reported alongside the apex radius so the panel can show the same derived
    quantities PyThea does.
    """
    return float(params.height_rsun - apex_cross_section_radius_rsun(params))


def angular_widths_deg(params: GCSParameters) -> tuple[float, float]:
    """Return intrinsic (face-on, edge-on) angular widths in degrees.

    Thernisien's cone half angle is ``delta = asin(kappa)``. The full
    face-on width is ``2 * (alpha + delta)`` and edge-on width ``2 * delta``;
    ``2 * alpha`` alone omits the finite shell cross-section. These intrinsic
    widths are not the apparent width in an arbitrary observer's image.
    """
    delta = math.degrees(math.asin(params.kappa))
    return 2.0 * (params.alpha_deg + delta), 2.0 * delta


# --- Mesh -----------------------------------------------------------------


@dataclass(frozen=True)
class GCSMesh:
    """The shell as a point cloud in the model's own frame, in solar radii.

    Depends only on ``(height, alpha, kappa)`` — never on the orientation — so a
    drag of ``lon``/``lat``/``tilt`` reuses the cached mesh and costs one 3x3
    matrix multiply.
    """

    #: ``(3, N)`` model-frame coordinates, contiguous for a fast ``M @ points``.
    points: np.ndarray
    #: ``(N,)`` skeleton station index of each point, for wireframe rings.
    station: np.ndarray
    #: Number of points around each cross-section circle.
    n_circle: int
    #: Number of skeleton stations (rings) the shell was built from.
    n_station: int

    @property
    def size(self) -> int:
        return int(self.points.shape[1])


def gcs_skeleton(
    alpha_rad: float,
    leg_height: float,
    kappa: float,
    *,
    n_straight: int = DEFAULT_N_STRAIGHT,
    n_front: int = DEFAULT_N_FRONT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Axis of the GCS shell: the two straight legs plus the circular front.

    Returns ``(positions, radii, cone_angles)`` where ``positions`` is ``(S, 3)``
    station centres, ``radii`` the shell's cross-section radius at each station
    and ``cone_angles`` the local tilt of that cross-section's plane.

    Follows ``gcs_python``'s ``skeleton()`` (MIT; see the module docstring), in
    turn a port of the IDL ``shellskeleton.pro``.
    """
    gamma = math.asin(kappa)

    # Straight legs: two rays from the origin, opening by +/- alpha, sampled to
    # the junction height. The shell radius grows linearly along them.
    right = np.array([0.0, math.sin(alpha_rad), math.cos(alpha_rad)])
    left = np.array([0.0, -math.sin(alpha_rad), math.cos(alpha_rad)])
    along = np.linspace(0.0, leg_height, n_straight)
    leg_right = np.outer(along, right)
    leg_left = np.outer(along, left)
    leg_radius = math.tan(gamma) * np.linalg.norm(leg_right, axis=1)
    leg_angle = np.full(n_straight, -alpha_rad)

    # Circular front: an arc of station centres whose distance from the axis and
    # cross-section radius both follow from the T2006 similarity relations.
    beta = np.linspace(-alpha_rad, math.pi / 2.0, n_front)
    hypot = leg_height / math.cos(alpha_rad)
    rho = leg_height * math.tan(alpha_rad)
    offset = (rho + hypot * kappa**2 * np.sin(beta)) / (1.0 - kappa**2)
    front_radius = np.sqrt(
        (hypot**2 * kappa**2 - rho**2) / (1.0 - kappa**2) + offset**2
    )
    front_right = np.array(
        [np.zeros(beta.shape), offset * np.cos(beta), hypot + offset * np.sin(beta)]
    ).T
    front_left = np.array(
        [np.zeros(beta.shape), -offset * np.cos(beta), hypot + offset * np.sin(beta)]
    ).T

    # Walk the whole skeleton once: up the right leg, over the front, down the
    # left leg. The [1:] slices drop the duplicated joint stations.
    radii = np.concatenate(
        (leg_radius, front_radius[1:], np.flipud(front_radius)[1:], np.flipud(leg_radius)[1:])
    )
    angles = np.concatenate(
        (
            leg_angle,
            beta[1:],
            math.pi - np.flipud(beta)[1:],
            math.pi - np.flipud(leg_angle)[1:],
        )
    )
    positions = np.concatenate(
        (leg_right, front_right[1:], np.flipud(front_left)[1:], np.flipud(leg_left)[1:])
    )
    return positions, radii, angles


def gcs_mesh(
    params: GCSParameters,
    *,
    n_straight: int = DEFAULT_N_STRAIGHT,
    n_front: int = DEFAULT_N_FRONT,
    n_circle: int = DEFAULT_N_CIRCLE,
) -> GCSMesh:
    """Revolve the skeleton into the full shell, in the model's own frame.

    Orientation-independent by construction, which is what lets a ``lon``/
    ``lat``/``tilt`` drag skip the rebuild entirely.
    """
    alpha = math.radians(params.alpha_deg)
    positions, radii, angles = gcs_skeleton(
        alpha,
        leg_height_rsun(params),
        params.kappa,
        n_straight=n_straight,
        n_front=n_front,
    )

    theta = np.linspace(0.0, 2.0 * math.pi, n_circle)
    stations = np.arange(positions.shape[0])
    theta_grid, station_grid = np.meshgrid(theta, stations)
    theta_flat = theta_grid.ravel()
    station_flat = station_grid.ravel()

    ring = np.array(
        [
            np.cos(theta_flat),
            np.sin(theta_flat) * np.cos(angles[station_flat]),
            np.sin(theta_flat) * np.sin(angles[station_flat]),
        ]
    )
    points = radii[station_flat] * ring + positions[station_flat].T
    return GCSMesh(
        points=np.ascontiguousarray(points),
        station=station_flat,
        n_circle=int(n_circle),
        n_station=int(positions.shape[0]),
    )


# --- Orientation and observer geometry ------------------------------------


def orientation_matrix(params: GCSParameters) -> np.ndarray:
    """``(3, 3)`` rotation from the model frame into HGS Cartesian.

    The model is built with its propagation axis along +Z and its legs in the
    YZ plane; this swings that axis to ``(lon, lat)`` and spins the croissant by
    ``tilt``. Depends on none of ``height``/``alpha``/``kappa``, which is why the
    mesh survives an orientation drag untouched.

    GCS is symmetric under ``tilt -> tilt + 180``, so only 180 degrees of tilt is
    identifiable; callers reporting a fitted tilt should fold it accordingly.
    """
    from scipy.spatial.transform import Rotation

    lon = math.radians(params.lon_deg)
    lat = math.radians(params.lat_deg)
    tilt = math.radians(params.tilt_deg)
    # Extrinsic z-y-x in scipy's spelling, so the angles are supplied
    # innermost-first: spin by tilt, drop to the latitude, then swing round to
    # the longitude. The order is easy to invert -- gcs_python's rotate_mesh
    # passes [tilt, lat, lon] for exactly this reason -- and getting it wrong
    # silently files tilt as longitude, so the test suite asserts the apex lands
    # at the requested (lon, lat).
    rotation = Rotation.from_euler("zyx", [tilt, lat, lon]).as_matrix()
    # gcs_python emits (z, y, x) with y flipped relative to HGS Cartesian; fold
    # that axis permutation into the matrix so the hot path stays a single
    # multiply instead of a permute-and-negate per update.
    permute = np.array([[0.0, 0.0, 1.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0]])
    return permute @ rotation


@dataclass(frozen=True)
class ObserverGeometry:
    """Where a frame was observed from, in the few numbers the projection needs.

    Built from ``frame.coordinate_frame`` alone — never ``.wcs``,
    ``.observer_coordinate`` or ``.world_to_pixel``. That matters because cropped
    and composited frames are ``AiaArrayMap`` (see
    ``src.Backend.solar_data_analysis``), which copies ``coordinate_frame``
    through but has none of the ``sunpy.map.Map`` WCS methods. Depending on the
    narrower attribute is what lets a GCS fit survive a crop.
    """

    #: Stonyhurst longitude of the observer, degrees.
    lon_deg: float
    #: Stonyhurst latitude of the observer, degrees.
    lat_deg: float
    #: Observer distance from Sun centre, in solar radii.
    dsun_rsun: float
    #: Apparent solar radius as seen from there, arcsec.
    rsun_arcsec: float
    #: Observation time, when the frame carries one.
    obstime: datetime | None = None
    #: Short label for panels and exports ("LASCO C3", "STEREO-A COR2").
    label: str = ""

    @property
    def projection_matrix(self) -> np.ndarray:
        """``(3, 3)`` HGS Cartesian -> Heliocentric Cartesian for this observer.

        Heliocentric Cartesian has +Z toward the observer, +Y toward solar north
        projected and +X completing the right-handed set, which is the frame the
        helioprojective formulae below are written in.
        """
        lon = math.radians(self.lon_deg)
        lat = math.radians(self.lat_deg)
        sin_lon, cos_lon = math.sin(lon), math.cos(lon)
        sin_lat, cos_lat = math.sin(lat), math.cos(lat)
        return np.array(
            [
                [-sin_lon, cos_lon, 0.0],
                [-cos_lon * sin_lat, -sin_lon * sin_lat, cos_lat],
                [cos_lon * cos_lat, sin_lon * cos_lat, sin_lat],
            ]
        )

    @classmethod
    def from_frame(cls, frame: Any, *, label: str = "") -> "ObserverGeometry | None":
        """Read the geometry off a displayed frame, or ``None`` if it has none.

        Returns ``None`` rather than raising for frames with no usable coordinate
        system (a non-solar FITS, or a derived array that lost its
        ``coordinate_frame``), so the GCS tool can hide itself the same way
        ``solar_grid.graticule_arcsec`` returns empty lists.
        """
        coordinate_frame = getattr(frame, "coordinate_frame", None)
        if coordinate_frame is None:
            return None
        observer = getattr(coordinate_frame, "observer", None)
        if observer is None:
            return None

        try:
            import astropy.units as u
            from astropy.coordinates import SkyCoord
            from sunpy.coordinates import frames as sunpy_frames

            # ``coordinate_frame.observer`` is already a populated frame, not a
            # SkyCoord, and transforming to a frame *class* raises inside astropy
            # -- it must be an instantiated frame carrying the same obstime.
            observer_sky = SkyCoord(observer)
            stonyhurst = observer_sky.transform_to(
                sunpy_frames.HeliographicStonyhurst(obstime=observer_sky.obstime)
            )
            rsun = getattr(coordinate_frame, "rsun", None)
            if rsun is None:
                from astropy.constants import R_sun

                rsun = R_sun
            rsun_rsun_units = float((rsun / (1.0 * u.R_sun).to(u.km)).decompose().value)
            dsun = float(stonyhurst.radius.to_value(u.R_sun))
            if not (math.isfinite(dsun) and dsun > rsun_rsun_units):
                return None
            # Apparent limb is the tangent to the sphere: sin(angle) = R / d.
            rsun_arcsec = math.degrees(math.asin(rsun_rsun_units / dsun)) * 3600.0
            lon = float(stonyhurst.lon.to_value(u.deg))
            lat = float(stonyhurst.lat.to_value(u.deg))
        except Exception:
            return None

        if not (math.isfinite(lon) and math.isfinite(lat) and math.isfinite(rsun_arcsec)):
            return None

        from src.Backend.solar_data_analysis import frame_observation_time

        try:
            obstime = frame_observation_time(frame)
        except Exception:
            obstime = None
        return cls(
            lon_deg=lon,
            lat_deg=lat,
            dsun_rsun=dsun,
            rsun_arcsec=rsun_arcsec,
            obstime=obstime,
            label=str(label or getattr(frame, "nickname", "") or ""),
        )


def observer_separation_deg(a: ObserverGeometry, b: ObserverGeometry) -> float:
    """Angle between two observers as seen from the Sun, 0-180 degrees.

    The multi-viewpoint constraint lives here: below roughly 20 degrees the two
    views see nearly the same projection, so longitude stays degenerate with
    height and width no matter how many points are clicked.
    """
    lon_a, lat_a = math.radians(a.lon_deg), math.radians(a.lat_deg)
    lon_b, lat_b = math.radians(b.lon_deg), math.radians(b.lat_deg)
    # Haversine rather than acos(dot): acos loses most of its precision for small
    # separations, which is exactly the range the degeneracy gate cares about.
    delta_lon = lon_b - lon_a
    haversine = (
        math.sin((lat_b - lat_a) / 2.0) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2.0) ** 2
    )
    root = math.sqrt(max(0.0, min(1.0, haversine)))
    return float(math.degrees(2.0 * math.asin(root)))


# --- Projection to helioprojective arcsec ---------------------------------


@dataclass(frozen=True)
class GCSProjection:
    """The shell as the canvas draws it: helioprojective arcsec, NaN where hidden."""

    #: ``(N,)`` helioprojective Tx in arcsec, NaN where the point is not drawn.
    tx_arcsec: np.ndarray
    #: ``(N,)`` helioprojective Ty in arcsec, NaN where the point is not drawn.
    ty_arcsec: np.ndarray
    #: Apex position in arcsec, for the drag handle. NaN when the apex is hidden.
    apex_tx: float
    apex_ty: float
    #: Share of mesh points actually drawn — near 0 means the fit has wandered
    #: outside the detector, which the panel can warn about.
    visible_fraction: float


def project_points_to_arcsec(
    points_hgs: np.ndarray, observer: ObserverGeometry
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project HGS Cartesian points (R_sun) to helioprojective arcsec.

    ``points_hgs`` is ``(3, N)``. Returns ``(tx_arcsec, ty_arcsec, occulted)``.

    This is the hot path: one ``(3, 3) @ (3, N)`` multiply and a handful of
    elementwise operations, no astropy. It agrees with
    ``SkyCoord.transform_to(Helioprojective)`` to ~2e-10 arcsec.

    ``occulted`` is True where the point must not be drawn: either the solar disk
    blocks the sight line (the segment from observer to point passes within one
    solar radius of Sun centre *before* reaching the point), or the point is
    itself inside the photosphere.

    The second case is not hypothetical — the GCS legs are anchored at Sun
    centre, so the innermost skeleton stations sit at r = 0. Those points are
    unphysical and are also where the cheap ray/sphere test stops agreeing with
    ``Helioprojective.is_visible()``, so dropping them is both the correct
    rendering and what makes the fast path exact over everything drawn.
    """
    x, y, z = observer.projection_matrix @ points_hgs
    dsun = observer.dsun_rsun

    # Sight line runs from the observer at (0, 0, dsun) to the point.
    to_point_z = z - dsun
    length_sq = x * x + y * y + to_point_z * to_point_z
    with np.errstate(divide="ignore", invalid="ignore"):
        # Parameter along the segment at closest approach to Sun centre.
        closest = -(dsun * to_point_z) / length_sq
        perp_sq = (closest * x) ** 2 + (closest * y) ** 2 + (dsun + closest * to_point_z) ** 2
    rsun_model = math.sin(math.radians(observer.rsun_arcsec / 3600.0)) * dsun
    behind_disk = (perp_sq < rsun_model * rsun_model) & (closest > 0.0) & (closest < 1.0)
    inside_sun = (
        points_hgs[0] ** 2 + points_hgs[1] ** 2 + points_hgs[2] ** 2
    ) < (MIN_MODEL_RADIUS_RSUN * rsun_model) ** 2
    occulted = behind_disk | inside_sun

    depth = dsun - z
    distance = np.sqrt(x * x + y * y + depth * depth)
    with np.errstate(divide="ignore", invalid="ignore"):
        tx = np.arctan2(x, depth) * ARCSEC_PER_RADIAN
        ty = np.arcsin(np.clip(y / distance, -1.0, 1.0)) * ARCSEC_PER_RADIAN
    return tx, ty, occulted


def project_to_arcsec(
    mesh: GCSMesh,
    params: GCSParameters,
    observer: ObserverGeometry,
    *,
    fov_rsun: tuple[float, float] | None = None,
) -> GCSProjection:
    """Orient ``mesh`` and project it for ``observer``.

    ``fov_rsun`` is the detector's ``(inner, outer)`` field of view in solar
    radii — pass ``INSTRUMENT_FOV_RSUN[...]`` from
    ``src.Backend.coronagraph_composite`` so the wireframe vanishes behind a
    coronagraph's occulting disk instead of being drawn over it.
    """
    oriented = orientation_matrix(params) @ mesh.points
    tx, ty, occulted = project_points_to_arcsec(oriented, observer)

    hidden = occulted | ~np.isfinite(tx) | ~np.isfinite(ty)
    if fov_rsun is not None:
        inner, outer = (float(fov_rsun[0]), float(fov_rsun[1]))
        plane_radius = np.hypot(tx, ty) / observer.rsun_arcsec
        hidden = hidden | (plane_radius < inner) | (plane_radius > outer)

    tx = np.where(hidden, np.nan, tx)
    ty = np.where(hidden, np.nan, ty)

    apex_hgs = orientation_matrix(params) @ np.array([[0.0], [0.0], [params.height_rsun]])
    apex_tx, apex_ty, apex_occulted = project_points_to_arcsec(apex_hgs, observer)
    apex_x = float(apex_tx[0]) if not apex_occulted[0] else math.nan
    apex_y = float(apex_ty[0]) if not apex_occulted[0] else math.nan

    drawn = int(np.count_nonzero(~hidden))
    return GCSProjection(
        tx_arcsec=tx,
        ty_arcsec=ty,
        apex_tx=apex_x,
        apex_ty=apex_y,
        visible_fraction=float(drawn) / float(max(tx.size, 1)),
    )


# --- Wireframe ------------------------------------------------------------


def wireframe_polylines(
    mesh: GCSMesh,
    projection: GCSProjection,
    *,
    ring_stride: int = 3,
    n_longitudinal: int = 8,
) -> list[Polyline]:
    """The projected shell as cross-section rings plus longitudinal lines.

    Drawing all 5130 mesh points as one polyline — as PyThea's ``plot_coord``
    does — is both cluttered and needlessly expensive to paint. Sampling every
    ``ring_stride``-th cross-section and a few lines along the shell gives the
    familiar croissant outline at roughly a quarter of the vertices.
    """
    tx = projection.tx_arcsec.reshape(mesh.n_station, mesh.n_circle)
    ty = projection.ty_arcsec.reshape(mesh.n_station, mesh.n_circle)

    polylines: list[Polyline] = []
    stride = max(1, int(ring_stride))
    for index in range(0, mesh.n_station, stride):
        polylines.append((tx[index], ty[index]))
    # Always include the last ring so the shell is visually closed.
    if (mesh.n_station - 1) % stride:
        polylines.append((tx[-1], ty[-1]))

    count = max(0, int(n_longitudinal))
    if count:
        for index in np.linspace(0, mesh.n_circle - 1, count, dtype=int):
            polylines.append((tx[:, index], ty[:, index]))
    return polylines


def join_polylines(polylines: Sequence[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate polylines into one NaN-separated pair of arrays.

    A single ``PlotCurveItem.setData(x, y, connect="finite")`` then draws the
    whole wireframe in one call, which is what keeps a slider drag smooth: no
    graphics items are created or destroyed per update.
    """
    if not polylines:
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    separator = np.array([np.nan])
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    for x, y in polylines:
        xs.append(np.asarray(x, dtype=float))
        xs.append(separator)
        ys.append(np.asarray(y, dtype=float))
        ys.append(separator)
    return np.concatenate(xs[:-1]), np.concatenate(ys[:-1])


def wireframe_arcsec(
    params: GCSParameters,
    observer: ObserverGeometry,
    *,
    mesh: GCSMesh | None = None,
    fov_rsun: tuple[float, float] | None = None,
    ring_stride: int = 3,
    n_longitudinal: int = 8,
) -> tuple[np.ndarray, np.ndarray, GCSProjection]:
    """One-call path from parameters to what the canvas draws.

    Pass a cached ``mesh`` when only the orientation changed, which skips the
    rebuild. Returns ``(x_arcsec, y_arcsec, projection)``.
    """
    shell = mesh if mesh is not None else gcs_mesh(params)
    projection = project_to_arcsec(shell, params, observer, fov_rsun=fov_rsun)
    polylines = wireframe_polylines(
        shell, projection, ring_stride=ring_stride, n_longitudinal=n_longitudinal
    )
    x, y = join_polylines(polylines)
    return x, y, projection


# --- Inverse geometry, for direct manipulation -----------------------------


def direction_from_arcsec(
    tx_arcsec: float,
    ty_arcsec: float,
    *,
    radius_rsun: float,
    observer: ObserverGeometry,
    prefer: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """HGS lon/lat where a sight line meets the sphere of ``radius_rsun``.

    The exact inverse of :func:`project_points_to_arcsec` for a known radius, so
    dragging the apex handle lands the apex precisely under the cursor instead of
    creeping as a linearised step would. Returns ``None`` when the sight line
    misses the sphere entirely.

    A sight line crosses the sphere **twice**, and the two roots are genuinely
    different CMEs — one pointing toward the observer's hemisphere, one away.
    ``prefer`` is the current ``(lon, lat)``; when given, the root nearer to it
    wins, so a drag stays continuous instead of flipping the eruption to the
    other side of the Sun the moment its apex passes the limb. Without it the
    near-side root is returned.

    At radius 1 R_sun this is the same geometry ``solar_grid.point_lonlat`` gets
    from sunpy, which makes that function a ready cross-check in the tests.
    """
    if not (radius_rsun > 0.0 and math.isfinite(tx_arcsec) and math.isfinite(ty_arcsec)):
        return None
    tx = math.radians(tx_arcsec / 3600.0)
    ty = math.radians(ty_arcsec / 3600.0)
    dsun = observer.dsun_rsun

    # Unit sight direction in heliocentric Cartesian, observer at (0, 0, dsun).
    sight = np.array([math.cos(ty) * math.sin(tx), math.sin(ty), -math.cos(ty) * math.cos(tx)])
    half_b = dsun * sight[2]
    discriminant = half_b * half_b - (dsun * dsun - radius_rsun * radius_rsun)
    if discriminant < 0.0:
        return None
    root = math.sqrt(discriminant)

    candidates: list[tuple[float, float]] = []
    for distance in (-half_b - root, -half_b + root):
        if distance <= 0.0:
            continue
        hcc = np.array([0.0, 0.0, dsun]) + distance * sight
        hgs = observer.projection_matrix.T @ hcc
        norm = float(np.linalg.norm(hgs))
        if norm <= 0.0:
            continue
        candidates.append(
            (
                math.degrees(math.atan2(hgs[1], hgs[0])),
                math.degrees(math.asin(max(-1.0, min(1.0, hgs[2] / norm)))),
            )
        )
    if not candidates:
        return None
    if prefer is None or len(candidates) == 1:
        return candidates[0]

    def separation(candidate: tuple[float, float]) -> float:
        delta_lon = abs(((candidate[0] - prefer[0] + 180.0) % 360.0) - 180.0)
        return math.hypot(delta_lon, candidate[1] - prefer[1])

    return min(candidates, key=separation)


def apex_arcsec(params: GCSParameters, observer: ObserverGeometry) -> tuple[float, float]:
    """Where the apex projects, in arcsec. Projects one point, not the mesh."""
    apex = orientation_matrix(params) @ np.array([[0.0], [0.0], [params.height_rsun]])
    tx, ty, _ = project_points_to_arcsec(apex, observer)
    return float(tx[0]), float(ty[0])


def height_for_apex_radius(
    params: GCSParameters,
    observer: ObserverGeometry,
    target_radius_arcsec: float,
    *,
    bounds: tuple[float, float] = (MIN_MODEL_RADIUS_RSUN, 30.0),
) -> float:
    """Solve for the ``height`` whose projected apex sits at a given sky radius.

    A one-dimensional ``brentq`` on a single-point forward model, so it costs
    microseconds. Monotone as long as the propagation direction is not close to
    the line of sight; within a few degrees of it the projected radius barely
    responds to height, so the current height is returned unchanged rather than
    letting the solve run away.
    """
    from scipy.optimize import brentq

    target = float(target_radius_arcsec)
    if not (math.isfinite(target) and target > 0.0):
        return float(params.height_rsun)

    def plane_radius(height: float) -> float:
        tx, ty = apex_arcsec(params.replace_values(height_rsun=height), observer)
        return math.hypot(tx, ty)

    low, high = float(bounds[0]), float(bounds[1])
    # Near the line of sight the apex hardly moves on the sky, so there is no
    # well-posed inverse; refuse instead of returning a wild height.
    if plane_radius(high) - plane_radius(low) < observer.rsun_arcsec * 0.05:
        return float(params.height_rsun)

    f_low = plane_radius(low) - target
    f_high = plane_radius(high) - target
    if f_low > 0.0:
        return low
    if f_high < 0.0:
        return high
    try:
        return float(brentq(lambda h: plane_radius(h) - target, low, high, xtol=1e-6))
    except Exception:
        return float(params.height_rsun)


def apply_apex_drag(
    params: GCSParameters,
    observer: ObserverGeometry,
    target_arcsec: tuple[float, float],
    *,
    lock: str = "",
    passes: int = 2,
) -> GCSParameters:
    """Parameters that move the apex handle to ``target_arcsec``.

    The drag is split in polar coordinates about disk centre, because a plain
    two-unknown inverse is degenerate: any sky position can be reached by
    swinging the direction alone, which would let a drag straight outward rack
    the CME around the limb instead of raising it. So the *position angle* of the
    target drives the direction and its *sky radius* drives the height, which is
    what the gesture visually promises.

    ``lock`` constrains the drag: ``"radial"`` changes only ``height`` (the
    modifier-key "pure height" gesture), ``"tangential"`` only the direction, and
    ``""`` does both.

    The handle always lands exactly under the cursor. The *direction* keeps a
    small residual on an unlocked radial drag — the two steps are weakly coupled,
    and the pair settles on a fixed point about 2e-3 degrees from the starting
    direction rather than converging to it. Extra passes do not reduce it
    (measured: identical at 2, 3 and 4 passes, at 0.4 ms each), and 2e-3 degrees
    is ~0.3 arcsec at 12 R_sun — well under a pixel on any coronagraph — so two
    passes is the right stopping point. Use ``lock="radial"`` when the direction
    must be held exactly.
    """
    target_x, target_y = float(target_arcsec[0]), float(target_arcsec[1])
    if not (math.isfinite(target_x) and math.isfinite(target_y)):
        return params
    target_radius = math.hypot(target_x, target_y)
    if target_radius <= 0.0:
        return params

    updated = params
    for _ in range(max(1, int(passes))):
        if lock != "radial":
            # Re-aim at the target's position angle, but at the radius the apex
            # currently projects to, so this step carries no height information.
            current_x, current_y = apex_arcsec(updated, observer)
            current_radius = math.hypot(current_x, current_y)
            if current_radius > 0.0:
                scale = current_radius / target_radius
                direction = direction_from_arcsec(
                    target_x * scale,
                    target_y * scale,
                    radius_rsun=updated.height_rsun,
                    observer=observer,
                    prefer=(updated.lon_deg, updated.lat_deg),
                )
                if direction is not None:
                    updated = updated.replace_values(
                        lon_deg=direction[0], lat_deg=direction[1]
                    )
        if lock != "tangential":
            updated = updated.replace_values(
                height_rsun=height_for_apex_radius(updated, observer, target_radius)
            )
    return updated


def handle_positions_arcsec(
    params: GCSParameters, observer: ObserverGeometry
) -> dict[str, tuple[float, float]]:
    """Where every drag handle belongs for these parameters.

    One source of truth, used after a slider change, a drag, a refine and a frame
    change, so the handles can never drift off the wireframe.
    """
    rotation = orientation_matrix(params)
    alpha = math.radians(params.alpha_deg)
    leg = leg_height_rsun(params)
    rapex = apex_cross_section_radius_rsun(params)

    # Model-frame anchor points: apex on the axis, flank out along one leg, rim
    # offset perpendicular to the axis at the apex.
    model = {
        "apex": np.array([0.0, 0.0, params.height_rsun]),
        "flank": np.array([0.0, leg * math.sin(alpha), leg * math.cos(alpha)]),
        "rim": np.array([rapex, 0.0, params.height_rsun - rapex]),
    }
    columns = np.column_stack([model[name] for name in ("apex", "flank", "rim")])
    tx, ty, occulted = project_points_to_arcsec(rotation @ columns, observer)
    # NaN for an occulted handle, which the canvas renders as "hidden". A handle
    # you cannot see must not be draggable: direction_from_arcsec only ever
    # returns the near-side sphere intersection, so dragging a far-side apex
    # would silently mirror the CME onto the near side.
    return {
        name: (
            (math.nan, math.nan)
            if occulted[index]
            else (float(tx[index]), float(ty[index]))
        )
        for index, name in enumerate(("apex", "flank", "rim"))
    }


# --- Refinement against clicked front points -------------------------------


@dataclass(frozen=True)
class GCSViewpoint:
    """One observer's geometry plus the front points clicked in that view."""

    observer: ObserverGeometry
    #: ``(K, 2)`` clicked helioprojective positions, arcsec.
    clicks_arcsec: np.ndarray
    label: str = ""
    #: Visible detector range (inner, outer), in apparent solar radii.
    fov_rsun: tuple[float, float] | None = None

    @property
    def n_clicks(self) -> int:
        return int(np.asarray(self.clicks_arcsec).reshape(-1, 2).shape[0])


@dataclass(frozen=True)
class GCSRefinement:
    """What :func:`refine_gcs` hands back.

    ``sigma`` and ``weakly_constrained`` are not decoration. GCS constrains
    ``alpha`` and ``kappa`` poorly even from a good seed, so a caller that shows
    six bare numbers misrepresents the fit; the panel is expected to show the
    error bars and name the weak parameters.
    """

    parameters: GCSParameters
    #: What the refinement started from — a refine is a polish, not a fit.
    seed: GCSParameters
    #: 1-sigma per parameter name; NaN when there is no spare degree of freedom.
    #:
    #: These are **formal** errors: they propagate the scatter of the clicks
    #: through the Jacobian and assume the model itself is unbiased. GCS plus a
    #: nearest-point cost is not unbiased, so they understate the truth, badly
    #: for the weak parameters — on the validation case in the test suite the
    #: formal sigma on ``alpha_deg`` is 0.7 degrees while the actual error is 9.
    #: Treat sigma as a precision estimate, never an accuracy claim, and read
    #: :attr:`weakly_constrained` alongside it.
    sigma: dict[str, float]
    #: Covariance in ``free`` order, or ``None`` when undetermined or unreliable.
    covariance: np.ndarray | None
    #: Nonnegative nearest-mesh distances in arcsec, in finite-click input order.
    residuals_arcsec: np.ndarray
    rms_arcsec: float
    #: RMS the seed produced, so the caller can show the improvement.
    seed_rms_arcsec: float
    max_residual_arcsec: float
    #: Index of the worst click, for highlighting which point to redo.
    worst_index: int
    n_points: int
    n_viewpoints: int
    separation_deg: float
    #: Names actually varied; the rest were held (see :func:`free_parameters`).
    free: tuple[str, ...]
    weakly_constrained: tuple[str, ...]
    #: Condition of the normal matrix after scaling columns by parameter units.
    condition_number: float
    converged: bool
    n_evaluations: int
    elapsed_s: float
    #: Safe to show verbatim in the status bar.
    message: str


#: Parameter bounds. Angles are bounded *relative to the seed* at call time,
#: because a hard [-180, 180] puts a seam in the middle of the search space.
_ABSOLUTE_BOUNDS: dict[str, tuple[float, float]] = {
    "lat_deg": (-89.0, 89.0),
    "height_rsun": (MIN_MODEL_RADIUS_RSUN, 30.0),
    "alpha_deg": (1.0, 80.0),
    "kappa": (0.05, 0.95),
}
_RELATIVE_BOUNDS: dict[str, float] = {"lon_deg": 90.0, "tilt_deg": 90.0}

#: Per-parameter step scale for the optimiser. Roughly "how much of this
#: parameter is one meaningful unit of change".
_X_SCALE: dict[str, float] = {
    "lon_deg": 10.0,
    "lat_deg": 10.0,
    "tilt_deg": 10.0,
    "height_rsun": 1.0,
    "alpha_deg": 10.0,
    "kappa": 0.1,
}

#: Conservative geometry heuristic, not a universal accuracy threshold.
MIN_USEFUL_SEPARATION_DEG = 20.0


def has_independent_viewpoints(viewpoints: Sequence[GCSViewpoint]) -> bool:
    """Whether point-bearing views include a usefully non-collinear pair.

    Empty views supply no constraints. Near-opposite observers also have
    nearly parallel lines of sight, so their large angular separation alone
    does not make direction recovery well posed. The 20-degree margin is a
    practical guard, not a guarantee of a unique or accurate solution.
    """
    active = [view for view in viewpoints if len(_clicks_array(view))]
    return any(
        MIN_USEFUL_SEPARATION_DEG
        <= observer_separation_deg(a.observer, b.observer)
        <= 180.0 - MIN_USEFUL_SEPARATION_DEG
        for index, a in enumerate(active)
        for b in active[index + 1 :]
    )


def free_parameters(viewpoints: Sequence[GCSViewpoint]) -> tuple[str, ...]:
    """Which parameters the given viewpoints can actually constrain.

    A single view — or two views closer together than
    :data:`MIN_USEFUL_SEPARATION_DEG` — cannot separate the propagation
    direction from the shell's height and width, so ``lon``/``lat`` are held at
    whatever the user set. Freeing them anyway yields a fit that looks converged
    and means nothing.
    """
    geometric = ("tilt_deg", "height_rsun", "alpha_deg", "kappa")
    if not has_independent_viewpoints(viewpoints):
        return geometric
    return PARAMETER_NAMES


#: The order a refine frees GCS parameters in as front points accumulate, best
#: constrained first: any point on the front fixes the height, the direction
#: needs points in two separated views, and the width parameters α and κ — which
#: GCS constrains worst — come last.
REFINE_STAGES: tuple[tuple[str, ...], ...] = (
    ("height_rsun",),
    ("lon_deg", "lat_deg"),
    ("tilt_deg",),
    ("alpha_deg",),
    ("kappa",),
)


def staged_parameters(
    allowed: Sequence[str],
    stages: Sequence[Sequence[str]],
    n_points: int,
    order: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The parameters ``n_points`` front points can refine, and the stage after them.

    A least-squares refine needs more points than free parameters, so stages are
    released in order while the points outnumber what is free; members the
    viewing geometry cannot constrain (absent from ``allowed``) are skipped.
    Returns ``(free, next_stage)`` in ``order``; ``next_stage`` is empty once
    everything allowed is free.
    """
    budget = int(n_points) - 1
    chosen: list[str] = []
    upcoming: list[str] = []
    for stage in stages:
        members = [name for name in stage if name in allowed and name not in chosen]
        if not members:
            continue
        if len(chosen) + len(members) > budget:
            upcoming = members
            break
        chosen.extend(members)
    return (
        tuple(name for name in order if name in chosen),
        tuple(name for name in order if name in upcoming),
    )


def refine_plan(viewpoints: Sequence[GCSViewpoint]) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """What a GCS refine of these front points fits: ``(free, next stage, points)``.

    With enough points in two separated views this is every parameter, exactly
    :func:`free_parameters`; with fewer, the best-constrained ones, so a refine
    is useful from the second point on.
    """
    total = int(sum(len(_clicks_array(view)) for view in viewpoints))
    free, upcoming = staged_parameters(free_parameters(viewpoints), REFINE_STAGES, total, PARAMETER_NAMES)
    return free, upcoming, total


def _clicks_array(viewpoint: GCSViewpoint) -> np.ndarray:
    clicks = np.asarray(viewpoint.clicks_arcsec, dtype=float).reshape(-1, 2)
    return clicks[np.isfinite(clicks).all(axis=1)]


def refine_gcs(
    viewpoints: Sequence[GCSViewpoint],
    seed: GCSParameters,
    *,
    free: Sequence[str] | None = None,
    click_tolerance_arcsec: float = 30.0,
    robust: bool = True,
    max_nfev: int = 200,
    mesh_resolution: Mapping[str, int] | None = None,
) -> GCSRefinement:
    """Least-squares polish of a manual GCS fit against clicked front points.

    This is a **refinement, not a fit**. Measured on synthetic two-viewpoint
    data, a realistic manual seed (off by ~8 degrees in direction and 1 R_sun in
    height) is improved to a few degrees and ~0.2 R_sun, while a cold start does
    not converge at all — ``lon``, ``alpha`` and ``kappa`` are genuinely
    ill-conditioned in GCS. Callers must therefore seed from the user's manual
    fit and present this as a polish step.

    Raises ``ValueError`` with user-facing text when the inputs cannot support a
    fit, matching ``image_measure.fit_circle`` and ``coronagraph.fit_height_time``
    so the controller can surface the message verbatim.
    """
    import time

    from scipy.optimize import least_squares

    if not viewpoints:
        raise ValueError("A GCS refine needs at least one viewpoint.")
    clicks = [_clicks_array(view) for view in viewpoints]
    total = int(sum(len(c) for c in clicks))
    if total == 0:
        raise ValueError("Click along the CME front before refining the GCS fit.")
    if not seed.is_physical:
        raise ValueError("The starting GCS parameters are outside the model's valid range.")

    # An open image without usable clicks contributes no information, including
    # no justification for releasing direction parameters in a one-view fit.
    active = [(view, points) for view, points in zip(viewpoints, clicks) if len(points)]
    viewpoints = [view for view, _ in active]
    clicks = [points for _, points in active]
    for view in viewpoints:
        geometry = view.observer
        if not (
            all(math.isfinite(value) for value in (
                geometry.lon_deg, geometry.lat_deg, geometry.dsun_rsun, geometry.rsun_arcsec
            ))
            and -90.0 <= geometry.lat_deg <= 90.0
            and geometry.dsun_rsun > 1.0
            and 0.0 < geometry.rsun_arcsec < 90.0 * 3600.0
        ):
            raise ValueError("Every fitted viewpoint needs valid observer coordinates and distance.")
        if view.fov_rsun is not None:
            inner, outer = view.fov_rsun
            if not (math.isfinite(inner) and math.isfinite(outer) and 0.0 <= inner < outer):
                raise ValueError("The detector field of view must have increasing, finite radii.")
    if not (math.isfinite(click_tolerance_arcsec) and click_tolerance_arcsec > 0.0):
        raise ValueError("Click tolerance must be a positive finite value in arcseconds.")

    names = tuple(free) if free is not None else free_parameters(viewpoints)
    unknown = [name for name in names if name not in PARAMETER_NAMES]
    if unknown:
        raise ValueError(f"Unknown GCS parameter(s): {', '.join(unknown)}.")
    if not names or len(set(names)) != len(names):
        raise ValueError("Choose at least one distinct parameter to refine.")
    if total < len(names) + 1:
        raise ValueError(
            f"A {len(names)}-parameter GCS refine needs at least {len(names) + 1} "
            f"clicked points, got {total}."
        )

    separation = 0.0
    if len(viewpoints) >= 2:
        separation = max(
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(viewpoints)
            for b in viewpoints[index + 1 :]
        )

    resolution = dict(mesh_resolution or {})
    seed_values = {name: getattr(seed, name) for name in PARAMETER_NAMES}

    lower: list[float] = []
    upper: list[float] = []
    scale: list[float] = []
    for name in names:
        if name in _RELATIVE_BOUNDS:
            span = _RELATIVE_BOUNDS[name]
            lower.append(seed_values[name] - span)
            upper.append(seed_values[name] + span)
        else:
            low, high = _ABSOLUTE_BOUNDS[name]
            lower.append(low)
            upper.append(high)
            if not low <= seed_values[name] <= high:
                raise ValueError(
                    f"Starting {name} is outside the refinement range [{low:g}, {high:g}]. "
                    "Adjust the manual model before refining."
                )
        scale.append(_X_SCALE[name])
    start = np.asarray([seed_values[name] for name in names], dtype=float)

    # Use one scale for numerical conditioning only. All clicks and the robust
    # tolerance are specified in arcsec; dividing each observer by a different
    # solar radius changes their relative weights and corrupts reported units.
    reference_rsun = float(np.mean([view.observer.rsun_arcsec for view in viewpoints]))

    seed_mesh = gcs_mesh(seed, **resolution)
    for view in viewpoints:
        seed_projection = project_to_arcsec(seed_mesh, seed, view.observer, fov_rsun=view.fov_rsun)
        if not np.any(np.isfinite(seed_projection.tx_arcsec) & np.isfinite(seed_projection.ty_arcsec)):
            label = view.label or view.observer.label or "a fitted viewpoint"
            raise ValueError(
                f"The starting model is outside the visible field in {label}. "
                "Move the wireframe onto the observed CME before refining."
            )

    def build(values: Sequence[float]) -> GCSParameters:
        merged = dict(seed_values)
        merged.update({name: float(value) for name, value in zip(names, values)})
        return GCSParameters.from_array([merged[key] for key in PARAMETER_NAMES])

    def residuals(values: Sequence[float]) -> np.ndarray:
        from scipy.spatial import cKDTree

        params = build(values)
        if not params.is_physical:
            return np.full(total, 1.0e4)
        mesh = gcs_mesh(params, **resolution)
        out: list[np.ndarray] = []
        for view, points in zip(viewpoints, clicks):
            if not len(points):
                continue
            projection = project_to_arcsec(mesh, params, view.observer, fov_rsun=view.fov_rsun)
            drawn = np.isfinite(projection.tx_arcsec) & np.isfinite(projection.ty_arcsec)
            if not drawn.any():
                out.append(np.full(len(points), 1.0e4))
                continue
            tree = cKDTree(
                np.column_stack((projection.tx_arcsec[drawn], projection.ty_arcsec[drawn]))
            )
            out.append(tree.query(points)[0] / reference_rsun)
        return np.concatenate(out) if out else np.full(total, 1.0e4)

    seed_residual = residuals([seed_values[name] for name in names]) * reference_rsun
    started = time.perf_counter()
    result = least_squares(
        residuals,
        start,
        bounds=(lower, upper),
        method="trf",
        x_scale=scale,
        # Must stay small: at 3e-2 the fitted alpha degrades from -2 to +18 deg.
        diff_step=1e-3,
        loss="soft_l1" if robust else "linear",
        f_scale=float(click_tolerance_arcsec) / reference_rsun,
        max_nfev=int(max_nfev),
    )
    elapsed = time.perf_counter() - started

    fitted = build(result.x)
    residual_arcsec = np.asarray(result.fun, dtype=float) * reference_rsun
    rms = float(np.sqrt(np.mean(residual_arcsec**2))) if residual_arcsec.size else math.nan
    worst = int(np.argmax(np.abs(residual_arcsec))) if residual_arcsec.size else -1

    # With a robust loss, scipy returns raw residuals in ``fun`` but a Jacobian
    # that is ALREADY reweighted (scipy/optimize/_lsq/trf.py: f_true is copied
    # before scale_for_robust_loss_function rescales J, and both are returned).
    # So J.T @ J is the correct robust Gauss-Newton Hessian and must not be
    # reweighted again here.
    jacobian = np.asarray(result.jac, dtype=float)
    n_obs, n_free = jacobian.shape
    sigma = {name: math.nan for name in PARAMETER_NAMES}
    covariance: np.ndarray | None = None
    condition = math.inf
    if n_obs > n_free:
        variance = 2.0 * float(result.cost) / float(n_obs - n_free)
        # Scale the Jacobian columns before judging conditioning; otherwise a
        # degree, a solar radius and a dimensionless kappa create an arbitrary
        # condition number merely from their unit choices.
        scales = np.asarray(scale, dtype=float)
        scaled_jacobian = jacobian * scales
        normal = scaled_jacobian.T @ scaled_jacobian
        try:
            condition = float(np.linalg.cond(normal))
        except Exception:
            condition = math.inf
        # A pseudo-inverse assigns zero variance to unconstrained null-space
        # directions. Those are *unknown*, not precise. Suppress uncertainties
        # for rank deficiency, an unstable Hessian, an unfinished solve or an
        # active parameter bound, where symmetric local error bars mislead.
        if (
            result.success
            and np.linalg.matrix_rank(scaled_jacobian) == n_free
            and math.isfinite(condition)
            and condition <= 1.0e8
            and not np.any(result.active_mask)
        ):
            covariance = variance * np.linalg.inv(normal) * np.outer(scales, scales)
            for index, name in enumerate(names):
                value = covariance[index, index]
                sigma[name] = float(math.sqrt(value)) if value >= 0.0 else math.nan

    notes = [f"rms {rms:.0f}\" over {total} point(s)"]
    if not has_independent_viewpoints(viewpoints):
        direction_status = (
            "direction held fixed" if not {"lon_deg", "lat_deg"}.intersection(names)
            else "direction was released explicitly and may be degenerate"
        )
        notes.append(f"one effective viewpoint — {direction_status}; height and width are degenerate with it")
    if condition > 1.0e8:
        notes.append("poorly constrained — add clicks on the opposite flank")
    if covariance is None:
        notes.append("formal uncertainties unavailable for this solution")
    else:
        notes.append("errors are local formal estimates; exclude model and feature-identification uncertainty")
    if np.any(result.active_mask):
        notes.append("parameter bound reached — review the manual model")
    weak_free = [name for name in WEAKLY_CONSTRAINED if name in names]
    if weak_free:
        notes.append(
            f"{' and '.join(n.removesuffix('_deg') for n in weak_free)} weakly constrained"
        )
    if not result.success:
        notes.insert(0, "did not converge")
    message = "GCS refine: " + "; ".join(notes)

    return GCSRefinement(
        parameters=fitted,
        seed=seed,
        sigma=sigma,
        covariance=covariance,
        residuals_arcsec=residual_arcsec,
        rms_arcsec=rms,
        seed_rms_arcsec=float(np.sqrt(np.mean(seed_residual**2))),
        max_residual_arcsec=float(np.max(np.abs(residual_arcsec))) if residual_arcsec.size else math.nan,
        worst_index=worst,
        n_points=total,
        n_viewpoints=len(viewpoints),
        separation_deg=float(separation),
        free=tuple(names),
        weakly_constrained=tuple(n for n in WEAKLY_CONSTRAINED if n in names),
        condition_number=condition,
        converged=bool(result.success),
        n_evaluations=int(result.nfev),
        elapsed_s=float(elapsed),
        message=message,
    )
