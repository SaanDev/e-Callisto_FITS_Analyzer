"""
e-CALLISTO FITS Analyzer
Version 2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import io
import json
import zipfile

import numpy as np
import pytest

from src.Backend.project_session import MAGIC, ProjectFormatError, read_project, write_project


def test_write_read_project_round_trip(tmp_path):
    path = tmp_path / "roundtrip.efaproj"

    meta_in = {
        "foo": "bar",
        "view": {"xlim": (0.0, 1.0), "ylim": (1.0, 0.0)},
    }
    arrays_in = {
        "raw_data": np.arange(6, dtype=float).reshape(2, 3),
        "freqs": np.array([100.0, 200.0], dtype=float),
        "time": np.array([0.0, 1.0, 2.0], dtype=float),
        "mask": np.array([[True, False, True], [False, False, True]]),
    }

    write_project(str(path), meta=meta_in, arrays=arrays_in)
    payload = read_project(str(path))

    assert payload.meta["magic"] == MAGIC
    assert payload.meta["foo"] == "bar"
    assert "created_at" in payload.meta

    assert np.array_equal(payload.arrays["raw_data"], arrays_in["raw_data"])
    assert np.array_equal(payload.arrays["freqs"], arrays_in["freqs"])
    assert np.array_equal(payload.arrays["time"], arrays_in["time"])
    assert np.array_equal(payload.arrays["mask"], arrays_in["mask"])


def test_read_project_rejects_magic(tmp_path):
    path = tmp_path / "bad_magic.efaproj"

    meta = {"magic": "not-a-project", "schema_version": 1}
    arrays_buf = io.BytesIO()
    np.savez_compressed(arrays_buf, a=np.array([1, 2, 3], dtype=int))

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta).encode("utf-8"))
        zf.writestr("arrays.npz", arrays_buf.getvalue())

    with pytest.raises(ProjectFormatError):
        read_project(str(path))
