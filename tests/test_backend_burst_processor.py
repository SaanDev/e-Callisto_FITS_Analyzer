"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

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


def test_reduce_noise_applies_mean_clip_and_scale():
    data = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    low, high = -5.0, 20.0
    expected = data - data.mean(axis=1, keepdims=True)
    expected = np.clip(expected, low, high)
    expected = (expected - low) * 2500.0 / 256.0 / 25.4

    result = burst_processor.reduce_noise(data)

    assert np.allclose(result, expected)


def test_parse_filename_valid_and_invalid():
    station, date, time, focus = burst_processor.parse_filename("ABCD_20240101_120000_A.fit")
    assert (station, date, time, focus) == ("ABCD", "20240101", "120000", "A")

    with pytest.raises(ValueError):
        burst_processor.parse_filename("invalid.fit")


def test_are_frequency_combinable_true(monkeypatch):
    time = np.array([0.0, 1.0])
    freqs = np.array([100.0, 200.0])
    data = np.zeros((2, 2))

    def fake_load(_):
        return data, freqs, time

    monkeypatch.setattr(burst_processor, "load_fits", fake_load)

    f1 = "STAT_20240101_120000_A.fit"
    f2 = "STAT_20240101_120000_B.fit"

    assert burst_processor.are_frequency_combinable([f1, f2]) is True


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


def test_combine_frequency_merges_data(monkeypatch):
    data1 = np.ones((2, 2))
    data2 = np.zeros((2, 2))
    freqs1 = np.array([100.0, 200.0])
    freqs2 = np.array([300.0, 400.0])
    time = np.array([0.0, 1.0])

    hdr = fits.Header()
    hdr["TIME-OBS"] = "01:02:03"

    def fake_load(path, memmap=False):
        if path.endswith("A.fit"):
            return fits_io.FitsLoadResult(data=data1, freqs=freqs1, time=time, header0=hdr)
        return fits_io.FitsLoadResult(data=data2, freqs=freqs2, time=time, header0=hdr)

    monkeypatch.setattr(burst_processor, "load_callisto_fits", fake_load)

    result = burst_processor.combine_frequency([
        "STAT_20240101_120000_A.fit",
        "STAT_20240101_120000_B.fit",
    ])

    assert result["data"].shape == (4, 2)
    assert np.array_equal(result["freqs"], np.concatenate([freqs1, freqs2]))
    assert result["filename"] == "STAT_20240101_120000_freq_combined"
    assert result["ut_start_sec"] == 3723


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
