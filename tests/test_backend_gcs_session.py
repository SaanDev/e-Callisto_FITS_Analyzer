"""
e-CALLISTO FITS Analyzer
Round-trip tests for GCS fit persistence (src/Backend/solar_session.py).
"""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from src.Backend.solar_session import (
    deserialize_gcs_fits,
    deserialize_gcs_parameters,
    deserialize_gcs_points,
    serialize_gcs_fits,
    serialize_gcs_parameters,
    serialize_gcs_points,
    session_gcs_count,
)

ENTRY = (
    datetime(2012, 7, 12, 16, 0, 0),
    9.0,      # apex height
    35.0,     # lon
    -12.0,    # lat
    25.0,     # tilt
    32.0,     # alpha
    0.32,     # kappa
    69.0,     # rms
    25,       # n points
    2,        # n viewpoints
    62.0,     # separation
    0.27,     # lon err
    0.21,     # lat err
    0.036,    # height err
    True,     # refined
)


def test_fits_round_trip_through_json_shapes():
    raw = serialize_gcs_fits({0: ENTRY, 3: ENTRY})
    assert len(raw) == 2
    assert raw[0]["frame_index"] == 0 and raw[1]["frame_index"] == 3

    back = deserialize_gcs_fits(raw)
    assert sorted(back) == [0, 3]
    restored = back[0]
    assert restored[0] == ENTRY[0]
    for index in range(1, 14):
        assert float(restored[index]) == pytest.approx(float(ENTRY[index]))
    assert restored[14] is True


def test_a_fit_missing_its_model_parameters_is_dropped():
    """A zeroed longitude would silently restore a different CME."""
    assert deserialize_gcs_fits([{"frame_index": 1, "time": None}]) == {}
    assert deserialize_gcs_fits([{"frame_index": 1, "apex_height_rsun": 9.0}]) == {}
    partial = {
        "frame_index": 1,
        "apex_height_rsun": 9.0,
        "lon_deg": 35.0,
        "lat_deg": -12.0,
        "tilt_deg": 25.0,
        "alpha_deg": 32.0,
        # kappa missing
    }
    assert deserialize_gcs_fits([partial]) == {}


def test_missing_error_bars_degrade_to_nan_not_zero():
    """Zero would claim a perfectly determined fit, which is the wrong story."""
    row = {
        "frame_index": 0,
        "apex_height_rsun": 9.0,
        "lon_deg": 35.0,
        "lat_deg": -12.0,
        "tilt_deg": 25.0,
        "alpha_deg": 32.0,
        "kappa": 0.32,
    }
    restored = deserialize_gcs_fits([row])[0]
    for index in (7, 10, 11, 12, 13):
        assert math.isnan(float(restored[index]))
    assert restored[14] is False


def test_malformed_input_yields_an_empty_map():
    for bad in (None, 42, "nonsense", [None], [42]):
        assert deserialize_gcs_fits(bad) == {}
    assert serialize_gcs_fits(None) == []
    assert serialize_gcs_fits({0: (1, 2)}) == []  # too short to be an entry


def test_clicked_front_points_round_trip():
    points = {0: [(1.5, 2.5), (3.5, 4.5)], 2: [(9.0, -1.0)]}
    back = deserialize_gcs_points(serialize_gcs_points(points))
    assert sorted(back) == [0, 2]
    assert back[0] == [(1.5, 2.5), (3.5, 4.5)]


def test_live_parameters_round_trip_and_reject_junk():
    assert deserialize_gcs_parameters(serialize_gcs_parameters([1, 2, 3, 4, 5, 6])) == [
        1.0, 2.0, 3.0, 4.0, 5.0, 6.0
    ]
    assert serialize_gcs_parameters(None) is None
    assert serialize_gcs_parameters([1, 2, 3]) is None  # wrong arity
    for bad in (None, "abc", {"a": 1}, [1, 2, 3], [1, 2, 3, 4, 5, "x"]):
        assert deserialize_gcs_parameters(bad) is None


def test_parameters_round_trip_from_a_real_dataclass():
    from src.Backend.gcs_model import GCSParameters

    params = GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32)
    values = deserialize_gcs_parameters(serialize_gcs_parameters(params))
    assert GCSParameters.from_array(values) == params


def test_the_count_helper_reads_the_saved_meta():
    raw = serialize_gcs_fits({0: ENTRY})
    assert session_gcs_count({"measurements": {"gcs_fits": raw}}) == 1
    assert session_gcs_count({}) == 0
    assert session_gcs_count(None) == 0
