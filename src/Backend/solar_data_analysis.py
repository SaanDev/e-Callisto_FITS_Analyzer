"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


CropBounds = tuple[int, int, int, int]


@dataclass(frozen=True)
class AiaFrameSet:
    paths: list[str]
    maps: list[Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AiaRegion:
    region_id: int
    bbox: CropBounds
    centroid_x: float
    centroid_y: float
    centroid_x_arcsec: float
    centroid_y_arcsec: float
    area_px: int
    peak: float
    mean: float
    label: str = ""
    noaa_number: str = ""
    metadata_source: str = ""
    metadata_distance_arcsec: float | None = None


@dataclass(frozen=True)
class AiaMetadataRegion:
    label: str
    noaa_number: str = ""
    event_type: str = ""
    center_x_arcsec: float | None = None
    center_y_arcsec: float | None = None
    bbox_arcsec: tuple[float, float, float, float] | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    source: str = "HEK/SRS"


@dataclass(frozen=True)
class AiaCompositeSpec:
    frame_indexes: tuple[int, ...] = (0, 1, 2)
    percentile_low: float = 1.0
    percentile_high: float = 99.0


@dataclass(frozen=True)
class AiaMovieExportSpec:
    path: str
    fps: float = 4.0
    mode: str = "raw"
    crop_bounds: CropBounds | None = None
    percentile_low: float = 1.0
    percentile_high: float = 99.0
    colormap_name: str = "inferno"
    scale: str = "linear"
    # Divide frames by EXPTIME before differencing (set when exposures vary).
    normalize_exposure: bool = False


class AiaArrayMap:
    """Small SunPy-map-like wrapper for derived arrays.

    The plot window only needs a map object with ``data`` and a handful of
    metadata attributes. Keeping this wrapper light avoids forcing every
    derived product through FITS/WCS reconstruction.
    """

    def __init__(self, data: Any, source_map: Any | None = None, *, nickname: str = ""):
        self.data = np.asarray(data)
        self.meta = dict(getattr(source_map, "meta", {}) or {})
        self.observatory = _safe_text(getattr(source_map, "observatory", "")) or _safe_text(
            self.meta.get("obsrvtry", "")
        )
        self.instrument = _safe_text(getattr(source_map, "instrument", "")) or _safe_text(
            self.meta.get("instrume", "")
        )
        self.detector = _safe_text(getattr(source_map, "detector", "")) or _safe_text(self.meta.get("detector", ""))
        self.wavelength = _safe_text(getattr(source_map, "wavelength", ""))
        self.date = _safe_text(getattr(source_map, "date", ""))
        self.nickname = str(nickname or _safe_text(getattr(source_map, "nickname", ""))).strip()
        self.source = _safe_text(getattr(source_map, "source", ""))

        for attr in ("scale", "reference_pixel", "reference_coordinate", "coordinate_frame", "rsun_meters"):
            if hasattr(source_map, attr):
                try:
                    setattr(self, attr, getattr(source_map, attr))
                except Exception:
                    pass


def load_aia_maps(paths: Iterable[str | Path], *, map_loader: Any | None = None) -> AiaFrameSet:
    normalized = [str(Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
    if not normalized:
        raise ValueError("Select at least one AIA FITS file.")

    if map_loader is None:
        try:
            from sunpy.map import Map as map_loader
        except Exception as exc:
            raise RuntimeError(f"SunPy map loading is not available: {exc}") from exc

    loaded = map_loader(normalized, sequence=True)
    maps = extract_map_frames(loaded)
    if not maps:
        raise RuntimeError("No AIA map frames were loaded.")

    first = maps[0]
    metadata = {
        "n_frames": len(maps),
        "observatory": _safe_text(getattr(first, "observatory", "")),
        "instrument": _safe_text(getattr(first, "instrument", "")),
        "detector": _safe_text(getattr(first, "detector", "")),
        "wavelength": _safe_text(getattr(first, "wavelength", "")),
        "date": _safe_text(getattr(first, "date", "")),
    }
    return AiaFrameSet(paths=normalized, maps=maps, metadata=metadata)


def load_aia_maps_streaming(
    paths: Iterable[str | Path],
    *,
    progress_cb: Any | None = None,
    cancel_cb: Any | None = None,
    map_loader: Any | None = None,
) -> AiaFrameSet:
    """Load AIA FITS one file at a time, reporting progress.

    Loading every file at once (``Map(paths, sequence=True)``) blocks until the
    whole set is parsed — for many high-resolution frames that hangs the UI.
    Loading file by file lets a worker thread report ``progress_cb(done, total)``
    and stop early via ``cancel_cb()``. A single unreadable file is skipped
    rather than failing the whole load.
    """
    normalized = [str(Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
    if not normalized:
        raise ValueError("Select at least one AIA FITS file.")

    if map_loader is None:
        try:
            from sunpy.map import Map as map_loader
        except Exception as exc:
            raise RuntimeError(f"SunPy map loading is not available: {exc}") from exc

    maps: list[Any] = []
    loaded_paths: list[str] = []
    errors: list[str] = []
    cancelled = False
    total = len(normalized)
    for index, path in enumerate(normalized):
        if cancel_cb is not None:
            try:
                if bool(cancel_cb()):
                    cancelled = True
                    break
            except Exception:
                pass
        try:
            frames = extract_map_frames(map_loader(path))
            if frames:
                maps.extend(frames)
                loaded_paths.append(path)
        except Exception as exc:  # noqa: BLE001 - skip one bad file, keep the rest
            errors.append(f"{Path(path).name}: {exc}")
        if progress_cb is not None:
            try:
                progress_cb(index + 1, total)
            except Exception:
                pass

    if not maps:
        if cancelled:
            return AiaFrameSet(paths=[], maps=[], metadata={"n_frames": 0})
        detail = ("\n" + "\n".join(errors[:5])) if errors else ""
        raise RuntimeError("No AIA map frames were loaded." + detail)

    first = maps[0]
    metadata = {
        "n_frames": len(maps),
        "observatory": _safe_text(getattr(first, "observatory", "")),
        "instrument": _safe_text(getattr(first, "instrument", "")),
        "detector": _safe_text(getattr(first, "detector", "")),
        "wavelength": _safe_text(getattr(first, "wavelength", "")),
        "date": _safe_text(getattr(first, "date", "")),
    }
    return AiaFrameSet(paths=loaded_paths, maps=maps, metadata=metadata)


def extract_map_frames(loaded_obj: Any) -> list[Any]:
    maps_attr = getattr(loaded_obj, "maps", None)
    if maps_attr is not None:
        try:
            return list(maps_attr)
        except Exception:
            pass
    if isinstance(loaded_obj, (list, tuple)):
        return list(loaded_obj)
    return [loaded_obj]


def clip_crop_bounds(shape: Sequence[int], bounds: CropBounds | None) -> CropBounds:
    if len(shape) < 2:
        raise ValueError("AIA image data must be at least two-dimensional.")
    ny = int(shape[0])
    nx = int(shape[1])
    if bounds is None:
        return (0, nx, 0, ny)

    x0, x1, y0, y1 = bounds
    x_low, x_high = sorted((int(x0), int(x1)))
    y_low, y_high = sorted((int(y0), int(y1)))
    x_low = max(0, min(nx, x_low))
    x_high = max(0, min(nx, x_high))
    y_low = max(0, min(ny, y_low))
    y_high = max(0, min(ny, y_high))
    if x_high <= x_low or y_high <= y_low:
        raise ValueError("Crop region does not overlap the image.")
    return (x_low, x_high, y_low, y_high)


def crop_array(data: Any, bounds: CropBounds | None) -> np.ndarray:
    arr = _prepare_image_array(data)
    x0, x1, y0, y1 = clip_crop_bounds(arr.shape, bounds)
    if arr.ndim == 3:
        return np.asarray(arr[y0:y1, x0:x1, :])
    return np.asarray(arr[y0:y1, x0:x1])


def crop_maps(frames: Sequence[Any], bounds: CropBounds | None) -> list[AiaArrayMap]:
    out: list[AiaArrayMap] = []
    for idx, frame in enumerate(frames):
        arr = _prepare_image_array(getattr(frame, "data"))
        x0, x1, y0, y1 = clip_crop_bounds(arr.shape, bounds)
        cropped = AiaArrayMap(arr[y0:y1, x0:x1, ...] if arr.ndim == 3 else arr[y0:y1, x0:x1], frame, nickname=f"Cropped frame {idx + 1}")
        prior_origin = getattr(frame, "_crop_origin_px", (0, 0))
        try:
            prior_x, prior_y = int(prior_origin[0]), int(prior_origin[1])
        except Exception:
            prior_x, prior_y = 0, 0
        cropped._crop_origin_px = (prior_x + int(x0), prior_y + int(y0))
        _shift_crpix_metadata(cropped.meta, x0=int(x0), y0=int(y0))
        out.append(cropped)
    return out


def dominant_shape_frames(frames: Sequence[Any]) -> tuple[list[Any], list[Any], tuple[int, ...] | None]:
    """Partition frames into ``(kept, dropped, shape)`` by their data-array shape.

    Only the frames matching the single most common image shape are kept. Mixed
    image sizes — e.g. STEREO/SECCHI COR "double" 256x256 browse frames
    interspersed among 2048x2048 science frames — are undefined for running/base
    difference, movie export and compositing (you cannot subtract differently
    sized arrays), so they must be excluded from a multi-frame sequence. Ties are
    resolved in favour of the shape that appears first. Returns the original list
    unchanged (nothing dropped) when every frame already shares one shape.
    """
    frame_list = list(frames)
    if not frame_list:
        return [], [], None
    shapes = [tuple(np.asarray(getattr(frame, "data")).shape) for frame in frame_list]
    counts: dict[tuple[int, ...], int] = {}
    first_index: dict[tuple[int, ...], int] = {}
    for i, shape in enumerate(shapes):
        counts[shape] = counts.get(shape, 0) + 1
        first_index.setdefault(shape, i)
    dominant = max(counts, key=lambda shape: (counts[shape], -first_index[shape]))
    kept = [frame for frame, shape in zip(frame_list, shapes) if shape == dominant]
    dropped = [frame for frame, shape in zip(frame_list, shapes) if shape != dominant]
    return kept, dropped, dominant


@dataclass(frozen=True)
class FramePartition:
    """Result of grouping a frame sequence by observing configuration."""

    kept: list[Any]
    dropped: list[Any]
    kept_key: tuple | None  # config key of the retained science group
    dropped_keys: dict  # dropped config key -> frame count
    note: str  # human-readable summary ("" when nothing dropped)


def _polar_state(frame: Any) -> str:
    """Normalise a frame's POLAR header into a comparable polarizer state.

    SECCHI/LASCO conventions: a numeric polarizer angle (0/120/240...) marks one
    leg of a polarization triplet, while total-brightness products carry POLAR
    >= 1000 (e.g. 1001), a textual value like "Clear", or no POLAR at all. All
    of the latter collapse to "total" so ordinary EUV/HMI frames (no POLAR)
    compare equal to coronagraph total-brightness frames.
    """
    value = _frame_meta_get(frame, "polar")
    number = _as_float(value)
    if number is None or number >= 1000.0:
        return "total"
    return f"pol{int(round(number))}"


def _filter_state(frame: Any) -> str:
    """Normalise a frame's optical FILTER into a comparable token.

    SOHO/LASCO C2 & C3 sequences interleave frames taken through different colour
    filters (Orange/Clear/Blue/Deep Red/IR) at the same image size and the same
    total-brightness polarizer state, so POLAR and shape alone do not tell them
    apart. Differencing across filters does NOT cancel the static corona — the two
    passbands have very different overall response — so a cross-filter running
    difference looks like a raw, undifferenced frame dropped into the movie.
    Including the filter in the config key keeps a difference sequence to a single
    passband. Frames with no FILTER keyword (EUV imagers, HMI) collapse to "none"
    so their grouping is unchanged.
    """
    text = _safe_text(_frame_meta_get(frame, "filter")).lower()
    return text or "none"


def frame_config_key(frame: Any) -> tuple:
    """Observation-configuration identity for sequence compatibility.

    ``(instrument, detector, wavelength, polar_state, filter_state, shape)`` — two
    frames are difference/movie-compatible only when all six match. Exposure time
    is deliberately NOT part of the key: AIA's automatic exposure control varies
    EXPTIME by design inside a perfectly valid sequence, so exposure mismatch is
    corrected by normalisation (see ``exposures_differ``), not by exclusion.
    """
    instrument = _safe_text(
        getattr(frame, "instrument", None) or _frame_meta_get(frame, "instrume", "instrument")
    ).strip().upper()
    detector = _safe_text(
        getattr(frame, "detector", None) or _frame_meta_get(frame, "detector")
    ).strip().upper()
    wavelength = _as_float(_frame_meta_get(frame, "wavelnth"))
    wavelength_key = int(round(wavelength)) if wavelength is not None else None
    shape = tuple(np.asarray(getattr(frame, "data")).shape)
    return (instrument, detector, wavelength_key, _polar_state(frame), _filter_state(frame), shape)


def partition_frames_by_config(frames: Sequence[Any]) -> FramePartition:
    """Split frames into the dominant science group and incompatible leftovers.

    Archive time windows routinely mix observing configurations that must never
    be differenced against each other: STEREO/SECCHI COR sequences interleave
    polarizer triplets (POLAR=0/120/240) with total-brightness frames
    (POLAR=1001) at the same image size, plus small browse ("double") frames;
    mixed AIA uploads can span wavelengths. Selection policy:

    1. Only groups at the maximum pixel area are science candidates (drops
       browse/thumbnail frames regardless of their headers).
    2. Among candidates, a total-brightness group beats polarizer groups even
       when it has fewer frames (the physically meaningful movie sequence).
    3. Otherwise the most numerous group wins; ties break to first occurrence.

    Frame order is preserved. ``note`` is "" when nothing was dropped.
    """
    frame_list = list(frames)
    if not frame_list:
        return FramePartition(kept=[], dropped=[], kept_key=None, dropped_keys={}, note="")

    keys = [frame_config_key(frame) for frame in frame_list]
    counts: dict[tuple, int] = {}
    first_index: dict[tuple, int] = {}
    for i, key in enumerate(keys):
        counts[key] = counts.get(key, 0) + 1
        first_index.setdefault(key, i)

    if len(counts) == 1:
        return FramePartition(
            kept=frame_list, dropped=[], kept_key=keys[0], dropped_keys={}, note=""
        )

    def _area(key: tuple) -> int:
        shape = key[-1]
        area = 1
        for dim in shape:
            area *= int(dim)
        return area

    max_area = max(_area(key) for key in counts)
    candidates = [key for key in counts if _area(key) == max_area]
    total_candidates = [key for key in candidates if key[3] == "total"]
    pool = total_candidates or candidates
    chosen = max(pool, key=lambda key: (counts[key], -first_index[key]))

    kept = [frame for frame, key in zip(frame_list, keys) if key == chosen]
    dropped = [frame for frame, key in zip(frame_list, keys) if key != chosen]
    dropped_keys: dict[tuple, int] = {}
    for key in keys:
        if key != chosen:
            dropped_keys[key] = dropped_keys.get(key, 0) + 1

    reasons = []
    for key, n in dropped_keys.items():
        parts = []
        if key[-1] != chosen[-1]:
            parts.append(f"{key[-1][1]}x{key[-1][0]} px" if len(key[-1]) == 2 else "different size")
        if key[3] != chosen[3]:
            parts.append("polarizer " + key[3][3:] + "°" if key[3].startswith("pol") else key[3])
        if key[4] != chosen[4] and key[4] != "none":
            parts.append(f"{key[4]} filter")
        if key[2] != chosen[2] and key[2] is not None:
            parts.append(f"{key[2]} Å")
        if key[0] != chosen[0] or key[1] != chosen[1]:
            parts.append("/".join(x for x in (key[0], key[1]) if x))
        reasons.append(f"{n} × ({', '.join(parts) or 'other configuration'})")

    kept_desc = "total-brightness" if chosen[3] == "total" else f"polarizer {chosen[3][3:]}°"
    note = (
        f"excluded {len(dropped)} frame(s) with a different observing configuration "
        f"[{'; '.join(reasons)}] so differences and movies stay physically consistent "
        f"(kept the {kept_desc} science sequence)."
    )
    return FramePartition(
        kept=kept, dropped=dropped, kept_key=chosen, dropped_keys=dropped_keys, note=note
    )


def exposures_differ(frames: Sequence[Any], *, tolerance: float = 0.01) -> bool:
    """True when known exposure times in a sequence spread more than ``tolerance``.

    Differencing frames with unequal EXPTIME in raw DN creates false
    brightenings/dimmings proportional to the exposure ratio, so callers use
    this to decide whether to normalise to DN/s first. Frames without a usable
    EXPTIME are ignored; fewer than two known exposures -> False.
    """
    known = [t for t in (frame_exposure_time(f) for f in frames) if t and t > 0]
    if len(known) < 2:
        return False
    return (max(known) / min(known)) - 1.0 > float(tolerance)


def difference_sequence(
    frames: Sequence[Any],
    *,
    mode: str = "running",
    crop_bounds: CropBounds | None = None,
    normalize: bool = False,
) -> list[np.ndarray]:
    # normalize: divide each frame by its EXPTIME (DN -> DN/s) before
    # differencing, so unequal exposures don't masquerade as brightness changes.
    arrays = [crop_array(getattr(frame, "data"), crop_bounds) for frame in frames]
    if normalize:
        scaled: list[np.ndarray] = []
        for frame, arr in zip(frames, arrays):
            exptime = frame_exposure_time(frame)
            arr = np.asarray(arr, dtype=float)
            scaled.append(arr / exptime if exptime and exptime > 0 else arr)
        arrays = scaled
    if not arrays:
        return []
    mode_key = str(mode or "running").strip().lower()
    if mode_key in {"raw", "none"}:
        return arrays
    if mode_key in {"base", "base_difference"}:
        base = np.asarray(arrays[0], dtype=float)
        return [np.asarray(arr, dtype=float) - base for arr in arrays]
    if mode_key not in {"running", "running_difference"}:
        raise ValueError(f"Unsupported difference mode: {mode}")

    out: list[np.ndarray] = []
    for idx, arr in enumerate(arrays):
        current = np.asarray(arr, dtype=float)
        if len(arrays) == 1:
            out.append(current.copy())
            continue
        if idx == 0:
            out.append(np.asarray(arrays[1], dtype=float) - current)
        else:
            out.append(current - np.asarray(arrays[idx - 1], dtype=float))
    return out


@dataclass(frozen=True)
class AiaLightcurve:
    """Intensity-vs-time profile extracted over a region of interest."""

    times: list[datetime]
    values: np.ndarray
    bounds: CropBounds | None
    unit: str = "DN/s"
    statistic: str = "mean"
    wavelength: str = ""

    def peak_index(self) -> int:
        if self.values is None or len(self.values) == 0:
            return -1
        return int(np.nanargmax(self.values))

    def peak_time(self) -> datetime | None:
        idx = self.peak_index()
        if idx < 0 or idx >= len(self.times):
            return None
        return self.times[idx]


def frame_exposure_time(frame: Any) -> float | None:
    """Exposure time (seconds) from a map's header, or None if unavailable."""
    value = _frame_meta_get(frame, "exptime", "exptimeu", "exposure")
    seconds = _as_float(value)
    if seconds is not None and seconds > 0:
        return seconds
    # sunpy maps expose exposure_time as an astropy Quantity.
    quantity = getattr(frame, "exposure_time", None)
    if quantity is not None:
        try:
            return float(quantity.to("s").value)
        except Exception:
            seconds = _as_float(getattr(quantity, "value", quantity))
            if seconds is not None and seconds > 0:
                return seconds
    return None


def frame_observation_time(frame: Any) -> datetime | None:
    """Best-effort observation time for a frame as a naive UTC datetime."""
    date_attr = getattr(frame, "date", None)
    parsed = _coerce_datetime(date_attr)
    if parsed is not None:
        return parsed
    for key in ("date-obs", "date_obs", "t_obs", "t_rec", "date"):
        parsed = _coerce_datetime(_frame_meta_get(frame, key))
        if parsed is not None:
            return parsed
    return None


def normalize_exposure(frames: Sequence[Any]) -> list[AiaArrayMap]:
    """Divide each frame by its exposure time to give calibrated DN/s.

    Comparing AIA frames (composites, differences, light curves) is only
    physically meaningful in rate units: raw DN scales with how long the shutter
    was open, which varies frame to frame (especially across flares via AEC).
    Frames without a usable EXPTIME are passed through unchanged so the call is
    always safe.
    """
    out: list[AiaArrayMap] = []
    for frame in frames:
        data = np.asarray(getattr(frame, "data"), dtype=float)
        exptime = frame_exposure_time(frame)
        if exptime and exptime > 0:
            data = data / exptime
        wrapped = AiaArrayMap(data, frame, nickname=_safe_text(getattr(frame, "nickname", "")))
        wrapped.meta["exptime"] = 1.0  # data is now per-second
        wrapped.meta["bunit"] = "DN/s"
        out.append(wrapped)
    return out


def register_aia_maps(frames: Sequence[Any], *, aiapy_register: Any | None = None) -> list[Any]:
    """Co-register AIA frames to a common 0.6\"/pix grid and pointing (aiapy).

    This is what makes multi-wavelength composites and difference images
    pixel-aligned and quantitatively correct. Requires the optional ``aiapy``
    package; when it is missing we raise a clear, actionable error rather than
    silently returning misaligned data.
    """
    if aiapy_register is None:
        try:
            from aiapy.calibrate import register as aiapy_register
        except Exception as exc:  # pragma: no cover - exercised only without aiapy
            raise RuntimeError(
                "AIA registration needs the optional 'aiapy' package.\n"
                "Install it with: python3 -m pip install aiapy"
            ) from exc
    out: list[Any] = []
    for frame in frames:
        try:
            out.append(aiapy_register(frame))
        except Exception:
            out.append(frame)
    return out


def extract_region_lightcurve(
    frames: Sequence[Any],
    bounds: CropBounds | None = None,
    *,
    normalize: bool = True,
    statistic: str = "mean",
) -> AiaLightcurve:
    """Build an intensity-vs-time light curve over a region of interest.

    Sums or averages the pixels inside ``bounds`` for every frame, giving the
    time profile used to time flares/EUV brightenings against radio bursts. With
    ``normalize`` the data is converted to DN/s first so the curve is physical.
    """
    if not frames:
        raise ValueError("At least one frame is required for a light curve.")
    work = normalize_exposure(frames) if normalize else list(frames)

    times: list[datetime] = []
    values: list[float] = []
    reducer = np.nansum if str(statistic).lower() in {"sum", "total"} else np.nanmean
    for original, frame in zip(frames, work):
        region = crop_array(getattr(frame, "data"), bounds)
        region = np.asarray(region, dtype=float)
        values.append(float(reducer(region)) if region.size else float("nan"))
        # Times come from the original frame headers (DN/s wrapper preserves them).
        times.append(frame_observation_time(original) or frame_observation_time(frame))

    return AiaLightcurve(
        times=times,
        values=np.asarray(values, dtype=float),
        bounds=bounds,
        unit="DN/s" if normalize else "DN",
        statistic="sum" if reducer is np.nansum else "mean",
        wavelength=_safe_text(getattr(frames[0], "wavelength", "")),
    )


def nearest_frame_index(times: Sequence[datetime | None], target: datetime) -> int:
    """Index of the frame whose time is closest to ``target`` (ignores Nones)."""
    best_idx = -1
    best_delta: float | None = None
    for idx, when in enumerate(times):
        if when is None:
            continue
        delta = abs((when - target).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = idx
    return best_idx


def radio_euv_lag(radio_onset: datetime | None, euv_peak: datetime | None) -> float | None:
    """Seconds between a radio burst onset and an EUV brightening peak.

    Positive means the EUV peak follows the radio onset. Used to quantify the
    e-Callisto ↔ SDO timing relationship for a selected burst.
    """
    if radio_onset is None or euv_peak is None:
        return None
    return (euv_peak - radio_onset).total_seconds()


def make_composite(frames: Sequence[Any], spec: AiaCompositeSpec | None = None) -> AiaArrayMap:
    if not frames:
        raise ValueError("At least one AIA frame is required for a composite.")
    spec = spec or AiaCompositeSpec()
    indexes = [idx for idx in spec.frame_indexes if 0 <= int(idx) < len(frames)]
    if not indexes:
        indexes = [0]

    channels = []
    for idx in indexes[:3]:
        arr = crop_array(getattr(frames[int(idx)], "data"), None)
        channels.append(_normalize_to_unit(arr, spec.percentile_low, spec.percentile_high))

    while len(channels) < 3:
        channels.append(channels[-1].copy())

    rgb = np.dstack(channels[:3])
    return AiaArrayMap(rgb, frames[indexes[0]], nickname="AIA Composite")


def make_magnetogram_composite(
    base_frame: Any,
    magnetogram_frame: Any,
    *,
    base_colormap: str = "sdoaia171",
    base_percentile_low: float = 1.0,
    base_percentile_high: float = 99.5,
    threshold_gauss: float = 100.0,
    base_scale: str = "log",
) -> AiaArrayMap:
    """Overlay HMI magnetogram polarity contours on a colour-mapped AIA image.

    Positive line-of-sight field (toward observer) is outlined in red, negative
    in blue, at ``±threshold_gauss``. The AIA base is rendered exactly as in the
    movie/preview (scale + percentile clip) and the contour edges are drawn on
    top, producing the standard active-region composite. The magnetogram is
    resampled to the AIA grid if their shapes differ.
    """
    base = np.asarray(crop_array(getattr(base_frame, "data"), None), dtype=float)
    mag = np.asarray(crop_array(getattr(magnetogram_frame, "data"), None), dtype=float)
    if base.ndim != 2:
        raise ValueError("The AIA base frame must be a 2-D image for a magnetogram composite.")
    if mag.shape != base.shape:
        mag = _resample_nearest(mag, base.shape)

    rgb = _array_to_rgb_uint8(
        apply_display_scale(base, base_scale),
        percentile_low=base_percentile_low,
        percentile_high=base_percentile_high,
        colormap_name=base_colormap,
    )
    rgb = np.array(rgb, dtype=np.uint8, copy=True)

    thr = float(abs(threshold_gauss))
    pos_edge = _binary_edge(np.isfinite(mag) & (mag > thr))
    neg_edge = _binary_edge(np.isfinite(mag) & (mag < -thr))
    rgb[pos_edge] = (255, 70, 70)    # positive polarity -> red
    rgb[neg_edge] = (80, 150, 255)   # negative polarity -> blue

    return AiaArrayMap(rgb, base_frame, nickname="AIA + HMI magnetogram contours")


def _binary_edge(mask: np.ndarray) -> np.ndarray:
    """Boolean array marking the 1-pixel boundary of True regions in ``mask``."""
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return mask
    try:
        from scipy import ndimage

        return mask & ~ndimage.binary_erosion(mask, border_value=0)
    except Exception:
        # numpy fallback: a pixel is an edge if any 4-neighbour is False.
        eroded = np.ones_like(mask)
        eroded[:-1, :] &= mask[1:, :]
        eroded[1:, :] &= mask[:-1, :]
        eroded[:, :-1] &= mask[:, 1:]
        eroded[:, 1:] &= mask[:, :-1]
        eroded &= mask
        return mask & ~eroded


def _resample_nearest(arr: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resample a 2-D array to ``shape`` (no SciPy needed)."""
    arr = np.asarray(arr, dtype=float)
    ny, nx = int(shape[0]), int(shape[1])
    sy = max(1, arr.shape[0])
    sx = max(1, arr.shape[1])
    rows = (np.linspace(0, sy - 1, ny)).round().astype(int)
    cols = (np.linspace(0, sx - 1, nx)).round().astype(int)
    return arr[np.ix_(rows, cols)]


def detect_active_regions(
    data: Any,
    *,
    threshold_percentile: float = 98.0,
    min_area_px: int = 12,
    max_regions: int = 25,
    axis_transform: dict[str, float] | None = None,
) -> list[AiaRegion]:
    arr = np.asarray(crop_array(data, None), dtype=float)
    if arr.ndim == 3:
        arr = np.nanmean(arr[..., :3], axis=2)

    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return []

    threshold = float(np.nanpercentile(finite, float(threshold_percentile)))
    mask = np.isfinite(arr) & (arr >= threshold)

    try:
        from scipy import ndimage
    except Exception:
        return _detect_regions_without_scipy(arr, mask, min_area_px=min_area_px, max_regions=max_regions, axis_transform=axis_transform)

    labeled, count = ndimage.label(mask)
    objects = ndimage.find_objects(labeled)
    regions: list[AiaRegion] = []
    region_id = 1
    for label_idx in range(1, int(count) + 1):
        slc = objects[label_idx - 1] if label_idx - 1 < len(objects) else None
        if slc is None:
            continue
        y_slice, x_slice = slc
        component = labeled[slc] == label_idx
        area = int(np.count_nonzero(component))
        if area < int(min_area_px):
            continue
        values = arr[slc][component]
        if values.size == 0:
            continue
        cy, cx = ndimage.center_of_mass(mask, labeled, label_idx)
        if not np.isfinite(cx) or not np.isfinite(cy):
            cy, cx = _component_centroid(component, y_slice.start, x_slice.start)
        x0, x1 = int(x_slice.start), int(x_slice.stop)
        y0, y1 = int(y_slice.start), int(y_slice.stop)
        x_arc, y_arc = _pixel_to_arcsec(cx, cy, axis_transform)
        regions.append(
            AiaRegion(
                region_id=region_id,
                bbox=(x0, x1, y0, y1),
                centroid_x=float(cx),
                centroid_y=float(cy),
                centroid_x_arcsec=float(x_arc),
                centroid_y_arcsec=float(y_arc),
                area_px=area,
                peak=float(np.nanmax(values)),
                mean=float(np.nanmean(values)),
            )
        )
        region_id += 1

    regions.sort(key=lambda r: (r.peak, r.area_px), reverse=True)
    limited = regions[: max(0, int(max_regions))]
    return [replace(region, region_id=idx + 1) for idx, region in enumerate(limited)]


def label_regions_with_metadata(
    regions: Sequence[AiaRegion],
    metadata_regions: Sequence[AiaMetadataRegion],
    *,
    max_distance_arcsec: float = 180.0,
) -> list[AiaRegion]:
    out: list[AiaRegion] = []
    for region in regions:
        best: tuple[float, AiaMetadataRegion] | None = None
        for meta in metadata_regions:
            dist = _metadata_region_distance(region, meta)
            if dist is None:
                continue
            if best is None or dist < best[0]:
                best = (dist, meta)
        if best is None or best[0] > float(max_distance_arcsec):
            out.append(region)
            continue
        meta = best[1]
        label = str(meta.label or meta.noaa_number or "").strip()
        out.append(
            replace(
                region,
                label=label,
                noaa_number=str(meta.noaa_number or ""),
                metadata_source=str(meta.source or ""),
                metadata_distance_arcsec=float(best[0]),
            )
        )
    return out


def fetch_active_region_metadata(
    start_dt: datetime,
    end_dt: datetime,
    *,
    hek_client: Any | None = None,
    attrs_module: Any | None = None,
) -> list[AiaMetadataRegion]:
    if hek_client is None or attrs_module is None:
        try:
            from sunpy.net import attrs as sunpy_attrs
            from sunpy.net.hek import HEKClient
        except Exception as exc:
            raise RuntimeError(f"SunPy HEK support is not available: {exc}") from exc
        attrs_module = attrs_module or sunpy_attrs
        hek_client = hek_client or HEKClient()

    results = hek_client.search(
        attrs_module.Time(start_dt, end_dt),
        attrs_module.hek.EventType("AR"),
    )
    return [_metadata_region_from_hek(row) for row in results]


def apply_display_scale(data: Any, scale: str) -> np.ndarray:
    """Apply the linear/log display scaling shared by the live preview and the
    exported movie, so the two never diverge.

    Log mode floors the data at its 0.5th positive percentile before ``log10``,
    identical to the on-screen renderer. RGB composites (3-D) are returned
    unchanged because they are already display-ready.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or str(scale or "linear").strip().lower() != "log":
        return arr
    positive = arr[np.isfinite(arr) & (arr > 0)]
    floor = float(np.nanpercentile(positive, 0.5)) if positive.size else 1.0
    floor = max(floor, 1e-12)
    return np.log10(np.clip(arr, floor, None))


def iter_rendered_movie_frames(
    frames: Sequence[Any],
    *,
    mode: str = "raw",
    crop_bounds: CropBounds | None = None,
    percentile_low: float = 1.0,
    percentile_high: float = 99.0,
    colormap_name: str = "inferno",
    scale: str = "linear",
    normalize: bool = False,
):
    """Yield rendered RGB uint8 frames one at a time.

    Streaming avoids holding every rendered frame in memory at once, which for
    full-resolution AIA (≈50 MB per RGB frame) otherwise exhausts RAM and hangs
    the app. Difference modes keep only the base/previous frame in hand. Mirrors
    :func:`difference_sequence` exactly so the output matches the preview
    (including exposure normalisation when ``normalize`` is set).
    """
    n = len(frames)
    if n == 0:
        return
    # Match the on-screen clip clamp so the movie contrast equals the preview.
    p_low = min(float(percentile_low), float(percentile_high) - 0.1)
    p_high = max(float(percentile_high), p_low + 0.1)
    mode_key = str(mode or "raw").strip().lower()
    if mode_key not in {"raw", "none", "base", "base_difference", "running", "running_difference"}:
        raise ValueError(f"Unsupported difference mode: {mode}")

    def _cropped(i: int) -> np.ndarray:
        arr = np.asarray(crop_array(getattr(frames[i], "data"), crop_bounds), dtype=float)
        if normalize:
            exptime = frame_exposure_time(frames[i])
            if exptime and exptime > 0:
                arr = arr / exptime
        return arr

    def _render(arr: np.ndarray) -> np.ndarray:
        return _array_to_rgb_uint8(
            apply_display_scale(arr, scale),
            percentile_low=p_low,
            percentile_high=p_high,
            colormap_name=colormap_name,
        )

    if mode_key in {"base", "base_difference"}:
        base = _cropped(0)
        for i in range(n):
            yield _render(_cropped(i) - base)
    elif mode_key in {"running", "running_difference"}:
        prev: np.ndarray | None = None
        for i in range(n):
            current = _cropped(i)
            if n == 1:
                yield _render(current.copy())
            elif i == 0:
                yield _render(_cropped(1) - current)
            else:
                yield _render(current - prev)
            prev = current
    else:  # raw / none
        for i in range(n):
            yield _render(_cropped(i))


def render_movie_frames(
    frames: Sequence[Any],
    *,
    mode: str = "raw",
    crop_bounds: CropBounds | None = None,
    percentile_low: float = 1.0,
    percentile_high: float = 99.0,
    colormap_name: str = "inferno",
    scale: str = "linear",
) -> list[np.ndarray]:
    return list(
        iter_rendered_movie_frames(
            frames,
            mode=mode,
            crop_bounds=crop_bounds,
            percentile_low=percentile_low,
            percentile_high=percentile_high,
            colormap_name=colormap_name,
            scale=scale,
        )
    )


def export_movie(
    frames: Sequence[Any],
    spec: AiaMovieExportSpec,
    *,
    progress_cb: Any | None = None,
    cancel_cb: Any | None = None,
) -> None:
    """Render and write a movie, one frame at a time.

    ``progress_cb(done, total)`` is called after each frame; ``cancel_cb()``
    returning True stops early and removes the partial file. Designed to run in
    a worker thread so the UI stays responsive.
    """
    out_path = Path(spec.path).expanduser()
    if not out_path.suffix:
        raise ValueError("Movie export path must include .gif or .mp4.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not frames:
        raise ValueError("No frames are available for movie export.")

    suffix = out_path.suffix.lower()
    if suffix not in (".gif", ".mp4"):
        raise ValueError("Movie export supports only .gif and .mp4 files.")
    fps = max(0.1, float(spec.fps or 4.0))

    frame_iter = iter_rendered_movie_frames(
        frames,
        mode=spec.mode,
        crop_bounds=spec.crop_bounds,
        percentile_low=spec.percentile_low,
        percentile_high=spec.percentile_high,
        colormap_name=spec.colormap_name,
        scale=spec.scale,
        normalize=bool(getattr(spec, "normalize_exposure", False)),
    )
    _write_movie_stream(
        out_path,
        frame_iter,
        total=len(frames),
        fps=fps,
        is_mp4=(suffix == ".mp4"),
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )


def _write_movie_stream(
    out_path: Path,
    frame_iter,
    *,
    total: int,
    fps: float,
    is_mp4: bool,
    progress_cb: Any | None = None,
    cancel_cb: Any | None = None,
) -> None:
    imageio = _import_imageio_v2()
    if is_mp4:
        _ensure_imageio_ffmpeg_available()
        try:
            writer = imageio.get_writer(
                str(out_path),
                format="FFMPEG",
                mode="I",
                fps=max(0.1, float(fps)),
                codec="libx264",
                macro_block_size=16,
            )
        except Exception as exc:
            raise RuntimeError("MP4 export requires a working FFmpeg writer backend.") from exc
    else:
        writer = imageio.get_writer(str(out_path), mode="I", duration=1.0 / max(0.1, float(fps)))

    wrote = 0
    cancelled = False
    try:
        for frame in frame_iter:
            if cancel_cb is not None:
                try:
                    if bool(cancel_cb()):
                        cancelled = True
                        break
                except Exception:
                    pass
            data = _pad_frame_to_block(frame, 16) if is_mp4 else np.asarray(frame, dtype=np.uint8)
            writer.append_data(data)
            del data, frame
            wrote += 1
            if progress_cb is not None:
                try:
                    progress_cb(wrote, total)
                except Exception:
                    pass
    finally:
        writer.close()

    if cancelled:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        return
    if wrote == 0:
        raise ValueError("No frames are available for movie export.")


def _import_imageio_v2() -> Any:
    try:
        import imageio.v2 as imageio
    except Exception as exc:
        raise RuntimeError("Movie export requires imageio. Install the pinned runtime dependencies.") from exc
    return imageio


def _ensure_imageio_ffmpeg_available() -> None:
    try:
        import imageio_ffmpeg  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "MP4 export requires imageio-ffmpeg. Install imageio-ffmpeg==0.6.0 with the runtime dependencies."
        ) from exc


def _pad_frame_to_block(frame: Any, block: int = 16) -> np.ndarray:
    """Pad an RGB frame with black so its width/height are multiples of ``block``.

    H.264/imageio require frame dimensions divisible by the macro block size and
    otherwise silently *resize* (interpolating and distorting) the image. Padding
    keeps every original pixel intact and only adds a thin black border.
    """
    arr = np.asarray(frame, dtype=np.uint8)
    if arr.ndim < 2:
        return arr
    h, w = arr.shape[:2]
    pad_h = (-h) % block
    pad_w = (-w) % block
    if pad_h == 0 and pad_w == 0:
        return arr
    pad_spec = [(0, pad_h), (0, pad_w)] + ([(0, 0)] if arr.ndim == 3 else [])
    return np.pad(arr, pad_spec, mode="constant", constant_values=0)


def write_cropped_fits(frame: Any, bounds: CropBounds | None, path: str | Path) -> None:
    try:
        from astropy.io import fits
    except Exception as exc:
        raise RuntimeError(f"FITS export requires astropy: {exc}") from exc

    out_path = Path(path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = crop_array(getattr(frame, "data"), bounds)
    header = None
    meta = getattr(frame, "meta", None)
    if meta:
        try:
            header = fits.Header()
            for key, value in dict(meta).items():
                text_key = str(key).upper()[:8]
                if not text_key:
                    continue
                try:
                    header[text_key] = value
                except Exception:
                    continue
            if bounds is not None:
                x0, _x1, y0, _y1 = clip_crop_bounds(np.asarray(getattr(frame, "data")).shape, bounds)
                _shift_header_crpix(header, x0=int(x0), y0=int(y0))
        except Exception:
            header = None
    fits.PrimaryHDU(data=np.asarray(data), header=header).writeto(out_path, overwrite=True)


def _shift_crpix_metadata(meta: dict[str, Any], *, x0: int, y0: int) -> None:
    if not meta:
        return
    for key, offset in (("crpix1", x0), ("CRPIX1", x0), ("crpix2", y0), ("CRPIX2", y0)):
        if key not in meta:
            continue
        try:
            meta[key] = float(meta[key]) - float(offset)
        except Exception:
            pass


def _shift_header_crpix(header: Any, *, x0: int, y0: int) -> None:
    for key, offset in (("CRPIX1", x0), ("CRPIX2", y0)):
        if key not in header:
            continue
        try:
            header[key] = float(header[key]) - float(offset)
        except Exception:
            pass


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return repr(value)


def _frame_meta_get(frame: Any, *keys: str) -> Any:
    """Case-insensitive lookup in a frame's FITS-style metadata dict."""
    meta = getattr(frame, "meta", None)
    if not meta:
        return None
    try:
        items = dict(meta)
    except Exception:
        return None
    lowered = {str(k).strip().lower(): v for k, v in items.items()}
    for key in keys:
        value = lowered.get(str(key).strip().lower())
        if value is not None:
            return value
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    """Coerce a header value or astropy Time into a naive UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)

    # astropy Time exposes .to_datetime() and .isot.
    to_datetime = getattr(value, "to_datetime", None)
    if callable(to_datetime):
        try:
            result = to_datetime()
            if isinstance(result, datetime):
                return result.replace(tzinfo=None)
        except Exception:
            pass
    isot = getattr(value, "isot", None)
    if isinstance(isot, str) and isot.strip():
        parsed = _parse_datetime_text(isot.strip())
        if parsed is not None:
            return parsed
    if isinstance(value, str):
        return _parse_datetime_text(value.strip())
    return None


def _parse_datetime_text(text: str) -> datetime | None:
    if not text:
        return None
    candidate = text.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d_%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _prepare_image_array(data: Any) -> np.ndarray:
    arr = np.asarray(data)
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        return arr
    if arr.ndim > 2:
        arr2 = np.asarray(arr[0]).squeeze()
        if arr2.ndim == 2:
            return arr2
    raise ValueError(f"Unsupported AIA image shape: {arr.shape}")


def _normalize_to_unit(data: Any, p_low: float, p_high: float) -> np.ndarray:
    arr = np.asarray(data, dtype=float)
    if arr.ndim == 3:
        arr = np.nanmean(arr[..., :3], axis=2)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape[:2], dtype=float)
    lo = float(np.nanpercentile(finite, float(p_low)))
    hi = float(np.nanpercentile(finite, float(p_high)))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(finite))
        hi = float(np.nanmax(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.zeros(arr.shape[:2], dtype=float)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def _array_to_rgb_uint8(
    arr: Any,
    *,
    percentile_low: float,
    percentile_high: float,
    colormap_name: str = "inferno",
) -> np.ndarray:
    data = np.asarray(arr)
    if data.ndim == 3 and data.shape[-1] in (3, 4):
        rgb = np.asarray(data[..., :3], dtype=float)
        if np.nanmax(rgb) <= 1.0:
            rgb = rgb * 255.0
        return np.clip(rgb, 0, 255).astype(np.uint8)

    norm = _normalize_to_unit(data, percentile_low, percentile_high)
    try:
        try:
            import sunpy.visualization.colormaps  # noqa: F401
        except Exception:
            pass
        from matplotlib import colormaps

        rgba = colormaps.get_cmap(str(colormap_name or "inferno"))(norm)
        rgb = np.asarray(rgba[..., :3] * 255.0, dtype=np.uint8)
        return rgb
    except Exception:
        fallback = _fallback_colormap_rgb(norm, str(colormap_name or "inferno"))
        if fallback is not None:
            return fallback
        gray = np.asarray(norm * 255.0, dtype=np.uint8)
        return np.dstack([gray, gray, gray])


def _fallback_colormap_rgb(norm: np.ndarray, name: str) -> np.ndarray | None:
    palettes = {
        "sdoaia94": ((0, 0, 0), (16, 91, 64), (64, 142, 128), (145, 196, 192), (255, 255, 255)),
        "sdoaia131": ((0, 0, 0), (0, 92, 92), (15, 185, 185), (136, 255, 255), (255, 255, 255)),
        "sdoaia171": ((0, 0, 0), (92, 64, 0), (185, 128, 0), (255, 192, 7), (255, 255, 255)),
        "sdoaia193": ((0, 0, 0), (128, 64, 16), (181, 128, 64), (221, 192, 145), (255, 255, 255)),
        "sdoaia211": ((0, 0, 0), (128, 64, 91), (181, 128, 142), (221, 192, 196), (255, 255, 255)),
        "sdoaia304": ((0, 0, 0), (70, 0, 18), (170, 28, 20), (255, 128, 34), (255, 244, 180)),
        "sdoaia335": ((0, 0, 0), (16, 64, 128), (64, 128, 181), (145, 192, 221), (255, 255, 255)),
        "sdoaia1600": ((0, 0, 0), (91, 91, 16), (142, 142, 64), (196, 196, 145), (255, 255, 255)),
        "sdoaia1700": ((0, 0, 0), (128, 64, 64), (181, 128, 128), (221, 192, 192), (255, 255, 255)),
    }
    colors = palettes.get(str(name or "").lower())
    if not colors:
        return None
    stops = np.linspace(0.0, 1.0, len(colors))
    flat = np.asarray(norm, dtype=float).ravel()
    rgb = np.empty((flat.size, 3), dtype=np.uint8)
    color_arr = np.asarray(colors, dtype=float)
    for channel in range(3):
        rgb[:, channel] = np.clip(np.interp(flat, stops, color_arr[:, channel]), 0, 255).astype(np.uint8)
    return rgb.reshape((*norm.shape, 3))


def _component_centroid(component: np.ndarray, y_start: int, x_start: int) -> tuple[float, float]:
    y_idx, x_idx = np.where(component)
    if x_idx.size == 0:
        return float(y_start), float(x_start)
    return float(y_start + np.mean(y_idx)), float(x_start + np.mean(x_idx))


def _pixel_to_arcsec(x_pix: float, y_pix: float, axis_transform: dict[str, float] | None) -> tuple[float, float]:
    if not axis_transform:
        return float(x_pix), float(y_pix)
    x_ref_pix = float(axis_transform.get("x_ref_pix", 0.0))
    y_ref_pix = float(axis_transform.get("y_ref_pix", 0.0))
    x_scale = float(axis_transform.get("x_scale_arcsec_per_pix", 1.0)) or 1.0
    y_scale = float(axis_transform.get("y_scale_arcsec_per_pix", 1.0)) or 1.0
    x_ref_arcsec = float(axis_transform.get("x_ref_arcsec", 0.0))
    y_ref_arcsec = float(axis_transform.get("y_ref_arcsec", 0.0))
    return (
        x_ref_arcsec + (float(x_pix) - x_ref_pix) * x_scale,
        y_ref_arcsec + (float(y_pix) - y_ref_pix) * y_scale,
    )


def _detect_regions_without_scipy(
    arr: np.ndarray,
    mask: np.ndarray,
    *,
    min_area_px: int,
    max_regions: int,
    axis_transform: dict[str, float] | None,
) -> list[AiaRegion]:
    y_idx, x_idx = np.where(mask)
    if x_idx.size < int(min_area_px):
        return []
    values = arr[y_idx, x_idx]
    x0, x1 = int(np.min(x_idx)), int(np.max(x_idx)) + 1
    y0, y1 = int(np.min(y_idx)), int(np.max(y_idx)) + 1
    cx = float(np.mean(x_idx))
    cy = float(np.mean(y_idx))
    x_arc, y_arc = _pixel_to_arcsec(cx, cy, axis_transform)
    return [
        AiaRegion(
            region_id=1,
            bbox=(x0, x1, y0, y1),
            centroid_x=cx,
            centroid_y=cy,
            centroid_x_arcsec=x_arc,
            centroid_y_arcsec=y_arc,
            area_px=int(x_idx.size),
            peak=float(np.nanmax(values)),
            mean=float(np.nanmean(values)),
        )
    ][: max(0, int(max_regions))]


def _metadata_region_distance(region: AiaRegion, meta: AiaMetadataRegion) -> float | None:
    if meta.bbox_arcsec is not None:
        x0, x1, y0, y1 = meta.bbox_arcsec
        if min(x0, x1) <= region.centroid_x_arcsec <= max(x0, x1) and min(y0, y1) <= region.centroid_y_arcsec <= max(y0, y1):
            return 0.0
    if meta.center_x_arcsec is None or meta.center_y_arcsec is None:
        return None
    return float(
        np.hypot(
            float(region.centroid_x_arcsec) - float(meta.center_x_arcsec),
            float(region.centroid_y_arcsec) - float(meta.center_y_arcsec),
        )
    )


def _metadata_region_from_hek(row: Any) -> AiaMetadataRegion:
    get = row.get if hasattr(row, "get") else lambda key, default=None: getattr(row, key, default)
    noaa = _safe_text(get("ar_noaanum", "")) or _safe_text(get("frm_identifier", ""))
    label = f"NOAA {noaa}" if noaa else _safe_text(get("event_type", "AR")) or "Active Region"
    center_x = _as_float(get("hpc_x", None))
    center_y = _as_float(get("hpc_y", None))
    bbox = _parse_hek_bbox(get("hpc_bbox", None))
    return AiaMetadataRegion(
        label=label,
        noaa_number=noaa,
        event_type=_safe_text(get("event_type", "AR")),
        center_x_arcsec=center_x,
        center_y_arcsec=center_y,
        bbox_arcsec=bbox,
        start_time=get("event_starttime", None),
        end_time=get("event_endtime", None),
        source="HEK",
    )


def _parse_hek_bbox(value: Any) -> tuple[float, float, float, float] | None:
    text = _safe_text(value)
    if not text:
        return None
    import re

    nums = [float(match.group(0)) for match in re.finditer(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)]
    if len(nums) < 4:
        return None
    xs = nums[0::2]
    ys = nums[1::2]
    return (float(min(xs)), float(max(xs)), float(min(ys)), float(max(ys)))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    to_value = getattr(value, "to_value", None)
    if callable(to_value):
        try:
            return float(to_value())
        except Exception:
            pass
    raw = getattr(value, "value", None)
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass
    try:
        return float(value)
    except Exception:
        return None
