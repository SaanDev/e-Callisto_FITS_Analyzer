"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Multi-instrument coronagraph composites: SDO and STEREO layered onto SOHO/LASCO.

A CME is never visible in one instrument. The eruption starts on the disk (AIA,
EUVI), crosses the low corona (LASCO C2, COR1), and expands into the outer
corona (LASCO C3, COR2) — each imager blind to the others' domain because of its
occulter. Viewing them separately means reconstructing the event in your head.

This module blends them into one image. Every instrument has a well-defined
field of view in solar radii, so each layer is masked to its own annulus and
alpha-composited over the layer beneath it, widest first. The result is a
continuous view from the photosphere out to 30 R_sun, in which a CME front can
be followed without switching windows.

Two things make the blend non-trivial and are handled here:

* **Brightness.** The disk in EUV and the outer corona in white light differ by
  orders of magnitude. A single normalisation makes one layer invisible, so each
  layer carries its own colormap, scale and percentile clip, and is colour-mapped
  to RGB *before* blending rather than after.

* **Viewpoint.** Layers are reprojected onto the base image's WCS by the caller
  (see :mod:`src.Backend.multiview`). For disk emission the standard
  solar-surface assumption holds. For the optically-thin corona it does not —
  a white-light feature has no unique line-of-sight depth — so coronagraph
  layers default to a spherical-screen assumption and are flagged approximate.
  Cross-observer coronagraph overlays are morphological context, not photometry.

Plain numpy throughout (no Qt, no network), so the geometry and blending unit-test
without downloading anything.

Pixel convention matches :mod:`src.Backend.coronagraph`: images are indexed
``[row, col]`` and ``center`` is ``(col, row)`` of Sun centre.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np

from src.Backend.instrument_profiles import (
    CORONAGRAPH,
    DISK_EUV,
    HELIOSPHERIC,
    MAGNETOGRAPH,
    classify_observable,
)


# Screen assumptions for reprojection (see multiview.reproject_map_to).
SCREEN_SURFACE = "surface"
SCREEN_SPHERICAL = "spherical"
SCREEN_PLANAR = "planar"
SCREEN_AUTO = "auto"

# Screens that place emission somewhere other than the photosphere. These are
# approximations for optically-thin structure and the UI must say so.
APPROXIMATE_SCREENS = (SCREEN_SPHERICAL, SCREEN_PLANAR)


# Nominal field of view per instrument, in solar radii, as (inner, outer).
# Inner is the occulter edge; outer is where the image stops being usable.
# These are stable per detector, which is why the masks are geometric rather
# than detected from pixel values (detection is unreliable on differenced or
# NRGF-filtered data).
INSTRUMENT_FOV_RSUN: dict[tuple[str, str | None], tuple[float, float]] = {
    ("LASCO", "C2"): (2.2, 6.0),
    ("LASCO", "C3"): (3.7, 30.0),
    ("SECCHI", "COR1"): (1.4, 4.0),
    ("SECCHI", "COR2"): (2.5, 15.0),
    ("SECCHI", "EUVI"): (0.0, 1.7),
    ("SECCHI", "HI1"): (12.0, 90.0),
    ("SECCHI", "HI2"): (66.0, 318.0),
    ("AIA", None): (0.0, 1.28),
    ("SUVI", None): (0.0, 1.6),
    ("HMI", None): (0.0, 1.0),
}

_DEFAULT_FOV_RSUN = (0.0, 1.3)


@dataclass(frozen=True)
class LayerSpec:
    """One instrument layer in an overlay stack.

    ``instrument``/``value`` follow the observable-combo userData convention used
    across the Solar Image Analysis window: ``("AIA", wavelength)``,
    ``("HMI", product)``, ``("LASCO", detector)``,
    ``("SECCHI", (spacecraft, detector, wavelength_or_None))``,
    ``("SUVI", wavelength)``.

    ``inner_rsun``/``outer_rsun`` default to the instrument's nominal field of
    view; setting them overrides an unusual header. ``feather_rsun`` softens both
    mask edges so nested layers do not meet at a hard ring.
    """

    instrument: str
    value: Any = None
    label: str = ""
    colormap: str = "inferno"
    scale: str = "log"
    clip_low: float = 1.0
    clip_high: float = 99.5
    opacity: float = 1.0
    gamma: float = 1.0
    inner_rsun: float | None = None
    outer_rsun: float | None = None
    feather_rsun: float = 0.15
    screen: str = SCREEN_AUTO
    enabled: bool = True

    def resolved_fov(self) -> tuple[float, float]:
        """Inner/outer radius in R_sun, falling back to the instrument default."""
        inner_default, outer_default = default_fov_rsun(self.instrument, self.value)
        inner = inner_default if self.inner_rsun is None else float(self.inner_rsun)
        outer = outer_default if self.outer_rsun is None else float(self.outer_rsun)
        return max(0.0, inner), max(inner, outer)

    def resolved_screen(self) -> str:
        """Concrete screen assumption, resolving ``"auto"`` by instrument class."""
        if str(self.screen or SCREEN_AUTO).strip().lower() != SCREEN_AUTO:
            return str(self.screen).strip().lower()
        return default_screen_for(self.instrument, self.value)

    def is_approximate(self) -> bool:
        """True when this layer's reprojection uses a non-surface screen."""
        return self.resolved_screen() in APPROXIMATE_SCREENS


def resolve_detector(instrument: str, value: Any) -> str | None:
    """Detector name for an observable selection, or None when it has none."""
    inst = str(instrument or "").strip().upper()
    if inst == "LASCO":
        return str(value or "").strip().upper() or None
    if inst == "SECCHI":
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return str(value[1] or "").strip().upper() or None
        return None
    return None


def default_fov_rsun(instrument: str, value: Any = None) -> tuple[float, float]:
    """Nominal ``(inner, outer)`` field of view in R_sun for an observable."""
    inst = str(instrument or "").strip().upper()
    detector = resolve_detector(inst, value)
    if (inst, detector) in INSTRUMENT_FOV_RSUN:
        return INSTRUMENT_FOV_RSUN[(inst, detector)]
    if (inst, None) in INSTRUMENT_FOV_RSUN:
        return INSTRUMENT_FOV_RSUN[(inst, None)]
    return _DEFAULT_FOV_RSUN


def default_gamma_for(instrument: str, value: Any = None) -> float:
    """Midtone stretch that makes an instrument's data readable as a layer.

    Solar EUV and white-light coronagraph data are strongly bottom-heavy: for a
    real STEREO/EUVI frame reprojected into a LASCO view, the median pixel sits
    at 13% of its own 1-99.5 percentile range, so a straight mapping paints the
    disk almost black. A 0.5 exponent lifts that median to 36%, which is the
    conventional stretch for solar imagery. Magnetograms are signed and
    genuinely want a linear ramp, so they keep 1.0.
    """
    science_class = classify_observable(instrument, value)
    if science_class in (DISK_EUV, CORONAGRAPH, HELIOSPHERIC):
        return 0.5
    return 1.0


def default_screen_for(instrument: str, value: Any = None) -> str:
    """Screen assumption implied by an observable's science class.

    Disk emission genuinely lies on the photosphere, so those layers reproject
    with the surface assumption. Optically-thin coronal structure does not, and
    is better served by a spherical screen at the observer's distance.
    """
    science_class = classify_observable(instrument, value)
    if science_class in (CORONAGRAPH, HELIOSPHERIC):
        return SCREEN_SPHERICAL
    if science_class in (DISK_EUV, MAGNETOGRAPH):
        return SCREEN_SURFACE
    return SCREEN_SURFACE


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def _meta_get(meta: Any, *keys: str) -> float | None:
    if meta is None:
        return None
    for key in keys:
        try:
            if key in meta:
                value = float(meta[key])
                if np.isfinite(value):
                    return value
        except Exception:
            continue
    return None


def rsun_pixels(frame: Any, data_shape: tuple[int, int] | None = None) -> float:
    """Pixels per solar radius for ``frame``.

    The plate scale and apparent solar radius both come from the header
    (``CDELT1`` and ``RSUN_OBS``), which is the same pairing
    :func:`src.Backend.coronagraph.pixel_radius_to_rsun` inverts. Falls back to
    the map object's own attributes, then to a fraction of the array so a
    composite still renders (approximately) on a header-poor frame.
    """
    meta = getattr(frame, "meta", None)
    cdelt = _meta_get(meta, "cdelt1", "CDELT1")
    rsun_arcsec = _meta_get(meta, "rsun_obs", "RSUN_OBS", "rsun", "RSUN", "solar_r", "SOLAR_R")

    if rsun_arcsec is None:
        try:
            import astropy.units as u

            rsun_arcsec = float(getattr(frame, "rsun_obs").to_value(u.arcsec))
        except Exception:
            rsun_arcsec = None

    if cdelt is None:
        try:
            import astropy.units as u

            scale = getattr(frame, "scale", None)
            axis1 = getattr(scale, "axis1", None) if scale is not None else None
            cdelt = float(axis1.to_value(u.arcsec / u.pix))
        except Exception:
            cdelt = None

    if cdelt and rsun_arcsec and cdelt > 0 and rsun_arcsec > 0:
        return float(rsun_arcsec / cdelt)

    # Last resort: assume the disk spans a fifth of the frame so masks stay sane.
    if data_shape:
        return max(1.0, float(min(data_shape[:2])) / 10.0)
    return 1.0


def _smoothstep(t: np.ndarray) -> np.ndarray:
    """Cosine ramp: 0 for t<=0, 1 for t>=1, smooth in between."""
    clipped = np.clip(t, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * clipped)


def radial_alpha_mask(
    shape: tuple[int, int],
    center: tuple[float, float],
    rsun_px: float,
    *,
    inner_rsun: float = 0.0,
    outer_rsun: float | None = None,
    feather_rsun: float = 0.15,
) -> np.ndarray:
    """Feathered annulus mask in ``[0, 1]`` covering ``inner_rsun``…``outer_rsun``.

    The ramps are centred on the boundaries, so alpha is exactly 0.5 at
    ``inner_rsun`` and at ``outer_rsun`` and reaches 0/1 half a feather width
    either side. A zero ``inner_rsun`` gives a filled disk (no central hole).
    """
    ny, nx = int(shape[0]), int(shape[1])
    if ny <= 0 or nx <= 0:
        return np.zeros((max(ny, 0), max(nx, 0)), dtype=float)

    cx, cy = float(center[0]), float(center[1])
    scale = float(rsun_px) if rsun_px and float(rsun_px) > 0 else 1.0

    yy, xx = np.mgrid[0:ny, 0:nx]
    radius = np.hypot(xx - cx, yy - cy) / scale

    feather = max(float(feather_rsun), 1e-6)
    alpha = np.ones((ny, nx), dtype=float)

    inner = float(inner_rsun or 0.0)
    if inner > 0.0:
        alpha *= _smoothstep((radius - inner) / feather + 0.5)
    if outer_rsun is not None and np.isfinite(outer_rsun):
        alpha *= _smoothstep((float(outer_rsun) - radius) / feather + 0.5)

    return np.clip(alpha, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Layer rendering and blending
# --------------------------------------------------------------------------- #
def layer_rgb(data: Any, spec: LayerSpec, visible: Any = None) -> np.ndarray:
    """Colour-map one layer's scalar data to float RGB in ``[0, 1]``.

    Each layer is mapped with its own scale/clip/colormap before blending, which
    is what keeps an EUV disk and a white-light corona simultaneously visible.

    ``visible`` is an optional boolean mask of the pixels that will survive the
    radial mask. The percentile clip is then computed over those pixels alone,
    which matters a great deal in practice: a reprojected coronagraph layer still
    carries its own occulter and its vignetted outer edge, and letting those
    values set the stretch spends most of the colour range on pixels the blend
    is about to discard, flattening the annulus the user actually looks at.
    """
    from src.Backend.solar_data_analysis import apply_display_scale, array_to_rgb_uint8

    arr = np.asarray(data)
    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        rgb = np.asarray(arr[..., :3], dtype=float)
        return np.clip(rgb / 255.0 if np.nanmax(rgb) > 1.0 else rgb, 0.0, 1.0)

    scaled = apply_display_scale(arr, spec.scale)
    if visible is not None:
        mask = np.asarray(visible, dtype=bool)
        if mask.shape == scaled.shape and mask.any():
            # NaN is excluded by the percentile helpers, so blanking the hidden
            # pixels restricts the stretch to the visible band without changing
            # where anything is drawn.
            scaled = np.where(mask, scaled, np.nan)

    low = min(float(spec.clip_low), float(spec.clip_high) - 0.1)
    high = max(float(spec.clip_high), low + 0.1)
    rendered = array_to_rgb_uint8(
        scaled,
        percentile_low=low,
        percentile_high=high,
        colormap_name=spec.colormap,
        gamma=spec.gamma,
    )
    return np.asarray(rendered, dtype=float) / 255.0


def layer_alpha(
    data: Any,
    spec: LayerSpec,
    *,
    center: tuple[float, float],
    rsun_px: float,
) -> np.ndarray:
    """Per-pixel alpha for a layer: annulus mask x opacity, zeroed where blank.

    Reprojection leaves NaN outside the source footprint; those pixels must stay
    fully transparent or they punch holes through the layer beneath.
    """
    arr = np.asarray(data, dtype=float)
    plane = np.nanmean(arr[..., :3], axis=2) if arr.ndim == 3 else arr
    inner, outer = spec.resolved_fov()

    alpha = radial_alpha_mask(
        plane.shape,
        center,
        rsun_px,
        inner_rsun=inner,
        outer_rsun=outer,
        feather_rsun=spec.feather_rsun,
    )
    alpha = alpha * float(np.clip(spec.opacity, 0.0, 1.0))
    return np.where(np.isfinite(plane), alpha, 0.0)


def order_layers(layers: Sequence[tuple[LayerSpec, Any]]) -> list[tuple[LayerSpec, Any]]:
    """Sort layers back-to-front: widest field of view painted first.

    Ordering by outer radius descending gives the natural nesting (C3, then C2,
    then the disk imager) without the user having to arrange anything.
    """
    return sorted(layers, key=lambda item: item[0].resolved_fov()[1], reverse=True)


def composite_frame(
    base_data: Any,
    base_spec: LayerSpec,
    layers: Sequence[tuple[LayerSpec, Any]],
    *,
    center: tuple[float, float],
    rsun_px: float,
) -> np.ndarray:
    """Blend ``layers`` over ``base_data``, returning an ``(H, W, 3)`` uint8 image.

    ``base_data`` is painted in full as the background; every other layer is
    masked to its own annulus and composited with the standard ``over`` operator,
    widest field of view first. All arrays must already share the base grid —
    the caller reprojects them (see :mod:`src.Backend.multiview`).
    """
    out = layer_rgb(base_data, base_spec)
    base_plane = np.asarray(base_data, dtype=float)
    if base_plane.ndim == 3:
        base_plane = np.nanmean(base_plane[..., :3], axis=2)
    # Blank base pixels render as black rather than leaking the colormap floor.
    out = np.where(np.isfinite(base_plane)[..., None], out, 0.0)

    for spec, data in order_layers(layers):
        if not spec.enabled or data is None:
            continue
        arr = np.asarray(data)
        if arr.shape[:2] != out.shape[:2]:
            # Reprojection forces the base grid, so this means an upstream bug;
            # skip rather than crash a long composite build.
            continue
        alpha = layer_alpha(arr, spec, center=center, rsun_px=rsun_px)
        rgb = layer_rgb(arr, spec, visible=alpha > 0.0)
        alpha = alpha[..., None]
        out = rgb * alpha + out * (1.0 - alpha)

    return np.clip(out * 255.0, 0.0, 255.0).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Time matching
# --------------------------------------------------------------------------- #
def match_layer_frames(
    base_times: Sequence[datetime | None],
    layer_times: Sequence[datetime | None],
    *,
    max_gap_seconds: float | None = None,
) -> list[int | None]:
    """Index of the nearest-in-time layer frame for each base frame.

    Instruments cadence independently (AIA seconds, LASCO C3 tens of minutes),
    so each base frame takes whichever layer frame is closest. Entries are
    ``None`` where nothing falls within ``max_gap_seconds``, which is how a gap
    in the overlay archive is reported instead of silently reusing a stale image.
    """
    dated = [(t, i) for i, t in enumerate(layer_times) if isinstance(t, datetime)]
    if not dated:
        return [None] * len(base_times)
    dated.sort(key=lambda item: item[0])
    sorted_times = [item[0] for item in dated]
    sorted_index = [item[1] for item in dated]

    matches: list[int | None] = []
    for when in base_times:
        if not isinstance(when, datetime):
            matches.append(None)
            continue
        pos = bisect_left(sorted_times, when)
        best: int | None = None
        best_gap = float("inf")
        for candidate in (pos - 1, pos):
            if 0 <= candidate < len(sorted_times):
                gap = abs((sorted_times[candidate] - when).total_seconds())
                if gap < best_gap:
                    best_gap, best = gap, sorted_index[candidate]
        if best is not None and max_gap_seconds is not None and best_gap > float(max_gap_seconds):
            best = None
        matches.append(best)
    return matches


def layer_config_hash(specs: Iterable[LayerSpec], *, extra: str = "") -> str:
    """Stable digest of a layer stack, used to invalidate cached composites."""
    payload = [asdict(spec) for spec in specs]
    blob = json.dumps({"layers": payload, "extra": str(extra)}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def composite_nickname(base_label: str, specs: Sequence[LayerSpec]) -> str:
    """Human-readable name for a composite, e.g. ``LASCO C2 + AIA 193``."""
    parts = [str(base_label or "Base").strip()]
    parts.extend(str(spec.label or spec.instrument).strip() for spec in specs if spec.enabled)
    return " + ".join(part for part in parts if part)
