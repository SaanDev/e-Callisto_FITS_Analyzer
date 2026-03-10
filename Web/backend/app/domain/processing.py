from __future__ import annotations

from typing import Any

import numpy as np


def background_reduce(data: np.ndarray, *, clip_low: float, clip_high: float) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    low = float(min(clip_low, clip_high))
    high = float(max(clip_low, clip_high))
    row_mean = arr.mean(axis=1, keepdims=True, dtype=np.float32)
    base = arr - row_mean
    return np.clip(base, low, high).astype(np.float32, copy=False)


def extract_maxima_points(data: np.ndarray, freqs: np.ndarray) -> list[dict[str, float]]:
    arr = np.asarray(data, dtype=np.float32)
    freq_arr = np.asarray(freqs, dtype=np.float32).reshape(-1)
    if arr.ndim != 2:
        raise ValueError("Maxima extraction expects 2D data.")
    if arr.shape[0] != freq_arr.size:
        raise ValueError("Frequency axis length does not match data.")
    peak_indices = np.argmax(arr, axis=0)
    points: list[dict[str, float]] = []
    for channel, freq_index in enumerate(peak_indices.tolist()):
        points.append(
            {
                "timeChannel": float(channel),
                "timeSeconds": float(channel * 0.25),
                "freqMHz": float(freq_arr[int(freq_index)]),
            }
        )
    return points


def build_spectrum_payload(
    *,
    label: str,
    data: np.ndarray,
    freqs: np.ndarray,
    time_axis: np.ndarray,
) -> dict[str, Any]:
    arr = np.asarray(data, dtype=np.float32)
    freq_arr = np.asarray(freqs, dtype=np.float32).reshape(-1)
    time_arr = np.asarray(time_axis, dtype=np.float32).reshape(-1)
    return {
        "label": str(label),
        "shape": [int(arr.shape[0]), int(arr.shape[1])],
        "freqs": freq_arr.astype(float).tolist(),
        "time": time_arr.astype(float).tolist(),
        "data": arr.astype(float).tolist(),
        "displayMin": float(np.nanmin(arr)) if arr.size else None,
        "displayMax": float(np.nanmax(arr)) if arr.size else None,
    }

