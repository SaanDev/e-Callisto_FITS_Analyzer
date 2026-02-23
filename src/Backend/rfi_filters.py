"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    from scipy.ndimage import median_filter as _median_filter
except Exception:  # pragma: no cover - scipy optional in isolated environments
    _median_filter = None


@dataclass(frozen=True)
class RFIResult:
    data: np.ndarray
    masked_channel_indices: list[int]


def _robust_z(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    med = np.nanmedian(arr)
    mad = np.nanmedian(np.abs(arr - med))
    if not np.isfinite(mad) or mad <= 0:
        std = np.nanstd(arr)
        if np.isfinite(std) and std > 0:
            return (arr - med) / std
        return np.where(np.abs(arr - med) > 0, np.inf, 0.0).astype(float)
    return 0.6745 * (arr - med) / mad


def _ensure_odd(v: int) -> int:
    out = max(1, int(v))
    if out % 2 == 0:
        out += 1
    return out


def _median2d(arr: np.ndarray, kernel_freq: int, kernel_time: int) -> np.ndarray:
    if _median_filter is None:
        return arr.copy()
    return _median_filter(arr, size=(_ensure_odd(kernel_freq), _ensure_odd(kernel_time)), mode="nearest")


def _mask_hot_channels(data: np.ndarray, z_thresh: float) -> list[int]:
    if data.ndim != 2 or data.shape[0] == 0:
        return []

    # Per-frequency score: blend absolute channel level and channel variability.
    row_med = np.nanmedian(data, axis=1)
    row_centered = np.nanmedian(np.abs(data - row_med[:, None]), axis=1)
    score = np.abs(row_med) + row_centered
    z = _robust_z(score)
    idx = np.where(z > float(z_thresh))[0]
    return [int(i) for i in idx.tolist()]


def _repair_masked_channels(cleaned: np.ndarray, masked_indices: list[int]) -> np.ndarray:
    if cleaned.ndim != 2 or not masked_indices:
        return cleaned

    out = cleaned.copy()
    n_rows = out.shape[0]
    for idx in masked_indices:
        if idx <= 0:
            donor = 1 if n_rows > 1 else 0
        elif idx >= n_rows - 1:
            donor = n_rows - 2 if n_rows > 1 else 0
        else:
            out[idx] = 0.5 * (out[idx - 1] + out[idx + 1])
            continue

        out[idx] = out[donor]
    return out


def _percentile_clip_per_channel(data: np.ndarray, upper_percentile: float) -> np.ndarray:
    if data.ndim != 2:
        return data

    pct = float(upper_percentile)
    if pct <= 0 or pct >= 100:
        return data

    out = data.copy()
    # clip only high outliers; preserve low side for burst morphology
    highs = np.nanpercentile(out, pct, axis=1)
    for i in range(out.shape[0]):
        out[i] = np.minimum(out[i], highs[i])
    return out


def clean_rfi(
    data: np.ndarray,
    *,
    kernel_time: int = 3,
    kernel_freq: int = 3,
    channel_z_threshold: float = 6.0,
    percentile_clip: float = 99.5,
    enabled: bool = True,
) -> RFIResult:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError("RFI cleaning expects 2D (freq, time) data.")
    if not enabled:
        return RFIResult(data=arr.copy(), masked_channel_indices=[])

    filtered = _median2d(arr, kernel_freq=kernel_freq, kernel_time=kernel_time)
    residual = arr - filtered
    masked = _mask_hot_channels(arr, z_thresh=float(channel_z_threshold))
    repaired = _repair_masked_channels(filtered, masked)
    clipped = _percentile_clip_per_channel(repaired, upper_percentile=float(percentile_clip))

    return RFIResult(data=np.asarray(clipped, dtype=np.float32), masked_channel_indices=masked)


def config_dict(
    *,
    enabled: bool,
    kernel_time: int,
    kernel_freq: int,
    channel_z_threshold: float,
    percentile_clip: float,
    masked_channel_indices: list[int] | None,
    applied: bool,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "kernel_time": int(kernel_time),
        "kernel_freq": int(kernel_freq),
        "channel_z_threshold": float(channel_z_threshold),
        "percentile_clip": float(percentile_clip),
        "masked_channel_indices": [int(i) for i in (masked_channel_indices or [])],
        "applied": bool(applied),
    }
