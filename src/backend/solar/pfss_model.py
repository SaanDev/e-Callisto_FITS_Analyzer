"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Potential Field Source Surface (PFSS) coronal magnetic field extrapolation.

An EUV image shows where the plasma is, not where the field is. The loops seen in
193 A trace field lines, but the image alone cannot say which of them close back
to the surface and which open into the solar wind. A PFSS model supplies that:
given the radial photospheric field as a boundary condition, it solves for the
current-free (potential) field between the photosphere and a spherical "source
surface" at a few solar radii, where the field is forced radial to mimic the
wind dragging it open.

The model is the crudest useful description of the corona -- it has no currents,
no time dependence and no plasma -- but it is the standard first comparison for
loop topology and coronal-hole boundaries, and it is cheap enough to run
interactively.

Three things about it shape this module:

  * The boundary condition is a *synoptic* magnetogram, built up over a full
    Carrington rotation (see ``src.backend.solar.pfss_magnetograms``). It
    therefore describes the global field *around* an observation, never the
    instantaneous field, and the far side is always at least two weeks stale.
    The age of the map relative to the frame is carried through as provenance so
    the UI can show it rather than bury it.
  * The solve happens in Carrington coordinates, independent of any observer,
    while the overlay has to land in the displayed frame's helioprojective
    arcsec. Those two concerns are split deliberately: :func:`trace_field_lines`
    reduces the solver's objects to plain arrays in Carrington coordinates
    (:class:`TracedField`), and :func:`field_lines_arcsec` projects those arrays
    into one frame. Only the first half needs ``sunkit_magex``, so the
    projection is testable, cacheable and re-runnable per frame for free.
  * Field lines are three-dimensional and half of each closed loop is usually
    behind the limb. Occultation is resolved with ``Helioprojective.is_visible``
    and the hidden vertices become NaN, exactly as the coordinate graticule does
    in ``src.backend.solar.solar_grid``, so ``connect="finite"`` breaks the
    curve instead of drawing a chord through the Sun.

``sunkit_magex`` is an optional dependency: it pulls in ``streamtracer`` (a
compiled extension) and ``scikit-image``, either of which can fail to bundle in
a frozen build. Every entry point that needs it imports it lazily and raises
:class:`PfssUnavailableError` with an actionable message.

No Qt here, so the module stays unit-testable.
"""

from __future__ import annotations

import hashlib
import time
import warnings
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

import numpy as np


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Seed placement strategies, in the order the UI lists them.
SEED_UNIFORM = "uniform"
SEED_OPEN_FIELD = "open_field"
SEED_ROI = "roi"
SEED_CLICK = "click"
SEED_MODES: tuple[str, ...] = (SEED_UNIFORM, SEED_OPEN_FIELD, SEED_ROI, SEED_CLICK)
SEED_MODE_LABELS: dict[str, str] = {
    SEED_UNIFORM: "Uniform grid",
    SEED_OPEN_FIELD: "Open-field regions",
    SEED_ROI: "Current crop",
    SEED_CLICK: "Clicked points",
}

#: Overlay classes. Open field lines are split by polarity because the sign of
#: Br at the footpoint is what distinguishes the two magnetic hemispheres of the
#: solar wind, and it is the first thing you look for in a coronal hole.
CLASS_OPEN_POSITIVE = "open_positive"
CLASS_OPEN_NEGATIVE = "open_negative"
CLASS_CLOSED = "closed"
OVERLAY_CLASSES: tuple[str, ...] = (CLASS_OPEN_POSITIVE, CLASS_OPEN_NEGATIVE, CLASS_CLOSED)

# Defaults. nrho=35 radial grid points and a source surface at 2.5 R_sun are the
# long-standing conventional choices, and what the sunkit_magex examples use.
DEFAULT_NRHO = 35
DEFAULT_RSS = 2.5
# Chosen by looking at the result over a real AIA 193 image: 24 traces ~550
# field lines, which paints over the disk almost completely, while 12 gives
# ~150 -- enough to show the topology and still read the image underneath.
DEFAULT_SEED_DENSITY = 12
DEFAULT_SEED_RADIUS_RSUN = 1.01
DEFAULT_STEP_SIZE = 1.0

# Seeds sit just above the photosphere: a tracer started exactly at r = 1 can
# step inward out of the domain on its first move and terminate immediately.
MIN_SEED_RADIUS_RSUN = 1.001

PFSS_INSTALL_HINT = "Install it with: python3 -m pip install sunkit-magex"

#: A single overlay polyline: matched arcsec arrays, NaN where a vertex is
#: occulted or where one field line ends and the next begins.
Polyline = tuple[np.ndarray, np.ndarray]


class PfssUnavailableError(RuntimeError):
    """``sunkit_magex`` (or one of its compiled dependencies) is unusable."""


# --------------------------------------------------------------------------- #
# Optional dependency
# --------------------------------------------------------------------------- #

def _format_pfss_dependency_error(exc: BaseException) -> str:
    """User-facing reason the PFSS stack could not be imported.

    Only a genuinely missing package gets the install hint. Anything else means
    it is installed but broken -- in a packaged build, typically ``streamtracer``
    or ``scikit-image`` left out of the bundle -- where "pip install" is the
    wrong advice, so the underlying error is named instead.
    """
    missing = {"sunkit_magex", "streamtracer", "skimage", "scipy", "lazy_loader"}
    if isinstance(exc, ModuleNotFoundError) and (exc.name or "").split(".")[0] in missing:
        name = (exc.name or "").split(".")[0]
        detail = "" if name == "sunkit_magex" else f" (its '{name}' dependency is missing)"
        return (
            f"PFSS modelling needs the optional 'sunkit-magex' package{detail}.\n"
            f"{PFSS_INSTALL_HINT}"
        )
    return (
        "PFSS modelling is unavailable: sunkit-magex is installed but failed to "
        f"load ({type(exc).__name__}: {exc})."
    )


def import_pfss() -> Any:
    """Return the ``sunkit_magex.pfss`` module, or raise with a usable message."""
    try:
        from sunkit_magex import pfss
    except Exception as exc:  # pragma: no cover - exercised via the formatter
        raise PfssUnavailableError(_format_pfss_dependency_error(exc)) from exc
    return pfss


def pfss_available() -> bool:
    """Whether a PFSS solve can be attempted at all (used to gate the UI)."""
    try:
        import_pfss()
    except PfssUnavailableError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Parameters, solution and traced field
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PfssParameters:
    """Everything that changes a PFSS result, and nothing that does not.

    ``cache_key`` depends on exactly these values plus the magnetogram, so two
    runs that agree here can share a cached solve.
    """

    nrho: int = DEFAULT_NRHO
    rss: float = DEFAULT_RSS
    seed_mode: str = SEED_UNIFORM
    seed_density: int = DEFAULT_SEED_DENSITY
    seed_radius_rsun: float = DEFAULT_SEED_RADIUS_RSUN
    max_steps: str | int = "auto"
    step_size: float = DEFAULT_STEP_SIZE

    def is_physical(self) -> bool:
        """Whether the parameters describe a solvable model."""
        return (
            int(self.nrho) >= 4
            and float(self.rss) > 1.0
            and int(self.seed_density) >= 1
            and float(self.seed_radius_rsun) >= MIN_SEED_RADIUS_RSUN
            and float(self.seed_radius_rsun) < float(self.rss)
            and float(self.step_size) > 0.0
        )

    def normalized(self) -> PfssParameters:
        """Clamp to the solvable range rather than failing deep in the solver."""
        return replace(
            self,
            nrho=max(4, int(self.nrho)),
            rss=max(1.05, float(self.rss)),
            seed_density=max(1, int(self.seed_density)),
            seed_radius_rsun=min(
                max(MIN_SEED_RADIUS_RSUN, float(self.seed_radius_rsun)),
                max(1.05, float(self.rss)) - 0.01,
            ),
            step_size=max(0.01, float(self.step_size)),
        )

    def cache_key(self) -> str:
        """Stable fragment identifying this parameter set."""
        parts = (
            f"nrho={int(self.nrho)}",
            f"rss={float(self.rss):.4f}",
            f"seed={self.seed_mode}",
            f"density={int(self.seed_density)}",
            f"radius={float(self.seed_radius_rsun):.4f}",
            f"steps={self.max_steps}",
            f"step={float(self.step_size):.4f}",
        )
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PfssSolution:
    """A solved potential field, plus how long it took and where it came from.

    ``output`` is the ``sunkit_magex.pfss.Output`` object. It is deliberately not
    cached to disk -- see :class:`TracedField` for the serialisable products.
    """

    output: Any
    params: PfssParameters
    provenance: dict[str, Any]
    solve_seconds: float = 0.0

    @property
    def coordinate_frame(self) -> Any:
        """The Carrington frame the solution (and its field lines) live in."""
        return self.output.coordinate_frame


@dataclass(frozen=True)
class TracedField:
    """Traced field lines as plain arrays in Carrington coordinates.

    This is the frame-independent, serialisable form of a traced result: it is
    what gets written to the disk cache and what :func:`field_lines_arcsec`
    projects. Vertices of every line are concatenated, with ``offsets`` giving
    each line's start index (``offsets[-1] == lon_deg.size``), so a variable
    number of variable-length lines still round-trips through ``np.savez``.

    ``polarity`` is 0 for a closed line and the sign of Br at its solar
    footpoint otherwise. Note the solver returns it as a float (including
    ``-0.0``), so classification tests the sign rather than an integer identity.
    """

    lon_deg: np.ndarray
    lat_deg: np.ndarray
    radius_rsun: np.ndarray
    offsets: np.ndarray
    polarity: np.ndarray
    open_mask: np.ndarray
    obstime: str = ""
    #: Open-field region outlines, as Carrington polylines at the surface. Built
    #: at trace time (it needs scikit-image) so projection stays dependency-free.
    boundary_lon_deg: np.ndarray | None = None
    boundary_lat_deg: np.ndarray | None = None
    boundary_offsets: np.ndarray | None = None
    #: Footpoint polarity sampled on a regular Carrington grid, for the
    #: diagnostics open/closed map. Shape (nlat, nlon), values -1 / 0 / +1.
    polarity_grid: np.ndarray | None = None
    grid_lon_deg: np.ndarray | None = None
    grid_lat_deg: np.ndarray | None = None

    @property
    def line_count(self) -> int:
        return max(0, int(self.offsets.size) - 1)

    def line_slice(self, index: int) -> slice:
        return slice(int(self.offsets[index]), int(self.offsets[index + 1]))

    def class_of(self, index: int) -> str:
        """Overlay class of one line."""
        if not bool(self.open_mask[index]):
            return CLASS_CLOSED
        return CLASS_OPEN_POSITIVE if float(self.polarity[index]) > 0 else CLASS_OPEN_NEGATIVE


@dataclass(frozen=True)
class PfssOverlay:
    """Projected overlay geometry for one displayed frame, in arcsec."""

    open_positive: Polyline | None = None
    open_negative: Polyline | None = None
    closed: Polyline | None = None
    open_boundaries: Polyline | None = None

    def is_empty(self) -> bool:
        return all(
            item is None
            for item in (self.open_positive, self.open_negative, self.closed, self.open_boundaries)
        )


# --------------------------------------------------------------------------- #
# Solving
# --------------------------------------------------------------------------- #

def solve_pfss(
    magnetogram: Any,
    params: PfssParameters | None = None,
    *,
    provenance: dict[str, Any] | None = None,
    pfss_module: Any | None = None,
) -> PfssSolution:
    """Solve the potential field for a normalised synoptic magnetogram.

    ``magnetogram`` must already be a full-Sun Carrington map in a cylindrical
    equal-area projection -- use
    :func:`src.backend.solar.pfss_magnetograms.load_magnetogram` to get there.
    The solver validates this itself and raises a clear ``ValueError`` if not,
    which is left to propagate.

    This is the expensive, uninterruptible step: ``pfss()`` is a single call into
    compiled code, so there is no cancellation seam inside it. Callers that need
    to be responsive should check their cancel flag before and after.
    """
    params = (params or PfssParameters()).normalized()
    pfss = pfss_module or import_pfss()

    started = time.monotonic()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pfss_input = pfss.Input(magnetogram, int(params.nrho), float(params.rss))
        output = pfss.pfss(pfss_input)
    elapsed = time.monotonic() - started

    meta = dict(provenance or {})
    meta.setdefault("nrho", int(params.nrho))
    meta.setdefault("rss", float(params.rss))
    meta.setdefault("magnetogram_shape", tuple(np.shape(getattr(magnetogram, "data", ()))))
    return PfssSolution(output=output, params=params, provenance=meta, solve_seconds=elapsed)


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #

def _carrington_grid(density: int) -> tuple[np.ndarray, np.ndarray]:
    """Longitude/latitude of an equal-area Carrington grid.

    Latitude is spaced in sin(lat), not in degrees: a grid uniform in latitude
    over-samples the poles, wasting most of the traced lines on the two places a
    synoptic magnetogram knows least about.
    """
    density = max(1, int(density))
    lon = np.linspace(0.0, 360.0, density * 2, endpoint=False)
    lat = np.degrees(np.arcsin(np.linspace(-0.95, 0.95, density)))
    return lon, lat


def uniform_seeds(
    solution: PfssSolution,
    *,
    density: int = DEFAULT_SEED_DENSITY,
    radius_rsun: float = DEFAULT_SEED_RADIUS_RSUN,
    frame: Any | None = None,
    visible_only: bool = True,
) -> Any:
    """Seeds on an equal-area Carrington grid.

    When ``frame`` is given and ``visible_only`` is set, seeds on the far side of
    the Sun are dropped: their field lines would be projected away entirely, so
    tracing them is wasted work.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    lon_1d, lat_1d = _carrington_grid(density)
    lon, lat = np.meshgrid(lon_1d, lat_1d)
    lon = lon.ravel()
    lat = lat.ravel()

    seeds = SkyCoord(
        lon * u.deg,
        lat * u.deg,
        float(radius_rsun) * u.R_sun,
        frame=solution.coordinate_frame,
    )
    if frame is not None and visible_only:
        keep = _visible_mask(seeds, frame)
        if keep is not None and bool(np.any(keep)):
            seeds = seeds[keep]
    return seeds


def seeds_from_arcsec(
    points: Sequence[tuple[float, float]],
    frame: Any,
    solution: PfssSolution,
    *,
    radius_rsun: float = DEFAULT_SEED_RADIUS_RSUN,
) -> Any:
    """Seeds from on-disk arcsec positions, for click-to-seed.

    Helioprojective positions are back-projected onto the solar surface by sunpy,
    then lifted to the seed radius in Carrington coordinates. Points whose sight
    line misses the Sun (past the limb) are dropped.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    frame_coord = getattr(frame, "coordinate_frame", None)
    if frame_coord is None:
        raise ValueError("This frame has no usable solar coordinate system.")

    valid = [(float(x), float(y)) for x, y in points if np.isfinite(x) and np.isfinite(y)]
    if not valid:
        raise ValueError("Click the solar disk to place at least one seed point.")

    tx = np.array([p[0] for p in valid]) * u.arcsec
    ty = np.array([p[1] for p in valid]) * u.arcsec
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        on_disk = SkyCoord(tx, ty, frame=frame_coord).transform_to(solution.coordinate_frame)
        lon = np.atleast_1d(on_disk.lon.to_value(u.deg))
        lat = np.atleast_1d(on_disk.lat.to_value(u.deg))

    finite = np.isfinite(lon) & np.isfinite(lat)
    if not np.any(finite):
        raise ValueError("None of the clicked points land on the solar disk.")
    return SkyCoord(
        lon[finite] * u.deg,
        lat[finite] * u.deg,
        float(radius_rsun) * u.R_sun,
        frame=solution.coordinate_frame,
    )


def roi_seeds(
    solution: PfssSolution,
    frame: Any,
    bounds_arcsec: tuple[float, float, float, float],
    *,
    density: int = DEFAULT_SEED_DENSITY,
    radius_rsun: float = DEFAULT_SEED_RADIUS_RSUN,
) -> Any:
    """Seeds filling one arcsec rectangle, for concentrating lines on a crop.

    The rectangle is sampled in arcsec and each point back-projected onto the
    surface, rather than converting the corners to longitude/latitude and
    gridding there: the map from arcsec to heliographic coordinates is strongly
    non-linear near the limb, so a "rectangle" in longitude/latitude would not
    be the region the user selected.
    """
    x0, y0, x1, y1 = (float(v) for v in bounds_arcsec)
    x_lo, x_hi = min(x0, x1), max(x0, x1)
    y_lo, y_hi = min(y0, y1), max(y0, y1)
    if not (np.isfinite(x_lo) and np.isfinite(x_hi) and np.isfinite(y_lo) and np.isfinite(y_hi)):
        raise ValueError("The crop bounds are not usable for seeding.")

    side = max(2, int(round(np.sqrt(max(1, int(density) ** 2)))))
    xs = np.linspace(x_lo, x_hi, side)
    ys = np.linspace(y_lo, y_hi, side)
    grid_x, grid_y = np.meshgrid(xs, ys)
    return seeds_from_arcsec(
        list(zip(grid_x.ravel(), grid_y.ravel())),
        frame,
        solution,
        radius_rsun=radius_rsun,
    )


def open_field_seeds(
    solution: PfssSolution,
    *,
    density: int = DEFAULT_SEED_DENSITY,
    radius_rsun: float = DEFAULT_SEED_RADIUS_RSUN,
    frame: Any | None = None,
    tracer: Any | None = None,
) -> Any:
    """Seeds confined to open-field regions.

    Traces a coarse survey first, keeps the footpoints whose lines reach the
    source surface, then re-seeds densely around those. The result shows
    coronal-hole connectivity without the closed loops that otherwise dominate
    the picture.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    coarse_density = max(6, int(density) // 2)
    survey = uniform_seeds(
        solution, density=coarse_density, radius_rsun=radius_rsun, frame=frame
    )
    lines = _trace(solution, survey, tracer=tracer)
    polarities = np.asarray(lines.polarities, dtype=float)
    open_idx = np.nonzero(polarities != 0)[0]
    if open_idx.size == 0:
        # No open field at this source-surface height: fall back rather than
        # returning an empty seed set the tracer would reject.
        return survey

    lon = np.atleast_1d(survey.lon.to_value(u.deg))[open_idx]
    lat = np.atleast_1d(survey.lat.to_value(u.deg))[open_idx]

    # Jitter a small cluster around each open footpoint to fill the region.
    per_seed = max(1, int(round((int(density) / max(1, coarse_density)) ** 2)))
    if per_seed > 1:
        rng = np.random.default_rng(0)  # deterministic: same controls, same picture
        dlon = 360.0 / (coarse_density * 2)
        dlat = 180.0 / coarse_density
        lon = np.concatenate([lon] + [
            lon + rng.uniform(-dlon / 2, dlon / 2, lon.size) for _ in range(per_seed - 1)
        ])
        lat = np.concatenate([lat] + [
            lat + rng.uniform(-dlat / 2, dlat / 2, lat.size) for _ in range(per_seed - 1)
        ])
        lat = np.clip(lat, -89.5, 89.5)

    return SkyCoord(
        lon * u.deg, lat * u.deg, float(radius_rsun) * u.R_sun,
        frame=solution.coordinate_frame,
    )


def build_seeds(
    solution: PfssSolution,
    *,
    frame: Any | None = None,
    params: PfssParameters | None = None,
    clicked_arcsec: Sequence[tuple[float, float]] | None = None,
    roi_bounds_arcsec: tuple[float, float, float, float] | None = None,
    tracer: Any | None = None,
) -> Any:
    """Dispatch to the seed builder named by ``params.seed_mode``."""
    params = (params or solution.params).normalized()
    mode = str(params.seed_mode or SEED_UNIFORM)
    radius = float(params.seed_radius_rsun)

    if mode == SEED_CLICK:
        if frame is None:
            raise ValueError("Click-to-seed needs a displayed frame.")
        return seeds_from_arcsec(clicked_arcsec or (), frame, solution, radius_rsun=radius)
    if mode == SEED_ROI:
        if frame is None or roi_bounds_arcsec is None:
            raise ValueError("Crop seeding needs an active crop rectangle.")
        return roi_seeds(
            solution, frame, roi_bounds_arcsec,
            density=params.seed_density, radius_rsun=radius,
        )
    if mode == SEED_OPEN_FIELD:
        return open_field_seeds(
            solution, density=params.seed_density, radius_rsun=radius,
            frame=frame, tracer=tracer,
        )
    return uniform_seeds(
        solution, density=params.seed_density, radius_rsun=radius, frame=frame
    )


# --------------------------------------------------------------------------- #
# Tracing
# --------------------------------------------------------------------------- #

def _make_tracer(params: PfssParameters, *, pfss_module: Any | None = None) -> Any:
    """The streamtracer-backed tracer.

    ``PerformanceTracer`` is the compiled tracer (``FortranTracer`` is its
    retained legacy alias). The pure-Python ``PythonTracer`` exists but is orders
    of magnitude slower, and it would not help here: ``streamtracer`` is a hard
    dependency of ``sunkit_magex``, so if it is missing the import has already
    failed.
    """
    pfss = pfss_module or import_pfss()
    from sunkit_magex.pfss import tracing

    kwargs: dict[str, Any] = {"step_size": float(params.step_size)}
    if params.max_steps not in (None, "", "auto"):
        kwargs["max_steps"] = int(params.max_steps)
    tracer_cls = getattr(tracing, "PerformanceTracer", None) or tracing.FortranTracer
    return tracer_cls(**kwargs)


def _trace(solution: PfssSolution, seeds: Any, *, tracer: Any | None = None) -> Any:
    """Trace ``seeds`` through ``solution``, returning the solver's FieldLines."""
    tracer = tracer or _make_tracer(solution.params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return tracer.trace(seeds, solution.output)


def trace_field_lines(
    solution: PfssSolution,
    seeds: Any,
    *,
    tracer: Any | None = None,
    with_boundaries: bool = True,
    cancel_cb: Callable[[], bool] | None = None,
) -> TracedField:
    """Trace field lines and reduce them to frame-independent arrays.

    The solver's ``FieldLines`` objects hold live references to the solution and
    astropy frames, which do not serialise. Flattening them here is what makes
    the result cacheable and what lets the projection step run without
    ``sunkit_magex`` installed.
    """
    import astropy.units as u

    lines = _trace(solution, seeds, tracer=tracer)
    if cancel_cb is not None and cancel_cb():
        raise InterruptedError("PFSS tracing was cancelled.")

    lons: list[np.ndarray] = []
    lats: list[np.ndarray] = []
    radii: list[np.ndarray] = []
    offsets: list[int] = [0]
    polarity: list[float] = []
    open_mask: list[bool] = []

    total = 0
    for line in lines:
        coords = line.coords
        if coords is None or len(coords) == 0:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lon = np.atleast_1d(coords.lon.to_value(u.deg)).astype(float)
            lat = np.atleast_1d(coords.lat.to_value(u.deg)).astype(float)
            rad = np.atleast_1d(coords.radius.to_value(u.R_sun)).astype(float)
        lons.append(lon)
        lats.append(lat)
        radii.append(rad)
        total += lon.size
        offsets.append(total)
        polarity.append(float(line.polarity))
        open_mask.append(bool(line.is_open))

    empty = np.zeros(0, dtype=float)
    traced = TracedField(
        lon_deg=np.concatenate(lons) if lons else empty,
        lat_deg=np.concatenate(lats) if lats else empty,
        radius_rsun=np.concatenate(radii) if radii else empty,
        offsets=np.asarray(offsets, dtype=np.int64),
        polarity=np.asarray(polarity, dtype=float),
        open_mask=np.asarray(open_mask, dtype=bool),
        obstime=str(solution.output.dtime),
    )

    if not with_boundaries:
        return traced
    grid = _footpoint_polarity_grid(solution, density=solution.params.seed_density, tracer=tracer)
    if grid is None:
        return traced
    polarity_grid, grid_lon, grid_lat = grid
    boundaries = _open_field_boundaries_carrington(polarity_grid, grid_lon, grid_lat)
    return replace(
        traced,
        polarity_grid=polarity_grid,
        grid_lon_deg=grid_lon,
        grid_lat_deg=grid_lat,
        boundary_lon_deg=boundaries[0],
        boundary_lat_deg=boundaries[1],
        boundary_offsets=boundaries[2],
    )


def _footpoint_polarity_grid(
    solution: PfssSolution,
    *,
    density: int = DEFAULT_SEED_DENSITY,
    tracer: Any | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Footpoint polarity on a full Carrington grid: -1 / 0 / +1.

    Traced over the whole sphere (not just the visible hemisphere) because this
    feeds the Carrington-projection diagnostics map and the region outlines.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    lon_1d, lat_1d = _carrington_grid(max(12, int(density)))
    lon, lat = np.meshgrid(lon_1d, lat_1d)
    seeds = SkyCoord(
        lon.ravel() * u.deg,
        lat.ravel() * u.deg,
        float(solution.params.seed_radius_rsun) * u.R_sun,
        frame=solution.coordinate_frame,
    )
    try:
        lines = _trace(solution, seeds, tracer=tracer)
        polarities = np.asarray(lines.polarities, dtype=float)
    except Exception:
        return None
    if polarities.size != lon.size:
        return None
    return np.sign(polarities).reshape(lon.shape), lon_1d, lat_1d


def _open_field_boundaries_carrington(
    polarity_grid: np.ndarray,
    grid_lon_deg: np.ndarray,
    grid_lat_deg: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    """Outlines of the open-field regions, as Carrington polylines.

    Contours the boundary between open (|polarity| = 1) and closed (0) footpoint
    regions. Outlines rather than filled areas, so the overlay needs no new
    drawing primitive on either renderer.

    ``scikit-image`` arrives with ``sunkit_magex``; if it is somehow absent the
    boundaries are simply omitted rather than failing the whole trace.
    """
    try:
        from skimage import measure
    except Exception:  # pragma: no cover - skimage ships with sunkit_magex
        return None, None, None

    openness = (np.abs(np.asarray(polarity_grid, dtype=float)) > 0.5).astype(float)
    if not (0.0 < float(openness.mean()) < 1.0):
        return None, None, None  # all open or all closed: no boundary to draw

    lons: list[np.ndarray] = []
    lats: list[np.ndarray] = []
    offsets: list[int] = [0]
    total = 0
    for contour in measure.find_contours(openness, 0.5):
        rows = np.asarray(contour)[:, 0]
        cols = np.asarray(contour)[:, 1]
        lat = np.interp(rows, np.arange(grid_lat_deg.size), grid_lat_deg)
        lon = np.interp(cols, np.arange(grid_lon_deg.size), grid_lon_deg)
        if lon.size < 2:
            continue
        lons.append(lon)
        lats.append(lat)
        total += lon.size
        offsets.append(total)

    if not lons:
        return None, None, None
    return (
        np.concatenate(lons),
        np.concatenate(lats),
        np.asarray(offsets, dtype=np.int64),
    )


# --------------------------------------------------------------------------- #
# Projection into a displayed frame
# --------------------------------------------------------------------------- #

def _carrington_frame(obstime: str, frame_coord: Any) -> Any:
    """Rebuild the Carrington frame traced coordinates belong to.

    Needed on the cache path, where the original solver frame is long gone and
    only ``obstime`` survives. ``observer="self"`` matches what the solver uses,
    which is what makes the radius meaningful.
    """
    from sunpy.coordinates import HeliographicCarrington

    stamp = str(obstime or "").strip() or getattr(frame_coord, "obstime", None)
    return HeliographicCarrington(obstime=stamp, observer="self")


def _visible_mask(coords: Any, frame: Any) -> np.ndarray | None:
    """Boolean mask of which ``coords`` are visible in ``frame``."""
    frame_coord = getattr(frame, "coordinate_frame", None)
    if frame_coord is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.atleast_1d(np.asarray(coords.transform_to(frame_coord).is_visible(), dtype=bool))
    except Exception:
        return None


def _near_side_mask(hpc: Any, frame_coord: Any) -> np.ndarray:
    """Which helioprojective points lie in front of the plane of the sky.

    Helioprojective to Heliocentric shares the observer and obstime, so this is a
    local rotation with no ephemeris lookup -- cheap enough to run on every
    vertex alongside the occultation test.
    """
    import astropy.units as u
    from sunpy.coordinates import Heliocentric

    try:
        hcc = hpc.transform_to(
            Heliocentric(observer=frame_coord.observer, obstime=frame_coord.obstime)
        )
        return np.atleast_1d(np.asarray(hcc.z.to_value(u.R_sun), dtype=float)) > 0.0
    except Exception:
        # Without the hemisphere test the overlay is cluttered, not wrong, so a
        # failure here degrades to the occultation mask alone.
        return np.ones(np.atleast_1d(hpc.Tx).size, dtype=bool)


def _project_vertices(
    lon_deg: np.ndarray,
    lat_deg: np.ndarray,
    radius_rsun: np.ndarray,
    frame: Any,
    obstime: str,
    *,
    near_side_only: bool = True,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Carrington vertices to NaN-masked helioprojective arcsec.

    Every vertex of every line goes through a *single* ``transform_to``: the
    per-call overhead of astropy's frame machinery dominates, so one transform of
    N points is far cheaper than N transforms of one point. Hidden vertices
    become NaN so ``connect="finite"`` breaks the curve at the limb instead of
    drawing a chord through the Sun.

    Two different masks are available, and the distinction is easy to get wrong:

    ``Helioprojective.is_visible`` answers "is this point unoccluded", which is
    true for anything *beyond* the limb in projection -- including a point behind
    the plane of the sky, because nothing blocks the line of sight to it. That is
    the right answer for a coronagraph, but on a disk image it means far-side
    field lines are drawn as arcs outside the limb with no visible footpoint. A
    2.5 R_sun open line rooted 100 degrees away from the sub-observer point
    projects out past 2300 arcsec while sitting a third of a solar radius *behind*
    the plane of the sky.

    ``near_side_only`` (the default) therefore adds the hemisphere test
    ``z > 0`` in Heliocentric coordinates, keeping only field lines rooted on the
    hemisphere the imager can actually see. Clear it to get the geometrically
    complete picture, including far-side structure projected past the limb.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    frame_coord = getattr(frame, "coordinate_frame", None)
    if frame_coord is None or lon_deg.size == 0:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            carrington = SkyCoord(
                np.asarray(lon_deg, dtype=float) * u.deg,
                np.asarray(lat_deg, dtype=float) * u.deg,
                np.asarray(radius_rsun, dtype=float) * u.R_sun,
                frame=_carrington_frame(obstime, frame_coord),
            )
            hpc = carrington.transform_to(frame_coord)
            tx = np.atleast_1d(np.asarray(hpc.Tx.to_value(u.arcsec), dtype=float))
            ty = np.atleast_1d(np.asarray(hpc.Ty.to_value(u.arcsec), dtype=float))
            visible = np.atleast_1d(np.asarray(hpc.is_visible(), dtype=bool))
            if near_side_only:
                visible &= _near_side_mask(hpc, frame_coord)
    except Exception:
        return None

    tx = np.where(visible, tx, np.nan)
    ty = np.where(visible, ty, np.nan)
    if not np.any(np.isfinite(tx) & np.isfinite(ty)):
        return None
    return tx, ty


def _join_segments(
    tx: np.ndarray,
    ty: np.ndarray,
    offsets: np.ndarray,
    indices: Sequence[int],
) -> Polyline | None:
    """Concatenate selected line segments, NaN-separated.

    One polyline per overlay class instead of one per field line: a few hundred
    field lines would otherwise mean a few hundred graphics items, which is
    exactly the cost the GCS wireframe was designed to avoid.
    """
    pieces_x: list[np.ndarray] = []
    pieces_y: list[np.ndarray] = []
    gap = np.array([np.nan])
    for index in indices:
        start, stop = int(offsets[index]), int(offsets[index + 1])
        if stop <= start:
            continue
        segment_x = tx[start:stop]
        if not np.any(np.isfinite(segment_x)):
            continue
        if pieces_x:
            pieces_x.append(gap)
            pieces_y.append(gap)
        pieces_x.append(segment_x)
        pieces_y.append(ty[start:stop])
    if not pieces_x:
        return None
    return np.concatenate(pieces_x), np.concatenate(pieces_y)


def field_lines_arcsec(
    traced: TracedField,
    frame: Any,
    *,
    show_open: bool = True,
    show_closed: bool = True,
    show_boundaries: bool = True,
    near_side_only: bool = True,
) -> PfssOverlay:
    """Project a traced field into one frame's helioprojective arcsec.

    Deliberately free of any ``sunkit_magex`` dependency -- it works on the plain
    arrays in :class:`TracedField`, so it runs on a cache hit and is testable
    without the optional packages installed.

    Returns an empty overlay (rather than raising) for frames with no usable
    coordinate system, mirroring how ``graticule_arcsec`` degrades for derived
    arrays that lost their WCS.
    """
    if traced.line_count == 0 and traced.boundary_offsets is None:
        return PfssOverlay()

    open_positive = open_negative = closed = boundaries = None

    if traced.line_count and (show_open or show_closed):
        projected = _project_vertices(
            traced.lon_deg, traced.lat_deg, traced.radius_rsun, frame, traced.obstime,
            near_side_only=near_side_only,
        )
        if projected is not None:
            tx, ty = projected
            buckets: dict[str, list[int]] = {name: [] for name in OVERLAY_CLASSES}
            for index in range(traced.line_count):
                buckets[traced.class_of(index)].append(index)
            if show_open:
                open_positive = _join_segments(tx, ty, traced.offsets, buckets[CLASS_OPEN_POSITIVE])
                open_negative = _join_segments(tx, ty, traced.offsets, buckets[CLASS_OPEN_NEGATIVE])
            if show_closed:
                closed = _join_segments(tx, ty, traced.offsets, buckets[CLASS_CLOSED])

    if show_boundaries and traced.boundary_offsets is not None:
        count = max(0, int(traced.boundary_offsets.size) - 1)
        projected = _project_vertices(
            traced.boundary_lon_deg,
            traced.boundary_lat_deg,
            np.full(traced.boundary_lon_deg.size, 1.0),
            frame,
            traced.obstime,
            near_side_only=near_side_only,
        )
        if projected is not None:
            tx, ty = projected
            boundaries = _join_segments(tx, ty, traced.boundary_offsets, range(count))

    return PfssOverlay(
        open_positive=open_positive,
        open_negative=open_negative,
        closed=closed,
        open_boundaries=boundaries,
    )


# --------------------------------------------------------------------------- #
# Diagnostics products
# --------------------------------------------------------------------------- #

def source_surface_products(solution: PfssSolution) -> tuple[Any, list[Any]]:
    """Radial field on the source surface, and its polarity inversion lines.

    The inversion lines are where the heliospheric current sheet leaves the
    model, so they are the most directly checkable prediction it makes.
    """
    output = solution.output
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ss_br = output.source_surface_br
        try:
            pils = list(output.source_surface_pils)
        except Exception:
            pils = []
    return ss_br, pils


def solution_stats(solution: PfssSolution, traced: TracedField | None = None) -> dict[str, Any]:
    """Scalar summary of a solve, for the diagnostics panel."""
    import astropy.units as u

    stats: dict[str, Any] = {
        "nrho": int(solution.params.nrho),
        "rss": float(solution.params.rss),
        "solve_seconds": float(solution.solve_seconds),
    }

    grid = getattr(solution.output, "grid", None)
    if grid is not None:
        stats["grid"] = f"{getattr(grid, 'nphi', '?')} x {getattr(grid, 'ns', '?')} x {getattr(grid, 'nr', '?')}"

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ss_br = solution.output.source_surface_br
            data = np.asarray(ss_br.data, dtype=float)
            # Open flux through the source surface, where the field is radial by
            # construction: integral of |Br| over a sphere of radius rss. The
            # source-surface map is equal-area, so a plain mean suffices.
            radius = float(solution.params.rss) * u.R_sun.to(u.cm)
            area = 4.0 * np.pi * radius**2
            stats["open_flux_mx"] = float(np.nanmean(np.abs(data)) * area)
            stats["source_surface_br_unit"] = str(getattr(solution.output, "bunit", "") or "")
    except Exception:
        pass

    if traced is not None and traced.line_count:
        open_count = int(np.count_nonzero(traced.open_mask))
        stats["line_count"] = traced.line_count
        stats["open_line_count"] = open_count
        stats["closed_line_count"] = traced.line_count - open_count
    if traced is not None and traced.polarity_grid is not None:
        grid_values = np.asarray(traced.polarity_grid, dtype=float)
        stats["open_area_fraction"] = float(np.mean(np.abs(grid_values) > 0.5))

    return stats
