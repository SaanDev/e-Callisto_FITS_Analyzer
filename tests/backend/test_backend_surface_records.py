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
