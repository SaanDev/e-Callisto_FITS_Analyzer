"""
e-CALLISTO FITS Analyzer
Unit tests for recorded length / area measurements
(src/backend/solar/surface_records.py).
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from src.backend.solar.surface_measure import (
    SurfaceGeometry,
    mask_surface_area,
    polyline_surface_length,
)
from src.backend.solar.surface_records import (
    CSV_COLUMNS,
    CSV_NOTES,
    SurfaceRecord,
    area_values,
    csv_row,
    deserialize_records,
    deserialize_shapes,
    length_values,
    serialize_records,
    serialize_shapes,
    write_csv,
)

G = SurfaceGeometry(
    rsun_arcsec=960.0,
    x_ref_pix=99.5,
    y_ref_pix=99.5,
    x_scale_arcsec_per_pix=12.0,
    y_scale_arcsec_per_pix=12.0,
    b0_deg=2.5,
)


def _area(mask):
    return mask_surface_area(mask, G, image=np.full(mask.shape, 7.0))


def _area_result(*, sunspot=False, quicklook=False):
    region = np.zeros((200, 200), dtype=bool)
    region[80:120, 80:120] = True
    spot = np.zeros_like(region)
    spot[90:110, 90:110] = True
    umbra = np.zeros_like(region)
    umbra[96:104, 96:104] = True
    return SimpleNamespace(
        region=_area(region),
        selected=_area(spot) if sunspot else None,
        umbra=_area(umbra) if sunspot else None,
        penumbra=_area(spot & ~umbra) if sunspot else None,
        intensity_unit="DN",
        quicklook=quicklook,
    )


def _length_record(**kw):
    result = polyline_surface_length([(-300.0, 100.0), (0.0, 200.0), (300.0, 100.0)], G)
    base = dict(
        name="Filament 1",
        kind="length",
        shape="length",
        points=[(-300.0, 100.0), (0.0, 200.0), (300.0, 100.0)],
        frame_index=2,
        time=datetime(2024, 5, 10, 12, 0, 5),
        values=length_values(result, G),
        summary="Length · ...",
    )
    base.update(kw)
    return SurfaceRecord(**base), result


def _sunspot_record():
    result = _area_result(sunspot=True)
    return SurfaceRecord(
        name="Sunspot 1",
        kind="area",
        shape="rectangle",
        points=[(-240.0, -240.0), (240.0, 240.0)],
        frame_index=0,
        time=datetime(2024, 5, 10, 12, 0, 0),
        threshold={"mode": "sunspot", "umbra_level": 0.6, "penumbra_level": 0.9, "level": 0.85, "min_cluster_px": 3},
        values=area_values(result, G),
        summary="Sunspot (rectangle) · ...",
    ), result


def test_length_values_are_in_megametres():
    record, result = _length_record()
    v = record.values
    assert v["length_total_mm"] == pytest.approx(result.total_km / 1000.0)
    assert v["length_plane_of_sky_arcsec"] == pytest.approx(result.plane_of_sky_arcsec)
    assert v["n_points"] == 3
    assert v["start_lon_deg"] == pytest.approx(result.start_lonlat[0])
    assert v["b0_deg"] == pytest.approx(2.5)
    assert v["rsun_arcsec"] == pytest.approx(960.0)
    assert record.headline == f"{result.total_km / 1000.0:,.1f} Mm"
    assert record.measure_label == "Length"
    assert record.line == "[Filament 1] Length · ..."


def test_area_values_use_the_thresholded_pixels():
    record, result = _sunspot_record()
    v = record.values
    assert v["area_msh"] == pytest.approx(result.selected.on_disk_msh)
    assert v["region_msh"] == pytest.approx(result.region.on_disk_msh)
    assert v["umbra_msh"] == pytest.approx(result.umbra.on_disk_msh)
    assert v["umbra_msh"] + v["penumbra_msh"] == pytest.approx(v["area_msh"])
    assert v["mean_intensity"] == pytest.approx(7.0)
    assert record.measure_label == "Sunspot"
    # Above 100 MSH the table drops the decimal, for the umbra too.
    assert record.headline == f"{v['area_msh']:,.0f} MSH (U {v['umbra_msh']:,.0f})"
    small = SurfaceRecord("S", "area", "polygon", [(0, 0), (1, 1)], 0, values={"area_msh": 42.25})
    assert small.headline == "42.2 MSH"


def test_area_values_whole_shape_and_quicklook():
    v = area_values(_area_result(quicklook=True), G)
    assert v["area_msh"] == pytest.approx(v["region_msh"])
    assert v["umbra_msh"] is None
    assert v["mean_intensity"] is None  # quicklook bytes are not intensities


def test_measure_labels_for_level_thresholds():
    record = SurfaceRecord("A", "area", "polygon", [(0, 0), (1, 1)], 0, threshold={"mode": "bright", "level": 1.5})
    assert record.measure_label == "Area ≥ 1.50"
    record.threshold = {"mode": "dark", "level": 0.8}
    assert record.measure_label == "Area < 0.80"
    record.values = {"area_msh": 0.0, "off_limb_mm2": 1234.0}
    assert record.headline == "1,234 Mm² off-limb"


def test_csv_round_trip(tmp_path):
    length, _ = _length_record()
    spot, _ = _sunspot_record()
    path = tmp_path / "out.csv"
    assert write_csv(str(path), [length, spot]) == 2

    text = path.read_text(encoding="utf-8").splitlines()
    assert text[: len(CSV_NOTES)] == list(CSV_NOTES)
    rows = list(csv.DictReader(text[len(CSV_NOTES):]))
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert rows[0]["name"] == "Filament 1"
    assert rows[0]["frame"] == "3"  # 1-based
    assert rows[0]["time_utc"] == "2024-05-10T12:00:05"
    assert rows[0]["threshold"] == ""
    assert float(rows[0]["length_total_mm"]) == pytest.approx(length.values["length_total_mm"], rel=1e-5)
    assert rows[0]["area_msh"] == ""
    assert rows[0]["vertices_arcsec"] == "-300.00 100.00; 0.00 200.00; 300.00 100.00"
    assert rows[1]["threshold"] == "sunspot"
    assert rows[1]["umbra_level"] == "0.6"
    assert rows[1]["level"] == ""  # only meaningful for dark / bright
    assert rows[1]["near_limb"] == "0"
    assert float(rows[1]["umbra_msh"]) == pytest.approx(spot.values["umbra_msh"], rel=1e-5)


def test_csv_row_blanks_missing_values():
    record = SurfaceRecord("X", "area", "polygon", [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], 0, values={"area_msh": float("nan")})
    row = csv_row(record)
    assert row["area_msh"] == ""
    assert row["time_utc"] == ""
    assert row["threshold"] == "none"


def test_records_serialize_to_json_and_back():
    length, _ = _length_record()
    spot, _ = _sunspot_record()
    spot.values["weird"] = float("inf")
    raw = serialize_records([length, spot])
    text = json.dumps(raw, allow_nan=False)  # must be strict JSON
    back = deserialize_records(json.loads(text))
    assert [r.name for r in back] == ["Filament 1", "Sunspot 1"]
    assert back[0].points == length.points
    assert back[0].time == length.time
    assert back[0].frame_index == 2
    assert back[1].threshold["mode"] == "sunspot"
    assert back[1].values["umbra_msh"] == pytest.approx(spot.values["umbra_msh"])
    assert back[1].values["weird"] is None
    assert back[1].summary == spot.summary


def test_deserialize_skips_malformed_rows():
    raw = [
        "nonsense",
        {"kind": "volume", "shape": "x", "points": [[0, 0], [1, 1]]},
        {"kind": "area", "shape": "polygon", "points": [[0, 0]]},  # one point
        {"kind": "area", "shape": "polygon", "points": [[0, 0], [1, "a"], [2, 2]], "frame_index": "bad"},
        {"kind": "length", "shape": "length", "points": [[0, 0], [math.nan, 1], [3, 3]], "time": "not a time"},
    ]
    back = deserialize_records(raw)
    assert len(back) == 1
    assert back[0].points == [(0.0, 0.0), (3.0, 3.0)]
    assert back[0].time is None
    assert back[0].name == "Length"
    assert deserialize_records(None) == []


def test_shapes_serialize_round_trip():
    shapes = {
        "length": SimpleNamespace(kind="length", points=[(0.0, 0.0), (1.0, 2.0)]),
        "area": SimpleNamespace(kind="ellipse", points=[(-5.0, -5.0), (5.0, 5.0)]),
        "other": SimpleNamespace(kind="x", points=[(0, 0), (1, 1)]),
    }
    raw = serialize_shapes(shapes)
    assert set(raw) == {"length", "area"}
    back = deserialize_shapes(json.loads(json.dumps(raw)))
    assert back["area"] == ("ellipse", [(-5.0, -5.0), (5.0, 5.0)])
    assert deserialize_shapes({"area": {"kind": "polygon", "points": [[0, 0]]}}) == {}
    assert deserialize_shapes([]) == {}


# ------------------------------------------------------------------ series
from datetime import timedelta

from src.backend.solar.surface_records import (
    feature_series,
    group_rows_by_feature,
    linear_rate,
)


def _row(name, frame, hours, *, kind="area", msh=None, umbra=None, mm=None, off=None, mode="none"):
    values = {}
    if kind == "area":
        values = {"area_msh": msh, "umbra_msh": umbra, "off_limb_mm2": off}
    else:
        values = {"length_total_mm": mm}
    return SurfaceRecord(
        name, kind, "polygon" if kind == "area" else "length", [(0.0, 0.0), (1.0, 1.0)], frame,
        time=None if hours is None else datetime(2024, 5, 10) + timedelta(hours=hours),
        threshold={"mode": mode}, values=values,
    )


def test_feature_series_orders_by_time_and_adds_umbra():
    rows = [
        _row("Spot", 2, 12.0, msh=90.0, umbra=20.0, mode="sunspot"),
        _row("Other", 0, 0.0, msh=5.0),
        _row("Spot", 0, 0.0, msh=100.0, umbra=25.0, mode="sunspot"),
        _row("Spot", 1, 6.0, msh=95.0, umbra=22.0, mode="sunspot"),
        _row("Spot", 3, None, msh=1.0, mode="sunspot"),  # no time: left out
    ]
    series = feature_series(rows, "Spot")
    assert series.values == [100.0, 95.0, 90.0]
    assert series.second == [25.0, 22.0, 20.0]
    assert series.seconds == [0.0, 6 * 3600.0, 12 * 3600.0]
    assert series.unit == "MSH"
    assert series.skipped == 1
    assert feature_series(rows, "Nobody") is None


def test_feature_series_one_point_per_frame_and_off_limb_fallback():
    rows = [
        _row("Arcade", 0, 0.0, msh=0.0, off=500.0),
        _row("Arcade", 1, 1.0, msh=0.0, off=700.0),
        _row("Arcade", 1, 1.0, msh=0.0, off=750.0),  # re-recorded: the later row wins
    ]
    series = feature_series(rows, "Arcade")
    assert series.values == [500.0, 750.0]
    assert series.unit.startswith("Mm²")
    assert series.second is None
    lengths = feature_series([_row("F", 0, 0.0, kind="length", mm=120.0)], "F")
    assert lengths.unit == "Mm" and lengths.values == [120.0]


def test_linear_rate_slope_and_error():
    hours = [0.0, 1.0, 2.0, 3.0]
    rate = linear_rate([h * 3600 for h in hours], [10.0, 12.0, 14.0, 16.0])
    assert rate.per_hour == pytest.approx(2.0)
    assert rate.error_per_hour == pytest.approx(0.0, abs=1e-12)
    noisy = linear_rate([h * 3600 for h in hours], [10.0, 12.5, 13.5, 16.0])
    assert noisy.error_per_hour > 0.0
    two = linear_rate([0.0, 7200.0], [5.0, 1.0])
    assert two.per_hour == pytest.approx(-2.0)
    assert two.error_per_hour is None
    assert linear_rate([0.0, 0.0], [1.0, 2.0]) is None
    assert linear_rate([0.0], [1.0]) is None
    assert rate.text("MSH", span_hours=3.0) == "+2.00 ± 0.00 MSH/h"
    assert two.text("Mm", span_hours=72.0) == "-48.00 Mm/day"


def test_group_rows_by_feature():
    rows = [_row("B", 2, 0.0, msh=1.0), _row("A", 1, 0.0, msh=1.0), _row("B", 0, 0.0, msh=1.0), _row("A", 0, 0.0, msh=1.0)]
    assert [(r.name, r.frame_index) for r in group_rows_by_feature(rows)] == [("B", 0), ("B", 2), ("A", 0), ("A", 1)]


# ---------------------------------------------- corrected vs as-seen values
def test_area_values_keep_the_as_seen_area_in_msh():
    from src.backend.solar.surface_measure import km2_to_msh

    record, result = _sunspot_record()
    v = record.values
    assert v["area_as_seen_msh"] == pytest.approx(km2_to_msh(result.selected.on_disk_plane_of_sky_km2, rsun_km=G.rsun_km))
    assert v["umbra_as_seen_msh"] == pytest.approx(km2_to_msh(result.umbra.on_disk_plane_of_sky_km2, rsun_km=G.rsun_km))
    assert v["region_as_seen_msh"] == pytest.approx(km2_to_msh(result.region.on_disk_plane_of_sky_km2, rsun_km=G.rsun_km))
    # Seen near disk centre the two barely differ; the corrected one is the true size.
    assert record.value("area_msh", corrected=False) == pytest.approx(v["area_as_seen_msh"])
    assert record.headline_for(corrected=False).startswith(f"{v['area_as_seen_msh']:,.0f} MSH")
    assert record.headline == record.headline_for(corrected=True)


def test_as_seen_falls_back_for_rows_saved_before_it_existed():
    old = SurfaceRecord("Old", "area", "polygon", [(0, 0), (1, 1)], 0, values={"area_msh": 120.0, "area_plane_of_sky_mm2": 182.0})
    seen = old.value("area_msh", corrected=False)
    assert seen == pytest.approx(182.0e6 / (2 * math.pi * 695_700.0**2) * 1e6)
    length, result = _length_record()
    assert length.headline_for(corrected=False) == f"{result.plane_of_sky_km / 1000.0:,.1f} Mm"


def test_feature_series_as_seen():
    rows = [
        _row("Spot", 0, 0.0, msh=100.0, umbra=25.0, mode="sunspot"),
        _row("Spot", 1, 6.0, msh=90.0, umbra=20.0, mode="sunspot"),
    ]
    rows[0].values.update(area_as_seen_msh=60.0, umbra_as_seen_msh=15.0)
    rows[1].values.update(area_as_seen_msh=40.0, umbra_as_seen_msh=9.0)
    seen = feature_series(rows, "Spot", corrected=False)
    assert seen.values == [60.0, 40.0]
    assert seen.second == [15.0, 9.0]
    assert "as seen" in seen.label
    assert feature_series(rows, "Spot").values == [100.0, 90.0]
