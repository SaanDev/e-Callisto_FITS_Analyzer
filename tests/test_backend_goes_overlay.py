"""
e-CALLISTO FITS Analyzer
Version 2.3.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.Backend.goes_overlay import (
    build_goes_overlay_payload,
    fetch_goes_overlay,
    goes_class_ticks_for_limits,
    goes_flux_axis_limits,
    normalize_goes_satellite_numbers,
    pick_goes_long_channel,
)


def test_pick_goes_long_channel_prefers_long_tokens():
    cols = ["xrsa_flux", "XRSB_Flux_Long", "quality_flag"]
    assert pick_goes_long_channel(cols) == "XRSB_Flux_Long"


def test_build_goes_overlay_payload_rejects_missing_long_channel():
    idx = pd.to_datetime(["2026-02-10T01:00:00Z", "2026-02-10T01:01:00Z"], utc=True)
    frame = pd.DataFrame({"xrsa": [1e-8, 2e-8]}, index=idx)

    with pytest.raises(RuntimeError, match="long channel"):
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
    frame = pd.DataFrame({"xrsb_flux_long": [1e-8, 2e-8, 5e-8, 3e-8]}, index=idx)

    payload = build_goes_overlay_payload(
        frame,
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 2, 0, tzinfo=timezone.utc),
    )

    assert payload.channel_label == "xrsb_flux_long"
    assert np.allclose(payload.x_seconds, np.array([0.0, 60.0, 120.0], dtype=float))
    assert np.allclose(payload.flux_wm2, np.array([2e-8, 5e-8, 3e-8], dtype=float))


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
    frame = pd.DataFrame({"long_channel_xrsb": [1e-8, 2e-8, 3e-8, 4e-8, 5e-8]}, index=idx)

    payload = build_goes_overlay_payload(
        frame,
        base_utc=datetime(2026, 2, 10, 23, 58, 0, tzinfo=timezone.utc),
        start_utc=datetime(2026, 2, 10, 23, 58, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 11, 0, 1, 0, tzinfo=timezone.utc),
    )

    assert np.allclose(payload.x_seconds, np.array([0.0, 60.0, 120.0, 180.0], dtype=float))
    assert np.allclose(payload.flux_wm2, np.array([2e-8, 3e-8, 4e-8, 5e-8], dtype=float))


def test_normalize_goes_satellite_numbers_defaults_and_deduplicates():
    assert normalize_goes_satellite_numbers(None) == (16, 17, 18, 19)
    assert normalize_goes_satellite_numbers([17, 17, 18, -1, 0, 19]) == (17, 18, 19)


def test_goes_class_ticks_and_limits_use_class_boundaries():
    limits = goes_flux_axis_limits(np.array([2e-8, 3e-6], dtype=float))
    assert limits is not None
    ticks = goes_class_ticks_for_limits(*limits)
    assert ticks == [(1e-08, "A"), (1e-07, "B"), (1e-06, "C"), (1e-05, "M")]


def test_fetch_goes_overlay_searches_all_satellites_and_selects_best(monkeypatch, tmp_path):
    calls = {"search": [], "fetch": [], "load": []}

    class _FakeTimeSeries:
        def __init__(self, sat: int):
            self.sat = sat

        def to_dataframe(self):
            idx = pd.to_datetime(
                [
                    "2026-02-10T01:00:00Z",
                    "2026-02-10T01:01:00Z",
                    "2026-02-10T01:02:00Z",
                    "2026-02-10T01:03:00Z",
                ],
                utc=True,
            )
            if self.sat == 17:
                vals = [1e-8, 2e-8, 3e-8, 4e-8]
            else:
                vals = [1e-8, 2e-8]
                idx = idx[:2]
            return pd.DataFrame({"xrsb_long": vals}, index=idx)

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

    def fake_load(paths, data_kind):
        calls["load"].append(list(paths))
        sat = 17 if "goes17" in str(paths[0]) else 18
        return type("LoadResult", (), {"maps_or_timeseries": _FakeTimeSeries(sat)})()

    monkeypatch.setattr("src.Backend.goes_overlay.search", fake_search)
    monkeypatch.setattr("src.Backend.goes_overlay.fetch", fake_fetch)
    monkeypatch.setattr("src.Backend.goes_overlay.load_downloaded", fake_load)

    payload = fetch_goes_overlay(
        start_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 10, 1, 3, 0, tzinfo=timezone.utc),
        base_utc=datetime(2026, 2, 10, 1, 0, 0, tzinfo=timezone.utc),
        cache_dir=tmp_path,
        satellite_numbers=(16, 17, 18, 19),
    )

    assert calls["search"] == [16, 17, 18, 19]
    assert payload.satellite_number == 17
    assert np.allclose(payload.x_seconds, np.array([0.0, 60.0, 120.0, 180.0], dtype=float))
