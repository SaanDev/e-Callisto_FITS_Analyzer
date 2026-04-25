"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

pytest.importorskip("requests")
pytest.importorskip("astropy")

from src.Backend.fits_io import load_callisto_fits
from src.Backend.learmonth import (
    LearmonthNotFoundError,
    download_learmonth_day,
    list_learmonth_chunks,
    resolve_learmonth_url,
    write_learmonth_chunk_fit,
)
from tests.helpers_learmonth import build_test_learmonth_srs, make_scan_rows


def test_resolve_learmonth_url_uses_year_subdirectory():
    assert (
        resolve_learmonth_url(date(2024, 4, 1))
        == "https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/raw/24/LM240401.srs"
    )


def test_download_learmonth_day_raises_not_found(tmp_path, monkeypatch):
    class FakeResponse:
        status_code = 404

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_content(self, chunk_size=0):
            return iter(())

    monkeypatch.setattr("src.Backend.learmonth.requests.get", lambda *_a, **_k: FakeResponse())

    with pytest.raises(LearmonthNotFoundError):
        download_learmonth_day(date(2024, 4, 1), tmp_path)


def test_list_learmonth_chunks_parses_cross_midnight_and_partial_final_chunk(tmp_path):
    first_chunk = make_scan_rows(
        datetime(2024, 3, 31, 23, 50, 0),
        [index * 3 for index in range(300)],
        base_value=10,
    )
    second_chunk = make_scan_rows(
        datetime(2024, 4, 1, 0, 5, 0),
        [index * 3 for index in range(10)],
        base_value=100,
    )
    path = build_test_learmonth_srs(tmp_path / "LM240401.srs", first_chunk + second_chunk)

    chunks = list_learmonth_chunks(path)

    assert len(chunks) == 2
    assert chunks[0].start_dt == datetime(2024, 3, 31, 23, 50, 0)
    assert chunks[0].end_dt == datetime(2024, 4, 1, 0, 5, 0)
    assert chunks[0].scan_count == 300
    assert chunks[0].is_partial is False

    assert chunks[1].start_dt == datetime(2024, 4, 1, 0, 5, 0)
    assert chunks[1].end_dt == datetime(2024, 4, 1, 0, 5, 30)
    assert chunks[1].scan_count == 10
    assert chunks[1].is_partial is True
    assert chunks[1].offset_start > chunks[0].offset_start
    assert chunks[1].offset_end > chunks[1].offset_start


def test_write_learmonth_chunk_fit_round_trips_with_actual_scan_offsets(tmp_path):
    rows = make_scan_rows(
        datetime(2024, 4, 1, 0, 0, 0),
        [0, 3, 16],
        base_value=20,
    )
    day_path = build_test_learmonth_srs(tmp_path / "LM240401.srs", rows)
    chunk = list_learmonth_chunks(day_path)[0]

    fit_path = write_learmonth_chunk_fit(day_path, chunk, tmp_path / "converted" / "LEARMONTH_20240401_000000_01.fit")
    result = load_callisto_fits(fit_path, memmap=False)

    assert result.data.shape == (802, 3)
    assert np.all(result.data[:, 0] == 20)
    assert np.all(result.data[:, 1] == 21)
    assert np.all(result.data[:, 2] == 22)
    assert np.allclose(result.time, np.array([0.0, 3.0, 16.0]))
    assert len(result.freqs) == 802
    assert result.header0["INSTRUME"] == "LEARMONTH"
    assert result.header0["TIME-OBS"] == "00:00:00"
    assert result.header0["TIME-END"] == "00:00:19"
    assert result.header0["RAWFILE"] == "LM240401.srs"


def test_write_learmonth_chunk_fit_reorients_frequency_axis_descending(tmp_path):
    scan_a = np.arange(802, dtype=np.uint8)
    scan_b = ((np.arange(802, dtype=np.int16) + 10) % 256).astype(np.uint8)
    rows = [
        (datetime(2024, 4, 1, 0, 0, 0), scan_a),
        (datetime(2024, 4, 1, 0, 0, 3), scan_b),
    ]
    day_path = build_test_learmonth_srs(tmp_path / "LM240401.srs", rows)
    chunk = list_learmonth_chunks(day_path)[0]

    fit_path = write_learmonth_chunk_fit(day_path, chunk, tmp_path / "converted" / "LEARMONTH_20240401_000000_01.fit")
    result = load_callisto_fits(fit_path, memmap=False)

    assert result.freqs[0] > result.freqs[-1]
    assert result.data[0, 0] == scan_a[-1]
    assert result.data[-1, 0] == scan_a[0]
    assert result.data[0, 1] == scan_b[-1]
    assert result.data[-1, 1] == scan_b[0]
