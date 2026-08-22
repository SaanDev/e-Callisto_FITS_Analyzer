"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Jitted array primitives shared by the accelerated code paths.

Every function here assumes a JAX backend is already active — callers reach them
through :func:`src.Backend.compute.dispatch`, which owns the "is it worth it?"
decision and the NumPy fallback. Each one takes and returns device arrays; the
conversion back to NumPy happens at the public boundary in the calling module.

Two JAX constraints shape the code below:

  * arrays are immutable, so row writes become ``.at[].set()``;
  * boolean indexing produces a dynamic shape, which ``jit`` rejects. Wherever
    the NumPy version compacts to valid rows and writes results back, the JAX
    version computes over every row and masks afterwards with ``jnp.where``.
    The results are identical because the reductions are all NaN-aware.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.Backend.compute import jit_kernel, numpy_module

_ROBUST_SIGMA_SCALE = 1.4826
_IQR_TO_SIGMA = 1.349
_MIN_NOISE_SCALE = 1e-6

# Above this the shifted-stack median needs k^2 copies of the image, which stops
# being a good trade. scipy handles the rare large-kernel case instead.
MAX_STACK_MEDIAN_KERNEL = 7


def _jnp() -> Any:
    return numpy_module()


# --------------------------------------------------------------------------
# Row statistics (noise reduction)
# --------------------------------------------------------------------------


def _invalid_rows(jnp: Any, arr: Any, gap_row_mask: Any) -> Any:
    mask = ~jnp.any(jnp.isfinite(arr), axis=1)
    if gap_row_mask is not None:
        gap = jnp.asarray(gap_row_mask, dtype=bool).ravel()
        if gap.shape[0] == arr.shape[0]:
            mask = jnp.logical_or(mask, gap)
    return mask


def _rowwise_baseline_impl(arr, row_invalid, mode_code, robust_percentile):
    """mode_code: 0 = mean, 1 = median, 2 = robust percentile."""
    jnp = _jnp()
    if mode_code == 0:
        baseline = jnp.nanmean(arr, axis=1, keepdims=True)
    elif mode_code == 1:
        baseline = jnp.nanmedian(arr, axis=1, keepdims=True)
    else:
        pct = jnp.clip(robust_percentile, 0.0, 50.0)
        baseline = jnp.nanpercentile(arr, pct, axis=1, keepdims=True)
    baseline = baseline.astype(jnp.float32)
    return jnp.where(row_invalid[:, None], jnp.float32(jnp.nan), baseline)


def _rowwise_noise_scale_impl(arr, row_invalid):
    jnp = _jnp()
    center = jnp.nanmedian(arr, axis=1, keepdims=True).astype(jnp.float32)
    mad = jnp.nanmedian(jnp.abs(arr - center), axis=1, keepdims=True).astype(jnp.float32)
    sigma = mad * jnp.float32(_ROBUST_SIGMA_SCALE)

    q75 = jnp.nanpercentile(arr, 75.0, axis=1, keepdims=True)
    q25 = jnp.nanpercentile(arr, 25.0, axis=1, keepdims=True)
    fallback = ((q75 - q25) / jnp.float32(_IQR_TO_SIGMA)).astype(jnp.float32)

    good = jnp.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE)
    sigma = jnp.where(good, sigma, fallback)

    std = jnp.nanstd(arr, axis=1, keepdims=True).astype(jnp.float32)
    good = jnp.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE)
    sigma = jnp.where(good, sigma, std)

    good = jnp.isfinite(sigma) & (sigma > _MIN_NOISE_SCALE)
    sigma = jnp.where(good, sigma, jnp.float32(1.0)).astype(jnp.float32)

    return jnp.where(row_invalid[:, None], jnp.float32(jnp.nan), sigma)


def _subtract_background_impl(
    arr,
    gap_row_mask,
    mode_code,
    robust_percentile,
    equalize_noise,
    equalize_percentile,
    attenuate_only,
):
    jnp = _jnp()
    row_invalid = _invalid_rows(jnp, arr, gap_row_mask)
    baseline = _rowwise_baseline_impl(arr, row_invalid, mode_code, robust_percentile)
    out = (arr - baseline).astype(jnp.float32)

    if equalize_noise:
        scales = _rowwise_noise_scale_impl(out, row_invalid)
        row_scales = scales[:, 0]
        valid = (~row_invalid) & jnp.isfinite(row_scales) & (row_scales > _MIN_NOISE_SCALE)

        # NumPy compacts to `row_scales[valid]` here; masking to NaN is
        # equivalent because nanpercentile ignores NaN, and it keeps the shape
        # static so this stays jittable.
        masked_scales = jnp.where(valid, row_scales, jnp.nan)
        target = jnp.nanpercentile(
            masked_scales,
            jnp.clip(equalize_percentile, 0.0, 100.0),
        )

        row_factors = (target / row_scales).astype(jnp.float32)
        if attenuate_only:
            row_factors = jnp.minimum(row_factors, jnp.float32(1.0))

        usable = valid & jnp.isfinite(target) & (target > _MIN_NOISE_SCALE)
        factors = jnp.where(usable, row_factors, jnp.float32(1.0)).astype(jnp.float32)
        out = (out * factors[:, None]).astype(jnp.float32)

    return jnp.where(row_invalid[:, None], jnp.float32(jnp.nan), out)


rowwise_baseline_kernel = jit_kernel(
    _rowwise_baseline_impl,
    static_argnums=(2,),
)
rowwise_noise_scale_kernel = jit_kernel(_rowwise_noise_scale_impl)
subtract_background_kernel = jit_kernel(
    _subtract_background_impl,
    static_argnums=(2, 4, 6),
)


# --------------------------------------------------------------------------
# RFI cleaning
# --------------------------------------------------------------------------


def _robust_z_impl(values):
    jnp = _jnp()
    arr = values.astype(jnp.float64)
    med = jnp.nanmedian(arr)
    mad = jnp.nanmedian(jnp.abs(arr - med))
    std = jnp.nanstd(arr)

    mad_ok = jnp.isfinite(mad) & (mad > 0)
    std_ok = jnp.isfinite(std) & (std > 0)

    by_mad = 0.6745 * (arr - med) / jnp.where(mad_ok, mad, 1.0)
    by_std = (arr - med) / jnp.where(std_ok, std, 1.0)
    degenerate = jnp.where(jnp.abs(arr - med) > 0, jnp.inf, 0.0)

    return jnp.where(mad_ok, by_mad, jnp.where(std_ok, by_std, degenerate))


def _median_filter_2d_impl(arr, kernel_freq: int, kernel_time: int):
    """Median over a (kernel_freq, kernel_time) window with edge replication.

    Built as a stack of k*k edge-padded shifts because JAX has no direct
    equivalent of ``scipy.ndimage.median_filter`` — ``lax.reduce_window`` cannot
    express a median.
    """
    jnp = _jnp()
    kf = int(kernel_freq)
    kt = int(kernel_time)
    pf = kf // 2
    pt = kt // 2

    padded = jnp.pad(arr, ((pf, pf), (pt, pt)), mode="edge")
    rows, cols = arr.shape

    windows = [
        padded[i : i + rows, j : j + cols]
        for i in range(kf)
        for j in range(kt)
    ]
    return jnp.median(jnp.stack(windows, axis=0), axis=0).astype(arr.dtype)


def _hot_channel_score_impl(data):
    jnp = _jnp()
    row_med = jnp.nanmedian(data, axis=1)
    row_spread = jnp.nanmedian(jnp.abs(data - row_med[:, None]), axis=1)
    score = jnp.abs(row_med) + row_spread
    return _robust_z_impl(score)


def _percentile_clip_impl(data, upper_percentile):
    jnp = _jnp()
    highs = jnp.nanpercentile(data, upper_percentile, axis=1)
    return jnp.minimum(data, highs[:, None])


robust_z_kernel = jit_kernel(_robust_z_impl)
median_filter_2d_kernel = jit_kernel(_median_filter_2d_impl, static_argnums=(1, 2))
hot_channel_score_kernel = jit_kernel(_hot_channel_score_impl)
percentile_clip_kernel = jit_kernel(_percentile_clip_impl)


# --------------------------------------------------------------------------
# Downsampling and block reduction
# --------------------------------------------------------------------------


def _block_reduce_mean_impl(arr, factor: int):
    jnp = _jnp()
    f = int(factor)
    rows = (arr.shape[0] // f) * f
    cols = (arr.shape[1] // f) * f
    trimmed = arr[:rows, :cols]
    reshaped = trimmed.reshape(rows // f, f, cols // f, f)
    return jnp.nanmean(reshaped, axis=(1, 3))


def _peak_downsample_impl(arr, count: int):
    """Column-wise max into ``count`` equal blocks, NaN-padding the remainder."""
    jnp = _jnp()
    n_cols = arr.shape[1]
    block = -(-n_cols // int(count))  # ceil
    padded_cols = block * int(count)
    pad = padded_cols - n_cols
    if pad:
        arr = jnp.pad(arr, ((0, 0), (0, pad)), mode="constant", constant_values=jnp.nan)
    reshaped = arr.reshape(arr.shape[0], int(count), block)
    return jnp.nanmax(reshaped, axis=2)


block_reduce_mean_kernel = jit_kernel(_block_reduce_mean_impl, static_argnums=(1,))
peak_downsample_kernel = jit_kernel(_peak_downsample_impl, static_argnums=(1,))


# --------------------------------------------------------------------------
# Radial statistics (coronagraph)
# --------------------------------------------------------------------------


def _radial_distance_impl(ny: int, nx: int, cy, cx):
    jnp = _jnp()
    yy = jnp.arange(int(ny), dtype=jnp.float64)[:, None]
    xx = jnp.arange(int(nx), dtype=jnp.float64)[None, :]
    return jnp.hypot(xx - cx, yy - cy)


def _radial_bin_stats_impl(flat_i, flat_r, edges, n_bins: int, lo, hi):
    jnp = _jnp()
    finite = jnp.isfinite(flat_i) & (flat_r >= lo) & (flat_r <= hi)
    values = jnp.where(finite, flat_i, 0.0)
    weights = finite.astype(jnp.float64)

    idx = jnp.clip(jnp.digitize(flat_r, edges) - 1, 0, int(n_bins) - 1)

    counts = jnp.bincount(idx, weights=weights, length=int(n_bins))
    sums = jnp.bincount(idx, weights=values, length=int(n_bins))
    sums_sq = jnp.bincount(idx, weights=values * values, length=int(n_bins))

    nonzero = counts > 0
    safe_counts = jnp.where(nonzero, counts, 1.0)
    mean = jnp.where(nonzero, sums / safe_counts, jnp.nan)
    var = jnp.where(nonzero, sums_sq / safe_counts - mean**2, jnp.nan)
    var = jnp.where(jnp.isfinite(var), jnp.clip(var, 0.0, None), jnp.nan)
    return mean, jnp.sqrt(var), counts


radial_distance_kernel = jit_kernel(_radial_distance_impl, static_argnums=(0, 1))
radial_bin_stats_kernel = jit_kernel(_radial_bin_stats_impl, static_argnums=(3,))


# --------------------------------------------------------------------------
# Live preview: normalise and colour-map in one pass
# --------------------------------------------------------------------------


def _rgba_from_lut_impl(work, lut, vmin, vmax):
    """Map a 2-D array through a uint8 colour table.

    Mirrors the NumPy path in ``src/UI/accelerated_plot_widget`` exactly,
    including matplotlib's ``floor(norm * N)`` bucketing, so the two backends
    produce identical images. Gap-row shading and the alpha mask stay on the
    host: they are cheap and touch only a few rows.
    """
    jnp = _jnp()
    levels = lut.shape[0]
    scale = jnp.maximum(vmax - vmin, jnp.float32(1e-12))

    index = (work - vmin) * (levels / scale)
    index = jnp.nan_to_num(index, nan=0.0, posinf=float(levels), neginf=0.0)
    index = jnp.clip(index, 0.0, float(levels - 1)).astype(jnp.int32)

    rgba = jnp.take(lut, index, axis=0)
    alpha = jnp.where(jnp.isfinite(work), rgba[..., 3], jnp.uint8(0))
    return rgba.at[..., 3].set(alpha)


rgba_from_lut_kernel = jit_kernel(_rgba_from_lut_impl)


# --------------------------------------------------------------------------
# Warmup
# --------------------------------------------------------------------------


def warmup_thunks(shape: tuple[int, int]) -> list:
    """Zero-arg thunks that compile the kernels used at ``shape``.

    XLA compiles per input shape, so the first slider drag after a file loads
    would otherwise pay the compile cost. A spectrogram keeps one shape for the
    whole of a drag, so warming once per load covers the interactive path.
    """
    from src.Backend import compute

    if not compute.should_accelerate(shape, gpu_only=True):
        return []

    try:
        rows, cols = int(shape[0]), int(shape[1])
    except Exception:
        return []
    if rows <= 0 or cols <= 0:
        return []

    sample = np.zeros((rows, cols), dtype=np.float32)
    gap = np.zeros(rows, dtype=bool)
    lut = np.zeros((256, 4), dtype=np.uint8)

    def _background():
        return subtract_background_kernel(
            compute.to_device(sample, dtype="float32"), gap, 2, 25.0, True, 25.0, True
        )

    def _median():
        return median_filter_2d_kernel(compute.to_device(sample), 3, 3)

    def _preview():
        return rgba_from_lut_kernel(
            compute.to_device(sample, dtype="float32"),
            compute.to_device(lut),
            np.float32(0.0),
            np.float32(1.0),
        )

    return [_background, _median, _preview]


def _rgb_lut_impl(norm, lut):
    """Colour-map a normalised [0, 1] image through a uint8 table."""
    jnp = _jnp()
    levels = lut.shape[0]
    index = norm * levels
    index = jnp.nan_to_num(index, nan=0.0, posinf=float(levels), neginf=0.0)
    index = jnp.clip(index, 0.0, float(levels - 1)).astype(jnp.int32)
    return jnp.take(lut, index, axis=0)


rgb_lut_kernel = jit_kernel(_rgb_lut_impl)
