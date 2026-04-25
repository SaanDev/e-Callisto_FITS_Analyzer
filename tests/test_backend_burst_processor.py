"""
Backend tests for FITS combination helpers.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("astropy")

from astropy.io import fits

from src.Backend import burst_processor
from src.Backend import fits_io


class FakeHDU:
    def __init__(self, data=None, header=None):
        self.data = data
        self.header = header or fits.Header()


class FakeHDUList(list):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self):
        return None


class FakeBinaryColumns:
    def __init__(self, dtype):
        self.dtype = np.dtype(dtype)
        self.names = list(self.dtype.names or [])


class FakeBinaryTableHDU:
    def __init__(self, raw_table):
        self._raw_table = np.asarray(raw_table)
        self.columns = FakeBinaryColumns(self._raw_table.dtype.newbyteorder("<"))
        self._nrows = int(self._raw_table.shape[0])
        self._data_offset = 0

    @property
    def data(self):
        raise AssertionError("Astropy-style table .data should not be accessed")

    def _get_raw_data(self, nrows, dtype, data_offset):
        assert int(nrows) == self._nrows
        assert int(data_offset) == 0
        return self._raw_table.astype(dtype, copy=False)


def _header(freq_min, freq_max, *, step=10.0, focus=None):
    hdr = fits.Header()
    hdr["TIME-OBS"] = "01:02:03"
    hdr["FREQMIN"] = float(freq_min)
    hdr["FREQMAX"] = float(freq_max)
    hdr["CDELT2"] = float(step)
    if focus is not None:
        hdr["FOCUS"] = str(focus)
    return hdr


def _install_preview_and_load(monkeypatch, mapping):
    def fake_preview(path, memmap=False):
        payload = mapping[path]
        return fits_io.FitsPreviewResult(
            freqs=np.asarray(payload["freqs"], dtype=float),
            time=np.asarray(payload["time"], dtype=float),
            header0=payload["header0"],
            data_shape=np.asarray(payload["data"], dtype=float).shape,
            freq_source=payload.get("freq_source", "table"),
            time_source=payload.get("time_source", "table"),
        )

    def fake_load(path, memmap=False):
        payload = mapping[path]
        return fits_io.FitsLoadResult(
            data=np.asarray(payload["data"], dtype=float),
            freqs=np.asarray(payload["freqs"], dtype=float),
            time=np.asarray(payload["time"], dtype=float),
            header0=payload["header0"],
        )

    monkeypatch.setattr(burst_processor, "preview_callisto_fits", fake_preview)
    monkeypatch.setattr(burst_processor, "load_callisto_fits", fake_load)


def test_load_fits_reads_arrays(monkeypatch):
    data = np.array([[1.0, 2.0], [3.0, 4.0]])
    freqs = np.array([100.0, 200.0])
    times = np.array([0.0, 1.0])

    fake_hdul = FakeHDUList(
        [
            FakeHDU(data=data, header=fits.Header()),
            FakeHDU(data={"frequency": [freqs], "time": [times]}),
        ]
    )

    monkeypatch.setattr(fits_io.fits, "open", lambda *_args, **_kwargs: fake_hdul)

    loaded_data, loaded_freqs, loaded_time = burst_processor.load_fits("test.fit")

    assert np.array_equal(loaded_data, data)
    assert np.array_equal(loaded_freqs, freqs)
    assert np.array_equal(loaded_time, times)


def test_preview_and_load_use_header_frequency_range_when_axis_columns_missing(monkeypatch):
    data = np.arange(12, dtype=float).reshape(6, 2)
    hdr = fits.Header()
    hdr["NAXIS1"] = 2
    hdr["NAXIS2"] = 6
    hdr["FREQMIN"] = 30.0
    hdr["FREQMAX"] = 80.0

    fake_hdul = FakeHDUList([FakeHDU(data=data, header=hdr)])
    monkeypatch.setattr(fits_io.fits, "open", lambda *_args, **_kwargs: fake_hdul)

    preview = fits_io.preview_callisto_fits("test.fit")
    loaded = fits_io.load_callisto_fits("test.fit")

    assert preview.freq_source == "header-range"
    assert np.allclose(preview.freqs, np.array([80.0, 70.0, 60.0, 50.0, 40.0, 30.0]))
    assert np.allclose(loaded.freqs, np.array([80.0, 70.0, 60.0, 50.0, 40.0, 30.0]))
    assert np.array_equal(loaded.data, data)


def test_preview_and_load_read_binary_table_axes_without_touching_lazy_data(monkeypatch):
    data = np.arange(6, dtype=float).reshape(2, 3)
    hdr = fits.Header()
    hdr["NAXIS1"] = 3
    hdr["NAXIS2"] = 2

    raw_table = np.zeros(
        1,
        dtype=np.dtype(
            [
                ("TIME", ">f8", (3,)),
                ("FREQUENCY", ">f8", (2,)),
            ]
        ),
    )
    raw_table["TIME"][0] = np.array([0.0, 1.0, 2.0], dtype=float)
    raw_table["FREQUENCY"][0] = np.array([80.0, 70.0], dtype=float)

    fake_hdul = FakeHDUList([FakeHDU(data=data, header=hdr), FakeBinaryTableHDU(raw_table)])
    monkeypatch.setattr(fits_io.fits, "open", lambda *_args, **_kwargs: fake_hdul)

    preview = fits_io.preview_callisto_fits("test.fit")
    loaded = fits_io.load_callisto_fits("test.fit")

    assert np.array_equal(preview.time, np.array([0.0, 1.0, 2.0]))
    assert np.array_equal(preview.freqs, np.array([80.0, 70.0]))
    assert np.array_equal(loaded.time, np.array([0.0, 1.0, 2.0]))
    assert np.array_equal(loaded.freqs, np.array([80.0, 70.0]))
    assert np.array_equal(loaded.data, data)


def test_reduce_noise_applies_mean_clip_and_scale():
    data = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    low, high = -5.0, 20.0
    baseline = np.percentile(data, 25.0, axis=1, keepdims=True)
    expected = np.clip(data - baseline, low, high)
    expected = (expected - low) * 2500.0 / 256.0 / 25.4

    result = burst_processor.reduce_noise(data)

    assert np.allclose(result, expected)


def test_parse_filename_valid_and_invalid():
    station, obs_date, obs_time, focus = burst_processor.parse_filename("ABCD_20240101_120000_A.fit")
    assert (station, obs_date, obs_time, focus) == ("ABCD", "20240101", "120000", "A")

    with pytest.raises(ValueError):
        burst_processor.parse_filename("invalid.fit")


def test_are_frequency_combinable_true(monkeypatch):
    time = np.array([0.0, 1.0, 2.0])
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.array([[11.0, 12.0, 13.0], [21.0, 22.0, 23.0]]),
            "freqs": np.array([10.0, 20.0]),
            "time": time,
            "header0": _header(10.0, 20.0, step=10.0, focus="A"),
        },
        "STAT_20240101_120000_B.fit": {
            "data": np.array([[31.0, 32.0, 33.0], [41.0, 42.0, 43.0]]),
            "freqs": np.array([40.0, 35.0]),
            "time": time,
            "header0": _header(35.0, 40.0, step=5.0, focus="B"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    assert burst_processor.are_frequency_combinable(list(mapping)) is True


def test_are_frequency_combinable_accepts_labeled_header_focus_code(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_56.fit": {
            "data": np.array([[1.0, 2.0], [3.0, 4.0]]),
            "freqs": np.array([80.0, 70.0]),
            "time": time,
            "header0": _header(70.0, 80.0, step=10.0, focus="Focuscode: 56"),
        },
        "STAT_20240101_120000_62.fit": {
            "data": np.array([[5.0, 6.0], [7.0, 8.0]]),
            "freqs": np.array([110.0, 100.0]),
            "time": time,
            "header0": _header(100.0, 110.0, step=10.0, focus="Focuscode: 62"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    assert burst_processor.are_frequency_combinable(list(mapping)) is True


def test_are_time_combinable_true(monkeypatch):
    freqs = np.array([100.0, 200.0])
    data = np.zeros((2, 2))
    times = np.array([0.0, 1.0])

    def fake_load(_):
        return data, freqs, times

    monkeypatch.setattr(burst_processor, "load_fits", fake_load)

    f1 = "STAT_20240101_120000_A.fit"
    f2 = "STAT_20240101_121500_A.fit"

    assert burst_processor.are_time_combinable([f1, f2]) is True


def test_are_time_combinable_true_across_midnight(monkeypatch):
    freqs = np.array([100.0, 200.0])
    data = np.zeros((2, 2))
    times = np.array([0.0, 1.0])

    def fake_load(_):
        return data, freqs, times

    monkeypatch.setattr(burst_processor, "load_fits", fake_load)

    f1 = "STAT_20240331_235500_A.fit"
    f2 = "STAT_20240401_001100_A.fit"

    assert burst_processor.are_time_combinable([f1, f2]) is True


def test_combine_frequency_regularizes_order_and_inserts_gap_rows(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_B.fit": {
            "data": np.array([[30.0, 31.0], [40.0, 41.0]]),
            "freqs": np.array([130.0, 120.0]),
            "time": time,
            "header0": _header(120.0, 130.0, step=10.0, focus="B"),
        },
        "STAT_20240101_120000_A.fit": {
            "data": np.array([[10.0, 11.0], [20.0, 21.0]]),
            "freqs": np.array([20.0, 10.0]),
            "time": time,
            "header0": _header(10.0, 20.0, step=10.0, focus="A"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    result = burst_processor.combine_frequency(list(mapping))

    assert result["filename"] == "STAT_20240101_120000_freq_combined"
    assert result["ut_start_sec"] == 3723
    assert np.array_equal(result["freqs"], np.array([130.0, 120.0, 110.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0]))
    assert result["data"].shape == (13, 2)
    assert result["gap_row_mask"] is None
    assert np.allclose(
        result["data"][2:11],
        np.array(
            [
                [30.5, 31.5],
                [28.5, 29.5],
                [26.5, 27.5],
                [24.5, 25.5],
                [22.5, 23.5],
                [20.5, 21.5],
                [18.5, 19.5],
                [16.5, 17.5],
                [14.5, 15.5],
            ]
        ),
    )
    assert np.array_equal(result["data"][:2], np.array([[30.0, 31.0], [40.0, 41.0]]))
    assert np.array_equal(result["data"][-2:], np.array([[10.0, 11.0], [20.0, 21.0]]))
    assert float(result["frequency_step_mhz"]) == pytest.approx(10.0)


def test_combine_frequency_hatched_gap_keeps_explicit_invalid_rows(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_B.fit": {
            "data": np.array([[30.0, 31.0], [40.0, 41.0]]),
            "freqs": np.array([130.0, 120.0]),
            "time": time,
            "header0": _header(120.0, 130.0, step=10.0, focus="B"),
        },
        "STAT_20240101_120000_A.fit": {
            "data": np.array([[10.0, 11.0], [20.0, 21.0]]),
            "freqs": np.array([20.0, 10.0]),
            "time": time,
            "header0": _header(10.0, 20.0, step=10.0, focus="A"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    result = burst_processor.combine_frequency(list(mapping), gap_fill="hatched")

    assert result["gap_fill"] == "hatched"
    assert result["gap_row_mask"] is not None
    assert np.array_equal(
        result["gap_row_mask"],
        np.array([False, False, True, True, True, True, True, True, True, True, True, False, False]),
    )
    assert np.all(np.isnan(result["data"][2:11]))


def test_combine_frequency_rejects_overlapping_bands_when_requested(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.ones((2, 2)),
            "freqs": np.array([200.0, 100.0]),
            "time": time,
            "header0": _header(100.0, 200.0, step=100.0, focus="A"),
        },
        "STAT_20240101_120000_B.fit": {
            "data": np.ones((2, 2)),
            "freqs": np.array([150.0, 50.0]),
            "time": time,
            "header0": _header(50.0, 150.0, step=100.0, focus="B"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    with pytest.raises(ValueError, match="overlap|interleave"):
        burst_processor.combine_frequency(list(mapping), overlap_policy="reject")


def test_combine_frequency_splits_overlapping_bands_at_connection(monkeypatch):
    time = np.array([0.0])
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.array([[10.0], [20.0], [30.0], [40.0]]),
            "freqs": np.array([10.0, 20.0, 30.0, 40.0]),
            "time": time,
            "header0": _header(10.0, 40.0, step=10.0, focus="A"),
        },
        "STAT_20240101_120000_B.fit": {
            "data": np.array([[300.0], [400.0], [500.0], [600.0]]),
            "freqs": np.array([30.0, 40.0, 50.0, 60.0]),
            "time": time,
            "header0": _header(30.0, 60.0, step=10.0, focus="B"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    result = burst_processor.combine_frequency(
        list(mapping),
        overlap_policy="split",
        overlap_connection_mhz=35.0,
    )

    assert np.array_equal(result["freqs"], np.array([60.0, 50.0, 40.0, 30.0, 20.0, 10.0]))
    assert np.array_equal(result["data"].ravel(), np.array([600.0, 500.0, 400.0, 30.0, 20.0, 10.0]))
    assert result["overlap_policy"] == "split"
    assert result["overlap_connection_mhz"] == pytest.approx(35.0)


def test_combine_frequency_overlap_policy_keeps_low_or_high_band(monkeypatch):
    time = np.array([0.0])
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.array([[10.0], [20.0], [30.0], [40.0]]),
            "freqs": np.array([10.0, 20.0, 30.0, 40.0]),
            "time": time,
            "header0": _header(10.0, 40.0, step=10.0, focus="A"),
        },
        "STAT_20240101_120000_B.fit": {
            "data": np.array([[300.0], [400.0], [500.0], [600.0]]),
            "freqs": np.array([30.0, 40.0, 50.0, 60.0]),
            "time": time,
            "header0": _header(30.0, 60.0, step=10.0, focus="B"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    low = burst_processor.combine_frequency(list(mapping), overlap_policy="low")
    high = burst_processor.combine_frequency(list(mapping), overlap_policy="high")

    assert np.array_equal(low["data"].ravel(), np.array([600.0, 500.0, 40.0, 30.0, 20.0, 10.0]))
    assert np.array_equal(high["data"].ravel(), np.array([600.0, 500.0, 400.0, 300.0, 20.0, 10.0]))


def test_combine_frequency_rejects_duplicate_focus_codes(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.ones((2, 2)),
            "freqs": np.array([20.0, 10.0]),
            "time": time,
            "header0": _header(10.0, 20.0, step=10.0, focus="A"),
        },
        "STAT_20240101_120000_A.fits": {
            "data": np.ones((2, 2)),
            "freqs": np.array([40.0, 30.0]),
            "time": time,
            "header0": _header(30.0, 40.0, step=10.0, focus="A"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    with pytest.raises(ValueError, match="distinct focus codes"):
        burst_processor.combine_frequency(list(mapping))


def test_combine_frequency_regularizes_different_channel_spacings(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.array(
                [
                    [1.0, 2.0],
                    [3.0, 4.0],
                    [5.0, 6.0],
                    [7.0, 8.0],
                    [9.0, 10.0],
                    [11.0, 12.0],
                ],
                dtype=float,
            ),
            "freqs": np.array([80.0, 70.0, 60.0, 50.0, 40.0, 30.0]),
            "time": time,
            "header0": _header(30.0, 80.0, step=10.0, focus="A"),
        },
        "STAT_20240101_120000_B.fit": {
            "data": np.array(
                [
                    [10.0, 20.0],
                    [30.0, 40.0],
                    [50.0, 60.0],
                    [70.0, 80.0],
                    [90.0, 100.0],
                    [110.0, 120.0],
                ],
                dtype=float,
            ),
            "freqs": np.array([350.0, 300.0, 250.0, 200.0, 150.0, 100.0]),
            "time": time,
            "header0": _header(100.0, 350.0, step=50.0, focus="B"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    result = burst_processor.combine_frequency(list(mapping))

    assert result["freqs"][0] == pytest.approx(350.0)
    assert result["freqs"][-1] == pytest.approx(30.0)
    assert result["frequency_step_mhz"] == pytest.approx(10.0)
    assert result["data"].shape == (33, 2)
    assert result["gap_row_mask"] is None
    assert np.allclose(result["data"][26], np.array([53.75, 59.25]))
    assert np.array_equal(result["data"][0], np.array([10.0, 20.0]))
    assert np.array_equal(result["data"][5], np.array([30.0, 40.0]))
    assert np.array_equal(result["data"][-1], np.array([11.0, 12.0]))


def test_combine_frequency_accepts_irregular_axes_with_non_integer_grid_span(monkeypatch):
    time = np.array([0.0, 1.0])
    mapping = {
        "STAT_20240101_120000_56.fit": {
            "data": np.array(
                [
                    [10.0, 11.0],
                    [12.0, 13.0],
                    [14.0, 15.0],
                    [16.0, 17.0],
                ],
                dtype=float,
            ),
            "freqs": np.array([88.370, 88.120, 87.810, 87.495]),
            "time": time,
            "header0": _header(87.495, 88.370, step=0.31, focus="Focuscode: 56"),
        },
        "STAT_20240101_120000_62.fit": {
            "data": np.array(
                [
                    [30.0, 31.0],
                    [32.0, 33.0],
                    [34.0, 35.0],
                    [36.0, 37.0],
                ],
                dtype=float,
            ),
            "freqs": np.array([107.995, 107.308, 106.621, 105.934]),
            "time": time,
            "header0": _header(105.934, 107.995, step=0.687, focus="Focuscode: 62"),
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    assert burst_processor.are_frequency_combinable(list(mapping)) is True

    result = burst_processor.combine_frequency(list(mapping))

    assert result["freqs"][0] == pytest.approx(107.995)
    assert result["freqs"][-1] == pytest.approx(87.495)
    assert result["data"].shape[0] == 67
    assert result["gap_row_mask"] is None
    assert not np.any(np.all(result["data"] == 0.0, axis=1))
    assert np.all(np.isfinite(result["data"]))
    assert np.array_equal(result["data"][0], np.array([30.0, 31.0]))
    assert np.array_equal(result["data"][-1], np.array([16.0, 17.0]))


def test_combine_frequency_allows_axis_range_without_freqminmax(monkeypatch):
    time = np.array([0.0, 1.0])
    hdr_a = fits.Header()
    hdr_a["TIME-OBS"] = "01:02:03"
    hdr_a["CDELT2"] = 10.0
    hdr_a["FOCUS"] = "A"
    hdr_b = fits.Header()
    hdr_b["TIME-OBS"] = "01:02:03"
    hdr_b["CDELT2"] = 10.0
    hdr_b["FOCUS"] = "B"
    mapping = {
        "STAT_20240101_120000_A.fit": {
            "data": np.array([[10.0, 11.0], [20.0, 21.0]]),
            "freqs": np.array([20.0, 10.0]),
            "time": time,
            "header0": hdr_a,
        },
        "STAT_20240101_120000_B.fit": {
            "data": np.array([[30.0, 31.0], [40.0, 41.0]]),
            "freqs": np.array([40.0, 30.0]),
            "time": time,
            "header0": hdr_b,
        },
    }
    _install_preview_and_load(monkeypatch, mapping)

    result = burst_processor.combine_frequency(list(mapping))

    assert np.array_equal(result["freqs"], np.array([40.0, 30.0, 20.0, 10.0]))
    assert result["gap_row_mask"] is None


def test_combine_time_stitches_time_axis(monkeypatch):
    freqs = np.array([100.0, 200.0])
    time = np.array([0.0, 1.0, 2.0])
    data1 = np.ones((2, 3))
    data2 = np.zeros((2, 3))

    hdr = fits.Header()
    hdr["TIME-OBS"] = "00:00:10"

    def fake_load(path, memmap=False):
        if path.endswith("120000_A.fit"):
            return fits_io.FitsLoadResult(data=data1, freqs=freqs, time=time, header0=hdr)
        return fits_io.FitsLoadResult(data=data2, freqs=freqs, time=time, header0=hdr)

    monkeypatch.setattr(burst_processor, "load_callisto_fits", fake_load)

    result = burst_processor.combine_time([
        "STAT_20240101_120000_A.fit",
        "STAT_20240101_121500_A.fit",
    ])

    assert result["data"].shape == (2, 6)
    assert np.array_equal(result["time"], np.array([0, 1, 2, 3, 4, 5], dtype=float))
    assert result["filename"] == "STAT_20240101_combined_time"
    assert result["ut_start_sec"] == 10


def test_combine_time_sorts_across_midnight(monkeypatch):
    freqs = np.array([100.0, 200.0])
    time = np.array([0.0, 1.0, 2.0])
    data1 = np.ones((2, 3))
    data2 = np.zeros((2, 3))

    hdr = fits.Header()
    hdr["TIME-OBS"] = "23:55:00"

    def fake_load(path, memmap=False):
        if path.endswith("20240331_235500_A.fit"):
            return fits_io.FitsLoadResult(data=data1, freqs=freqs, time=time, header0=hdr)
        return fits_io.FitsLoadResult(data=data2, freqs=freqs, time=time, header0=hdr)

    monkeypatch.setattr(burst_processor, "load_callisto_fits", fake_load)

    result = burst_processor.combine_time([
        "STAT_20240401_001100_A.fit",
        "STAT_20240331_235500_A.fit",
    ])

    assert result["data"].shape == (2, 6)
    assert np.array_equal(result["data"][:, :3], data1)
    assert np.array_equal(result["data"][:, 3:], data2)
    assert result["filename"] == "STAT_20240331_combined_time"
