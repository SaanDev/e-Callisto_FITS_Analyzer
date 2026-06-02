"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from typing import Any, Iterable

import matplotlib as mpl
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from src.Backend.frequency_axis import (
    finite_data_limits,
    masked_display_data,
    matplotlib_extent,
    pyqtgraph_extent,
    transparent_bad_cmap,
)
from src.Backend.fits_io import extract_ut_start_sec, load_callisto_fits
from src.Backend.noise_reduction import subtract_background_rows
from src.Backend.view_config import normalize_display_range, normalize_visual_config, normalize_view_config


TIME_ALIGNMENT_SECONDS = "seconds"
TIME_ALIGNMENT_UT = "ut"

COLOR_SCALE_SHARED = "shared"
COLOR_SCALE_PER_STATION = "per_station"
COLOR_SCALE_MANUAL = "manual"

NOISE_METHOD_NONE = "none"
NOISE_METHOD_MEAN = "mean"
NOISE_METHOD_MEDIAN = "median"
NOISE_METHOD_ROBUST = "robust"
NOISE_METHOD_CLIP = "clip"

DEFAULT_DB_SCALE = 2500.0 / 256.0 / 25.4


@dataclass(frozen=True)
class ComparisonDataset:
    path: str
    label: str
    data: np.ndarray
    freqs: np.ndarray
    time: np.ndarray
    ut_start_sec: float | None = None
    header0: Any | None = None
    gap_row_mask: np.ndarray | None = None
    frequency_step_mhz: float | None = None
    sources: tuple[str, ...] = field(default_factory=tuple)
    combine_type: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ComparisonViewState:
    alignment_mode: str = TIME_ALIGNMENT_UT
    display_range: dict[str, float] | None = None
    visual: dict[str, Any] = field(default_factory=dict)
    color_scale_mode: str = COLOR_SCALE_SHARED
    manual_limits: tuple[float, float] | None = None


@dataclass(frozen=True)
class ComparisonNoiseSettings:
    method: str = NOISE_METHOD_NONE
    clip_low: float = -5.0
    clip_high: float = 20.0


@dataclass(frozen=True)
class ComparisonRenderResult:
    figure: Figure
    axes: tuple[Any, ...]
    effective_alignment_mode: str
    xlim: tuple[float, float] | None
    ylim: tuple[float, float] | None
    color_limits: tuple[tuple[float, float], ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ComparisonPanelPayload:
    dataset: ComparisonDataset
    time_axis: np.ndarray
    display_data: np.ndarray
    mpl_extent: tuple[float, float, float, float]
    pg_extent: tuple[float, float, float, float]
    levels: tuple[float, float]


def _is_finite_float(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def normalize_comparison_noise_settings(settings: Any | None) -> ComparisonNoiseSettings:
    if isinstance(settings, ComparisonNoiseSettings):
        method = str(settings.method or NOISE_METHOD_NONE).strip().lower()
        clip_low = settings.clip_low
        clip_high = settings.clip_high
    elif isinstance(settings, dict):
        method = str(settings.get("method", NOISE_METHOD_NONE) or NOISE_METHOD_NONE).strip().lower()
        clip_low = settings.get("clip_low", -5.0)
        clip_high = settings.get("clip_high", 20.0)
    else:
        method = NOISE_METHOD_NONE
        clip_low = -5.0
        clip_high = 20.0

    if method not in {
        NOISE_METHOD_NONE,
        NOISE_METHOD_MEAN,
        NOISE_METHOD_MEDIAN,
        NOISE_METHOD_ROBUST,
        NOISE_METHOD_CLIP,
    }:
        method = NOISE_METHOD_NONE

    try:
        low = float(clip_low)
    except Exception:
        low = -5.0
    try:
        high = float(clip_high)
    except Exception:
        high = 20.0
    if not np.isfinite([low, high]).all():
        low, high = -5.0, 20.0
    if low > high:
        low, high = high, low
    return ComparisonNoiseSettings(method=method, clip_low=float(low), clip_high=float(high))


def _noise_settings_sequence(
    count: int,
    noise_settings: Iterable[Any] | None = None,
) -> tuple[ComparisonNoiseSettings, ...]:
    total = max(0, int(count))
    raw = list(noise_settings or [])
    out: list[ComparisonNoiseSettings] = []
    for idx in range(total):
        setting = raw[idx] if idx < len(raw) else None
        out.append(normalize_comparison_noise_settings(setting))
    return tuple(out)


def apply_comparison_noise(
    data: np.ndarray,
    settings: Any | None = None,
    *,
    gap_row_mask: np.ndarray | None = None,
) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D comparison data, got ndim={arr.ndim}.")

    normalized = normalize_comparison_noise_settings(settings)
    if normalized.method == NOISE_METHOD_NONE:
        return arr.astype(np.float32, copy=True)

    method = normalized.method
    if method == NOISE_METHOD_CLIP:
        method = NOISE_METHOD_ROBUST

    reduced = subtract_background_rows(
        arr,
        method=method,
        gap_row_mask=gap_row_mask,
        equalize_noise=False,
    ).astype(np.float32, copy=False)

    if normalized.method == NOISE_METHOD_CLIP:
        reduced = np.clip(reduced, float(normalized.clip_low), float(normalized.clip_high)).astype(np.float32, copy=False)
    return reduced


def _strip_fit_suffix(path: str) -> str:
    name = os.path.basename(str(path or "").strip())
    lower = name.lower()
    for ext in (".fits.gz", ".fit.gz", ".fits", ".fit"):
        if lower.endswith(ext):
            return name[: -len(ext)]
    return os.path.splitext(name)[0]


def station_label_from_header(path: str, header: Any | None) -> str:
    for key in ("STATION", "OBSERVAT", "INSTRUME", "TELESCOP"):
        try:
            value = header.get(key, None) if header is not None else None
        except Exception:
            value = None
        text = " ".join(str(value or "").split()).strip()
        if text:
            return text

    stem = _strip_fit_suffix(path)
    if "_" in stem:
        first = stem.split("_", 1)[0].strip()
        if first:
            return first
    return stem or "Station"


def load_comparison_dataset(path: str, *, memmap: bool = False) -> ComparisonDataset:
    result = load_callisto_fits(path, memmap=memmap)
    warnings: list[str] = []

    data = np.asarray(result.data, dtype=np.float32)
    freqs = np.asarray(result.freqs, dtype=float).ravel()
    time = np.asarray(result.time, dtype=float).ravel()
    if data.ndim != 2:
        raise ValueError(f"Expected 2D FITS data, got ndim={data.ndim}.")
    if freqs.size == 0 or time.size == 0:
        raise ValueError("Frequency/time axes cannot be empty.")
    if data.shape != (freqs.size, time.size):
        raise ValueError(
            f"Data shape {data.shape} does not match axes ({freqs.size}, {time.size})."
        )

    ut_start_sec = extract_ut_start_sec(result.header0)
    if ut_start_sec is None:
        warnings.append("Missing TIME-OBS; UT alignment unavailable for this file.")

    return ComparisonDataset(
        path=str(path),
        label=station_label_from_header(path, result.header0),
        data=data,
        freqs=freqs,
        time=time,
        ut_start_sec=ut_start_sec,
        header0=result.header0,
        sources=(str(path),),
        warnings=tuple(warnings),
    )


def comparison_dataset_from_combined(combined: dict[str, Any]) -> ComparisonDataset:
    data = np.asarray(combined.get("data"), dtype=np.float32)
    freqs = np.asarray(combined.get("freqs"), dtype=float).ravel()
    time = np.asarray(combined.get("time"), dtype=float).ravel()
    if data.ndim != 2:
        raise ValueError(f"Expected 2D combined data, got ndim={data.ndim}.")
    if data.shape != (freqs.size, time.size):
        raise ValueError(
            f"Combined data shape {data.shape} does not match axes ({freqs.size}, {time.size})."
        )

    combine_type = str(combined.get("combine_type") or "combined")
    filename = str(combined.get("filename") or "Combined")
    station = _strip_fit_suffix(filename).split("_", 1)[0].strip()
    prefix = f"{station} " if station else ""
    label = (
        f"{prefix}Combined Frequency"
        if combine_type == "frequency"
        else f"{prefix}Combined Time"
        if combine_type == "time"
        else f"{prefix}Combined"
    )
    warnings: list[str] = []
    if combine_type == "frequency" and combined.get("gap_row_mask") is not None:
        try:
            gap_count = int(np.count_nonzero(np.asarray(combined.get("gap_row_mask"), dtype=bool)))
            if gap_count:
                warnings.append(f"Frequency-combined view contains {gap_count} gap row(s).")
        except Exception:
            pass

    return ComparisonDataset(
        path=filename,
        label=label,
        data=data,
        freqs=freqs,
        time=time,
        ut_start_sec=combined.get("ut_start_sec", None),
        header0=combined.get("header0", None),
        gap_row_mask=None if combined.get("gap_row_mask", None) is None else np.asarray(combined.get("gap_row_mask"), dtype=bool).ravel(),
        frequency_step_mhz=combined.get("frequency_step_mhz", None),
        sources=tuple(str(path) for path in combined.get("sources", ()) or ()),
        combine_type=combine_type,
        warnings=tuple(warnings),
    )


def combined_comparison_dataset_from_paths(file_paths: Iterable[str]) -> ComparisonDataset | None:
    paths = [str(path) for path in file_paths if str(path or "").strip()]
    if len(paths) < 2:
        return None

    from src.Backend.burst_processor import (
        are_frequency_combinable,
        are_time_combinable,
        combine_frequency,
        combine_time,
    )

    try:
        if are_time_combinable(paths):
            return comparison_dataset_from_combined(combine_time(paths))
    except Exception:
        pass
    try:
        if are_frequency_combinable(paths):
            return comparison_dataset_from_combined(combine_frequency(paths))
    except Exception:
        pass
    return None


def combined_comparison_datasets_from_paths(file_paths: Iterable[str]) -> list[ComparisonDataset]:
    paths: list[str] = []
    seen: set[str] = set()
    for path in file_paths:
        text = str(path or "").strip()
        if not text or text in seen:
            continue
        paths.append(text)
        seen.add(text)
    if not paths:
        return []

    combined_all = combined_comparison_dataset_from_paths(paths)
    if combined_all is not None:
        return [combined_all]

    from src.Backend.burst_processor import (
        are_frequency_combinable,
        are_time_combinable,
        combine_frequency,
        combine_time,
        parse_filename,
    )

    path_index = {path: idx for idx, path in enumerate(paths)}
    consumed: set[str] = set()
    output: list[tuple[int, ComparisonDataset]] = []

    def _group_by(key_func):
        groups: dict[tuple[str, ...], list[str]] = {}
        for path in paths:
            if path in consumed:
                continue
            try:
                key = key_func(path)
            except Exception:
                continue
            groups.setdefault(tuple(key), []).append(path)
        return sorted(groups.values(), key=lambda group: min(path_index[path] for path in group))

    def _append_combined(group: list[str], dataset: ComparisonDataset) -> None:
        for path in group:
            consumed.add(path)
        output.append((min(path_index[path] for path in group), dataset))

    for group in _group_by(lambda path: (parse_filename(path)[0], parse_filename(path)[3])):
        if len(group) < 2:
            continue
        try:
            if are_time_combinable(group):
                _append_combined(group, comparison_dataset_from_combined(combine_time(group)))
        except Exception:
            continue

    for group in _group_by(lambda path: (parse_filename(path)[0], parse_filename(path)[1], parse_filename(path)[2])):
        if len(group) < 2:
            continue
        try:
            if are_frequency_combinable(group):
                _append_combined(group, comparison_dataset_from_combined(combine_frequency(group)))
        except Exception:
            continue

    for path in paths:
        if path in consumed:
            continue
        output.append((path_index[path], load_comparison_dataset(path, memmap=False)))

    output.sort(key=lambda item: item[0])
    return [dataset for _idx, dataset in output]


def _normalize_alignment_mode(value: str) -> str:
    return TIME_ALIGNMENT_UT if str(value or "").strip().lower() == TIME_ALIGNMENT_UT else TIME_ALIGNMENT_SECONDS


def _unwrap_seconds_of_day(values: Iterable[float]) -> list[float]:
    raw = [float(v) % 86400.0 for v in values]
    if len(raw) <= 1:
        return raw

    indexed = sorted(enumerate(raw), key=lambda item: item[1])
    vals = [v for _idx, v in indexed]
    gaps: list[tuple[float, int]] = []
    for idx in range(len(vals) - 1):
        gaps.append((vals[idx + 1] - vals[idx], idx))
    gaps.append(((vals[0] + 86400.0) - vals[-1], len(vals) - 1))
    _gap, cut_idx = max(gaps, key=lambda item: item[0])

    ordered = indexed[cut_idx + 1 :] + indexed[: cut_idx + 1]
    unwrapped: list[float] = [0.0] * len(raw)
    day_offset = 0.0
    previous = None
    for original_idx, value in ordered:
        if previous is not None and value + day_offset < previous - 1e-9:
            day_offset += 86400.0
        current = value + day_offset
        unwrapped[original_idx] = current
        previous = current
    return unwrapped


def aligned_time_axes(
    datasets: Iterable[ComparisonDataset],
    alignment_mode: str,
) -> tuple[list[np.ndarray], str, tuple[str, ...]]:
    items = list(datasets)
    requested = _normalize_alignment_mode(alignment_mode)
    warnings: list[str] = []
    if requested != TIME_ALIGNMENT_UT:
        return [np.asarray(item.time, dtype=float) for item in items], TIME_ALIGNMENT_SECONDS, tuple(warnings)

    missing = [item.label for item in items if item.ut_start_sec is None]
    if missing:
        warnings.append("UT alignment unavailable because one or more files are missing TIME-OBS.")
        return [np.asarray(item.time, dtype=float) for item in items], TIME_ALIGNMENT_SECONDS, tuple(warnings)

    starts = _unwrap_seconds_of_day(float(item.ut_start_sec or 0.0) for item in items)
    axes = [np.asarray(item.time, dtype=float) + float(start) for item, start in zip(items, starts)]
    return axes, TIME_ALIGNMENT_UT, tuple(warnings)


def seconds_of_day_range_to_unwrapped(
    start_seconds_of_day: float,
    stop_seconds_of_day: float,
    full_xlim: tuple[float, float],
) -> tuple[float, float] | None:
    try:
        start_sod = float(start_seconds_of_day) % 86400.0
        stop_sod = float(stop_seconds_of_day) % 86400.0
        full_lo = float(min(full_xlim))
        full_hi = float(max(full_xlim))
    except Exception:
        return None
    if not np.isfinite([start_sod, stop_sod, full_lo, full_hi]).all() or full_hi <= full_lo:
        return None
    if abs(stop_sod - start_sod) <= 1e-9:
        return None

    nominal_duration = stop_sod - start_sod if stop_sod > start_sod else (stop_sod + 86400.0) - start_sod
    first_day = int(math.floor(full_lo / 86400.0)) - 1
    last_day = int(math.ceil(full_hi / 86400.0)) + 2
    best: tuple[tuple[float, float, float], tuple[float, float]] | None = None
    for start_day in range(first_day, last_day + 1):
        start = start_sod + start_day * 86400.0
        for stop_day in range(start_day, last_day + 2):
            stop = stop_sod + stop_day * 86400.0
            if stop <= start:
                continue
            overlap = min(full_hi, stop) - max(full_lo, start)
            if overlap <= 0.0:
                continue
            outside = max(0.0, full_lo - start) + max(0.0, stop - full_hi)
            duration_distance = abs((stop - start) - nominal_duration)
            start_distance = abs(start - full_lo)
            score = (outside, duration_distance, start_distance)
            if best is None or score < best[0]:
                best = (score, (start, stop))
    return None if best is None else best[1]


def dataset_extent(dataset: ComparisonDataset, time_axis: np.ndarray | None = None) -> tuple[float, float, float, float]:
    axis = np.asarray(dataset.time if time_axis is None else time_axis, dtype=float).ravel()
    extent = matplotlib_extent(dataset.freqs, axis, default_step=dataset.frequency_step_mhz)
    x0, x1, y0, y1 = (float(extent[0]), float(extent[1]), float(extent[2]), float(extent[3]))
    return min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)


def shared_extent(
    datasets: Iterable[ComparisonDataset],
    alignment_mode: str,
) -> tuple[tuple[float, float], tuple[float, float], str, tuple[str, ...]]:
    items = list(datasets)
    if not items:
        raise ValueError("At least one comparison dataset is required.")
    time_axes, effective_mode, warnings = aligned_time_axes(items, alignment_mode)
    x_values: list[float] = []
    y_values: list[float] = []
    for dataset, axis in zip(items, time_axes):
        x0, x1, y0, y1 = dataset_extent(dataset, axis)
        x_values.extend((x0, x1))
        y_values.extend((y0, y1))
    if not x_values or not y_values:
        raise ValueError("Could not determine comparison extents.")
    return (
        (float(min(x_values)), float(max(x_values))),
        (float(min(y_values)), float(max(y_values))),
        effective_mode,
        warnings,
    )


def comparison_cmap(cmap_name: str):
    name = str(cmap_name or "").strip()
    if name.lower() == "custom":
        colors = [(0.0, "blue"), (0.5, "red"), (1.0, "yellow")]
        return mcolors.LinearSegmentedColormap.from_list("custom_RdYlBu_compare", colors)
    try:
        return mpl.colormaps.get_cmap(name)
    except Exception:
        return cm.get_cmap("viridis")


def _resolve_cmap(cmap_name: str):
    return comparison_cmap(cmap_name)


def display_data_for_visual(data: np.ndarray, visual: dict[str, Any] | None, *, cold_digits: float = 0.0) -> np.ndarray:
    normalized = normalize_visual_config(visual or {})
    arr = np.asarray(data, dtype=np.float32)
    if bool(normalized.get("use_db", False)):
        return (arr - float(cold_digits)) * DEFAULT_DB_SCALE
    return arr


def _compute_color_limits_for_arrays(
    data_arrays: Iterable[np.ndarray],
    visual: dict[str, Any] | None,
    color_scale_mode: str,
    manual_limits: tuple[float, float] | None = None,
) -> tuple[tuple[float, float], ...]:
    arrays = [np.asarray(data, dtype=np.float32) for data in data_arrays]
    if not arrays:
        return tuple()
    mode = str(color_scale_mode or COLOR_SCALE_SHARED).strip().lower()

    if mode == COLOR_SCALE_MANUAL and manual_limits is not None:
        lo, hi = sorted((float(manual_limits[0]), float(manual_limits[1])))
        if _is_finite_float(lo) and _is_finite_float(hi) and hi > lo:
            return tuple((lo, hi) for _ in arrays)

    per_dataset: list[tuple[float, float]] = []
    for data in arrays:
        limits = finite_data_limits(display_data_for_visual(data, visual))
        if limits[0] is None or limits[1] is None:
            per_dataset.append((0.0, 1.0))
        else:
            per_dataset.append((float(limits[0]), float(limits[1])))

    if mode == COLOR_SCALE_PER_STATION:
        return tuple(per_dataset)

    lo = min(limit[0] for limit in per_dataset)
    hi = max(limit[1] for limit in per_dataset)
    if hi <= lo:
        hi = lo + 1e-6
    return tuple((float(lo), float(hi)) for _ in arrays)


def compute_color_limits(
    datasets: Iterable[ComparisonDataset],
    visual: dict[str, Any] | None,
    color_scale_mode: str,
    manual_limits: tuple[float, float] | None = None,
    noise_settings: Iterable[Any] | None = None,
) -> tuple[tuple[float, float], ...]:
    items = list(datasets)
    if not items:
        return tuple()
    settings_seq = _noise_settings_sequence(len(items), noise_settings)
    processed = [
        apply_comparison_noise(item.data, settings, gap_row_mask=item.gap_row_mask)
        for item, settings in zip(items, settings_seq)
    ]
    return _compute_color_limits_for_arrays(
        processed,
        visual,
        color_scale_mode,
        manual_limits=manual_limits,
    )


def comparison_panel_payloads(
    datasets: Iterable[ComparisonDataset],
    *,
    alignment_mode: str = TIME_ALIGNMENT_UT,
    visual: dict[str, Any] | None = None,
    color_scale_mode: str = COLOR_SCALE_SHARED,
    manual_limits: tuple[float, float] | None = None,
    noise_settings: Iterable[Any] | None = None,
) -> tuple[list[ComparisonPanelPayload], str, tuple[str, ...]]:
    items = list(datasets)
    if not items:
        return [], TIME_ALIGNMENT_SECONDS, tuple()
    normalized_visual = normalize_visual_config(visual or {})
    settings_seq = _noise_settings_sequence(len(items), noise_settings)
    time_axes, effective_mode, warnings = aligned_time_axes(items, alignment_mode)
    processed_items = [
        apply_comparison_noise(item.data, settings, gap_row_mask=item.gap_row_mask)
        for item, settings in zip(items, settings_seq)
    ]
    color_limits = _compute_color_limits_for_arrays(
        processed_items,
        normalized_visual,
        color_scale_mode,
        manual_limits=manual_limits,
    )
    payloads: list[ComparisonPanelPayload] = []
    for item, axis, levels, processed in zip(items, time_axes, color_limits, processed_items):
        payloads.append(
            ComparisonPanelPayload(
                dataset=item,
                time_axis=np.asarray(axis, dtype=float),
                display_data=display_data_for_visual(processed, normalized_visual),
                mpl_extent=tuple(float(v) for v in matplotlib_extent(item.freqs, axis, default_step=item.frequency_step_mhz)),
                pg_extent=tuple(float(v) for v in pyqtgraph_extent(item.freqs, axis, default_step=item.frequency_step_mhz)),
                levels=(float(levels[0]), float(levels[1])),
            )
        )
    return payloads, effective_mode, warnings


def _normalize_display_range_or_none(display_range: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(display_range, dict):
        return None
    return normalize_display_range(display_range)


def _range_overlaps_extent(display_range: dict[str, float], extent: tuple[float, float, float, float]) -> bool:
    x0, x1, y0, y1 = extent
    rx0 = float(display_range["time_start_s"])
    rx1 = float(display_range["time_stop_s"])
    ry0 = float(display_range["freq_min_mhz"])
    ry1 = float(display_range["freq_max_mhz"])
    return bool(min(x1, rx1) - max(x0, rx0) > 0.0 and min(y1, ry1) - max(y0, ry0) > 0.0)


def _format_ut_tick(value: float, show_seconds: bool) -> str:
    total = int(round(float(value))) % 86400
    hour = (total // 3600) % 24
    minute = (total % 3600) // 60
    second = total % 60
    if show_seconds:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{hour:02d}:{minute:02d}"


def render_comparison_figure(
    datasets: Iterable[ComparisonDataset],
    *,
    figure: Figure | None = None,
    alignment_mode: str = TIME_ALIGNMENT_UT,
    display_range: dict[str, Any] | None = None,
    visual: dict[str, Any] | None = None,
    color_scale_mode: str = COLOR_SCALE_SHARED,
    manual_limits: tuple[float, float] | None = None,
    noise_settings: Iterable[Any] | None = None,
    title: str = "Multi-Station Comparison",
) -> ComparisonRenderResult:
    items = list(datasets)
    if not items:
        raise ValueError("At least one comparison dataset is required.")

    fig = figure if figure is not None else Figure(figsize=(11, max(3.0, 2.2 * len(items))))
    if fig.canvas is None:
        FigureCanvasAgg(fig)
    fig.clear()

    normalized_visual = normalize_visual_config(visual or {})
    display_range_norm = _normalize_display_range_or_none(display_range)
    panel_payloads, effective_mode, alignment_warnings = comparison_panel_payloads(
        items,
        alignment_mode=alignment_mode,
        visual=normalized_visual,
        color_scale_mode=color_scale_mode,
        manual_limits=manual_limits,
        noise_settings=noise_settings,
    )
    xlim = None
    ylim = None
    if display_range_norm:
        xlim = (float(display_range_norm["time_start_s"]), float(display_range_norm["time_stop_s"]))
        ylim = (float(display_range_norm["freq_min_mhz"]), float(display_range_norm["freq_max_mhz"]))
    else:
        x_values: list[float] = []
        y_values: list[float] = []
        for payload in panel_payloads:
            x0, x1, y0, y1 = dataset_extent(payload.dataset, payload.time_axis)
            x_values.extend((x0, x1))
            y_values.extend((y0, y1))
        xlim = (float(min(x_values)), float(max(x_values)))
        ylim = (float(min(y_values)), float(max(y_values)))

    cmap = transparent_bad_cmap(_resolve_cmap(str(normalized_visual.get("cmap") or "Custom")))

    axes = tuple(fig.subplots(len(items), 1, sharex=True, squeeze=False).ravel())
    images = []
    warnings: list[str] = []
    warnings.extend(alignment_warnings)
    for item in items:
        warnings.extend(item.warnings)

    for ax, payload in zip(axes, panel_payloads):
        item = payload.dataset
        image = ax.imshow(
            masked_display_data(payload.display_data),
            aspect="auto",
            extent=payload.mpl_extent,
            cmap=cmap,
            vmin=float(payload.levels[0]),
            vmax=float(payload.levels[1]),
        )
        images.append(image)
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_ylabel("MHz")
        ax.set_title(item.label, loc="left", fontsize=10)
        if display_range_norm and not _range_overlaps_extent(display_range_norm, dataset_extent(item, payload.time_axis)):
            warnings.append(f"{item.label}: no data inside the locked display range.")

    show_seconds = abs(float(xlim[1]) - float(xlim[0])) <= 5.0 * 60.0
    if effective_mode == TIME_ALIGNMENT_UT:
        axes[-1].xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_ut_tick(value, show_seconds)))
        axes[-1].set_xlabel("Time [UT]")
    else:
        axes[-1].set_xlabel("Time [s]")

    unit_label = "Intensity [dB]" if bool(normalized_visual.get("use_db", False)) else "Intensity [Digits]"
    if str(color_scale_mode or "").strip().lower() == COLOR_SCALE_PER_STATION:
        for ax, image in zip(axes, images):
            cbar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.01)
            cbar.set_label(unit_label)
    elif images:
        cbar = fig.colorbar(images[0], ax=list(axes), fraction=0.025, pad=0.01)
        cbar.set_label(unit_label)

    fig.suptitle(str(title or "Multi-Station Comparison"), fontsize=12)
    fig.subplots_adjust(left=0.08, right=0.88, top=0.92, bottom=0.08, hspace=0.32)
    return ComparisonRenderResult(
        figure=fig,
        axes=axes,
        effective_alignment_mode=effective_mode,
        xlim=(float(xlim[0]), float(xlim[1])) if xlim else None,
        ylim=(float(ylim[0]), float(ylim[1])) if ylim else None,
        color_limits=tuple(payload.levels for payload in panel_payloads),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def export_comparison_png(
    datasets: Iterable[ComparisonDataset],
    output_path: str,
    *,
    alignment_mode: str = TIME_ALIGNMENT_UT,
    display_range: dict[str, Any] | None = None,
    visual: dict[str, Any] | None = None,
    color_scale_mode: str = COLOR_SCALE_SHARED,
    manual_limits: tuple[float, float] | None = None,
    noise_settings: Iterable[Any] | None = None,
    title: str = "Multi-Station Comparison",
    dpi: int = 300,
) -> ComparisonRenderResult:
    result = render_comparison_figure(
        datasets,
        alignment_mode=alignment_mode,
        display_range=display_range,
        visual=visual,
        color_scale_mode=color_scale_mode,
        manual_limits=manual_limits,
        noise_settings=noise_settings,
        title=title,
    )
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    result.figure.savefig(output_path, dpi=int(dpi), bbox_inches="tight", format="png")
    return result


def visual_from_view_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return normalize_visual_config({})
    normalized = normalize_view_config(config)
    return dict(normalized.get("visual") or {})
