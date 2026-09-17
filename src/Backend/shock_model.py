"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Spheroid and ellipsoid models for 3-D reconstruction of CME-driven shocks.

A CME's flux rope is fitted with GCS (``src.Backend.gcs_model``); the shock it
drives is not a croissant. In white light the shock forms the faint, smooth
outer envelope ahead of and around the ejecta, and a closed quadric surface —
an ellipsoid, or its rotationally symmetric special case the spheroid — is the
established way to reconstruct it from several viewpoints at once.

Parameters (PyThea's self-similar convention)
---------------------------------------------
The parameters follow PyThea (Kouloumvakos et al. 2022) exactly, so a fit made
here can be compared number for number with one made there. Its own
documentation page states two of the definitions loosely; these are the
relations its model classes actually implement:

* ``lon``, ``lat`` — Stonyhurst direction of the centre of symmetry, which is also
  the direction of the first ("radial") semi-axis and of the apex.
* ``height`` — heliocentric distance of the apex, ``h = r_centre + a``.
* ``kappa`` — self-similar constant, ``kappa = b / (h - 1 R_sun)``. Holding it
  (with ``epsilon`` and ``alpha``) while ``h`` changes is self-similar expansion.
* ``epsilon`` — signed eccentricity of the radial/lateral cross-section:
  ``+sqrt(1 - (b/a)^2)`` for ``a > b`` (elongated radially, prolate),
  ``-sqrt(1 - (a/b)^2)`` for ``a < b`` (flattened, oblate), ``0`` for ``a = b``.
* ``alpha`` — ellipsoid only: ``b / c``, the ratio of the two lateral semi-axes.
* ``tilt`` — ellipsoid only: rotation about the radial axis. At zero the ``b``
  axis is parallel to the solar equator and ``c`` lies in the meridional plane.

``a`` is the radial semi-axis and ``b``, ``c`` the lateral ones. A spheroid has
``c = b``, so its ``alpha`` is 1 and its tilt has no effect and is held at 0.

Model frame: ``x`` along the radial axis with the centre at ``(r_centre, 0, 0)``,
``y`` along ``b`` and ``z`` along ``c``; HGS Cartesian is reached by
``R_z(lon) R_y(-lat) R_x(tilt)``, PyThea's extrinsic ``xyz`` Euler rotation.

The observed front is the outline
---------------------------------
A thin shell is brightest where the line of sight grazes it, so a shock front
in an image is the model's projected *outline*, not arbitrary points on its
surface. That outline is computed exactly: for an observer outside the
ellipsoid, the surface points whose tangent planes contain the observer lie on
one plane (the observer's polar plane), and its intersection with the surface is
an ellipse, found in closed form by mapping the ellipsoid onto the unit sphere.
Refinement measures clicked points against that curve.

Projection, occultation and the apex-drag solver are shared with the GCS model,
which validates the fast projection against sunpy.

References
----------
* Kouloumvakos et al. (2019), ApJ 876, 80 — doi:10.3847/1538-4357/ab15d7
* Kwon, Zhang & Olmedo (2014), ApJ 794, 148 — doi:10.1088/0004-637X/794/2/148
* Kouloumvakos et al. (2022), Front. Astron. Space Sci. 9, 974137 —
  doi:10.3389/fspas.2022.974137 (PyThea, GPL-3.0: the reference implementation
  the conventions are matched against; no PyThea code is reproduced here)

Pure numpy and scipy, no Qt: the geometry, refinement and the UI's gating all
share one description of the model.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

from src.Backend.gcs_model import (
    GCSParameters,
    GCSViewpoint,
    ObserverGeometry,
    apply_apex_drag,
    has_independent_viewpoints,
    join_polylines,
    observer_separation_deg,
    project_points_to_arcsec,
)

__all__ = [
    "ELLIPSOID",
    "SHOCK_MODELS",
    "SHOCK_MODEL_LABELS",
    "SHOCK_PARAMETER_NAMES",
    "SHOCK_WEAKLY_CONSTRAINED",
    "SPHEROID",
    "ShockAxes",
    "ShockParameters",
    "ShockRefinement",
    "apply_shock_apex_drag",
    "interpolate_shock_parameters",
    "refine_shock",
    "shock_apex_arcsec",
    "shock_free_parameters",
    "shock_handle_positions_arcsec",
    "shock_mesh_hgs",
    "shock_orientation_matrix",
    "shock_parameters_from_axes",
    "shock_semi_axes",
    "shock_silhouette_arcsec",
    "shock_silhouette_hgs",
    "shock_wireframe_arcsec",
]


# --- Parameters ------------------------------------------------------------

SPHEROID = "spheroid"
ELLIPSOID = "ellipsoid"
SHOCK_MODELS: tuple[str, ...] = (SPHEROID, ELLIPSOID)
SHOCK_MODEL_LABELS = {SPHEROID: "Spheroid", ELLIPSOID: "Ellipsoid"}

#: Ordered parameter names, used for bounds, Jacobians and error reporting.
SHOCK_PARAMETER_NAMES: tuple[str, ...] = (
    "lon_deg",
    "lat_deg",
    "tilt_deg",
    "height_rsun",
    "kappa",
    "epsilon",
    "alpha",
)

#: Parameters a clicked outline constrains poorly. Measured on synthetic fronts
#: seen through COR2, C2 and C3 fields of view with 15" click noise: with three
#: well-separated views every parameter was recovered, but with one or two the
#: fitted ``epsilon`` and ``tilt`` kept a third to three quarters of the starting
#: error while their formal sigma understated it several-fold — the occulter
#: hides the back of the shell, and tilt shows only as a small change of outline.
SHOCK_WEAKLY_CONSTRAINED: tuple[str, ...] = ("epsilon", "tilt_deg")


@dataclass(frozen=True)
class ShockParameters:
    """One spheroid or ellipsoid shock, in PyThea's self-similar parameters."""

    #: Stonyhurst longitude of the centre, and of the radial axis and apex.
    lon_deg: float
    #: Stonyhurst latitude of the centre, and of the radial axis and apex.
    lat_deg: float
    #: Heliocentric distance of the apex, ``r_centre + a``, in R_sun.
    height_rsun: float
    #: Self-similar constant ``b / (height - 1)``.
    kappa: float
    #: Signed eccentricity: positive radially elongated, negative flattened.
    epsilon: float
    #: Ellipsoid only: rotation about the radial axis, degrees.
    tilt_deg: float = 0.0
    #: Ellipsoid only: lateral aspect ratio ``b / c``.
    alpha: float = 1.0
    #: :data:`SPHEROID` or :data:`ELLIPSOID`.
    model: str = SPHEROID

    def replace_values(self, **changes: float) -> "ShockParameters":
        """A copy with named parameters overridden (frozen-dataclass friendly)."""
        return replace(self, **changes)

    def as_model(self, model: str) -> "ShockParameters":
        """These parameters for ``model``; a spheroid gets ``tilt = 0``, ``alpha = 1``.

        Going from an ellipsoid to a spheroid keeps ``b`` and discards ``c``,
        which is the only way to keep direction, apex and the ``kappa`` and
        ``epsilon`` the user set.
        """
        if model not in SHOCK_MODELS:
            raise ValueError(f"Unknown shock model: {model}")
        if model == SPHEROID:
            return replace(self, model=SPHEROID, tilt_deg=0.0, alpha=1.0)
        return replace(self, model=ELLIPSOID)

    @property
    def is_physical(self) -> bool:
        """False for parameter sets the model cannot evaluate at all."""
        values = (self.lon_deg, self.lat_deg, self.tilt_deg, self.height_rsun, self.kappa, self.epsilon, self.alpha)
        return (
            self.model in SHOCK_MODELS
            and all(math.isfinite(float(value)) for value in values)
            and self.height_rsun > 1.0
            and self.kappa > 0.0
            and -1.0 < self.epsilon < 1.0
            and self.alpha > 0.0
            and -90.0 <= self.lat_deg <= 90.0
        )


@dataclass(frozen=True)
class ShockAxes:
    """The ellipsoid's size and placement, in R_sun (PyThea's ``rcenter``, ``radaxis``, ...)."""

    #: Heliocentric distance of the centre of symmetry; negative beyond Sun centre.
    rcenter_rsun: float
    #: Radial semi-axis ``a``.
    radaxis_rsun: float
    #: First lateral semi-axis ``b``.
    orthoaxis1_rsun: float
    #: Second lateral semi-axis ``c``; equal to ``b`` for a spheroid.
    orthoaxis2_rsun: float


def shock_semi_axes(params: ShockParameters) -> ShockAxes:
    """Centre distance and semi-axes from the self-similar parameters."""
    b = float(params.kappa) * (float(params.height_rsun) - 1.0)
    epsilon = float(params.epsilon)
    if epsilon > 0.0:
        a = b / math.sqrt(1.0 - epsilon * epsilon)
    elif epsilon < 0.0:
        a = b * math.sqrt(1.0 - epsilon * epsilon)
    else:
        a = b
    c = b / float(params.alpha) if params.model == ELLIPSOID else b
    return ShockAxes(
        rcenter_rsun=float(params.height_rsun) - a,
        radaxis_rsun=a,
        orthoaxis1_rsun=b,
        orthoaxis2_rsun=c,
    )


def shock_parameters_from_axes(
    lon_deg: float,
    lat_deg: float,
    rcenter_rsun: float,
    radaxis_rsun: float,
    orthoaxis1_rsun: float,
    orthoaxis2_rsun: float | None = None,
    *,
    tilt_deg: float = 0.0,
    model: str = SPHEROID,
) -> ShockParameters:
    """The self-similar parameters of a shock given by its centre and semi-axes.

    The inverse of :func:`shock_semi_axes`. ``orthoaxis2_rsun`` defaults to ``b``
    and is ignored for a spheroid.
    """
    if model not in SHOCK_MODELS:
        raise ValueError(f"Unknown shock model: {model}")
    a = float(radaxis_rsun)
    b = float(orthoaxis1_rsun)
    c = b if orthoaxis2_rsun is None or model == SPHEROID else float(orthoaxis2_rsun)
    if not (a > 0.0 and b > 0.0 and c > 0.0):
        raise ValueError("Every semi-axis must be positive.")
    height = float(rcenter_rsun) + a
    if height <= 1.0:
        raise ValueError("The apex must lie above the photosphere.")
    if a > b:
        epsilon = math.sqrt(1.0 - (b / a) ** 2)
    elif a < b:
        epsilon = -math.sqrt(1.0 - (a / b) ** 2)
    else:
        epsilon = 0.0
    return ShockParameters(
        lon_deg=float(lon_deg),
        lat_deg=float(lat_deg),
        height_rsun=height,
        kappa=b / (height - 1.0),
        epsilon=epsilon,
        tilt_deg=float(tilt_deg) if model == ELLIPSOID else 0.0,
        alpha=b / c if model == ELLIPSOID else 1.0,
        model=model,
    )


# --- Geometry ----------------------------------------------------------------


def shock_orientation_matrix(params: ShockParameters) -> np.ndarray:
    """``(3, 3)`` model frame -> HGS Cartesian: ``R_z(lon) R_y(-lat) R_x(tilt)``.

    Tilt is applied first, about the radial axis, so it never moves the apex.
    """
    lon = math.radians(params.lon_deg)
    lat = math.radians(params.lat_deg)
    tilt = math.radians(params.tilt_deg) if params.model == ELLIPSOID else 0.0
    cos_lon, sin_lon = math.cos(lon), math.sin(lon)
    cos_lat, sin_lat = math.cos(lat), math.sin(lat)
    cos_tilt, sin_tilt = math.cos(tilt), math.sin(tilt)
    rz = np.array([[cos_lon, -sin_lon, 0.0], [sin_lon, cos_lon, 0.0], [0.0, 0.0, 1.0]])
    # R_y(-lat): cos(-lat) = cos(lat), sin(-lat) = -sin(lat).
    ry = np.array([[cos_lat, 0.0, -sin_lat], [0.0, 1.0, 0.0], [sin_lat, 0.0, cos_lat]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cos_tilt, -sin_tilt], [0.0, sin_tilt, cos_tilt]])
    return rz @ ry @ rx


def _to_hgs(params: ShockParameters, axes: ShockAxes, centred: np.ndarray) -> np.ndarray:
    """Centred model-frame points ``(3, N)`` -> HGS Cartesian ``(3, N)``."""
    shifted = centred + np.array([[axes.rcenter_rsun], [0.0], [0.0]])
    return shock_orientation_matrix(params) @ shifted


def shock_mesh_hgs(
    params: ShockParameters, *, n_polar: int = 37, n_azimuth: int = 97
) -> np.ndarray:
    """Surface grid ``(3, n_polar, n_azimuth)`` in HGS Cartesian R_sun.

    Polar angle is measured from the apex, so row 0 is the apex and the last row
    the base; each row is a closed ring around the radial axis (its last column
    repeats the first) and each column runs from apex to base.
    """
    axes = shock_semi_axes(params)
    polar = np.linspace(0.0, math.pi, max(3, int(n_polar)))
    azimuth = np.linspace(0.0, 2.0 * math.pi, max(4, int(n_azimuth)))
    sin_polar = np.sin(polar)[:, None]
    centred = np.stack(
        [
            np.broadcast_to(axes.radaxis_rsun * np.cos(polar)[:, None], (polar.size, azimuth.size)),
            axes.orthoaxis1_rsun * sin_polar * np.cos(azimuth)[None, :],
            axes.orthoaxis2_rsun * sin_polar * np.sin(azimuth)[None, :],
        ]
    ).reshape(3, -1)
    return _to_hgs(params, axes, centred).reshape(3, polar.size, azimuth.size)


def _observer_hgs(observer: ObserverGeometry) -> np.ndarray:
    """The observer's HGS Cartesian position, R_sun: the third row of its projection."""
    return observer.dsun_rsun * observer.projection_matrix[2]


def shock_silhouette_hgs(
    params: ShockParameters, observer: ObserverGeometry, *, samples: int = 360
) -> np.ndarray | None:
    """The outline of the shock as seen by ``observer``: ``(3, samples + 1)`` HGS points.

    The curve is closed (the last point repeats the first). ``None`` when the
    observer is inside the surface, where there is no outline.

    Scaling the model frame by ``1/a, 1/b, 1/c`` turns the ellipsoid into the unit
    sphere and keeps tangency, and from a point ``w`` outside the unit sphere the
    tangent points form the circle ``u . w = 1`` — centre ``w/|w|^2``, radius
    ``sqrt(1 - 1/|w|^2)``. Scaling back gives the outline exactly.
    """
    axes = shock_semi_axes(params)
    scale = np.array([axes.radaxis_rsun, axes.orthoaxis1_rsun, axes.orthoaxis2_rsun])
    rotation = shock_orientation_matrix(params)
    observer_centred = rotation.T @ _observer_hgs(observer) - np.array([axes.rcenter_rsun, 0.0, 0.0])
    w = observer_centred / scale
    norm_sq = float(w @ w)
    if not norm_sq > 1.0:
        return None
    unit = w / math.sqrt(norm_sq)
    helper = np.array([1.0, 0.0, 0.0]) if abs(unit[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    first = np.cross(unit, helper)
    first /= np.linalg.norm(first)
    second = np.cross(unit, first)
    angle = np.linspace(0.0, 2.0 * math.pi, max(8, int(samples)) + 1)
    radius = math.sqrt(1.0 - 1.0 / norm_sq)
    sphere = (w / norm_sq)[:, None] + radius * (
        first[:, None] * np.cos(angle)[None, :] + second[:, None] * np.sin(angle)[None, :]
    )
    return _to_hgs(params, axes, sphere * scale[:, None])


def _hidden_outside_fov(
    tx: np.ndarray, ty: np.ndarray, occulted: np.ndarray, observer: ObserverGeometry,
    fov_rsun: tuple[float, float] | None,
) -> tuple[np.ndarray, np.ndarray]:
    hidden = occulted | ~np.isfinite(tx) | ~np.isfinite(ty)
    if fov_rsun is not None:
        inner, outer = float(fov_rsun[0]), float(fov_rsun[1])
        plane_radius = np.hypot(tx, ty) / observer.rsun_arcsec
        hidden = hidden | (plane_radius < inner) | (plane_radius > outer)
    return np.where(hidden, np.nan, tx), np.where(hidden, np.nan, ty)


def shock_silhouette_arcsec(
    params: ShockParameters,
    observer: ObserverGeometry,
    *,
    samples: int = 360,
    fov_rsun: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """The projected outline, arcsec, NaN where hidden. Empty when there is none.

    Hidden means behind the solar disk, inside the photosphere, or — with
    ``fov_rsun`` — outside the detector's field, as for the GCS wireframe.
    """
    outline = shock_silhouette_hgs(params, observer, samples=samples)
    if outline is None:
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    tx, ty, occulted = project_points_to_arcsec(outline, observer)
    return _hidden_outside_fov(tx, ty, occulted, observer, fov_rsun)


def shock_wireframe_arcsec(
    params: ShockParameters,
    observer: ObserverGeometry,
    *,
    ring_stride: int = 3,
    n_meridians: int = 8,
    fov_rsun: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """What the canvas draws: rings about the radial axis, meridians and the outline.

    Rings every ``ring_stride`` x 5 degrees of polar angle from the apex, and
    ``n_meridians`` lines from apex to base, give the surface its shape; the
    outline is added because it is what a shock front in an image corresponds to.
    One NaN-separated pair of arrays, like :func:`gcs_model.wireframe_arcsec`.
    """
    mesh = shock_mesh_hgs(params)
    _, n_polar, n_azimuth = mesh.shape
    tx, ty, occulted = project_points_to_arcsec(mesh.reshape(3, -1), observer)
    tx, ty = _hidden_outside_fov(tx, ty, occulted, observer, fov_rsun)
    tx = tx.reshape(n_polar, n_azimuth)
    ty = ty.reshape(n_polar, n_azimuth)

    polylines: list[tuple[np.ndarray, np.ndarray]] = []
    stride = max(1, int(ring_stride))
    # Rows 0 and n_polar - 1 are single points (apex and base), not rings.
    for index in range(stride, n_polar - 1, stride):
        polylines.append((tx[index], ty[index]))
    count = max(0, int(n_meridians))
    if count:
        columns = np.linspace(0, n_azimuth - 1, count, endpoint=False).astype(int)
        for index in columns:
            polylines.append((tx[:, index], ty[:, index]))
    outline_x, outline_y = shock_silhouette_arcsec(params, observer, fov_rsun=fov_rsun)
    if outline_x.size:
        polylines.append((outline_x, outline_y))
    return join_polylines(polylines)


def shock_apex_arcsec(params: ShockParameters, observer: ObserverGeometry) -> tuple[float, float]:
    """Where the apex projects, arcsec (NaN-free; the caller checks occultation)."""
    apex = shock_orientation_matrix(params) @ np.array([[params.height_rsun], [0.0], [0.0]])
    tx, ty, _ = project_points_to_arcsec(apex, observer)
    return float(tx[0]), float(ty[0])


def shock_handle_positions_arcsec(
    params: ShockParameters, observer: ObserverGeometry
) -> dict[str, tuple[float, float]]:
    """The apex handle, NaN when it is hidden (a hidden handle must not be dragged)."""
    apex = shock_orientation_matrix(params) @ np.array([[params.height_rsun], [0.0], [0.0]])
    tx, ty, occulted = project_points_to_arcsec(apex, observer)
    if occulted[0] or not (math.isfinite(tx[0]) and math.isfinite(ty[0])):
        return {"apex": (math.nan, math.nan)}
    return {"apex": (float(tx[0]), float(ty[0]))}


def apply_shock_apex_drag(
    params: ShockParameters,
    observer: ObserverGeometry,
    target_arcsec: tuple[float, float],
    *,
    lock: str = "",
) -> ShockParameters:
    """Parameters that move the apex handle to ``target_arcsec``.

    Both models put the apex at ``height`` along ``(lon, lat)``, so the GCS drag
    solver — position angle steers the direction, sky radius the height — applies
    unchanged; it is run on a GCS shell with the same apex. ``kappa``,
    ``epsilon`` and ``alpha`` are held, so a radial drag is self-similar expansion.
    """
    proxy = GCSParameters(
        lon_deg=params.lon_deg,
        lat_deg=params.lat_deg,
        tilt_deg=0.0,
        height_rsun=params.height_rsun,
        alpha_deg=30.0,
        kappa=0.3,
    )
    moved = apply_apex_drag(proxy, observer, target_arcsec, lock=lock)
    return params.replace_values(lon_deg=moved.lon_deg, lat_deg=moved.lat_deg, height_rsun=moved.height_rsun)


def interpolate_shock_parameters(
    first: ShockParameters, second: ShockParameters, fraction: float
) -> ShockParameters:
    """The shock ``fraction`` of the way from ``first`` to ``second``, linearly.

    A display of how recorded fits evolve, never a fit. Longitude takes the short
    way round the Sun and tilt the short way round the ellipsoid's 180-degree
    symmetry about its radial axis. A spheroid is the ellipsoid with ``alpha = 1``,
    whose tilt means nothing, so between a spheroid and an ellipsoid the shape is
    interpolated as an ellipsoid holding the ellipsoid's tilt. Ends are exact.
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

    model = ELLIPSOID if ELLIPSOID in (first.model, second.model) else SPHEROID
    first_tilt = second.tilt_deg if first.model == SPHEROID else first.tilt_deg
    second_tilt = first.tilt_deg if second.model == SPHEROID else second.tilt_deg
    return ShockParameters(
        lon_deg=periodic(first.lon_deg, second.lon_deg, 360.0),
        lat_deg=linear(first.lat_deg, second.lat_deg),
        height_rsun=linear(first.height_rsun, second.height_rsun),
        kappa=linear(first.kappa, second.kappa),
        epsilon=linear(first.epsilon, second.epsilon),
        tilt_deg=periodic(first_tilt, second_tilt, 180.0) if model == ELLIPSOID else 0.0,
        alpha=linear(first.alpha, second.alpha) if model == ELLIPSOID else 1.0,
        model=model,
    )


# --- Refinement against clicked shock-front points -----------------------------


@dataclass(frozen=True)
class ShockRefinement:
    """What :func:`refine_shock` hands back; the fields mean what they do for GCS."""

    parameters: ShockParameters
    seed: ShockParameters
    #: 1-sigma per parameter name, **formal** only; NaN when not estimable.
    sigma: dict[str, float]
    covariance: np.ndarray | None
    #: Distances from each finite click to the visible outline, arcsec.
    residuals_arcsec: np.ndarray
    rms_arcsec: float
    seed_rms_arcsec: float
    max_residual_arcsec: float
    worst_index: int
    n_points: int
    n_viewpoints: int
    separation_deg: float
    free: tuple[str, ...]
    weakly_constrained: tuple[str, ...]
    condition_number: float
    converged: bool
    n_evaluations: int
    elapsed_s: float
    #: Safe to show verbatim in the status bar.
    message: str


#: Absolute bounds, matching the sliders so a model the user can set can be refined.
_ABSOLUTE_BOUNDS: dict[str, tuple[float, float]] = {
    "lat_deg": (-89.0, 89.0),
    "height_rsun": (1.05, 30.0),
    "kappa": (0.05, 2.0),
    "epsilon": (-0.99, 0.99),
    "alpha": (0.5, 1.5),
}
#: Angles are bounded relative to the seed, so there is no seam in the search.
_RELATIVE_BOUNDS: dict[str, float] = {"lon_deg": 90.0, "tilt_deg": 90.0}
_X_SCALE: dict[str, float] = {
    "lon_deg": 10.0,
    "lat_deg": 10.0,
    "tilt_deg": 10.0,
    "height_rsun": 1.0,
    "kappa": 0.1,
    "epsilon": 0.1,
    "alpha": 0.1,
}


def shock_free_parameters(viewpoints: Sequence[GCSViewpoint], model: str) -> tuple[str, ...]:
    """Which parameters the viewpoints can constrain, as for GCS.

    Without two usefully separated views carrying points, the direction cannot be
    told apart from size and shape, so ``lon``/``lat`` are held. A spheroid has no
    ``tilt`` or ``alpha`` to fit.
    """
    names = [name for name in SHOCK_PARAMETER_NAMES if model == ELLIPSOID or name not in ("tilt_deg", "alpha")]
    if not has_independent_viewpoints(viewpoints):
        names = [name for name in names if name not in ("lon_deg", "lat_deg")]
    return tuple(names)


def _finite_clicks(viewpoint: GCSViewpoint) -> np.ndarray:
    clicks = np.asarray(viewpoint.clicks_arcsec, dtype=float).reshape(-1, 2)
    return clicks[np.isfinite(clicks).all(axis=1)]


def _distance_to_curve(points: np.ndarray, tx: np.ndarray, ty: np.ndarray) -> np.ndarray | None:
    """Distance from each point to the nearest drawn segment of a NaN-broken curve."""
    starts = np.column_stack((tx[:-1], ty[:-1]))
    ends = np.column_stack((tx[1:], ty[1:]))
    drawn = np.isfinite(starts).all(axis=1) & np.isfinite(ends).all(axis=1)
    if not drawn.any():
        return None
    starts, ends = starts[drawn], ends[drawn]
    segment = ends - starts
    length_sq = np.einsum("ij,ij->i", segment, segment)
    offset = points[:, None, :] - starts[None, :, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        along = np.einsum("kmj,mj->km", offset, segment) / length_sq[None, :]
    along = np.clip(np.nan_to_num(along, nan=0.0), 0.0, 1.0)
    nearest = starts[None, :, :] + along[:, :, None] * segment[None, :, :]
    distance = np.linalg.norm(points[:, None, :] - nearest, axis=2)
    return distance.min(axis=1)


def refine_shock(
    viewpoints: Sequence[GCSViewpoint],
    seed: ShockParameters,
    *,
    free: Sequence[str] | None = None,
    click_tolerance_arcsec: float = 30.0,
    robust: bool = True,
    max_nfev: int = 200,
    outline_samples: int = 720,
) -> ShockRefinement:
    """Least-squares polish of a manual shock fit against clicked front points.

    Each click's residual is its distance to the model's projected outline in its
    own view, over the part a detector can show. Like GCS this is a
    **refinement, not a fit**: start from a shell aligned by hand. Raises
    ``ValueError`` with user-facing text when the inputs cannot support a fit.
    """
    from scipy.optimize import least_squares

    if not viewpoints:
        raise ValueError("A shock refine needs at least one viewpoint.")
    clicks = [_finite_clicks(view) for view in viewpoints]
    total = int(sum(len(points) for points in clicks))
    if total == 0:
        raise ValueError("Click along the shock front before refining the shock fit.")
    if not seed.is_physical:
        raise ValueError("The starting shock parameters are outside the model's valid range.")

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

    allowed = shock_free_parameters(viewpoints, seed.model) if free is None else tuple(free)
    names = tuple(allowed)
    unknown = [name for name in names if name not in SHOCK_PARAMETER_NAMES]
    if unknown:
        raise ValueError(f"Unknown shock parameter(s): {', '.join(unknown)}.")
    if seed.model == SPHEROID and {"tilt_deg", "alpha"}.intersection(names):
        raise ValueError("A spheroid has no tilt or lateral aspect ratio to refine.")
    if not names or len(set(names)) != len(names):
        raise ValueError("Choose at least one distinct parameter to refine.")
    if total < len(names) + 1:
        raise ValueError(
            f"A {len(names)}-parameter shock refine needs at least {len(names) + 1} "
            f"clicked points, got {total}."
        )

    separation = 0.0
    if len(viewpoints) >= 2:
        separation = max(
            observer_separation_deg(a.observer, b.observer)
            for index, a in enumerate(viewpoints)
            for b in viewpoints[index + 1 :]
        )

    seed_values = {name: float(getattr(seed, name)) for name in SHOCK_PARAMETER_NAMES}
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
            if not low <= seed_values[name] <= high:
                raise ValueError(
                    f"Starting {name} is outside the refinement range [{low:g}, {high:g}]. "
                    "Adjust the manual model before refining."
                )
            lower.append(low)
            upper.append(high)
        scale.append(_X_SCALE[name])
    start = np.asarray([seed_values[name] for name in names], dtype=float)
    # One arcsec scale for conditioning only; see refine_gcs for why it is shared.
    reference_rsun = float(np.mean([view.observer.rsun_arcsec for view in viewpoints]))

    for view in viewpoints:
        outline_x, _ = shock_silhouette_arcsec(seed, view.observer, samples=outline_samples, fov_rsun=view.fov_rsun)
        if not np.any(np.isfinite(outline_x)):
            label = view.label or view.observer.label or "a fitted viewpoint"
            raise ValueError(
                f"The starting shock outline is not visible in {label}. "
                "Move the shock model onto the observed front before refining."
            )

    def build(values: Sequence[float]) -> ShockParameters:
        merged = dict(seed_values)
        merged.update({name: float(value) for name, value in zip(names, values)})
        return seed.replace_values(**merged)

    def residuals(values: Sequence[float]) -> np.ndarray:
        params = build(values)
        if not params.is_physical:
            return np.full(total, 1.0e4)
        out: list[np.ndarray] = []
        for view, points in zip(viewpoints, clicks):
            outline_x, outline_y = shock_silhouette_arcsec(
                params, view.observer, samples=outline_samples, fov_rsun=view.fov_rsun
            )
            distance = _distance_to_curve(points, outline_x, outline_y) if outline_x.size else None
            if distance is None:
                out.append(np.full(len(points), 1.0e4))
            else:
                out.append(distance / reference_rsun)
        return np.concatenate(out)

    seed_residual = residuals(start) * reference_rsun
    started = time.perf_counter()
    result = least_squares(
        residuals,
        start,
        bounds=(lower, upper),
        method="trf",
        x_scale=scale,
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

    # scipy's robust-loss Jacobian is already reweighted; see refine_gcs.
    jacobian = np.asarray(result.jac, dtype=float)
    n_obs, n_free = jacobian.shape
    sigma = {name: math.nan for name in SHOCK_PARAMETER_NAMES}
    covariance: np.ndarray | None = None
    condition = math.inf
    if n_obs > n_free:
        variance = 2.0 * float(result.cost) / float(n_obs - n_free)
        scales = np.asarray(scale, dtype=float)
        scaled_jacobian = jacobian * scales
        normal = scaled_jacobian.T @ scaled_jacobian
        try:
            condition = float(np.linalg.cond(normal))
        except Exception:
            condition = math.inf
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
        notes.append(f"one effective viewpoint — {direction_status}; size and shape are degenerate with it")
    if condition > 1.0e8:
        notes.append("poorly constrained — add points on the opposite flank and in another view")
    if covariance is None:
        notes.append("formal uncertainties unavailable for this solution")
    else:
        notes.append("errors are local formal estimates; exclude model and feature-identification uncertainty")
    if np.any(result.active_mask):
        notes.append("parameter bound reached — review the manual model")
    weak_free = [name for name in SHOCK_WEAKLY_CONSTRAINED if name in names]
    if weak_free:
        notes.append(f"{' and '.join(name.removesuffix('_deg') for name in weak_free)} weakly constrained")
    if seed.model == ELLIPSOID and len(viewpoints) < 3:
        # Measured: with two views the fitted kappa was off by ~14x its formal
        # sigma, because the lateral axes, epsilon and tilt trade off.
        notes.append("ellipsoid shape is coupled in fewer than three views — prefer a spheroid or add a view")
    if not result.success:
        notes.insert(0, "did not converge")

    return ShockRefinement(
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
        free=names,
        weakly_constrained=tuple(name for name in SHOCK_WEAKLY_CONSTRAINED if name in names),
        condition_number=condition,
        converged=bool(result.success),
        n_evaluations=int(result.nfev),
        elapsed_s=float(elapsed),
        message="Shock refine: " + "; ".join(notes),
    )
