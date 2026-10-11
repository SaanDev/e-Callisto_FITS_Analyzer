"""
e-CALLISTO FITS Analyzer
Round-trip tests for the GCS fitting state saved in a project
(src/backend/gcs/gcs_session.py).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from src.backend.gcs.gcs_session import (
    deserialize_gcs_state,
    gcs_fit_count,
    gcs_state_has_content,
    match_frame_time,
    serialize_gcs_state,
)

T0 = datetime(2012, 7, 12, 16, 24, 0)


def _fit(when, height, **extra):
    row = dict(
        when=when, apex_height_rsun=height, lon_deg=-60.0, lat_deg=-10.0, tilt_deg=45.0,
        alpha_deg=30.0, kappa=0.4, rms_arcsec=12.5, n_points=7, n_viewpoints=3,
        separation_deg=120.0, lon_err_deg=float("nan"), lat_err_deg=1.5, height_err_rsun=0.1, refined=True,
    )
    row.update(extra)
    return row


def _state():
    provenance = {
        "target_time_utc": T0,
        "max_time_offset_seconds": 300.0,
        "frames": [
            {
                "panel": "B", "source_key": "LASCO C2", "instrument": "SOHO C2",
                "observation_time_utc": T0 + timedelta(seconds=20), "offset_seconds": 20.0,
                "included_in_fit": True, "display_mode": "running",
                "difference_reference_utc": T0 - timedelta(minutes=12),
                "observer": {"lon_deg": 0.0, "lat_deg": 1.0, "dsun_rsun": 213.0, "rsun_arcsec": 960.0,
                             "obstime": T0, "label": "B"},
                "front_points_arcsec": [(100.0, 200.0), (110.0, 190.0)],
                "image_shape": [1024, 1024],
            }
        ],
    }
    return {
        "event_range": (T0 - timedelta(hours=1), T0 + timedelta(hours=1)),
        "shared_time": T0,
        "requested_time": T0,
        "editing": "shock",
        "max_frames": 40,
        "panels": [
            {"label": "A", "source_key": "COR2-B", "start": T0 - timedelta(hours=1), "end": T0 + timedelta(hours=1),
             "colormap": "gray", "low": 1.0, "high": 99.0, "frame_times": [T0, T0 + timedelta(minutes=15)],
             "listing": [T0 - timedelta(minutes=15), T0, T0 + timedelta(minutes=15)],
             "jp2_members": ["gcs/jp2/A/hv31_20120712T162400.jp2"]},
            {"label": "B", "source_key": "LASCO C2", "start": T0, "end": T0 + timedelta(hours=1),
             "colormap": "soholasco2", "low": 2.5, "high": 97.0, "frame_times": [], "listing": []},
        ],
        "display": {"difference_mode": "base", "layout": "focus", "focus": 1, "wireframe_colour": (1, 2, 3),
                    "shock_colour": (4, 5, 6), "fps": 7, "sync_tolerance_min": 2.5, "fit_order": 2},
        "tracks": {
            "gcs": {
                "params": {"lon_deg": -60.0, "lat_deg": -10.0, "tilt_deg": 45.0, "height_rsun": 8.0,
                           "alpha_deg": 30.0, "kappa": 0.4},
                "clicks": {"A": [(1.0, 2.0)], "B": [], "C": []},
                "frame_points": [{"panel": "A", "frame_time": T0, "points": [(1.0, 2.0), (3.0, 4.0)]}],
                "click_order": [{"panel": "A", "frame_time": T0, "point": (1.0, 2.0)}],
                "fits": [_fit(T0, 6.5), _fit(T0 + timedelta(minutes=15), 8.0)],
                "provenance": {T0: provenance},
                "archived_fits": [_fit(T0 - timedelta(days=2), 4.0)],
                "archived_provenance": {},
            },
            "shock": {
                "params": {"lon_deg": -60.0, "lat_deg": -10.0, "height_rsun": 9.0, "kappa": 0.6,
                           "epsilon": 0.1, "tilt_deg": 0.0, "alpha": 1.0, "model": "spheroid"},
                "clicks": {}, "frame_points": [], "click_order": [],
                "fits": [_fit(T0, 9.0, epsilon=0.1, alpha=1.0, model="spheroid")],
                "provenance": {}, "archived_fits": [], "archived_provenance": {},
            },
        },
    }


def test_the_state_round_trips_through_json():
    raw = serialize_gcs_state(_state())
    text = json.dumps(raw, allow_nan=False)  # strict JSON: no NaN, no datetimes
    back = deserialize_gcs_state(json.loads(text))

    assert back["event_range"] == (T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert back["shared_time"] == T0
    assert back["editing"] == "shock"
    assert back["max_frames"] == 40
    assert back["display"]["wireframe_colour"] == (1, 2, 3)
    assert back["display"]["difference_mode"] == "base"
    assert back["display"]["fit_order"] == 2

    panel_a = back["panels"][0]
    assert panel_a["source_key"] == "COR2-B"
    assert panel_a["frame_times"] == [T0, T0 + timedelta(minutes=15)]
    assert panel_a["listing"][0] == T0 - timedelta(minutes=15)
    assert panel_a["jp2_members"] == ["gcs/jp2/A/hv31_20120712T162400.jp2"]
    assert back["panels"][1]["colormap"] == "soholasco2"

    gcs = back["tracks"]["gcs"]
    assert gcs["params"]["height_rsun"] == 8.0
    assert gcs["frame_points"] == [{"panel": "A", "frame_time": T0, "points": [(1.0, 2.0), (3.0, 4.0)]}]
    assert gcs["click_order"][0]["frame_time"] == T0
    assert gcs["clicks"]["A"] == [(1.0, 2.0)]
    assert [row["when"] for row in gcs["fits"]] == [T0, T0 + timedelta(minutes=15)]
    assert gcs["fits"][0]["n_points"] == 7 and gcs["fits"][0]["refined"] is True
    assert math.isnan(gcs["fits"][0]["lon_err_deg"])  # NaN survives as "no error quoted"
    assert gcs["archived_fits"][0]["apex_height_rsun"] == 4.0

    record = gcs["provenance"][T0]
    frame = record["frames"][0]
    assert record["target_time_utc"] == T0
    assert frame["observation_time_utc"] == T0 + timedelta(seconds=20)
    assert frame["difference_reference_utc"] == T0 - timedelta(minutes=12)
    assert frame["observer"]["obstime"] == T0
    assert frame["front_points_arcsec"] == [(100.0, 200.0), (110.0, 190.0)]

    shock = back["tracks"]["shock"]
    assert shock["params"]["model"] == "spheroid"
    assert shock["fits"][0]["model"] == "spheroid"
    assert gcs_fit_count(raw) == 3
    assert gcs_state_has_content(raw)


def test_a_fit_without_time_or_height_is_dropped():
    raw = serialize_gcs_state(_state())
    raw["tracks"]["gcs"]["fits"].append({"when": None, "apex_height_rsun": 5.0})
    raw["tracks"]["gcs"]["fits"].append({"when": T0.isoformat(), "apex_height_rsun": None})
    back = deserialize_gcs_state(raw)
    assert len(back["tracks"]["gcs"]["fits"]) == 2


def test_garbage_and_empty_states_are_tolerated():
    assert deserialize_gcs_state(None) == {}
    assert serialize_gcs_state(None) == {}
    back = deserialize_gcs_state({"event_range": ["bad", None], "tracks": "nope", "panels": [1, {"label": ""}]})
    assert back["event_range"] is None
    assert back["panels"] == []
    assert back["tracks"]["gcs"]["fits"] == []
    assert not gcs_state_has_content({})
    assert gcs_fit_count(None) == 0


def test_frame_times_match_within_a_second_only():
    candidates = [T0 + timedelta(milliseconds=400), T0 + timedelta(minutes=12)]
    assert match_frame_time(T0, candidates) == candidates[0]
    assert match_frame_time(T0 + timedelta(seconds=5), candidates) is None
    assert match_frame_time(None, candidates) is None
