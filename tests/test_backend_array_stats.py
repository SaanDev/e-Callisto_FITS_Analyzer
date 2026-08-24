"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The row-statistics helpers replace repeated ``np.nan*`` reductions with a single
sort. These tests pin them against the reference semantics of the reductions
they stand in for.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.Backend.array_stats import row_median, row_quantiles
from src.Backend.noise_reduction import (
    rowwise_baseline,
    rowwise_noise_scale,
    subtract_background_rows,
)

QUANTILES = (0.0, 25.0, 50.0, 75.0, 99.5, 100.0)


def _cases():
    rng = np.random.default_rng(1234)
    base = (rng.normal(size=(48, 257)) * 3.0 + 10.0).astype(np.float32)

    nan_rows = base.copy()
    nan_rows[3, :] = np.nan
    nan_rows[7, :120] = np.nan

    with_inf = base.copy()
    with_inf[1, 4] = np.inf
    with_inf[2, 9] = -np.inf

    return {
        "plain": base,
        "nan rows": nan_rows,
        "all nan": np.full((5, 32), np.nan, dtype=np.float32),
        "with inf": with_inf,
        "single row": (rng.normal(size=(1, 64)) * 2.0).astype(np.float32),
        "single column": (rng.normal(size=(9, 1)) * 2.0).astype(np.float32),
        "constant": np.full((6, 40), 7.0, dtype=np.float32),
    }


@pytest.mark.parametrize("name", list(_cases()))
@pytest.mark.parametrize("quantile", QUANTILES)
def test_row_quantiles_match_nanpercentile(name, quantile):
    data = _cases()[name]
    got = row_quantiles(data, (quantile,))[0]
    expected = np.nanpercentile(data, quantile, axis=1).astype(np.float32)
    assert np.allclose(got, expected, rtol=1e-5, atol=1e-4, equal_nan=True)


def test_row_quantiles_shares_one_sort_across_quantiles():
    data = _cases()["nan rows"]
    combined = row_quantiles(data, (25.0, 50.0, 75.0))
    assert combined.shape == (3, data.shape[0])
    for index, quantile in enumerate((25.0, 50.0, 75.0)):
        separate = row_quantiles(data, (quantile,))[0]
        assert np.allclose(combined[index], separate, equal_nan=True)


def test_row_median_matches_nanmedian():
    data = _cases()["nan rows"]
    assert np.allclose(
        row_median(data), np.nanmedian(data, axis=1).astype(np.float32), equal_nan=True, atol=1e-4
    )


def test_row_quantiles_rejects_non_2d():
    with pytest.raises(ValueError):
        row_quantiles(np.zeros(4, dtype=np.float32), (50.0,))


def test_row_quantiles_handles_empty_axes():
    assert row_quantiles(np.zeros((3, 0), dtype=np.float32), (50.0,)).shape == (1, 3)
    assert row_quantiles(np.zeros((0, 5), dtype=np.float32), (50.0,)).shape == (1, 0)


@pytest.mark.parametrize("name", list(_cases()))
@pytest.mark.parametrize("method", ["mean", "median", "robust"])
def test_rowwise_baseline_matches_reference(name, method):
    data = _cases()[name]
    got = rowwise_baseline(data, method)[:, 0]
    if method == "mean":
        expected = np.nanmean(data, axis=1)
    elif method == "median":
        expected = np.nanmedian(data, axis=1)
    else:
        expected = np.nanpercentile(data, 25.0, axis=1)
    assert np.allclose(got, expected.astype(np.float32), rtol=1e-4, atol=1e-4, equal_nan=True)


def test_rowwise_baseline_rejects_unknown_method():
    with pytest.raises(ValueError):
        rowwise_baseline(_cases()["plain"], "trimean")


def test_gap_rows_are_nan_in_baseline_and_scale():
    data = _cases()["plain"]
    gap = np.zeros(data.shape[0], dtype=bool)
    gap[2] = True
    assert np.all(np.isnan(rowwise_baseline(data, "robust", gap_row_mask=gap)[2]))
    assert np.all(np.isnan(rowwise_noise_scale(data, gap_row_mask=gap)[2]))


def test_subtract_background_always_returns_numpy_float32():
    result = subtract_background_rows(_cases()["nan rows"], method="robust", equalize_noise=True)
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32


def test_all_nan_input_short_circuits_to_nan():
    data = _cases()["all nan"]
    assert np.all(np.isnan(subtract_background_rows(data, method="robust")))
