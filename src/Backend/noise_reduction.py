"""
Shared background-subtraction helpers for dynamic spectrum noise reduction.
"""

from __future__ import annotations

import numpy as np

from src.Backend.frequency_axis import invalid_row_mask


ROBUST_BASELINE_PERCENTILE = 25.0
NOISE_EQUALIZE_TARGET_PERCENTILE = 25.0
_ROBUST_SIGMA_SCALE = 1.4826
_IQR_TO_SIGMA = 1.349
_MIN_NOISE_SCALE = 1e-6


def rowwise_baseline(
    data: np.ndarray,
    method: str = "mean",
    *,
    gap_row_mask: np.ndarray | None = None,
    robust_percentile: float = ROBUST_BASELINE_PERCENTILE,
) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")

    row_invalid = invalid_row_mask(arr, gap_row_mask)
    baseline = np.full((arr.shape[0], 1), np.nan, dtype=np.float32)
    valid_rows = ~row_invalid
    if not np.any(valid_rows):
        return baseline

    rows = arr[valid_rows, :]
    mode = str(method or "").strip().lower() or "mean"

    if mode == "mean":
        baseline[valid_rows, :] = np.nanmean(rows, axis=1, keepdims=True).astype(np.float32)
    elif mode == "median":
        baseline[valid_rows, :] = np.nanmedian(rows, axis=1, keepdims=True).astype(np.float32)
    elif mode in {"robust", "percentile", "p25"}:
        percentile = float(np.clip(robust_percentile, 0.0, 50.0))
        baseline[valid_rows, :] = np.nanpercentile(
            rows,
            percentile,
            axis=1,
            keepdims=True,
        ).astype(np.float32)
    else:
        raise ValueError(f"Unsupported baseline method: {method}")

    baseline[row_invalid, :] = np.nan
    return baseline


def rowwise_noise_scale(data: np.ndarray, *, gap_row_mask: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")

    row_invalid = invalid_row_mask(arr, gap_row_mask)
    scale = np.full((arr.shape[0], 1), np.nan, dtype=np.float32)
    valid_rows = ~row_invalid
    if not np.any(valid_rows):
        return scale

    rows = arr[valid_rows, :]
    center = np.nanmedian(rows, axis=1, keepdims=True).astype(np.float32)
    mad = np.nanmedian(np.abs(rows - center), axis=1, keepdims=True).astype(np.float32)
    sigma = mad * np.float32(_ROBUST_SIGMA_SCALE)

    fallback = (
        (
            np.nanpercentile(rows, 75.0, axis=1, keepdims=True)
            - np.nanpercentile(rows, 25.0, axis=1, keepdims=True)
        )
        / np.float32(_IQR_TO_SIGMA)
    ).astype(np.float32)
    sigma = np.where(np.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE), sigma, fallback)

    std = np.nanstd(rows, axis=1, keepdims=True).astype(np.float32)
    sigma = np.where(np.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE), sigma, std)
    sigma = np.where(np.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE), sigma, 1.0).astype(np.float32)

    scale[valid_rows, :] = sigma
    scale[row_invalid, :] = np.nan
    return scale


def subtract_background_rows(
    data: np.ndarray,
    *,
    method: str = "mean",
    gap_row_mask: np.ndarray | None = None,
    robust_percentile: float = ROBUST_BASELINE_PERCENTILE,
    equalize_noise: bool = False,
    equalize_percentile: float = NOISE_EQUALIZE_TARGET_PERCENTILE,
    attenuate_only: bool = True,
) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")

    row_invalid = invalid_row_mask(arr, gap_row_mask)
    baseline = rowwise_baseline(
        arr,
        method=method,
        gap_row_mask=gap_row_mask,
        robust_percentile=robust_percentile,
    )
    out = (arr - baseline).astype(np.float32, copy=False)

    if equalize_noise:
        scales = rowwise_noise_scale(out, gap_row_mask=gap_row_mask)
        row_scales = scales[:, 0]
        valid_rows = (~row_invalid) & np.isfinite(row_scales) & (row_scales > _MIN_NOISE_SCALE)
        valid_scales = row_scales[valid_rows]
        if valid_scales.size:
            target = float(np.nanpercentile(valid_scales, float(np.clip(equalize_percentile, 0.0, 100.0))))
            if np.isfinite(target) and target > _MIN_NOISE_SCALE:
                factors = np.ones(arr.shape[0], dtype=np.float32)
                row_factors = (target / row_scales[valid_rows]).astype(np.float32)
                if attenuate_only:
                    row_factors = np.minimum(row_factors, 1.0).astype(np.float32)
                factors[valid_rows] = row_factors
                out = (out * factors[:, None]).astype(np.float32, copy=False)

    out[row_invalid, :] = np.nan
    return out
