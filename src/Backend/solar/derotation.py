"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Solar differential-rotation compensation for a selected region.

The Sun is not a rigid body: over a multi-hour sequence a feature drifts across
the field of view (a point at 300 arcsec moves to roughly 351 arcsec in six
hours). A fixed crop rectangle therefore slides off the region under study, and
light curves, region statistics and difference images quietly start mixing in
neighbouring solar surface. Derotation pins the cut-out to the rotating surface
instead of to the detector.

Two modes, because they answer different scientific questions:

* ``MODE_TRACK`` — move the window, keep the pixels. The region centre is
  carried to each frame's observation time and an identically sized window is
  cut there. Nothing is resampled, so the original data values survive intact:
  this is the mode for photometry and light curves.
* ``MODE_REPROJECT`` — resample onto the reference-time grid. Each frame is cut
  with a margin and reprojected through
  ``sunpy.coordinates.propagate_with_solar_surface``, so foreshortening changes
  are corrected and every output frame shares one WCS: this is the mode for
  differencing and for regions well away from disk centre.

``sunpy.physics.differential_rotation.differential_rotate`` is deliberately not
used. It imports ``scikit-image``, which is not a dependency of this project,
and it warps the whole frame — measured at roughly 11 s per 4096x4096 frame,
which is unusable for a sequence. Reprojecting only the selected region costs
about 32 ms per frame instead.

Every output frame is a real sunpy map with a valid WCS, so the coordinate
graticule, hover read-out and limb overlay keep working afterwards — unlike the
plain rectangle crop, whose ``AiaArrayMap`` output has no ``.wcs``.

Pure functions with injectable rotation callables (no Qt), so the geometry is
unit-testable without network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


MODE_TRACK = "track"
MODE_REPROJECT = "reproject"
MODES = (MODE_TRACK, MODE_REPROJECT)

MODE_LABELS = {
    MODE_TRACK: "Track region (no resampling)",
    MODE_REPROJECT: "Reproject to reference time",
}

# Observation metadata that ``reproject_to`` does not carry across but the rest
# of the analyzer relies on. EXPTIME especially: difference imaging normalises
# by it, so losing it silently changes the numbers.
_PRESERVED_TIME_KEYS = ("date-obs", "date_obs", "dateobs", "t_obs", "date-beg", "date_beg")
_PRESERVED_META_KEYS = (
    "exptime",
    "instrume",
    "telescop",
    "detector",
    "obsrvtry",
    "origin",
    "lvl_num",
    "bunit",
)
# Wavelength needs its unit alongside it — copying WAVELNTH without WAVEUNIT
# makes sunpy raise UnitConversionError when it next reads the wavelength.
_PRESERVED_META_PAIRS = (("wavelnth", "waveunit"),)


@dataclass(frozen=True)
class DerotationSpec:
    """What to derotate and how.

    ``bounds_arcsec`` is ``(x_min, x_max, y_min, y_max)`` in helioprojective
    arcsec — the same convention as the analyzer's crop spin boxes — read on the
    frame named by ``reference_index``, which also fixes the epoch that every
    other frame is rotated to.
    """

    bounds_arcsec: tuple[float, float, float, float]
    mode: str = MODE_TRACK
    reference_index: int = 0
    pad_arcsec: float = 60.0


@dataclass(frozen=True)
class DerotationResult:
    frames: list[Any]
    kept_indexes: list[int] = field(default_factory=list)
    centres_arcsec: list[tuple[float, float]] = field(default_factory=list)
    dropped: list[tuple[int, str]] = field(default_factory=list)
    mode: str = MODE_TRACK
    reference_index: int = 0
    reference_time: str = ""
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False


def supports_derotation(frame: Any) -> bool:
    """Whether ``frame`` carries the full WCS the geometry needs.

    Derived arrays (``AiaArrayMap`` from a plain crop or an exposure
    normalisation) keep only a handful of attributes and have no ``.wcs``, so
    they cannot be rotated. Callers use this to disable the control with an
    explanation rather than failing mid-run.
    """
    return all(
        hasattr(frame, attr) for attr in ("wcs", "submap", "coordinate_frame", "observer_coordinate")
    )


def track_region_bounds(
    frames: Sequence[Any],
    spec: DerotationSpec,
    *,
    rotate_fn: Callable[..., Any] | None = None,
) -> list[tuple[float, float, float, float] | None]:
    """Where the region lands, in arcsec, on each frame.

    Cheap enough to drive an on-canvas preview rectangle: it does the rotation
    geometry only and never touches pixel data. ``None`` marks a frame whose
    centre could not be computed.
    """
    import astropy.units as u

    reference, centre = _reference_centre(frames, spec)
    half_w = abs(spec.bounds_arcsec[1] - spec.bounds_arcsec[0]) / 2.0
    half_h = abs(spec.bounds_arcsec[3] - spec.bounds_arcsec[2]) / 2.0

    out: list[tuple[float, float, float, float] | None] = []
    for frame in frames:
        try:
            rotated = _rotate_centre(centre, frame, reference, rotate_fn=rotate_fn)
            cx = float(rotated.Tx.to_value(u.arcsec))
            cy = float(rotated.Ty.to_value(u.arcsec))
        except Exception:  # noqa: BLE001 - a bad frame must not lose the rest
            out.append(None)
            continue
        out.append((cx - half_w, cx + half_w, cy - half_h, cy + half_h))
    return out


def derotate_region(
    frames: Sequence[Any],
    spec: DerotationSpec,
    *,
    progress_cb: Callable[[int, int], Any] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    rotate_fn: Callable[..., Any] | None = None,
) -> DerotationResult:
    """Cut the selected region out of every frame, following solar rotation."""
    work = list(frames)
    if len(work) < 2:
        raise ValueError("Derotation needs at least two frames.")
    if spec.mode not in MODES:
        raise ValueError(f"Unknown derotation mode: {spec.mode!r}")

    unsupported = [i for i, f in enumerate(work) if not supports_derotation(f)]
    if unsupported:
        raise ValueError(
            "These frames are derived arrays without full world coordinates, so they "
            "cannot be derotated. Reload the series and derotate before cropping."
        )

    reference, centre = _reference_centre(work, spec)
    ref_submap = _reference_submap(reference, spec)
    ny, nx = ref_submap.data.shape[:2]
    if ny < 2 or nx < 2:
        raise ValueError(_degenerate_region_message(reference, centre))

    cutter = _cut_tracked if spec.mode == MODE_TRACK else _cut_reprojected

    out: list[Any] = []
    kept: list[int] = []
    centres: list[tuple[float, float]] = []
    dropped: list[tuple[int, str]] = []
    warnings: list[str] = []
    total = len(work)

    for index, frame in enumerate(work):
        if _cancelled(cancel_cb):
            return DerotationResult(
                frames=out,
                kept_indexes=kept,
                centres_arcsec=centres,
                dropped=dropped,
                mode=spec.mode,
                reference_index=spec.reference_index,
                reference_time=_time_text(reference),
                warnings=warnings,
                cancelled=True,
            )

        try:
            rotated = _rotate_centre(centre, frame, reference, rotate_fn=rotate_fn)
            cut = cutter(frame, rotated, ref_submap, ny, nx, spec)
        except Exception as exc:  # noqa: BLE001 - report and skip, never abort the run
            dropped.append((index, str(exc)))
            _report(progress_cb, index + 1, total)
            continue

        if cut is None:
            dropped.append((index, "the region has rotated outside this frame"))
            _report(progress_cb, index + 1, total)
            continue

        import astropy.units as u

        _preserve_observation_meta(cut, frame)
        out.append(cut)
        kept.append(index)
        centres.append(
            (float(rotated.Tx.to_value(u.arcsec)), float(rotated.Ty.to_value(u.arcsec)))
        )
        _report(progress_cb, index + 1, total)

    if not out:
        raise ValueError(
            "The selected region rotated out of every frame. Pick a region nearer disk "
            "centre, or a shorter time span."
        )
    if dropped:
        warnings.append(
            f"{len(dropped)} of {total} frame(s) were dropped: {dropped[0][1]}."
        )

    return DerotationResult(
        frames=out,
        kept_indexes=kept,
        centres_arcsec=centres,
        dropped=dropped,
        mode=spec.mode,
        reference_index=spec.reference_index,
        reference_time=_time_text(reference),
        warnings=warnings,
    )


def _reference_centre(frames: Sequence[Any], spec: DerotationSpec):
    """``(reference_frame, centre_skycoord)`` for the region in ``spec``."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    if not frames:
        raise ValueError("Load solar frames before derotating.")
    index = max(0, min(int(spec.reference_index), len(frames) - 1))
    reference = frames[index]

    x0, x1, y0, y1 = (float(v) for v in spec.bounds_arcsec)
    centre = SkyCoord(
        ((x0 + x1) / 2.0) * u.arcsec,
        ((y0 + y1) / 2.0) * u.arcsec,
        frame=reference.coordinate_frame,
    )
    return reference, centre


def _reference_submap(reference: Any, spec: DerotationSpec) -> Any:
    """The region as cut from the reference frame — it defines the output grid."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    x0, x1, y0, y1 = (float(v) for v in spec.bounds_arcsec)
    x_lo, x_hi = sorted((x0, x1))
    y_lo, y_hi = sorted((y0, y1))
    bottom_left = SkyCoord(x_lo * u.arcsec, y_lo * u.arcsec, frame=reference.coordinate_frame)
    top_right = SkyCoord(x_hi * u.arcsec, y_hi * u.arcsec, frame=reference.coordinate_frame)
    return reference.submap(bottom_left, top_right=top_right)


def _degenerate_region_message(reference: Any, centre: Any) -> str:
    """Explain *why* the region yielded no usable pixels.

    A region off the edge of the image and a region only a pixel or two across
    both produce an empty submap, but they need opposite fixes, so they get
    different messages instead of one vague one.
    """
    try:
        px, py = reference.wcs.world_to_pixel(centre)
        height, width = reference.data.shape[:2]
        if not (0 <= float(px) < width and 0 <= float(py) < height):
            return (
                "The selected region lies outside the reference frame. Draw the "
                "rectangle over the image before derotating."
            )
    except Exception:
        pass
    return "The selected region is too small to derotate. Draw a larger area."


def _rotate_centre(
    centre: Any,
    frame: Any,
    reference: Any,
    *,
    rotate_fn: Callable[..., Any] | None = None,
) -> Any:
    """Carry the region centre from the reference epoch to ``frame``'s epoch.

    The new observer is the target frame's own observer, so the spacecraft's
    motion is accounted for as well as the surface rotation. ``observer`` and
    ``time`` are mutually exclusive in sunpy's API — passing both raises.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    if rotate_fn is None:
        from sunpy.physics.differential_rotation import solar_rotate_coordinate as rotate_fn

    rotated = rotate_fn(centre, observer=frame.observer_coordinate)
    return SkyCoord(
        rotated.Tx.to(u.arcsec), rotated.Ty.to(u.arcsec), frame=frame.coordinate_frame
    )


def _cut_tracked(
    frame: Any, centre: Any, ref_submap: Any, ny: int, nx: int, spec: DerotationSpec
) -> Any | None:
    """Cut an exactly ``(ny, nx)`` window centred on the rotated point.

    The window is sized in pixels rather than arcsec on purpose: an arcsec-sized
    cut rounds to a different pixel count from frame to frame, and the analyzer
    needs one consistent array shape across the sequence for playback,
    differencing and movie export.
    """
    import astropy.units as u

    px, py = frame.wcs.world_to_pixel(centre)
    x_lo = int(round(float(px) - (nx - 1) / 2.0))
    y_lo = int(round(float(py) - (ny - 1) / 2.0))
    height, width = frame.data.shape[:2]
    if x_lo < 0 or y_lo < 0 or x_lo + nx > width or y_lo + ny > height:
        return None
    return frame.submap(
        u.Quantity([x_lo, y_lo], u.pix),
        top_right=u.Quantity([x_lo + nx - 1, y_lo + ny - 1], u.pix),
    )


def _cut_reprojected(
    frame: Any, centre: Any, ref_submap: Any, ny: int, nx: int, spec: DerotationSpec
) -> Any | None:
    """Resample the region onto the reference frame's grid.

    A padded cut is taken first so the reprojection has data to interpolate from
    right up to the output edge; reprojecting the whole frame would cost roughly
    twenty times more for the same result.
    """
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import propagate_with_solar_surface

    x0, x1, y0, y1 = (float(v) for v in spec.bounds_arcsec)
    pad = max(0.0, float(spec.pad_arcsec))
    half_w = abs(x1 - x0) / 2.0 + pad
    half_h = abs(y1 - y0) / 2.0 + pad
    cx = float(centre.Tx.to_value(u.arcsec))
    cy = float(centre.Ty.to_value(u.arcsec))

    bottom_left = SkyCoord(
        (cx - half_w) * u.arcsec, (cy - half_h) * u.arcsec, frame=frame.coordinate_frame
    )
    top_right = SkyCoord(
        (cx + half_w) * u.arcsec, (cy + half_h) * u.arcsec, frame=frame.coordinate_frame
    )
    padded = frame.submap(bottom_left, top_right=top_right)
    if min(padded.data.shape[:2]) < 2:
        return None

    with propagate_with_solar_surface():
        return padded.reproject_to(ref_submap.wcs, algorithm="interpolation")


def _preserve_observation_meta(cut: Any, source: Any) -> None:
    """Restore observation metadata that reprojection drops.

    ``reproject_to`` adopts the *target* WCS wholesale, so every output frame
    would otherwise claim the reference frame's observation time and lose its
    exposure. That would silently break the light curve time axis, the movie
    timestamps and the exposure normalisation applied to differences.
    """
    meta = getattr(cut, "meta", None)
    source_meta = getattr(source, "meta", None)
    if meta is None or not source_meta:
        return
    try:
        lowered = {str(k).strip().lower(): v for k, v in dict(source_meta).items()}
    except Exception:
        return

    for key in _PRESERVED_TIME_KEYS + _PRESERVED_META_KEYS:
        value = lowered.get(key)
        if value is not None:
            meta[key] = value
    for primary, companion in _PRESERVED_META_PAIRS:
        value = lowered.get(primary)
        companion_value = lowered.get(companion)
        if value is not None and companion_value is not None:
            meta[primary] = value
            meta[companion] = companion_value


def _time_text(frame: Any) -> str:
    date = getattr(frame, "date", None)
    if date is None:
        return ""
    try:
        return str(getattr(date, "isot", date))
    except Exception:
        return ""


def _cancelled(cancel_cb: Callable[[], bool] | None) -> bool:
    if cancel_cb is None:
        return False
    try:
        return bool(cancel_cb())
    except Exception:
        return False


def _report(progress_cb: Callable[[int, int], Any] | None, done: int, total: int) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(int(done), int(total))
    except Exception:
        pass
