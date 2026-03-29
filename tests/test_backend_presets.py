"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from src.Backend.presets import (
    build_preset,
    delete_preset,
    dump_presets_json,
    normalize_preset,
    parse_presets_json,
    upsert_preset,
)


def test_preset_round_trip_json():
    p = build_preset(
        "Default Lab",
        {
            "noise_clip_low": -5.25,
            "noise_clip_high": 20.5,
            "noise_clip_scale": "signed_log",
            "cmap": "Turbo",
        },
    )
    raw = dump_presets_json([p])
    out = parse_presets_json(raw)
    assert len(out) == 1
    assert out[0]["name"] == "Default Lab"
    assert out[0]["settings"]["cmap"] == "Turbo"
    assert out[0]["settings"]["noise_clip_low"] == -5.25
    assert out[0]["settings"]["noise_clip_high"] == 20.5
    assert out[0]["settings"]["noise_clip_scale"] == "signed_log"
    assert out[0]["settings"]["lower_slider"] == -5
    assert out[0]["settings"]["upper_slider"] == 20


def test_upsert_and_delete_case_insensitive():
    items = []
    a = build_preset("Solar A", {"lower_slider": 1, "upper_slider": 2})
    items, replaced = upsert_preset(items, a)
    assert replaced is False

    b = build_preset("solar a", {"lower_slider": 3, "upper_slider": 4})
    items, replaced = upsert_preset(items, b)
    assert replaced is True
    assert len(items) == 1
    assert items[0]["settings"]["lower_slider"] == 3
    assert items[0]["settings"]["noise_clip_low"] == 3.0
    assert items[0]["settings"]["noise_clip_high"] == 4.0

    items, removed = delete_preset(items, "SOLAR A")
    assert removed is True
    assert items == []


def test_normalize_preset_reads_legacy_noise_thresholds():
    preset = normalize_preset(
        {
            "name": "Legacy",
            "settings": {
                "lower_slider": -9,
                "upper_slider": 14,
            },
        }
    )

    assert preset["settings"]["lower_slider"] == -9
    assert preset["settings"]["upper_slider"] == 14
    assert preset["settings"]["noise_clip_low"] == -9.0
    assert preset["settings"]["noise_clip_high"] == 14.0
    assert preset["settings"]["noise_clip_scale"] == "linear"
