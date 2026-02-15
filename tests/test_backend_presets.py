from __future__ import annotations

from src.Backend.presets import (
    build_preset,
    delete_preset,
    dump_presets_json,
    parse_presets_json,
    upsert_preset,
)


def test_preset_round_trip_json():
    p = build_preset("Default Lab", {"lower_slider": -5, "upper_slider": 20, "cmap": "Turbo"})
    raw = dump_presets_json([p])
    out = parse_presets_json(raw)
    assert len(out) == 1
    assert out[0]["name"] == "Default Lab"
    assert out[0]["settings"]["cmap"] == "Turbo"


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

    items, removed = delete_preset(items, "SOLAR A")
    assert removed is True
    assert items == []
