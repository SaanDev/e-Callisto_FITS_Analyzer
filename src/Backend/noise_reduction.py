"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np

from src.Backend import compute
from src.Backend.array_stats import row_quantiles
from src.Backend.frequency_axis import invalid_row_mask


ROBUST_BASELINE_PERCENTILE = 25.0
NOISE_EQUALIZE_TARGET_PERCENTILE = 25.0
_ROBUST_SIGMA_SCALE = 1.4826
_IQR_TO_SIGMA = 1.349
_MIN_NOISE_SCALE = 1e-6

_BASELINE_MODES = {"mean": 0, "median": 1, "robust": 2, "percentile": 2, "p25": 2}


def _baseline_mode_code(method: str) -> int:
    mode = str(method or "").strip().lower() or "mean"
    try:
        return _BASELINE_MODES[mode]
    except KeyError:
        raise ValueError(f"Unsupported baseline method: {method}") from None


def _rowwise_baseline_numpy(
    arr: np.ndarray,
    row_invalid: np.ndarray,
    mode_code: int,
    robust_percentile: float,
) -> np.ndarray:
    if mode_code == 0:
        values = np.nanmean(arr, axis=1).astype(np.float32)
    elif mode_code == 1:
        values = row_quantiles(arr, (50.0,))[0]
    else:
        percentile = float(np.clip(robust_percentile, 0.0, 50.0))
        values = row_quantiles(arr, (percentile,))[0]

    baseline = values.reshape(-1, 1).astype(np.float32)
    baseline[row_invalid, :] = np.nan
    return baseline


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

    mode_code = _baseline_mode_code(method)
    row_invalid = invalid_row_mask(arr, gap_row_mask)
    if not np.any(~row_invalid):
        return np.full((arr.shape[0], 1), np.nan, dtype=np.float32)

    return _rowwise_baseline_numpy(arr, row_invalid, mode_code, robust_percentile)


def _rowwise_noise_scale_numpy(arr: np.ndarray, row_invalid: np.ndarray) -> np.ndarray:
    center = row_quantiles(arr, (50.0,))[0]
    mad = row_quantiles(np.abs(arr - center[:, None]), (50.0,))[0]
    sigma = (mad * np.float32(_ROBUST_SIGMA_SCALE)).astype(np.float32)

    # MAD covers almost every row; the interquartile and standard-deviation
    # fallbacks each cost another pass, so only pay for them when a row needs it.
    weak = ~(np.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE))
    if np.any(weak):
        q25, q75 = row_quantiles(arr, (25.0, 75.0))
        iqr = ((q75 - q25) / np.float32(_IQR_TO_SIGMA)).astype(np.float32)
        sigma = np.where(weak, iqr, sigma).astype(np.float32)

        weak = ~(np.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE))
        if np.any(weak):
            std = np.nanstd(arr, axis=1).astype(np.float32)
            sigma = np.where(weak, std, sigma).astype(np.float32)
            weak = ~(np.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE))
            sigma = np.where(weak, np.float32(1.0), sigma).astype(np.float32)

    scale = sigma.reshape(-1, 1).astype(np.float32)
    scale[row_invalid, :] = np.nan
    return scale


def rowwise_noise_scale(data: np.ndarray, *, gap_row_mask: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")

    row_invalid = invalid_row_mask(arr, gap_row_mask)
    if not np.any(~row_invalid):
        return np.full((arr.shape[0], 1), np.nan, dtype=np.float32)

    return _rowwise_noise_scale_numpy(arr, row_invalid)


def _subtract_background_numpy(
    arr: np.ndarray,
    row_invalid: np.ndarray,
    mode_code: int,
    robust_percentile: float,
    equalize_noise: bool,
    equalize_percentile: float,
    attenuate_only: bool,
) -> np.ndarray:
    baseline = _rowwise_baseline_numpy(arr, row_invalid, mode_code, robust_percentile)
    out = (arr - baseline).astype(np.float32, copy=False)

    if equalize_noise:
        scales = _rowwise_noise_scale_numpy(out, row_invalid)
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


def _subtract_background_numpy_entry(
    arr: np.ndarray,
    row_invalid: np.ndarray,
    gap_row_mask: np.ndarray | None,
    mode_code: int,
    robust_percentile: float,
    equalize_noise: bool,
    equalize_percentile: float,
    attenuate_only: bool,
) -> np.ndarray:
    """Shared dispatch signature; ``gap_row_mask`` is already folded into
    ``row_invalid`` for the NumPy path."""
    return _subtract_background_numpy(
        arr,
        row_invalid,
        mode_code,
        robust_percentile,
        equalize_noise,
        equalize_percentile,
        attenuate_only,
    )


def _subtract_background_jax(
    arr: np.ndarray,
    row_invalid: np.ndarray,
    gap_row_mask: np.ndarray | None,
    mode_code: int,
    robust_percentile: float,
    equalize_noise: bool,
    equalize_percentile: float,
    attenuate_only: bool,
) -> np.ndarray:
    """Same signature as the NumPy entry point; ``row_invalid`` is recomputed
    on the device from ``gap_row_mask`` so the whole chain stays in one kernel."""
    from src.Backend.compute_kernels import subtract_background_kernel

    device_arr = compute.to_device(arr, dtype="float32")
    result = subtract_background_kernel(
        device_arr,
        gap_row_mask,
        mode_code,
        float(robust_percentile),
        bool(equalize_noise),
        float(equalize_percentile),
        bool(attenuate_only),
    )
    return compute.to_numpy(result).astype(np.float32, copy=False)


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

    mode_code = _baseline_mode_code(method)
    row_invalid = invalid_row_mask(arr, gap_row_mask)
    if not np.any(~row_invalid):
        out = np.array(arr, copy=True)
        out[row_invalid, :] = np.nan
        return out

    return compute.dispatch(
        _subtract_background_jax,
        _subtract_background_numpy_entry,
        arr,
        row_invalid,
        gap_row_mask,
        mode_code,
        robust_percentile,
        equalize_noise,
        equalize_percentile,
        attenuate_only,
        size_hint=arr,
        # Row statistics are sort-bound: XLA's CPU backend runs the same
        # algorithm as NumPy and measured no faster. Only a GPU pays here.
        gpu_only=True,
    )
