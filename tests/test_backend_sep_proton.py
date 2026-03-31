"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

pytest.importorskip("requests")
pytest.importorskip("netCDF4")
pytest.importorskip("cftime")

from src.Backend import sep_proton as sep


def test_pick_sgps_channel_indices_targets_10_and_100_mev():
    lower = np.array([4.0, 9.0, 45.0, 90.0])
    upper = np.array([6.0, 12.0, 75.0, 120.0])

    idx_low, idx_high = sep.pick_sgps_channel_indices(lower, upper)

    assert idx_low == 1
    assert idx_high == 3


def test_load_sep_proton_range_stitches_days_and_deduplicates(monkeypatch):
    start = datetime(2026, 2, 10, 23, 55, 0)
    end = datetime(2026, 2, 11, 0, 10, 0)

    def fake_download(goes_num, day, **_kwargs):
        return f"/tmp/goes{goes_num}_{day:%Y%m%d}.nc"

    def fake_slice(local_path, _start_dt, _end_dt):
        if local_path.endswith("20260210.nc"):
            return sep._DailySliceData(
                times=np.array(
                    [
                        datetime(2026, 2, 10, 23, 55, 0),
                        datetime(2026, 2, 11, 0, 0, 0),
                    ],
                    dtype=object,
                ),
                low_flux=np.array([1.0, 2.0], dtype=float),
                high_flux=np.array([10.0, 20.0], dtype=float),
                low_channel_label="P2 (9-12 MeV)",
                high_channel_label="P4 (90-120 MeV)",
                units="1/(cm^2 s sr MeV)",
            )
        return sep._DailySliceData(
            times=np.array(
                [
                    datetime(2026, 2, 11, 0, 0, 0),
                    datetime(2026, 2, 11, 0, 5, 0),
                    datetime(2026, 2, 11, 0, 10, 0),
                ],
                dtype=object,
            ),
            low_flux=np.array([2.0, 3.0, 4.0], dtype=float),
            high_flux=np.array([20.0, 30.0, 40.0], dtype=float),
            low_channel_label="P2 (9-12 MeV)",
            high_channel_label="P4 (90-120 MeV)",
            units="1/(cm^2 s sr MeV)",
        )

    monkeypatch.setattr(sep, "download_daily_file", fake_download)
    monkeypatch.setattr(sep, "load_daily_sgps_slice", fake_slice)

    result = sep.load_sep_proton_range(start, end, spacecraft=19)

    assert result.spacecraft == "GOES-19"
    assert result.source_files == ("goes19_20260210.nc", "goes19_20260211.nc")
    assert result.low_channel_label == "P2 (9-12 MeV)"
    assert result.high_channel_label == "P4 (90-120 MeV)"
    assert result.times == (
        datetime(2026, 2, 10, 23, 55, 0),
        datetime(2026, 2, 11, 0, 0, 0),
        datetime(2026, 2, 11, 0, 5, 0),
        datetime(2026, 2, 11, 0, 10, 0),
    )
    assert result.low_flux == (1.0, 2.0, 3.0, 4.0)
    assert result.high_flux == (10.0, 20.0, 30.0, 40.0)


def test_load_sep_proton_range_auto_spacecraft_falls_back_in_order(monkeypatch):
    attempts: list[int] = []

    def fake_load(goes_num, _start_dt, _end_dt, **_kwargs):
        attempts.append(goes_num)
        if goes_num in (19, 18):
            raise sep.SepProtonDataError(f"{sep.spacecraft_label(goes_num)} unavailable")
        return sep.SepProtonRangeData.empty(spacecraft=sep.spacecraft_label(goes_num))

    monkeypatch.setattr(sep, "_load_spacecraft_range", fake_load)

    result = sep.load_sep_proton_range(
        datetime(2026, 2, 10, 0, 0, 0),
        datetime(2026, 2, 10, 1, 0, 0),
        spacecraft="auto",
    )

    assert attempts == [19, 18, 17]
    assert result.spacecraft == "GOES-17"


def test_load_sep_proton_range_honors_manual_spacecraft_override(monkeypatch):
    attempts: list[int] = []

    def fake_load(goes_num, _start_dt, _end_dt, **_kwargs):
        attempts.append(goes_num)
        return sep.SepProtonRangeData.empty(spacecraft=sep.spacecraft_label(goes_num))

    monkeypatch.setattr(sep, "_load_spacecraft_range", fake_load)

    result = sep.load_sep_proton_range(
        datetime(2026, 2, 10, 0, 0, 0),
        datetime(2026, 2, 10, 1, 0, 0),
        spacecraft="GOES-16",
    )

    assert attempts == [16]
    assert result.spacecraft == "GOES-16"


def test_load_sep_proton_range_raises_on_missing_manual_day(monkeypatch):
    def fake_download(goes_num, day, **_kwargs):
        raise FileNotFoundError(f"{sep.spacecraft_label(goes_num)} missing {day:%Y-%m-%d}")

    monkeypatch.setattr(sep, "download_daily_file", fake_download)

    with pytest.raises(sep.SepProtonDataError, match="GOES-19 missing 2026-02-10"):
        sep.load_sep_proton_range(
            datetime(2026, 2, 10, 0, 0, 0),
            datetime(2026, 2, 10, 1, 0, 0),
            spacecraft=19,
        )
