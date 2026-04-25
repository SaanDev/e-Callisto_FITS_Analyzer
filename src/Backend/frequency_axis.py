"""
Frequency-axis helpers for CALLISTO spectrogram rendering and combination.
"""

from __future__ import annotations

import copy

import numpy as np


DEFAULT_FREQUENCY_DIRECTION = -1
_STEP_EPS = 1e-9


def dominant_frequency_direction(freqs: np.ndarray, default: int = DEFAULT_FREQUENCY_DIRECTION) -> int:
    arr = np.asarray(freqs, dtype=float).ravel()
    if arr.size < 2:
        return int(default)

    diffs = np.diff(arr)
    diffs = diffs[np.isfinite(diffs)]
    diffs = diffs[np.abs(diffs) > _STEP_EPS]
    if diffs.size == 0:
        return int(default)
    return -1 if float(np.nanmedian(diffs)) < 0.0 else 1


def orient_frequency_rows(
    data: np.ndarray,
    freqs: np.ndarray,
    *,
    direction: int = DEFAULT_FREQUENCY_DIRECTION,
) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data)
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    target = -1 if int(direction) < 0 else 1
    current = dominant_frequency_direction(freq_arr, default=target)
    if current == target:
        return arr, freq_arr
    return arr[::-1, ...], freq_arr[::-1]


def orient_frequency_axis(freqs: np.ndarray, *, direction: int = DEFAULT_FREQUENCY_DIRECTION) -> np.ndarray:
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    target = -1 if int(direction) < 0 else 1
    current = dominant_frequency_direction(freq_arr, default=target)
    if current == target:
        return freq_arr
    return freq_arr[::-1]


def frequency_step_mhz(freqs: np.ndarray, default: float = 1.0) -> float:
    arr = np.asarray(freqs, dtype=float).ravel()
    if arr.size < 2:
        return float(default)
    diffs = np.diff(arr)
    diffs = np.abs(diffs[np.isfinite(diffs)])
    diffs = diffs[diffs > _STEP_EPS]
    if diffs.size == 0:
        return float(default)
    return float(np.nanmedian(diffs))


def axis_edges(values: np.ndarray, default_step: float = 1.0) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        return np.empty(0, dtype=float)

    if arr.size == 1:
        half = 0.5 * float(default_step)
        return np.array([float(arr[0]) + half, float(arr[0]) - half], dtype=float)

    edges = np.empty(arr.size + 1, dtype=float)
    edges[1:-1] = 0.5 * (arr[:-1] + arr[1:])
    edges[0] = float(arr[0]) + 0.5 * float(arr[0] - arr[1])
    edges[-1] = float(arr[-1]) + 0.5 * float(arr[-1] - arr[-2])
    return edges


def frequency_edges(freqs: np.ndarray, default_step: float = 1.0) -> np.ndarray:
    return axis_edges(freqs, default_step=default_step)


def frequency_gap_spans(
    freqs: np.ndarray,
    gap_row_mask: np.ndarray | None,
    *,
    default_step: float = 1.0,
) -> list[tuple[float, float]]:
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    if gap_row_mask is None or freq_arr.size == 0:
        return []

    mask = np.asarray(gap_row_mask, dtype=bool).ravel()
    if mask.shape[0] != freq_arr.size or not np.any(mask):
        return []

    edges = frequency_edges(freq_arr, default_step=default_step)
    if edges.size != freq_arr.size + 1:
        return []

    spans: list[tuple[float, float]] = []
    idx = 0
    while idx < mask.size:
        if not bool(mask[idx]):
            idx += 1
            continue

        start = idx
        while idx < mask.size and bool(mask[idx]):
            idx += 1
        end = idx

        lo = min(float(edges[start]), float(edges[end]))
        hi = max(float(edges[start]), float(edges[end]))
        spans.append((lo, hi))

    return spans


def time_bounds(time: np.ndarray) -> tuple[float, float]:
    arr = np.asarray(time, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("Time axis cannot be empty.")
    x0 = float(arr[0])
    x1 = float(arr[-1])
    if abs(x1 - x0) < _STEP_EPS:
        x1 = x0 + 1.0
    return x0, x1


def matplotlib_extent(freqs: np.ndarray, time: np.ndarray, default_step: float | None = None) -> list[float]:
    step = float(frequency_step_mhz(freqs, default=1.0) if default_step is None else default_step)
    edges = frequency_edges(freqs, default_step=step)
    if edges.size < 2:
        raise ValueError("Frequency axis cannot be empty.")
    x0, x1 = time_bounds(time)
    return [x0, x1, float(edges[-1]), float(edges[0])]


def pyqtgraph_extent(freqs: np.ndarray, time: np.ndarray, default_step: float | None = None) -> list[float]:
    step = float(frequency_step_mhz(freqs, default=1.0) if default_step is None else default_step)
    edges = frequency_edges(freqs, default_step=step)
    if edges.size < 2:
        raise ValueError("Frequency axis cannot be empty.")
    x0, x1 = time_bounds(time)
    return [x0, x1, float(edges[0]), float(edges[-1])]


def invalid_row_mask(data: np.ndarray, gap_row_mask: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(data)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")
    mask = ~np.any(np.isfinite(arr), axis=1)
    if gap_row_mask is not None:
        gap_mask = np.asarray(gap_row_mask, dtype=bool).ravel()
        if gap_mask.shape[0] == arr.shape[0]:
            mask = np.logical_or(mask, gap_mask)
    return mask


def finite_data_limits(data: np.ndarray) -> tuple[float, float] | tuple[None, None]:
    arr = np.asarray(data, dtype=float)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return None, None
    vmin = float(np.nanmin(arr))
    vmax = float(np.nanmax(arr))
    if vmax <= vmin:
        vmax = vmin + 1e-6
    return vmin, vmax


def masked_display_data(data: np.ndarray) -> np.ma.MaskedArray:
    return np.ma.masked_invalid(np.asarray(data, dtype=float))


def transparent_bad_cmap(cmap):
    if cmap is None:
        return None
    try:
        out = copy.copy(cmap)
        if hasattr(out, "copy"):
            out = out.copy()
    except Exception:
        out = cmap

    try:
        out.set_bad((0.0, 0.0, 0.0, 0.0))
    except Exception:
        pass
    return out
