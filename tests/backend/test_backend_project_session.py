"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


import io
import json
import os
import zipfile

import numpy as np
import pytest

from src.backend.session.project_session import (
    MAGIC,
    SCHEMA_VERSION,
    ProjectFormatError,
    ProjectSaveCancelled,
    estimate_files_size,
    is_safe_arcname,
    project_parts,
    project_storage,
    read_project,
    read_project_meta,
    write_project,
)


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
    assert payload.meta["schema_version"] == SCHEMA_VERSION == 2
    assert payload.meta["foo"] == "bar"
    assert "created_at" in payload.meta

    assert np.array_equal(payload.arrays["raw_data"], arrays_in["raw_data"])
    assert np.array_equal(payload.arrays["freqs"], arrays_in["freqs"])
    assert np.array_equal(payload.arrays["time"], arrays_in["time"])
    assert np.array_equal(payload.arrays["mask"], arrays_in["mask"])
    assert payload.files == {}


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


def _write_v1(path, meta, arrays):
    """A schema-1 project exactly as v3.1 wrote it."""
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    payload = {"magic": MAGIC, "schema_version": 1, "created_at": "2026-01-01T00:00:00+00:00", **meta}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(payload).encode("utf-8"))
        zf.writestr("arrays.npz", buf.getvalue())


def test_a_version_1_project_still_opens_as_radio_only(tmp_path):
    path = tmp_path / "v31.efaproj"
    _write_v1(path, {"filename": "BIR_20240510_120000_01.fit"}, {"raw_data": np.ones((2, 3))})

    payload = read_project(str(path))
    assert payload.meta["schema_version"] == 1
    assert payload.meta["filename"] == "BIR_20240510_120000_01.fit"
    assert np.array_equal(payload.arrays["raw_data"], np.ones((2, 3)))
    parts = project_parts(payload.meta)
    assert parts["radio"] is True
    assert not any(value for key, value in parts.items() if key != "radio")
    assert project_storage(payload.meta) == "link"


def test_a_future_schema_is_refused(tmp_path):
    path = tmp_path / "future.efaproj"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("meta.json", json.dumps({"magic": MAGIC, "schema_version": 3}))
    with pytest.raises(ProjectFormatError, match="Unsupported project schema_version=3"):
        read_project(str(path))


def test_v2_round_trip_with_embedded_files(tmp_path):
    fits_src = tmp_path / "aia_193.fits"
    fits_src.write_bytes(b"SIMPLE  =                    T" + bytes(range(256)) * 40)
    jp2_src = tmp_path / "hv4_20240510T120000.jp2"
    jp2_src.write_bytes(b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"x" * 5000)
    path = tmp_path / "event.efaproj"

    files = {
        "solar/frames/0000_aia_193.fits": str(fits_src),
        "gcs/jp2/B/hv4_20240510T120000.jp2": str(jp2_src),
    }
    meta = {"parts": {"radio": False, "solar": True, "gcs": True}, "storage": "embed"}
    write_project(str(path), meta=meta, files=files)

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "arrays.npz" not in names  # optional when there are no arrays
        assert zf.getinfo("solar/frames/0000_aia_193.fits").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("gcs/jp2/B/hv4_20240510T120000.jp2").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo("meta.json").compress_type == zipfile.ZIP_DEFLATED

    extract = tmp_path / "extract"
    payload = read_project(str(path), extract_dir=str(extract))
    assert set(payload.files) == set(files)
    for arcname, src in files.items():
        with open(payload.files[arcname], "rb") as got, open(src, "rb") as want:
            assert got.read() == want.read()
        assert os.path.commonpath([str(extract.resolve()), payload.files[arcname]]) == str(extract.resolve())
    assert payload.meta["files"] == sorted(files)
    assert project_parts(payload.meta) == {
        "radio": False, "solar": True, "gcs": True,
        "geomagnetic": False, "event_context": False, "composer": False,
    }
    assert project_storage(payload.meta) == "embed"
    assert read_project_meta(str(path))["storage"] == "embed"


def test_extract_dir_may_depend_on_the_meta(tmp_path):
    src = tmp_path / "frame.fits"
    src.write_bytes(b"data")
    path = tmp_path / "p.efaproj"
    write_project(str(path), meta={"project_id": "abc123"}, files={"solar/frames/0000_frame.fits": str(src)})

    seen = {}

    def where(meta):
        seen["id"] = meta["project_id"]
        return tmp_path / "projects" / meta["project_id"]

    payload = read_project(str(path), extract_dir=where)
    assert seen["id"] == "abc123"
    assert payload.files["solar/frames/0000_frame.fits"].endswith(os.path.join("abc123", "solar", "frames", "0000_frame.fits"))


def test_a_linked_project_never_touches_the_extract_dir(tmp_path):
    # A linked project may reference files an earlier open unpacked there.
    path = tmp_path / "linked.efaproj"
    write_project(str(path), meta={"project_id": "abc123", "storage": "link"})

    def where(meta):
        raise AssertionError("a project with no embedded files asked for an extract folder")

    payload = read_project(str(path), extract_dir=where)
    assert payload.files == {}


@pytest.mark.parametrize(
    "name",
    ["../evil.fits", "/abs/evil.fits", "C:/evil.fits", "solar\\..\\evil", "solar//x", "./x", "meta.json", ""],
)
def test_unsafe_member_names_are_rejected(name):
    assert not is_safe_arcname(name)


def test_an_archive_with_an_unsafe_member_is_refused_before_extracting(tmp_path):
    path = tmp_path / "zipslip.efaproj"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("meta.json", json.dumps({"magic": MAGIC, "schema_version": 2}))
        zf.writestr("solar/frames/0000_ok.fits", b"ok")
        zf.writestr("../../escaped.txt", b"pwned")
    extract = tmp_path / "out"
    with pytest.raises(ProjectFormatError, match="unsafe"):
        read_project(str(path), extract_dir=str(extract))
    assert not (tmp_path / "escaped.txt").exists()
    assert not extract.exists() or not any(extract.rglob("*.fits"))


def test_write_refuses_an_unsafe_member_name(tmp_path):
    src = tmp_path / "x.fits"
    src.write_bytes(b"x")
    with pytest.raises(ProjectFormatError):
        write_project(str(tmp_path / "p.efaproj"), meta={}, files={"../x.fits": str(src)})
    assert not (tmp_path / "p.efaproj").exists()


def test_a_failed_write_leaves_no_partial_file_and_keeps_the_old_one(tmp_path):
    path = tmp_path / "keep.efaproj"
    write_project(str(path), meta={"version": "old"}, arrays={"a": np.arange(3)})
    before = path.read_bytes()

    with pytest.raises(ProjectFormatError):
        write_project(
            str(path),
            meta={"version": "new"},
            files={"solar/frames/0000_missing.fits": str(tmp_path / "does_not_exist.fits")},
        )
    assert path.read_bytes() == before
    assert not (tmp_path / "keep.efaproj.tmp").exists()
    assert read_project(str(path)).meta["version"] == "old"


def test_a_cancelled_write_leaves_no_file(tmp_path):
    src = tmp_path / "big.fits"
    src.write_bytes(b"0" * (3 * 1024 * 1024))
    path = tmp_path / "cancel.efaproj"
    progress = []
    with pytest.raises(ProjectSaveCancelled):
        write_project(
            str(path),
            meta={},
            files={"solar/frames/0000_big.fits": str(src)},
            progress_cb=lambda done, total, text: progress.append((done, total)),
            cancel_cb=lambda: bool(progress),
        )
    assert not path.exists()
    assert not (tmp_path / "cancel.efaproj.tmp").exists()


def test_progress_reaches_the_total(tmp_path):
    src = tmp_path / "f.fits"
    src.write_bytes(b"1" * 4096)
    progress = []
    write_project(
        str(tmp_path / "p.efaproj"),
        meta={},
        files={"solar/frames/0000_f.fits": str(src)},
        progress_cb=lambda done, total, text: progress.append((done, total)),
    )
    assert progress[-1][0] == progress[-1][1]
    assert estimate_files_size({"a": str(src), "b": str(tmp_path / "missing")}) == 4096
