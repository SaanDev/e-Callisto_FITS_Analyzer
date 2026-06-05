"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import pytest

from src.Backend.view_config import (
    build_display_range_preset,
    build_view_config,
    delete_display_range_preset,
    dump_display_range_presets_json,
    dump_view_config_json,
    normalize_display_range,
    parse_display_range_presets_json,
    parse_view_config_json,
    upsert_display_range_preset,
)


def test_display_range_preset_round_trip_json():
    preset = build_display_range_preset(
        "Type II Window",
        {"time_start_s": 120.0, "time_stop_s": 420.0, "freq_min_mhz": 180.0, "freq_max_mhz": 45.0},
        time_mode="ut",
    )

    raw = dump_display_range_presets_json([preset])
    out = parse_display_range_presets_json(raw)

    assert len(out) == 1
    assert out[0]["name"] == "Type II Window"
    assert out[0]["time_mode"] == "ut"
    assert out[0]["range"]["freq_min_mhz"] == pytest.approx(45.0)
    assert out[0]["range"]["freq_max_mhz"] == pytest.approx(180.0)


def test_view_config_round_trip_json():
    cfg = build_view_config(
        display_range={"time_start_s": 10.0, "time_stop_s": 20.0, "freq_min_mhz": 40.0, "freq_max_mhz": 90.0},
        visual={
            "use_db": True,
            "use_utc": True,
            "noise_clip_low": -12.0,
            "noise_clip_high": 18.0,
            "noise_clip_scale": "linear",
            "cmap": "inferno",
            "graph": {"remove_titles": True},
        },
        time_mode="seconds",
    )

    out = parse_view_config_json(dump_view_config_json(cfg))

    assert out["range"]["time_start_s"] == pytest.approx(10.0)
    assert out["visual"]["use_db"] is True
    assert out["visual"]["use_utc"] is True
    assert out["visual"]["cmap"] == "inferno"
    assert out["visual"]["graph"]["remove_titles"] is True


def test_display_range_validation_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        normalize_display_range({"time_start_s": 5.0, "time_stop_s": 5.0, "freq_min_mhz": 40.0, "freq_max_mhz": 90.0})

    with pytest.raises(ValueError):
        normalize_display_range({"time_start_s": 1.0, "time_stop_s": 2.0, "freq_min_mhz": 40.0, "freq_max_mhz": 40.0})


def test_display_range_preset_upsert_and_delete():
    first = build_display_range_preset(
        "Event",
        {"time_start_s": 1.0, "time_stop_s": 2.0, "freq_min_mhz": 40.0, "freq_max_mhz": 90.0},
    )
    second = build_display_range_preset(
        "event",
        {"time_start_s": 3.0, "time_stop_s": 4.0, "freq_min_mhz": 45.0, "freq_max_mhz": 95.0},
    )

    items, replaced = upsert_display_range_preset([], first)
    assert replaced is False
    items, replaced = upsert_display_range_preset(items, second)
    assert replaced is True
    assert len(items) == 1
    assert items[0]["range"]["time_start_s"] == pytest.approx(3.0)

    items, removed = delete_display_range_preset(items, "EVENT")
    assert removed is True
    assert items == []
