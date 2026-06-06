"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import re

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
    transparent_bad_cmap,
)
from src.Backend.noise_reduction import subtract_background_rows
from src.Backend.view_config import normalize_view_config


_FIT_SUFFIXES = (".fit.gz", ".fits.gz", ".fit", ".fits")
DEFAULT_DB_SCALE = 2500.0 / 256.0 / 25.4
PLOTUTIL_DB_SCALE = 2500.0 / 255.0 / 25.4

BACKGROUND_METHOD_MEAN = "mean"
BACKGROUND_METHOD_MEDIAN = "median"
BACKGROUND_METHOD_ROBUST = "robust"
BACKGROUND_METHOD_PLOTUTIL = "plotutil_median_db"

_BACKGROUND_METHOD_LABELS = {
    BACKGROUND_METHOD_MEAN: "Mean",
    BACKGROUND_METHOD_MEDIAN: "Median",
    BACKGROUND_METHOD_ROBUST: "Robust",
    BACKGROUND_METHOD_PLOTUTIL: "Plotutil Median (dB)",
}


def normalize_background_method(method: str, *, strict: bool = False) -> str:
    mode = str(method or "").strip().lower().replace("-", "_").replace(" ", "_")
    if mode in {
        "plotutil",
        "plotutil_median",
        "plotutil_median_(db)",
        "plotutil_median_db",
        "callisto_plotutil",
    }:
        return BACKGROUND_METHOD_PLOTUTIL
    if mode == BACKGROUND_METHOD_MEDIAN:
        return BACKGROUND_METHOD_MEDIAN
    if mode in {BACKGROUND_METHOD_ROBUST, "percentile", "p25"}:
        return BACKGROUND_METHOD_ROBUST
    if mode in {"", BACKGROUND_METHOD_MEAN}:
        return BACKGROUND_METHOD_MEAN
    if strict:
        raise ValueError(f"Unsupported baseline method: {method}")
    return BACKGROUND_METHOD_MEAN


def background_method_label(method: str) -> str:
    return _BACKGROUND_METHOD_LABELS[normalize_background_method(method)]


def _is_fit_path(path: str) -> bool:
    lower = str(path or "").strip().lower()
    return any(lower.endswith(ext) for ext in _FIT_SUFFIXES)


def _strip_fit_suffix(filename: str) -> str:
    name = os.path.basename(str(filename or "").strip())
    lower = name.lower()
    for ext in _FIT_SUFFIXES:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return os.path.splitext(name)[0]


def _sanitize_stem(stem: str) -> str:
    s = str(stem or "").strip()
    if not s:
        s = "output"
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[\\\\/:*?\"<>|]+", "_", s)
    s = s.strip(" .")
    return s or "output"


def list_fit_files(input_dir: str, recursive: bool = False) -> list[str]:
    """
    Return sorted FIT/FITS files found in input_dir.

    By default only top-level files are included.
    """
    base = str(input_dir or "").strip()
    if not base:
        return []
    if not os.path.isdir(base):
        return []

    files: list[str] = []
    if recursive:
        for root, _dirs, names in os.walk(base):
            for name in names:
                path = os.path.join(root, name)
                if _is_fit_path(path):
                    files.append(path)
    else:
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if os.path.isfile(path) and _is_fit_path(path):
                files.append(path)

    files.sort(key=lambda p: os.path.basename(p).lower())
    return files


def subtract_background(
    data: np.ndarray,
    method: str = "mean",
    *,
    gap_row_mask: np.ndarray | None = None,
    equalize_noise: bool = False,
) -> np.ndarray:
    mode = normalize_background_method(method, strict=True)
    baseline_method = BACKGROUND_METHOD_MEDIAN if mode == BACKGROUND_METHOD_PLOTUTIL else mode
    centered = subtract_background_rows(
        data,
        method=baseline_method,
        gap_row_mask=gap_row_mask,
        equalize_noise=equalize_noise,
    )
    if mode == BACKGROUND_METHOD_PLOTUTIL:
        # Equivalent to Plotutil's dref -> Digit2Voltage -> dB -> row-median
        # subtraction. The global minimum offset cancels during subtraction.
        return (centered * np.float32(PLOTUTIL_DB_SCALE)).astype(np.float32, copy=False)
    return centered


def subtract_mean_background(data: np.ndarray) -> np.ndarray:
    return subtract_background(data, method="mean")


def convert_digits_to_db(
    data: np.ndarray,
    cold_digits: float,
    db_scale: float = DEFAULT_DB_SCALE,
) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    return (arr - float(cold_digits)) * float(db_scale)


def build_unique_output_png_path(output_dir: str, input_filename: str) -> str:
    out_dir = str(output_dir or "").strip()
    stem_raw = _strip_fit_suffix(input_filename)
    stem = _sanitize_stem(stem_raw)

    candidate = os.path.join(out_dir, f"{stem}.png")
    if not os.path.exists(candidate):
        return candidate

    idx = 1
    while True:
        candidate = os.path.join(out_dir, f"{stem}_{idx}.png")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _resolve_cmap(cmap_name: str):
    name = str(cmap_name or "").strip()
    if name.lower() == "custom":
        colors = [(0.0, "blue"), (0.5, "red"), (1.0, "yellow")]
        return mcolors.LinearSegmentedColormap.from_list("custom_RdYlBu_batch", colors)
    try:
        return mpl.colormaps.get_cmap(name)
    except Exception:
        return cm.get_cmap("viridis")


def _view_range_from_config(view_config: dict | None) -> dict | None:
    if not view_config:
        return None
    try:
        cfg = normalize_view_config(view_config)
    except Exception:
        return None
    range_payload = cfg.get("range")
    return dict(range_payload) if isinstance(range_payload, dict) else None


def locked_view_overlaps_data(freqs: np.ndarray, time: np.ndarray, view_config: dict | None) -> bool:
    view_range = _view_range_from_config(view_config)
    if not view_range:
        return True
    try:
        extent = matplotlib_extent(freqs, time)
        data_x = (min(float(extent[0]), float(extent[1])), max(float(extent[0]), float(extent[1])))
        data_y = (min(float(extent[2]), float(extent[3])), max(float(extent[2]), float(extent[3])))
        view_x = (float(view_range["time_start_s"]), float(view_range["time_stop_s"]))
        view_y = (float(view_range["freq_min_mhz"]), float(view_range["freq_max_mhz"]))
    except Exception:
        return True
    x_overlap = min(data_x[1], view_x[1]) - max(data_x[0], view_x[0])
    y_overlap = min(data_y[1], view_y[1]) - max(data_y[0], view_y[0])
    return bool(x_overlap > 0.0 and y_overlap > 0.0)


def save_background_subtracted_png(
    data: np.ndarray,
    freqs: np.ndarray,
    time: np.ndarray,
    output_path: str,
    title: str,
    cmap_name: str,
    ut_start_sec: float | None = 0.0,
    cold_digits: float = 0.0,
    db_scale: float = DEFAULT_DB_SCALE,
    data_units: str = "digits",
    view_config: dict | None = None,
) -> None:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data for PNG export, got ndim={arr.ndim}.")

    freq_arr = np.asarray(freqs, dtype=float).ravel()
    time_arr = np.asarray(time, dtype=float).ravel()
    if freq_arr.size == 0 or time_arr.size == 0:
        raise ValueError("Frequency/time axes cannot be empty for PNG export.")

    ut_start = float(ut_start_sec) if ut_start_sec is not None else 0.0
    raw_cfg = dict(view_config or {}) if isinstance(view_config, dict) else {}
    cfg = normalize_view_config(raw_cfg) if raw_cfg else None
    apply_visual = bool(raw_cfg.get("_include_visual", True)) if raw_cfg else False
    visual = dict((cfg or {}).get("visual") or {}) if apply_visual else {}
    use_db = bool(visual.get("use_db", True)) if apply_visual else True
    input_is_db = str(data_units or "").strip().lower() == "db"
    if input_is_db:
        display_data = arr if use_db else (arr / float(db_scale)).astype(np.float32, copy=False)
    else:
        display_data = (
            convert_digits_to_db(arr, cold_digits=float(cold_digits), db_scale=float(db_scale))
            if use_db
            else np.asarray(arr, dtype=np.float32)
        )

    time_start = float(time_arr[0])
    time_end = float(time_arr[-1])
    if abs(time_end - time_start) < 1e-12:
        time_end = time_start + 1.0

    effective_cmap_name = str(visual.get("cmap") or cmap_name) if apply_visual else str(cmap_name or "Custom")
    cmap = transparent_bad_cmap(_resolve_cmap(effective_cmap_name))

    fig = Figure(figsize=(10, 6))
    FigureCanvasAgg(fig)
    try:
        ax = fig.add_subplot(111)
        im = ax.imshow(
            masked_display_data(display_data),
            aspect="auto",
            extent=matplotlib_extent(freq_arr, time_arr),
            cmap=cmap,
        )
        low = float(visual.get("noise_clip_low", 0.0)) if visual else 0.0
        high = float(visual.get("noise_clip_high", 0.0)) if visual else 0.0
        levels = None
        if abs(low) > 1e-9 or abs(high) > 1e-9:
            lo, hi = sorted((low, high))
            if use_db:
                lo = (lo - float(cold_digits)) * float(db_scale)
                hi = (hi - float(cold_digits)) * float(db_scale)
            if hi > lo:
                levels = (lo, hi)
        if levels is None:
            vmin, vmax = finite_data_limits(display_data)
            if vmin is not None and vmax is not None:
                im.set_clim(vmin, vmax)
        else:
            im.set_clim(*levels)
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_label("Intensity [dB]" if use_db else "Intensity [Digits]")

        graph = dict(visual.get("graph") or {}) if apply_visual else {}
        remove_titles = bool(graph.get("remove_titles", False))
        if remove_titles:
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_title("")
        else:
            ax.set_xlabel("Time [UT]" if bool(visual.get("use_utc", True)) else "Time [s]")
            ax.set_ylabel("Frequency [MHz]")
            ax.set_title(str(title or "").strip() or "Background Subtracted")

        show_seconds = abs(time_end - time_start) <= 5.0 * 60.0

        def _fmt_ut(x: float, _pos: int) -> str:
            total = int(round(ut_start + float(x)))
            total %= 24 * 3600
            hh = (total // 3600) % 24
            mm = (total % 3600) // 60
            ss = total % 60
            if show_seconds:
                return f"{hh:02d}:{mm:02d}:{ss:02d}"
            return f"{hh:02d}:{mm:02d}"

        use_utc_ticks = bool(visual.get("use_utc", True)) if apply_visual else True
        if use_utc_ticks:
            ax.xaxis.set_major_formatter(FuncFormatter(_fmt_ut))

        view_range = dict((cfg or {}).get("range") or {}) if cfg else {}
        if view_range:
            ax.set_xlim(float(view_range["time_start_s"]), float(view_range["time_stop_s"]))
            ax.set_ylim(float(view_range["freq_min_mhz"]), float(view_range["freq_max_mhz"]))

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.savefig(output_path, dpi=300, bbox_inches="tight", format="png")
    finally:
        # Explicitly clear figure state between files in long batch runs.
        try:
            fig.clear()
        except Exception:
            pass
