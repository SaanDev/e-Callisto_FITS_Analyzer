"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from src.Backend.annotations import make_annotation, normalize_annotations, toggle_all_visibility


def test_make_annotation_polygon_schema():
    ann = make_annotation(kind="polygon", points=[(1, 2), (3, 4), (5, 6)])
    assert ann["kind"] == "polygon"
    assert ann["id"]
    assert len(ann["points"]) == 3
    assert ann["visible"] is True


def test_normalize_filters_invalid_rows():
    rows = [
        {"kind": "polygon", "points": [[1, 2], [3, 4], [5, 6]]},
        {"kind": "line", "points": [[1, 2]]},
        {"kind": "text", "points": [[7, 8]], "text": "note"},
        {"kind": "bad", "points": [[1, 2]]},
    ]
    out = normalize_annotations(rows)
    assert len(out) == 2
    assert out[0]["kind"] == "polygon"
    assert out[1]["kind"] == "text"


def test_toggle_visibility_applies_to_all():
    rows = [
        make_annotation(kind="line", points=[(0, 0), (1, 1)]),
        make_annotation(kind="text", points=[(1, 1)], text="A"),
    ]
    hidden = toggle_all_visibility(rows, False)
    assert all(not x["visible"] for x in hidden)
