"""
Batch FIT processing helpers for e-CALLISTO FITS Analyzer.
"""

from __future__ import annotations

import os
import re

import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


_FIT_SUFFIXES = (".fit.gz", ".fits.gz", ".fit", ".fits")


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


def subtract_mean_background(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data for background subtraction, got ndim={arr.ndim}.")
    row_mean = arr.mean(axis=1, keepdims=True, dtype=np.float32)
    return (arr - row_mean).astype(np.float32, copy=False)


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
        return cm.get_cmap(name)
    except Exception:
        return cm.get_cmap("viridis")


def save_background_subtracted_png(
    data: np.ndarray,
    freqs: np.ndarray,
    time: np.ndarray,
    output_path: str,
    title: str,
    cmap_name: str,
) -> None:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data for PNG export, got ndim={arr.ndim}.")

    freq_arr = np.asarray(freqs, dtype=float).ravel()
    time_arr = np.asarray(time, dtype=float).ravel()
    if freq_arr.size == 0 or time_arr.size == 0:
        raise ValueError("Frequency/time axes cannot be empty for PNG export.")

    extent = [0.0, float(time_arr[-1]), float(freq_arr[-1]), float(freq_arr[0])]
    cmap = _resolve_cmap(cmap_name)

    fig = Figure(figsize=(10, 6))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    im = ax.imshow(arr, aspect="auto", extent=extent, cmap=cmap)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Intensity [Digits]")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Frequency [MHz]")
    ax.set_title(str(title or "").strip() or "Background Subtracted")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight", format="png")
