"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.Backend import goes_overlay as goes_overlay_module
from src.Backend.goes_overlay import (
    GOES_OVERLAY_CHANNEL_LABELS,
    build_goes_overlay_payload,
    fetch_goes_overlay,
    goes_class_ticks_for_limits,
    goes_flux_axis_limits,
    normalize_goes_overlay_curve,
    normalize_goes_satellite_numbers,
    pick_goes_long_channel,
    pick_goes_short_channel,
)


def test_pick_goes_long_channel_prefers_long_tokens():
    cols = ["xrsa_flux", "XRSB_Flux_Long", "quality_flag"]
    assert pick_goes_long_channel(cols) == "XRSB_Flux_Long"


def test_pick_goes_short_channel_prefers_short_tokens():
    cols = ["quality_flag", "XRSA_Flux_Short", "xrsb_flux"]
    assert pick_goes_short_channel(cols) == "XRSA_Flux_Short"


def test_pick_goes_channels_prefer_flux_over_flags_counts_and_quality():
    cols = ["xrsb_flags", "xrsb_primary_chan", "xrsb_flux", "xrsa_flags", "xrsa_flux", "quality_flag"]
    assert pick_goes_long_channel(cols) == "xrsb_flux"
    assert pick_goes_short_channel(cols) == "xrsa_flux"


def test_pick_goes_channels_support_legacy_a_flux_b_flux_aliases():
    cols = ["a_flags", "b_flags", "a_flux", "b_flux"]
    assert pick_goes_short_channel(cols) == "a_flux"
    assert pick_goes_long_channel(cols) == "b_flux"


def test_build_goes_overlay_payload_rejects_missing_overlay_channels():
    idx = pd.to_datetime(["2026-02-10T01:00:00Z", "2026-02-10T01:01:00Z"], utc=True)
    frame = pd.DataFrame({"quality_flag": [0, 0]}, index=idx)

    with pytest.raises(RuntimeError, match="XRS-A or XRS-B"):
        build_goes_overlay_payload(
            frame,
            base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
            start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
            end_utc=datetime(2026, 2, 10, 1, 1, 0, tzinfo=timezone.utc),
        )


def test_build_goes_overlay_payload_converts_times_to_plot_seconds():
    idx = pd.to_datetime(
        [
            "2026-02-10T00:59:30Z",
            "2026-02-10T01:00:00Z",
            "2026-02-10T01:01:00Z",
            "2026-02-10T01:02:00Z",
        ],
        utc=True,
    )
    frame = pd.DataFrame(
        {
            "xrsa_flux_short": [5e-9, 1e-8, 2e-8, 3e-8],
            "xrsb_flux_long": [1e-8, 2e-8, 5e-8, 3e-8],
        },
        index=idx,
    )

    payload = build_goes_overlay_payload(
        frame,
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 2, 0, tzinfo=timezone.utc),
    )

    assert payload.channel_label == "xrsb_flux_long"
    assert payload.satellite_numbers == (16,)
    assert np.allclose(payload.x_seconds, np.array([0.0, 60.0, 120.0], dtype=float))
    assert np.allclose(payload.flux_wm2, np.array([2e-8, 5e-8, 3e-8], dtype=float))
    assert tuple(payload.series.keys()) == ("xrsa", "xrsb")
    assert payload.series["xrsa"].display_label == GOES_OVERLAY_CHANNEL_LABELS["xrsa"]
    assert np.allclose(payload.series["xrsa"].x_seconds, np.array([0.0, 60.0, 120.0], dtype=float))
    assert np.allclose(payload.series["xrsa"].flux_wm2, np.array([1e-8, 2e-8, 3e-8], dtype=float))


def test_build_goes_overlay_payload_handles_cross_day_range():
    idx = pd.to_datetime(
        [
            "2026-02-10T23:57:30Z",
            "2026-02-10T23:58:00Z",
            "2026-02-10T23:59:00Z",
            "2026-02-11T00:00:00Z",
            "2026-02-11T00:01:00Z",
        ],
        utc=True,
    )
    frame = pd.DataFrame(
        {
            "short_channel_xrsa": [7e-9, 8e-9, 9e-9, 1e-8, 2e-8],
            "long_channel_xrsb": [1e-8, 2e-8, 3e-8, 4e-8, 5e-8],
        },
        index=idx,
    )

    payload = build_goes_overlay_payload(
        frame,
        base_utc=datetime(2026, 2, 10, 23, 58, 0, tzinfo=timezone.utc),
        start_utc=datetime(2026, 2, 10, 23, 58, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 11, 0, 1, 0, tzinfo=timezone.utc),
    )

    assert np.allclose(payload.x_seconds, np.array([0.0, 60.0, 120.0, 180.0], dtype=float))
    assert np.allclose(payload.flux_wm2, np.array([2e-8, 3e-8, 4e-8, 5e-8], dtype=float))
    assert np.allclose(payload.series["xrsa"].x_seconds, np.array([0.0, 60.0, 120.0, 180.0], dtype=float))


def test_build_goes_overlay_payload_accepts_legacy_a_flux_b_flux_columns():
    idx = pd.to_datetime(
        [
            "2026-02-10T01:00:00Z",
            "2026-02-10T01:01:00Z",
            "2026-02-10T01:02:00Z",
        ],
        utc=True,
    )
    frame = pd.DataFrame(
        {
            "a_flags": [0, 0, 0],
            "b_flags": [0, 0, 0],
            "a_flux": [6e-9, 8e-9, 1e-8],
            "b_flux": [1e-8, 2e-8, 4e-8],
        },
        index=idx,
    )

    payload = build_goes_overlay_payload(
        frame,
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 2, 0, tzinfo=timezone.utc),
    )

    assert payload.channel_label == "b_flux"
    assert np.allclose(payload.series["xrsa"].flux_wm2, np.array([6e-9, 8e-9, 1e-8], dtype=float))
    assert np.allclose(payload.series["xrsb"].flux_wm2, np.array([1e-8, 2e-8, 4e-8], dtype=float))


def test_normalize_goes_satellite_numbers_defaults_and_deduplicates():
    assert normalize_goes_satellite_numbers(None) == (16, 17, 18, 19)
    assert normalize_goes_satellite_numbers([17, 17, 18, -1, 0, 19]) == (17, 18, 19)


def test_goes_class_ticks_and_limits_use_class_boundaries():
    limits = goes_flux_axis_limits(np.array([2e-8, 3e-6], dtype=float))
    assert limits is not None
    ticks = goes_class_ticks_for_limits(*limits)
    assert ticks == [(1e-08, "A"), (1e-07, "B"), (1e-06, "C"), (1e-05, "M")]


def test_normalize_goes_overlay_curve_collapses_duplicates_and_isolated_spikes():
    xs = np.array([0.0, 60.0, 60.0, 120.0, 180.0], dtype=float)
    flux = np.array([1.0e-8, 1.1e-8, 1.2e-8, 8.0e-8, 1.15e-8], dtype=float)

    norm_x, norm_flux = normalize_goes_overlay_curve(xs, flux)

    assert np.allclose(norm_x, np.array([0.0, 60.0, 120.0, 180.0], dtype=float))
    assert np.isclose(norm_flux[1], 1.15e-8)
    assert np.isclose(norm_flux[2], 1.15e-8)


def test_load_goes_overlay_frame_prefers_direct_netcdf_loader(monkeypatch, tmp_path):
    nc_path = tmp_path / "goes18.nc"
    nc_path.write_bytes(b"placeholder")
    seen = {"direct": None, "fallback": False}

    def fake_direct(paths, *, cancel_cb=None):
        seen["direct"] = [str(item) for item in paths]
        idx = pd.to_datetime(["2026-02-10T01:00:00Z"], utc=True)
        return pd.DataFrame({"xrsb_long": [1e-8]}, index=idx)

    def fake_fallback(paths, data_kind):
        seen["fallback"] = True
        raise AssertionError("load_downloaded should not be used for local GOES netCDF overlay files")

    monkeypatch.setattr(goes_overlay_module, "_load_goes_overlay_frame_from_netcdf_paths", fake_direct)
    monkeypatch.setattr(goes_overlay_module, "load_downloaded", fake_fallback)

    frame = goes_overlay_module._load_goes_overlay_frame([nc_path])

    assert seen["direct"] == [str(nc_path.resolve())]
    assert seen["fallback"] is False
    assert list(frame.columns) == ["xrsb_long"]


def test_load_goes_overlay_time_values_prefers_manual_standard_unit_parser():
    class FakeNC:
        @staticmethod
        def num2date(*_args, **_kwargs):
            raise AssertionError("num2date should not be used for standard GOES time units")

    class FakeTimeVar:
        units = "seconds since 2026-02-10 01:00:00"
        calendar = "standard"

        def __getitem__(self, _item):
            return np.array([0, 60, 120], dtype=np.int64)

    values = goes_overlay_module._load_goes_overlay_time_values(FakeTimeVar(), nc_module=FakeNC)

    assert values == [
        datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 10, 1, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 10, 1, 2, 0, tzinfo=timezone.utc),
    ]


def test_load_goes_overlay_time_values_falls_back_to_python_num2date():
    class FakeNC:
        @staticmethod
        def num2date(values, units, calendar=None, only_use_cftime_datetimes=None, only_use_python_datetimes=None):
            assert units == "fortnights since 2026-02-10T01:00:00"
            assert calendar == "standard"
            assert only_use_cftime_datetimes is False
            assert only_use_python_datetimes is True
            return np.array(
                [
                    datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 2, 10, 1, 14, 0, tzinfo=timezone.utc),
                ],
                dtype=object,
            )

    class FakeTimeVar:
        units = "fortnights since 2026-02-10T01:00:00"
        calendar = "standard"

        def __getitem__(self, _item):
            return np.array([0, 1], dtype=np.int64)

    values = goes_overlay_module._load_goes_overlay_time_values(FakeTimeVar(), nc_module=FakeNC)

    assert values == [
        datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 10, 1, 14, 0, tzinfo=timezone.utc),
    ]


def test_coerce_goes_numeric_series_handles_masked_uint8_without_crashing():
    arr = np.ma.array([1, 2, 3], mask=[False, True, False], dtype=np.uint8)

    series = goes_overlay_module._coerce_goes_numeric_series(arr, expected_size=3)

    assert series is not None
    assert series.dtype.kind == "f"
    assert np.isclose(series[0], 1.0)
    assert np.isnan(series[1])
    assert np.isclose(series[2], 3.0)


def test_fetch_goes_overlay_searches_all_satellites_and_selects_best(monkeypatch, tmp_path):
    calls = {"search": [], "fetch": [], "load": []}

    def fake_search(spec):
        sat = int(spec.satellite_number)
        calls["search"].append(sat)
        rows = []
        if sat in {17, 18}:
            rows = [object()]
        return type("Result", (), {"rows": rows, "satellite_number": sat})()

    def fake_fetch(search_result, cache_dir, selected_rows=None, progress_cb=None):
        calls["fetch"].append(len(search_result.rows))
        if not search_result.rows:
            return type("FetchResult", (), {"paths": [], "errors": [], "failed_count": 0})()
        sat = int(search_result.satellite_number)
        return type("FetchResult", (), {"paths": [str(tmp_path / f"goes{sat}.nc")], "errors": [], "failed_count": 0})()

    def fake_load_frame(paths, *, cancel_cb=None):
        calls["load"].append(list(paths))
        sat = 17 if "goes17" in str(paths[0]) else 18
        idx = pd.to_datetime(
            [
                "2026-02-10T01:00:00Z",
                "2026-02-10T01:01:00Z",
                "2026-02-10T01:02:00Z",
                "2026-02-10T01:03:00Z",
            ],
            utc=True,
        )
        if sat == 17:
            vals = [1e-8, 2e-8, 3e-8, 4e-8]
        else:
            vals = [1e-8, 2e-8]
            idx = idx[:2]
        return pd.DataFrame(
            {
                "xrsa_short": np.asarray(vals, dtype=float) / 2.0,
                "xrsb_long": vals,
            },
            index=idx,
        )

    monkeypatch.setattr("src.Backend.goes_overlay.search", fake_search)
    monkeypatch.setattr("src.Backend.goes_overlay.fetch", fake_fetch)
    monkeypatch.setattr("src.Backend.goes_overlay._load_goes_overlay_frame", fake_load_frame)

    payload = fetch_goes_overlay(
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 3, 0, tzinfo=timezone.utc),
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        cache_dir=tmp_path,
        satellite_numbers=(16, 17, 18, 19),
    )

    assert calls["search"] == [16, 17, 18, 19]
    assert payload.satellite_number == 17
    assert payload.satellite_numbers == (17,)
    assert np.allclose(payload.x_seconds, np.array([0.0, 60.0, 120.0, 180.0], dtype=float))
    assert tuple(payload.series.keys()) == ("xrsa", "xrsb")


def test_fetch_goes_overlay_honors_cancel_cb_before_archive_search(tmp_path):
    with pytest.raises(goes_overlay_module.GoesOverlayCancelled):
        fetch_goes_overlay(
            start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
            end_utc=datetime(2026, 2, 10, 1, 3, 0, tzinfo=timezone.utc),
            base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
            cache_dir=tmp_path,
            satellite_numbers=(16, 17),
            cancel_cb=lambda: True,
        )


def test_fetch_goes_overlay_combines_best_channels_across_satellites(monkeypatch, tmp_path):
    def fake_search(spec):
        sat = int(spec.satellite_number)
        rows = [object()] if sat in {17, 18} else []
        return type("Result", (), {"rows": rows, "satellite_number": sat})()

    def fake_fetch(search_result, cache_dir, selected_rows=None, progress_cb=None):
        sat = int(search_result.satellite_number)
        return type("FetchResult", (), {"paths": [str(tmp_path / f"goes{sat}.nc")], "errors": [], "failed_count": 0})()

    def fake_load_frame(paths, *, cancel_cb=None):
        sat = 17 if "goes17" in str(paths[0]) else 18
        idx = pd.to_datetime(
            [
                "2026-02-10T01:00:00Z",
                "2026-02-10T01:01:00Z",
                "2026-02-10T01:02:00Z",
            ],
            utc=True,
        )
        if sat == 17:
            return pd.DataFrame({"xrsb_long": [1e-8, 2e-8, 4e-8]}, index=idx)
        if sat == 18:
            return pd.DataFrame({"xrsa_short": [8e-9, 9e-9, 1e-8]}, index=idx)
        return pd.DataFrame({"quality_flag": [0, 0, 0]}, index=idx)

    monkeypatch.setattr("src.Backend.goes_overlay.search", fake_search)
    monkeypatch.setattr("src.Backend.goes_overlay.fetch", fake_fetch)
    monkeypatch.setattr("src.Backend.goes_overlay._load_goes_overlay_frame", fake_load_frame)

    payload = fetch_goes_overlay(
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 2, 0, tzinfo=timezone.utc),
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        cache_dir=tmp_path,
        satellite_numbers=(16, 17, 18, 19),
    )

    assert payload.satellite_numbers == (17, 18)
    assert payload.series["xrsb"].satellite_number == 17
    assert payload.series["xrsa"].satellite_number == 18


def test_fetch_goes_overlay_skips_satellite_search_failures(monkeypatch, tmp_path):
    seen = {"search": []}

    def fake_search(spec):
        sat = int(spec.satellite_number)
        seen["search"].append(sat)
        if sat == 16:
            raise RuntimeError("This query was not understood by any clients. Did you miss an OR?")
        if sat == 17:
            return type("Result", (), {"rows": [object()], "satellite_number": sat})()
        return type("Result", (), {"rows": [], "satellite_number": sat})()

    def fake_fetch(search_result, cache_dir, selected_rows=None, progress_cb=None):
        sat = int(search_result.satellite_number)
        return type("FetchResult", (), {"paths": [str(tmp_path / f"goes{sat}.nc")], "errors": [], "failed_count": 0})()

    def fake_load_frame(paths, *, cancel_cb=None):
        idx = pd.to_datetime(
            [
                "2026-02-10T01:00:00Z",
                "2026-02-10T01:01:00Z",
                "2026-02-10T01:02:00Z",
            ],
            utc=True,
        )
        return pd.DataFrame(
            {
                "xrsa_short": [6e-9, 8e-9, 1e-8],
                "xrsb_long": [1e-8, 2e-8, 3e-8],
            },
            index=idx,
        )

    monkeypatch.setattr("src.Backend.goes_overlay.search", fake_search)
    monkeypatch.setattr("src.Backend.goes_overlay.fetch", fake_fetch)
    monkeypatch.setattr("src.Backend.goes_overlay._load_goes_overlay_frame", fake_load_frame)

    payload = fetch_goes_overlay(
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 2, 0, tzinfo=timezone.utc),
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        cache_dir=tmp_path,
        satellite_numbers=(16, 17, 18, 19),
    )

    assert seen["search"][:2] == [16, 17]
    assert payload.satellite_number == 17
    assert payload.satellite_numbers == (17,)
