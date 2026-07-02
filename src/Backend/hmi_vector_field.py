"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

SDO/HMI vector magnetic field (hmi.B_720s) support.

The HMI vector pipeline distributes the full-disk field as three FITS
segments per 720 s record — ``field`` (|B|, Gauss), ``inclination`` (angle
from the line of sight, degrees) and ``azimuth`` (angle of the transverse
component, degrees, counter-clockwise from CCD +y) — plus a ``disambig``
segment that resolves the 180° azimuth ambiguity. This module groups those
segment files into time steps, converts them to Cartesian CCD-frame
components (Bx, By, Bz), and turns a frame into renderer-agnostic overlay
geometry (quiver arrows, streamlines, |B| magnitude layer) in the same
arcsec coordinate system the SDO Analyzer canvases use, so the overlay
aligns with the displayed HMI image.

Everything is plain numpy with injectable loaders so it can be unit tested
without SunPy or network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Sequence

import numpy as np


HMI_VECTOR_SEGMENT_KINDS = ("field", "inclination", "azimuth", "disambig")
# Recommended disambiguation bit for full-disk data (radial-acute solution);
# strong-field pixels are annealed so all three bits agree there.
DEFAULT_DISAMBIG_METHOD = 2
# Components are stored block-averaged so a loaded frame stays ~12 MB instead
# of ~200 MB (4096² float32 × 3). Overlays never need more than ~1k² samples.
DEFAULT_MAX_DIMENSION = 1024


@dataclass(frozen=True)
class HmiVectorFrame:
    """One time step of the vector field, in CCD/array orientation.

    ``bx``/``by``/``bz`` are Gauss on a (possibly block-averaged) grid;
    ``axis_transform`` maps that grid's pixel indices to arcsec exactly like
    the analysis window maps its displayed frames, so geometry built from
    this frame lands on the displayed HMI image.
    """

    time: datetime | None
    bx: np.ndarray
    by: np.ndarray
    bz: np.ndarray
    axis_transform: dict[str, float]
    meta: dict[str, Any]
    source_paths: tuple[str, ...] = ()
    downsample_factor: int = 1

    @property
    def transverse(self) -> np.ndarray:
        return np.hypot(self.bx, self.by)


@dataclass(frozen=True)
class VectorOverlayOptions:
    show_arrows: bool = True
    show_streamlines: bool = False
    show_magnitude: bool = False
    grid_step_px: int = 64            # arrow spacing, in original detector pixels
    min_transverse_gauss: float = 200.0
    max_arrows: int = 1200
    arrow_scale: float = 1.5          # arrow length in units of the grid step
    max_streamlines: int = 60
    streamline_max_steps: int = 400
    magnitude_max_gauss: float = 1500.0


@dataclass(frozen=True)
class VectorOverlayGeometry:
    """Renderer-agnostic overlay in arcsec coordinates.

    Line sets are NaN-separated polylines (draw with connect='finite').
    Arrows are split by vertical-field polarity so the canvases can colour
    them red (+Bz, toward the observer) and blue (−Bz).
    """

    arrows_pos_x: np.ndarray
    arrows_pos_y: np.ndarray
    arrows_neg_x: np.ndarray
    arrows_neg_y: np.ndarray
    stream_x: np.ndarray
    stream_y: np.ndarray
    magnitude_rgba: np.ndarray | None
    magnitude_rect: tuple[float, float, float, float] | None
    arrow_count: int = 0
    streamline_count: int = 0

    def is_empty(self) -> bool:
        return (
            self.arrow_count == 0
            and self.streamline_count == 0
            and self.magnitude_rgba is None
        )


class HmiVectorError(RuntimeError):
    """Raised when vector-field files cannot be grouped or loaded."""


_SEGMENT_TOKEN_RE = re.compile(r"(?<![a-z])(field|inclination|azimuth|disambig)(?![a-z])")


def segment_kind_from_filename(name: str) -> str | None:
    """Identify which hmi.B segment a filename holds (field/inclination/...).

    JSOC names the files after the segment both in as-is exports
    ('.../field.fits') and staged FITS exports
    ('hmi.b_720s.20240514_160000_TAI.field.fits'), so the filename is the
    reliable discriminator — the segment headers themselves carry no
    distinguishing CONTENT keyword.
    """
    text = str(name or "").lower()
    match = _SEGMENT_TOKEN_RE.search(text)
    return match.group(1) if match else None


def apply_disambiguation(
    azimuth_deg: np.ndarray,
    disambig: np.ndarray | None,
    *,
    method: int = DEFAULT_DISAMBIG_METHOD,
) -> np.ndarray:
    """Resolve the 180° azimuth ambiguity using the disambig segment.

    Matches the reference SSW routine ``hmi_disambig.pro``: add 180° where
    bit ``method`` of the disambig value is set (bit 2 = radial acute is the
    JSOC-recommended solution for full-disk data).
    """
    azimuth = np.asarray(azimuth_deg, dtype=np.float32)
    if disambig is None:
        return azimuth
    flags = np.asarray(disambig, dtype=float)
    if flags.shape != azimuth.shape:
        raise HmiVectorError(
            f"Disambig shape {flags.shape} does not match azimuth shape {azimuth.shape}."
        )
    bits = np.zeros(flags.shape, dtype=np.float32)
    finite = np.isfinite(flags)
    bits[finite] = np.mod(np.floor(flags[finite] / float(2 ** int(method))), 2.0).astype(np.float32)
    return azimuth + bits * 180.0


def compute_field_components(
    field: np.ndarray,
    inclination_deg: np.ndarray,
    azimuth_deg: np.ndarray,
    disambig: np.ndarray | None = None,
    *,
    disambig_method: int = DEFAULT_DISAMBIG_METHOD,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert (|B|, inclination, azimuth) to CCD-frame (Bx, By, Bz), Gauss.

    Inclination is measured from the line of sight (0° = toward the
    observer), so Bz = |B| cos γ is the LOS component whose sign gives the
    magnetogram polarity. Azimuth is counter-clockwise from the CCD +y axis,
    hence the standard Bx = −B⊥ sin ψ, By = +B⊥ cos ψ.
    """
    b = np.asarray(field, dtype=np.float32)
    incl = np.deg2rad(np.asarray(inclination_deg, dtype=np.float32))
    azi = np.deg2rad(apply_disambiguation(azimuth_deg, disambig, method=disambig_method))
    if b.shape != incl.shape or b.shape != azi.shape:
        raise HmiVectorError(
            f"Segment shapes differ: field {b.shape}, inclination {incl.shape}, azimuth {azi.shape}."
        )
    bz = b * np.cos(incl)
    bt = b * np.sin(incl)
    bx = -bt * np.sin(azi)
    by = bt * np.cos(azi)
    return bx.astype(np.float32), by.astype(np.float32), bz.astype(np.float32)


def axis_transform_from_meta(meta: dict[str, Any] | None, shape: Sequence[int]) -> dict[str, float]:
    """Linear pixel→arcsec transform from FITS WCS keywords.

    Mirrors the analysis window's metadata fallback (CRPIX is 1-based in
    FITS, hence the −1) so the overlay and the displayed image share the
    same coordinate mapping. Rotation (CROTA2) is deliberately ignored, as
    it is for the displayed image — both stay in stored-array orientation.
    """
    ny = int(shape[0]) if len(shape) >= 1 else 1
    nx = int(shape[1]) if len(shape) >= 2 else 1
    out = {
        "x_ref_pix": (max(nx, 1) - 1) / 2.0,
        "y_ref_pix": (max(ny, 1) - 1) / 2.0,
        "x_scale_arcsec_per_pix": 1.0,
        "y_scale_arcsec_per_pix": 1.0,
        "x_ref_arcsec": 0.0,
        "y_ref_arcsec": 0.0,
    }
    if not meta:
        return out
    lowered = {}
    try:
        lowered = {str(k).strip().lower(): v for k, v in dict(meta).items()}
    except Exception:
        return out

    def _get(key: str) -> float | None:
        value = lowered.get(key)
        if value is None:
            return None
        try:
            number = float(getattr(value, "value", value))
        except Exception:
            return None
        return number if np.isfinite(number) else None

    cdelt1 = _get("cdelt1")
    cdelt2 = _get("cdelt2")
    crpix1 = _get("crpix1")
    crpix2 = _get("crpix2")
    crval1 = _get("crval1")
    crval2 = _get("crval2")
    if cdelt1 is not None:
        out["x_scale_arcsec_per_pix"] = cdelt1
    if cdelt2 is not None:
        out["y_scale_arcsec_per_pix"] = cdelt2
    if crpix1 is not None:
        out["x_ref_pix"] = crpix1 - 1.0
    if crpix2 is not None:
        out["y_ref_pix"] = crpix2 - 1.0
    if crval1 is not None:
        out["x_ref_arcsec"] = crval1
    if crval2 is not None:
        out["y_ref_arcsec"] = crval2
    return out


def _downsampled_transform(transform: dict[str, float], factor: int) -> dict[str, float]:
    """Adjust a pixel→arcsec transform for block-averaged data.

    Block ``i`` covers original pixels [i·f, (i+1)·f); its centre is at
    i·f + (f−1)/2, which the returned transform maps to the same arcsec
    position, so the frame extent is preserved exactly.
    """
    f = max(1, int(factor))
    if f == 1:
        return dict(transform)
    out = dict(transform)
    out["x_scale_arcsec_per_pix"] = float(transform["x_scale_arcsec_per_pix"]) * f
    out["y_scale_arcsec_per_pix"] = float(transform["y_scale_arcsec_per_pix"]) * f
    out["x_ref_pix"] = (float(transform["x_ref_pix"]) - (f - 1) / 2.0) / f
    out["y_ref_pix"] = (float(transform["y_ref_pix"]) - (f - 1) / 2.0) / f
    return out


def block_reduce_mean(arr: np.ndarray, factor: int) -> np.ndarray:
    """NaN-aware block mean over ``factor``×``factor`` cells (edges trimmed)."""
    a = np.asarray(arr, dtype=np.float32)
    f = max(1, int(factor))
    if f == 1:
        return a
    ny = (a.shape[0] // f) * f
    nx = (a.shape[1] // f) * f
    if ny == 0 or nx == 0:
        return a
    trimmed = a[:ny, :nx]
    blocks = trimmed.reshape(ny // f, f, nx // f, f)
    with np.errstate(invalid="ignore"):
        return np.nanmean(blocks, axis=(1, 3)).astype(np.float32)


def _coerce_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    to_datetime = getattr(value, "to_datetime", None)
    if callable(to_datetime):
        try:
            result = to_datetime()
            if isinstance(result, datetime):
                return result.replace(tzinfo=None)
        except Exception:
            pass
    text = str(value).strip()
    if not text:
        return None
    # JSOC T_REC style: 2024.05.14_16:00:00_TAI
    text = text.replace("_TAI", "").replace("_UTC", "").replace("Z", "")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d_%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except Exception:
        return None


def parse_trec_time(value: Any) -> datetime | None:
    """Parse a JSOC T_REC-style timestamp ('2024.05.14_16:00:00_TAI') or any
    ISO-ish time text into a naive datetime (None when unparseable)."""
    return _coerce_time(value)


def _frame_time_from_meta(meta: dict[str, Any] | None) -> datetime | None:
    if not meta:
        return None
    try:
        lowered = {str(k).strip().lower(): v for k, v in dict(meta).items()}
    except Exception:
        return None
    for key in ("t_rec", "t_obs", "date-obs", "date_obs", "date"):
        parsed = _coerce_time(lowered.get(key))
        if parsed is not None:
            return parsed
    return None


def _default_segment_loader(path: str) -> tuple[np.ndarray, dict[str, Any]]:
    """Load one segment FITS to (data, header dict) via sunpy, else astropy.

    sunpy handles the Rice-compressed JSOC files and header quirks; the
    astropy fallback keeps local loading working without sunpy installed.
    """
    try:
        from sunpy.map import Map

        loaded = Map(path)
        return np.asarray(loaded.data), dict(loaded.meta or {})
    except Exception:
        pass
    try:
        from astropy.io import fits
    except Exception as exc:  # pragma: no cover - needs neither sunpy nor astropy
        raise HmiVectorError(f"Loading HMI vector FITS requires sunpy or astropy: {exc}") from exc
    with fits.open(path) as hdul:
        for hdu in hdul:
            data = getattr(hdu, "data", None)
            if data is not None and np.asarray(data).ndim >= 2:
                return np.asarray(data), dict(hdu.header)
    raise HmiVectorError(f"No 2-D image HDU found in {Path(path).name}.")


def load_vector_frames(
    paths: Iterable[str | Path],
    *,
    segment_loader: Callable[[str], tuple[np.ndarray, dict[str, Any]]] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    disambig_method: int = DEFAULT_DISAMBIG_METHOD,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
) -> list[HmiVectorFrame]:
    """Group hmi.B_720s segment files by time step and build vector frames.

    Files are matched to segments by filename token and grouped by the
    observation time in their headers. A group needs at least field +
    inclination + azimuth; disambig is applied when present. Components are
    computed at full resolution and then block-averaged so each frame stays
    small enough to keep several time steps in memory.
    """
    normalized = [str(Path(p).expanduser()) for p in paths if str(p).strip()]
    if not normalized:
        raise HmiVectorError("Select at least one HMI vector FITS file.")
    if segment_loader is None:
        segment_loader = _default_segment_loader

    groups: dict[str, dict[str, tuple[np.ndarray, dict[str, Any], str]]] = {}
    group_times: dict[str, datetime | None] = {}
    skipped: list[str] = []
    total = len(normalized)
    for index, path in enumerate(normalized):
        if cancel_cb is not None:
            try:
                if bool(cancel_cb()):
                    return []
            except Exception:
                pass
        kind = segment_kind_from_filename(Path(path).name)
        if kind is None:
            skipped.append(f"{Path(path).name}: not a field/inclination/azimuth/disambig segment")
        else:
            try:
                data, meta = segment_loader(path)
            except Exception as exc:  # noqa: BLE001 - skip one bad file, keep the rest
                skipped.append(f"{Path(path).name}: {exc}")
                data, meta = None, None
            if data is not None:
                when = _frame_time_from_meta(meta)
                if when is not None:
                    key = when.replace(microsecond=0).isoformat()
                else:
                    # No usable header time: fall back to the filename with the
                    # segment token removed so same-record files still group.
                    key = _SEGMENT_TOKEN_RE.sub("", Path(path).name.lower())
                group = groups.setdefault(key, {})
                group[kind] = (np.asarray(data), dict(meta or {}), path)
                if group_times.get(key) is None:
                    group_times[key] = when
        if progress_cb is not None:
            try:
                progress_cb(index + 1, total)
            except Exception:
                pass

    frames: list[HmiVectorFrame] = []
    incomplete: list[str] = []
    for key, group in groups.items():
        missing = [k for k in ("field", "inclination", "azimuth") if k not in group]
        if missing:
            incomplete.append(f"{key}: missing {', '.join(missing)}")
            continue
        field, meta, path_field = group["field"]
        incl = group["inclination"][0]
        azim = group["azimuth"][0]
        disambig = group["disambig"][0] if "disambig" in group else None
        try:
            bx, by, bz = compute_field_components(
                field, incl, azim, disambig, disambig_method=disambig_method
            )
        except HmiVectorError as exc:
            incomplete.append(f"{key}: {exc}")
            continue

        transform = axis_transform_from_meta(meta, bx.shape)
        factor = 1
        largest = max(int(bx.shape[0]), int(bx.shape[1]))
        if max_dimension and largest > int(max_dimension):
            factor = int(np.ceil(largest / float(max_dimension)))
            bx = block_reduce_mean(bx, factor)
            by = block_reduce_mean(by, factor)
            bz = block_reduce_mean(bz, factor)
            transform = _downsampled_transform(transform, factor)

        frames.append(
            HmiVectorFrame(
                time=group_times.get(key),
                bx=bx,
                by=by,
                bz=bz,
                axis_transform=transform,
                meta=meta,
                source_paths=tuple(entry[2] for entry in group.values()),
                downsample_factor=factor,
            )
        )

    if not frames:
        detail = "\n".join((skipped + incomplete)[:6])
        raise HmiVectorError(
            "No complete HMI vector time step could be assembled.\n"
            "Each time step needs the field, inclination and azimuth segments "
            "of hmi.B_720s (disambig optional)." + (f"\n{detail}" if detail else "")
        )

    frames.sort(key=lambda fr: (fr.time is None, fr.time or datetime.min))
    return frames


def nearest_vector_frame(
    frames: Sequence[HmiVectorFrame],
    target: datetime | None,
    *,
    max_delta_seconds: float | None = None,
) -> HmiVectorFrame | None:
    """The frame whose time is closest to ``target`` (first frame if no times)."""
    candidates = list(frames or [])
    if not candidates:
        return None
    if target is None:
        return candidates[0]
    best: HmiVectorFrame | None = None
    best_delta: float | None = None
    for frame in candidates:
        if frame.time is None:
            continue
        delta = abs((frame.time - target.replace(tzinfo=None)).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = frame, delta
    if best is None:
        return candidates[0]
    if max_delta_seconds is not None and best_delta is not None and best_delta > float(max_delta_seconds):
        return None
    return best


def vector_display_frame(frame: HmiVectorFrame) -> Any:
    """A SunPy-map-like image frame of the vertical field Bz, for display.

    Downloading hmi.B_720s gives angles/strength segments that are not
    directly displayable; this wraps the derived Bz component as a normal
    HMI-magnetogram-style frame (duck-typed like the analyzer's other map
    frames) so the vector data can be plotted on its own — the overlay then
    draws on top of it. The WCS keywords are rebuilt from the frame's stored
    (possibly block-averaged) grid so overlay and image share coordinates.
    """
    from src.Backend.solar_data_analysis import AiaArrayMap

    wrapper = AiaArrayMap(frame.bz, None, nickname="HMI vector Bz")
    tx = frame.axis_transform
    iso = frame.time.isoformat() if frame.time is not None else ""
    meta: dict[str, Any] = {
        "telescop": "SDO/HMI",
        "instrume": "HMI_VECTOR",
        # CONTENT drives the analyzer's HMI product/colormap resolution.
        "content": "MAGNETOGRAM",
        "bunit": "Gauss",
        "cdelt1": float(tx["x_scale_arcsec_per_pix"]),
        "cdelt2": float(tx["y_scale_arcsec_per_pix"]),
        "crpix1": float(tx["x_ref_pix"]) + 1.0,  # FITS CRPIX is 1-based
        "crpix2": float(tx["y_ref_pix"]) + 1.0,
        "crval1": float(tx["x_ref_arcsec"]),
        "crval2": float(tx["y_ref_arcsec"]),
    }
    if iso:
        meta["date-obs"] = iso
    if frame.meta:
        try:
            lowered = {str(k).strip().lower(): v for k, v in dict(frame.meta).items()}
            if "rsun_obs" in lowered:
                meta["rsun_obs"] = lowered["rsun_obs"]
        except Exception:
            pass
    wrapper.meta = meta
    wrapper.observatory = "SDO"
    wrapper.instrument = "HMI"
    wrapper.date = iso
    return wrapper


def _pixels_to_arcsec(
    x_pix: np.ndarray, y_pix: np.ndarray, transform: dict[str, float]
) -> tuple[np.ndarray, np.ndarray]:
    x_arc = float(transform["x_ref_arcsec"]) + (
        np.asarray(x_pix, dtype=float) - float(transform["x_ref_pix"])
    ) * float(transform["x_scale_arcsec_per_pix"])
    y_arc = float(transform["y_ref_arcsec"]) + (
        np.asarray(y_pix, dtype=float) - float(transform["y_ref_pix"])
    ) * float(transform["y_scale_arcsec_per_pix"])
    return x_arc, y_arc


def _frame_rect_arcsec(shape: Sequence[int], transform: dict[str, float]) -> tuple[float, float, float, float]:
    """(x0, y0, width, height) of the full grid in arcsec — the same formula
    the canvases use to place the image, so the layers coincide exactly."""
    ny = int(shape[0])
    nx = int(shape[1])
    x_scale = float(transform["x_scale_arcsec_per_pix"])
    y_scale = float(transform["y_scale_arcsec_per_pix"])
    x0 = float(transform["x_ref_arcsec"]) - (float(transform["x_ref_pix"]) + 0.5) * x_scale
    y0 = float(transform["y_ref_arcsec"]) - (float(transform["y_ref_pix"]) + 0.5) * y_scale
    return x0, y0, nx * x_scale, ny * y_scale


def _sample_arrow_grid(
    frame: HmiVectorFrame, step_cells: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Block-mean the components onto the arrow grid; returns grid-centre
    pixel coordinates (in the frame's stored grid) plus (bx, by, bz)."""
    step = max(1, int(step_cells))
    bx = block_reduce_mean(frame.bx, step)
    by = block_reduce_mean(frame.by, step)
    bz = block_reduce_mean(frame.bz, step)
    ny, nx = bx.shape
    centres_x = (np.arange(nx, dtype=float) * step) + (step - 1) / 2.0
    centres_y = (np.arange(ny, dtype=float) * step) + (step - 1) / 2.0
    gx, gy = np.meshgrid(centres_x, centres_y)
    return gx, gy, bx, by, bz


def _arrow_polylines(
    x0: np.ndarray,
    y0: np.ndarray,
    dx: np.ndarray,
    dy: np.ndarray,
    lengths: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Build NaN-separated shaft+head polylines for unit directions (dx, dy)."""
    n = int(x0.size)
    if n == 0:
        empty = np.asarray([], dtype=float)
        return empty, empty
    tip_x = x0 + dx * lengths
    tip_y = y0 + dy * lengths
    head = 0.35 * lengths
    cos_a = np.cos(np.deg2rad(155.0))
    sin_a = np.sin(np.deg2rad(155.0))
    left_x = tip_x + head * (dx * cos_a - dy * sin_a)
    left_y = tip_y + head * (dx * sin_a + dy * cos_a)
    right_x = tip_x + head * (dx * cos_a + dy * sin_a)
    right_y = tip_y + head * (-dx * sin_a + dy * cos_a)

    nan = np.full(n, np.nan)
    xs = np.column_stack([x0, tip_x, nan, left_x, tip_x, right_x, nan]).ravel()
    ys = np.column_stack([y0, tip_y, nan, left_y, tip_y, right_y, nan]).ravel()
    return xs, ys


_MAG_COLOR_STOPS = np.asarray([0.0, 0.4, 0.75, 1.0])
_MAG_COLORS = np.asarray(
    [(30, 8, 60), (140, 40, 130), (235, 120, 40), (255, 235, 120)], dtype=float
)


def _magnitude_rgba(
    frame: HmiVectorFrame, options: VectorOverlayOptions
) -> np.ndarray | None:
    """Semi-transparent |B| layer: colour ramps with strength, alpha rises
    from the arrow threshold so quiet-sun pixels stay fully transparent."""
    magnitude = np.sqrt(frame.bx**2 + frame.by**2 + frame.bz**2)
    finite = np.isfinite(magnitude)
    if not finite.any():
        return None
    cap = max(50.0, float(options.magnitude_max_gauss))
    onset = min(float(options.min_transverse_gauss), cap * 0.9)

    norm = np.zeros(magnitude.shape, dtype=float)
    norm[finite] = np.clip(magnitude[finite] / cap, 0.0, 1.0)
    rgba = np.zeros((*magnitude.shape, 4), dtype=np.uint8)
    flat = norm.ravel()
    for channel in range(3):
        rgba[..., channel] = np.interp(
            flat, _MAG_COLOR_STOPS, _MAG_COLORS[:, channel]
        ).reshape(magnitude.shape).astype(np.uint8)
    alpha = np.zeros(magnitude.shape, dtype=float)
    span = max(1.0, cap - onset)
    alpha[finite] = np.clip((magnitude[finite] - onset) / span, 0.0, 1.0)
    rgba[..., 3] = (np.power(alpha, 0.7) * 200.0).astype(np.uint8)
    if int(rgba[..., 3].max()) == 0:
        return None
    return rgba


def _trace_streamlines(
    bx: np.ndarray,
    by: np.ndarray,
    bt: np.ndarray,
    *,
    step_cells: int,
    threshold: float,
    max_lines: int,
    max_steps: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Integrate field-direction streamlines through the (downsampled) grid.

    Seeds are the strongest transverse-field cells, greedily thinned so lines
    do not bunch up. Integration is midpoint (RK2) on the *unit* direction
    field, so step size is uniform and weak/strong regions trace equally.
    """
    ny, nx = bt.shape
    finite = np.isfinite(bt)
    candidates = np.argwhere(finite & (bt >= threshold))
    if candidates.size == 0:
        return []
    strengths = bt[candidates[:, 0], candidates[:, 1]]
    order = np.argsort(strengths)[::-1]
    seeds: list[tuple[float, float]] = []
    min_sep = 2.0
    for idx in order:
        row, col = float(candidates[idx][0]), float(candidates[idx][1])
        if any((row - r) ** 2 + (col - c) ** 2 < min_sep**2 for r, c in seeds):
            continue
        seeds.append((row, col))
        if len(seeds) >= int(max_lines):
            break

    bx_safe = np.nan_to_num(bx, nan=0.0)
    by_safe = np.nan_to_num(by, nan=0.0)
    bt_safe = np.nan_to_num(bt, nan=0.0)

    def _direction(row: float, col: float) -> tuple[float, float]:
        r0 = int(np.clip(np.floor(row), 0, ny - 2)) if ny > 1 else 0
        c0 = int(np.clip(np.floor(col), 0, nx - 2)) if nx > 1 else 0
        fr = float(np.clip(row - r0, 0.0, 1.0))
        fc = float(np.clip(col - c0, 0.0, 1.0))

        def _bilinear(grid: np.ndarray) -> float:
            r1 = min(r0 + 1, ny - 1)
            c1 = min(c0 + 1, nx - 1)
            top = grid[r0, c0] * (1 - fc) + grid[r0, c1] * fc
            bottom = grid[r1, c0] * (1 - fc) + grid[r1, c1] * fc
            return float(top * (1 - fr) + bottom * fr)

        vx = _bilinear(bx_safe)
        vy = _bilinear(by_safe)
        norm = float(np.hypot(vx, vy))
        if norm <= 0:
            return 0.0, 0.0
        return vx / norm, vy / norm

    stop_threshold = max(1.0, float(threshold) * 0.3)
    h = 0.5  # integration step, in grid cells
    lines: list[tuple[np.ndarray, np.ndarray]] = []
    for seed_row, seed_col in seeds:
        path_cols: list[float] = []
        path_rows: list[float] = []
        for direction in (1.0, -1.0):
            row, col = seed_row, seed_col
            segment_cols: list[float] = []
            segment_rows: list[float] = []
            for _ in range(int(max_steps) // 2):
                if not (0.0 <= row <= ny - 1 and 0.0 <= col <= nx - 1):
                    break
                r_idx = int(np.clip(round(row), 0, ny - 1))
                c_idx = int(np.clip(round(col), 0, nx - 1))
                if bt_safe[r_idx, c_idx] < stop_threshold:
                    break
                segment_rows.append(row)
                segment_cols.append(col)
                ux, uy = _direction(row, col)
                if ux == 0.0 and uy == 0.0:
                    break
                mid_row = row + direction * h * 0.5 * uy
                mid_col = col + direction * h * 0.5 * ux
                ux2, uy2 = _direction(mid_row, mid_col)
                if ux2 == 0.0 and uy2 == 0.0:
                    break
                row = row + direction * h * uy2
                col = col + direction * h * ux2
            if direction > 0:
                path_rows = segment_rows
                path_cols = segment_cols
            else:
                path_rows = list(reversed(segment_rows[1:])) + path_rows
                path_cols = list(reversed(segment_cols[1:])) + path_cols
        if len(path_rows) >= 3:
            rows_arr = np.asarray(path_rows, dtype=float)
            cols_arr = np.asarray(path_cols, dtype=float)
            # grid indices -> stored-grid pixel coordinates
            px = cols_arr * step_cells + (step_cells - 1) / 2.0
            py = rows_arr * step_cells + (step_cells - 1) / 2.0
            lines.append((px, py))
    return lines


def build_overlay_geometry(
    frame: HmiVectorFrame, options: VectorOverlayOptions | None = None
) -> VectorOverlayGeometry:
    """Turn a vector frame into arrows / streamlines / magnitude geometry."""
    options = options or VectorOverlayOptions()
    transform = dict(frame.axis_transform)
    empty = np.asarray([], dtype=float)
    arrows_pos = (empty, empty)
    arrows_neg = (empty, empty)
    stream = (empty, empty)
    arrow_count = 0
    streamline_count = 0
    magnitude_rgba = None
    magnitude_rect = None

    # Arrow spacing arrives in original detector pixels; the stored grid may
    # already be block-averaged, so convert to stored-grid cells (clamped so a
    # spacing wider than the frame still yields one valid cell).
    step_cells = max(1, int(round(float(options.grid_step_px) / max(1, frame.downsample_factor))))
    step_cells = min(step_cells, max(1, int(frame.bx.shape[0])), max(1, int(frame.bx.shape[1])))
    gx, gy, bx_g, by_g, bz_g = _sample_arrow_grid(frame, step_cells)
    bt_g = np.hypot(bx_g, by_g)
    threshold = max(0.0, float(options.min_transverse_gauss))

    if options.show_arrows:
        mask = np.isfinite(bt_g) & (bt_g >= threshold)
        xs = gx[mask]
        ys = gy[mask]
        bxs = bx_g[mask]
        bys = by_g[mask]
        bzs = bz_g[mask]
        bts = bt_g[mask]
        if xs.size > int(options.max_arrows) > 0:
            keep = np.argsort(bts)[::-1][: int(options.max_arrows)]
            xs, ys, bxs, bys, bzs, bts = (
                xs[keep], ys[keep], bxs[keep], bys[keep], bzs[keep], bts[keep]
            )
        if xs.size:
            unit_x = bxs / bts
            unit_y = bys / bts
            reference = float(np.nanpercentile(bts, 95.0)) or 1.0
            scale = np.clip(bts / max(reference, 1e-6), 0.35, 1.0)
            lengths = float(step_cells) * float(options.arrow_scale) * scale
            positive = bzs >= 0
            pos_lines = _arrow_polylines(
                xs[positive], ys[positive], unit_x[positive], unit_y[positive], lengths[positive]
            )
            neg_lines = _arrow_polylines(
                xs[~positive], ys[~positive], unit_x[~positive], unit_y[~positive], lengths[~positive]
            )
            arrows_pos = _pixels_to_arcsec(pos_lines[0], pos_lines[1], transform)
            arrows_neg = _pixels_to_arcsec(neg_lines[0], neg_lines[1], transform)
            arrow_count = int(xs.size)

    if options.show_streamlines:
        lines = _trace_streamlines(
            bx_g,
            by_g,
            bt_g,
            step_cells=step_cells,
            threshold=threshold,
            max_lines=int(options.max_streamlines),
            max_steps=int(options.streamline_max_steps),
        )
        if lines:
            xs_parts: list[np.ndarray] = []
            ys_parts: list[np.ndarray] = []
            for px, py in lines:
                x_arc, y_arc = _pixels_to_arcsec(px, py, transform)
                xs_parts.extend([x_arc, np.asarray([np.nan])])
                ys_parts.extend([y_arc, np.asarray([np.nan])])
            stream = (np.concatenate(xs_parts), np.concatenate(ys_parts))
            streamline_count = len(lines)

    if options.show_magnitude:
        magnitude_rgba = _magnitude_rgba(frame, options)
        if magnitude_rgba is not None:
            magnitude_rect = _frame_rect_arcsec(frame.bx.shape, transform)

    return VectorOverlayGeometry(
        arrows_pos_x=arrows_pos[0],
        arrows_pos_y=arrows_pos[1],
        arrows_neg_x=arrows_neg[0],
        arrows_neg_y=arrows_neg[1],
        stream_x=stream[0],
        stream_y=stream[1],
        magnitude_rgba=magnitude_rgba,
        magnitude_rect=magnitude_rect,
        arrow_count=arrow_count,
        streamline_count=streamline_count,
    )
