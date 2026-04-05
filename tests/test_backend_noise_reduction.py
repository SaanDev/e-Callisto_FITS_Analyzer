from __future__ import annotations

import numpy as np

from src.Backend.noise_reduction import subtract_background_rows


def test_subtract_background_rows_robust_uses_lower_percentile_baseline():
    data = np.array(
        [
            [1.0, 2.0, 100.0],
            [10.0, 11.0, 12.0],
        ],
        dtype=np.float32,
    )

    result = subtract_background_rows(data, method="robust")
    expected = data - np.percentile(data, 25.0, axis=1, keepdims=True)

    assert result.dtype == np.float32
    assert np.allclose(result, expected.astype(np.float32))


def test_subtract_background_rows_equalizes_noisier_band_without_touching_gap_rows():
    data = np.array(
        [
            [10.0, 10.0, 10.0, 11.0, 10.0, 10.0],
            [12.0, 12.0, 13.0, 12.0, 12.0, 12.0],
            [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan],
            [20.0, 24.0, 18.0, 26.0, 16.0, 28.0],
            [30.0, 35.0, 25.0, 36.0, 24.0, 38.0],
        ],
        dtype=np.float32,
    )
    gap_row_mask = np.array([False, False, True, False, False], dtype=bool)

    base = subtract_background_rows(data, method="robust", gap_row_mask=gap_row_mask, equalize_noise=False)
    equalized = subtract_background_rows(data, method="robust", gap_row_mask=gap_row_mask, equalize_noise=True)

    assert np.all(np.isnan(equalized[2]))
    assert np.allclose(equalized[:2], base[:2], equal_nan=True)
    assert np.nanstd(equalized[3]) < np.nanstd(base[3])
    assert np.nanstd(equalized[4]) < np.nanstd(base[4])
