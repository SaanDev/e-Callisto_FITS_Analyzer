"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Row-wise order statistics.

``np.nanmedian`` and ``np.nanpercentile`` each sort the array internally, so the
noise-reduction chain used to sort the same rows five or six times over. These
helpers sort once and read every requested quantile off the sorted rows, which
is the dominant cost in background subtraction and RFI cleaning.

Semantics match ``np.nanpercentile(data, q, axis=1)`` with linear interpolation,
including its treatment of infinities as ordinary values and all-NaN rows as
NaN.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def row_quantiles(
    data: np.ndarray,
    quantiles: Sequence[float],
    *,
    dtype: np.dtype | type = np.float32,
) -> np.ndarray:
    """Row-wise NaN-aware quantiles from a single sort.

    Returns shape ``(len(quantiles), nrows)``. Row ``i`` of the result for
    quantile ``q`` equals ``np.nanpercentile(data[i], q)``.
    """
    arr = np.asarray(data)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")

    n_rows, n_cols = arr.shape
    qs = [float(q) for q in quantiles]
    if n_rows == 0 or n_cols == 0:
        return np.full((len(qs), n_rows), np.nan, dtype=dtype)

    # np.sort places NaN last, so the first `counts[i]` entries of row i are its
    # non-NaN values in ascending order. Infinities keep their natural position,
    # which is what nanpercentile does too.
    ordered = np.sort(arr, axis=1)
    counts = n_cols - np.isnan(arr).sum(axis=1)
    has_values = counts > 0

    rows = np.arange(n_rows)
    span = np.maximum(counts - 1, 0).astype(np.float64)

    out = np.empty((len(qs), n_rows), dtype=dtype)
    for k, q in enumerate(qs):
        position = span * (q / 100.0)
        lower = np.floor(position).astype(np.intp)
        upper = np.ceil(position).astype(np.intp)
        weight = (position - lower).astype(np.float64)

        lower = np.clip(lower, 0, n_cols - 1)
        upper = np.clip(upper, 0, n_cols - 1)

        value = ordered[rows, lower] * (1.0 - weight) + ordered[rows, upper] * weight
        out[k] = np.where(has_values, value, np.nan).astype(dtype)

    return out


def row_median(data: np.ndarray, *, dtype: np.dtype | type = np.float32) -> np.ndarray:
    """Row-wise NaN-aware median, shape ``(nrows,)``."""
    return row_quantiles(data, (50.0,), dtype=dtype)[0]
