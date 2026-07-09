"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import json
import zipfile
from datetime import datetime

import pytest

from src.Backend.solar_session import (
    SOLAR_SCHEMA_VERSION,
    SOLAR_SESSION_MAGIC,
    SolarSessionError,
    deserialize_picks,
    read_solar_session,
    serialize_picks,
    session_frame_count,
    session_pick_count,
    write_solar_session,
)


def _make_frame_files(tmp_path, count):
    paths = []
    for i in range(count):
        p = tmp_path / f"aia_{i}.fits"
        p.write_bytes(f"FITS-BYTES-{i}".encode("ascii"))
        paths.append(str(p))
    return paths


def _sample_meta():
    picks = {
        1: (datetime(2012, 7, 12, 16, 30, 0), 2.5, 100.0, 200.0, 45.0),
        0: (datetime(2012, 7, 12, 16, 24, 0), 1.8, 80.0, 150.0, 44.0),
    }
    return {
        "source": {"instrument_label": "LASCO C2", "frame_times": [None, None]},
        "view": {"colormap": "soholasco2", "difference_mode": "Running Difference"},
        "measurements": {"height_time_picks": serialize_picks(picks)},
    }


def test_write_read_round_trip(tmp_path):
    frame_paths = _make_frame_files(tmp_path, 3)
    session_path = tmp_path / "event.ecsolar"

    embedded = write_solar_session(str(session_path), meta=_sample_meta(), frame_paths=frame_paths)
    assert embedded == 3

    extract_dir = tmp_path / "restore"
    result = read_solar_session(str(session_path), extract_dir=str(extract_dir))

    assert result.meta["magic"] == SOLAR_SESSION_MAGIC
    assert result.meta["schema_version"] == SOLAR_SCHEMA_VERSION
    assert "created_at" in result.meta
    assert result.meta["view"]["colormap"] == "soholasco2"

    # Frame order and bytes survive the round trip.
    assert len(result.frame_paths) == 3
    for i, path in enumerate(result.frame_paths):
        with open(path, "rb") as fh:
            assert fh.read() == f"FITS-BYTES-{i}".encode("ascii")


def test_frame_order_is_preserved(tmp_path):
    # Names that would sort differently alphabetically vs. load order.
    names = ["zulu.fits", "alpha.fits", "mike.fits"]
    frame_paths = []
    for i, name in enumerate(names):
        p = tmp_path / name
        p.write_bytes(f"payload-{i}".encode("ascii"))
        frame_paths.append(str(p))

    session_path = tmp_path / "order.ecsolar"
    write_solar_session(str(session_path), meta=_sample_meta(), frame_paths=frame_paths)
    result = read_solar_session(str(session_path), extract_dir=str(tmp_path / "out"))

    for i, path in enumerate(result.frame_paths):
        with open(path, "rb") as fh:
            assert fh.read() == f"payload-{i}".encode("ascii")


def test_picks_round_trip_preserves_values_and_order():
    picks = {
        2: (datetime(2012, 7, 12, 16, 36, 0), 3.1, 120.0, 240.0, 46.0),
        0: (datetime(2012, 7, 12, 16, 24, 0), 1.8, 80.0, 150.0, 44.0),
    }
    restored = deserialize_picks(serialize_picks(picks))
    assert set(restored.keys()) == {0, 2}
    assert restored[0][0] == datetime(2012, 7, 12, 16, 24, 0)
    assert restored[0][1] == pytest.approx(1.8)
    assert restored[2][4] == pytest.approx(46.0)


def test_serialize_picks_orders_by_frame_index():
    picks = {
        5: (datetime(2020, 1, 1), 5.0, 1.0, 1.0, 1.0),
        1: (datetime(2020, 1, 1), 1.0, 1.0, 1.0, 1.0),
        3: (datetime(2020, 1, 1), 3.0, 1.0, 1.0, 1.0),
    }
    rows = serialize_picks(picks)
    assert [r["frame_index"] for r in rows] == [1, 3, 5]


def test_deserialize_picks_drops_incomplete_rows():
    rows = [
        {"frame_index": 0, "time": None, "height_rsun": 2.0, "x_arc": 1.0, "y_arc": 1.0, "pa_deg": 10.0},
        {"frame_index": 1, "time": None, "height_rsun": None, "x_arc": 1.0, "y_arc": 1.0, "pa_deg": 10.0},
    ]
    restored = deserialize_picks(rows)
    assert set(restored.keys()) == {0}


def test_deserialize_picks_tolerates_missing_time():
    rows = serialize_picks({0: ("", 2.0, 1.0, 1.0, 10.0)})
    restored = deserialize_picks(rows)
    assert restored[0][0] is None
    assert restored[0][1] == pytest.approx(2.0)


def test_write_rejects_empty_frame_list(tmp_path):
    with pytest.raises(SolarSessionError):
        write_solar_session(str(tmp_path / "empty.ecsolar"), meta={}, frame_paths=[])


def test_write_rejects_missing_frame_file(tmp_path):
    with pytest.raises(SolarSessionError):
        write_solar_session(
            str(tmp_path / "missing.ecsolar"),
            meta={},
            frame_paths=[str(tmp_path / "does_not_exist.fits")],
        )


def test_failed_write_leaves_no_partial_file(tmp_path):
    session_path = tmp_path / "partial.ecsolar"
    good = _make_frame_files(tmp_path, 1)[0]
    with pytest.raises(SolarSessionError):
        write_solar_session(
            str(session_path),
            meta={},
            frame_paths=[good, str(tmp_path / "missing.fits")],
        )
    assert not session_path.exists()
    assert not (tmp_path / "partial.ecsolar.tmp").exists()


def test_read_rejects_bad_magic(tmp_path):
    session_path = tmp_path / "bad.ecsolar"
    with zipfile.ZipFile(session_path, "w") as zf:
        zf.writestr("meta.json", json.dumps({"magic": "nope", "schema_version": 1}).encode("utf-8"))
        zf.writestr("frames/0000_a.fits", b"x")
    with pytest.raises(SolarSessionError):
        read_solar_session(str(session_path), extract_dir=str(tmp_path / "out"))


def test_read_rejects_unsupported_schema(tmp_path):
    session_path = tmp_path / "future.ecsolar"
    meta = {"magic": SOLAR_SESSION_MAGIC, "schema_version": 999, "frame_files": []}
    with zipfile.ZipFile(session_path, "w") as zf:
        zf.writestr("meta.json", json.dumps(meta).encode("utf-8"))
    with pytest.raises(SolarSessionError):
        read_solar_session(str(session_path), extract_dir=str(tmp_path / "out"))


def test_read_rejects_zip_slip(tmp_path):
    session_path = tmp_path / "evil.ecsolar"
    meta = {
        "magic": SOLAR_SESSION_MAGIC,
        "schema_version": SOLAR_SCHEMA_VERSION,
        "frame_files": [{"arcname": "../escape.fits", "name": "escape.fits"}],
    }
    with zipfile.ZipFile(session_path, "w") as zf:
        zf.writestr("meta.json", json.dumps(meta).encode("utf-8"))
        zf.writestr("../escape.fits", b"x")
    with pytest.raises(SolarSessionError):
        read_solar_session(str(session_path), extract_dir=str(tmp_path / "out"))


def test_read_rejects_missing_embedded_frame(tmp_path):
    session_path = tmp_path / "incomplete.ecsolar"
    meta = {
        "magic": SOLAR_SESSION_MAGIC,
        "schema_version": SOLAR_SCHEMA_VERSION,
        "frame_files": [{"arcname": "frames/0000_a.fits", "name": "a.fits"}],
    }
    with zipfile.ZipFile(session_path, "w") as zf:
        zf.writestr("meta.json", json.dumps(meta).encode("utf-8"))
        # note: the frame listed in the manifest is not actually stored
    with pytest.raises(SolarSessionError):
        read_solar_session(str(session_path), extract_dir=str(tmp_path / "out"))


def test_summary_helpers(tmp_path):
    frame_paths = _make_frame_files(tmp_path, 2)
    session_path = tmp_path / "sum.ecsolar"
    write_solar_session(str(session_path), meta=_sample_meta(), frame_paths=frame_paths)
    result = read_solar_session(str(session_path), extract_dir=str(tmp_path / "out"))
    assert session_frame_count(result.meta) == 2
    assert session_pick_count(result.meta) == 2
    assert session_frame_count(None) == 0
    assert session_pick_count({}) == 0
