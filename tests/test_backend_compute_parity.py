"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Every accelerated path keeps a NumPy implementation. These tests pin the two
against each other, and pin the NumPy side against the reference semantics of
the ``np.nan*`` reductions it replaced.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.Backend import compute
from src.Backend.array_stats import row_median, row_quantiles
from src.Backend.noise_reduction import (
    _subtract_background_jax,
    _subtract_background_numpy_entry,
    rowwise_baseline,
    rowwise_noise_scale,
    subtract_background_rows,
)
from src.Backend.frequency_axis import invalid_row_mask

jax_available = pytest.mark.skipif(
    not compute.available_devices()[0].is_jax,
    reason="JAX is not installed in this environment",
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


@jax_available
@pytest.mark.parametrize("name", list(_cases()))
@pytest.mark.parametrize("equalize", [False, True])
@pytest.mark.parametrize("attenuate_only", [False, True])
def test_background_subtraction_numpy_matches_jax(name, equalize, attenuate_only):
    data = _cases()[name]
    gap = np.zeros(data.shape[0], dtype=bool)
    if data.shape[0] > 3:
        gap[2] = True
    row_invalid = invalid_row_mask(data, gap)

    args = (data, row_invalid, gap, 2, 25.0, equalize, 25.0, attenuate_only)
    from_numpy = _subtract_background_numpy_entry(*args)
    from_jax = _subtract_background_jax(*args)

    assert from_jax.dtype == np.float32
    assert isinstance(from_jax, np.ndarray)
    assert np.allclose(from_numpy, from_jax, rtol=1e-4, atol=1e-4, equal_nan=True)


def test_subtract_background_always_returns_numpy_float32():
    result = subtract_background_rows(_cases()["nan rows"], method="robust", equalize_noise=True)
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32


def test_all_nan_input_short_circuits_to_nan():
    data = _cases()["all nan"]
    assert np.all(np.isnan(subtract_background_rows(data, method="robust")))


@jax_available
def test_median_filter_kernel_matches_scipy():
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    from src.Backend.compute_kernels import median_filter_2d_kernel

    data = (np.random.default_rng(7).normal(size=(37, 61)) * 4.0).astype(np.float32)
    for kernel in (3, 5, 7):
        expected = scipy_ndimage.median_filter(data, size=(kernel, kernel), mode="nearest")
        got = compute.to_numpy(median_filter_2d_kernel(compute.to_device(data), kernel, kernel))
        assert np.allclose(expected, got, rtol=1e-5, atol=1e-5)


@jax_available
def test_median_filter_kernel_supports_asymmetric_kernels():
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    from src.Backend.compute_kernels import median_filter_2d_kernel

    data = (np.random.default_rng(8).normal(size=(23, 41)) * 4.0).astype(np.float32)
    expected = scipy_ndimage.median_filter(data, size=(3, 5), mode="nearest")
    got = compute.to_numpy(median_filter_2d_kernel(compute.to_device(data), 3, 5))
    assert np.allclose(expected, got, rtol=1e-5, atol=1e-5)


@jax_available
def test_peak_downsample_kernel_matches_numpy_path():
    from src.Backend.compute_kernels import peak_downsample_kernel
    from src.Backend.spectral_overview import _peak_preserving_downsample

    data = (np.random.default_rng(9).normal(size=(12, 96)) * 3.0).astype(np.float32)
    data[4, :] = np.nan
    times = np.arange(96, dtype=float)

    expected, _ = _peak_preserving_downsample(data, times, max_columns=12)
    got = compute.to_numpy(peak_downsample_kernel(compute.to_device(data, dtype="float32"), 12))
    assert np.allclose(expected, got, rtol=1e-5, atol=1e-5, equal_nan=True)


@jax_available
def test_radial_distance_kernel_matches_numpy_path():
    from src.Backend.compute_kernels import radial_distance_kernel
    from src.Backend.coronagraph import _radial_distance_grid_numpy

    expected = _radial_distance_grid_numpy((64, 96), (48.25, 31.5))
    got = compute.to_numpy(radial_distance_kernel(64, 96, 31.5, 48.25))
    assert np.allclose(expected, got, rtol=1e-6, atol=1e-6)


@jax_available
@pytest.mark.parametrize("cmap_name", ["viridis", "plasma", "gray", "jet"])
def test_rgba_lut_gather_numpy_matches_jax(cmap_name, monkeypatch):
    matplotlib = pytest.importorskip("matplotlib")
    import src.UI.accelerated_plot_widget as widget

    cmap = matplotlib.colormaps[cmap_name]
    data = (np.random.default_rng(11).normal(size=(40, 130)) * 3.0 + 10.0).astype(np.float32)
    data[3, :] = np.nan
    gap = np.zeros(data.shape[0], dtype=bool)
    gap[3] = True
    gap[9] = True

    from_numpy = widget._rgba_image_from_cmap(data, cmap, vmin=0.0, vmax=20.0, gap_row_mask=gap)

    # gpu_only keeps the JAX branch off on a CPU-only machine, so force it.
    monkeypatch.setattr(widget.compute, "should_accelerate", lambda *a, **k: True)
    from_jax = widget._rgba_image_from_cmap(data, cmap, vmin=0.0, vmax=20.0, gap_row_mask=gap)

    assert from_jax.dtype == np.uint8
    assert np.array_equal(from_numpy, from_jax)


@jax_available
def test_median_filter_dispatch_matches_scipy(monkeypatch):
    pytest.importorskip("scipy.ndimage")
    import src.Backend.rfi_filters as rfi

    data = (np.random.default_rng(12).normal(size=(29, 83)) * 4.0).astype(np.float32)
    from_scipy = rfi._median2d(data, 3, 3)

    monkeypatch.setattr(rfi.compute, "should_accelerate", lambda *a, **k: True)
    from_jax = rfi._median2d(data, 3, 3)

    assert np.allclose(from_scipy, from_jax, rtol=1e-5, atol=1e-5)


@jax_available
def test_clean_rfi_result_is_unchanged_on_the_jax_path(monkeypatch):
    pytest.importorskip("scipy.ndimage")
    import src.Backend.rfi_filters as rfi

    data = (np.random.default_rng(13).normal(size=(48, 200)) * 4.0 + 10.0).astype(np.float32)
    data[5, :] = 500.0
    baseline = rfi.clean_rfi(data)

    monkeypatch.setattr(rfi.compute, "should_accelerate", lambda *a, **k: True)
    accelerated = rfi.clean_rfi(data)

    assert accelerated.masked_channel_indices == baseline.masked_channel_indices
    assert np.allclose(accelerated.data, baseline.data, rtol=1e-5, atol=1e-5, equal_nan=True)


@jax_available
def test_radial_distance_dispatch_matches_numpy(monkeypatch):
    import src.Backend.coronagraph as coronagraph

    from_numpy = coronagraph.radial_distance_grid((70, 55), (27.5, 34.25))
    monkeypatch.setattr(coronagraph.compute, "should_accelerate", lambda *a, **k: True)
    from_jax = coronagraph.radial_distance_grid((70, 55), (27.5, 34.25))

    assert np.allclose(from_numpy, from_jax, rtol=1e-6, atol=1e-6)


def test_warmup_thunks_are_empty_without_a_gpu():
    from src.Backend.compute_kernels import warmup_thunks

    # gpu_only, so a CPU-only machine must not pay any compile cost.
    assert warmup_thunks((200, 3600)) == []


@jax_available
def test_warmup_thunks_compile_when_a_gpu_is_present(monkeypatch):
    from src.Backend import compute_kernels

    monkeypatch.setattr(compute, "should_accelerate", lambda *a, **k: True)
    thunks = compute_kernels.warmup_thunks((16, 48))
    assert len(thunks) == 3
    for thunk in thunks:
        compute.block_until_ready(thunk())


def test_warmup_thunks_reject_degenerate_shapes():
    from src.Backend.compute_kernels import warmup_thunks

    assert warmup_thunks((0, 10)) == []
    assert warmup_thunks(("a", "b")) == []
