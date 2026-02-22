"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from src.Backend.batch_processing import (
    build_unique_output_png_path,
    convert_digits_to_db,
    list_fit_files,
    save_background_subtracted_png,
    subtract_background,
    subtract_mean_background,
)


def test_list_fit_files_top_level_only(tmp_path: Path):
    (tmp_path / "a.fit").write_bytes(b"")
    (tmp_path / "b.fit.gz").write_bytes(b"")
    (tmp_path / "c.fits").write_bytes(b"")
    (tmp_path / "d.fits.gz").write_bytes(b"")
    (tmp_path / "ignore.txt").write_bytes(b"")

    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "inside.fit").write_bytes(b"")

    files = list_fit_files(str(tmp_path))
    names = [Path(p).name for p in files]

    assert names == ["a.fit", "b.fit.gz", "c.fits", "d.fits.gz"]
    assert "inside.fit" not in names


def test_subtract_mean_background_returns_float32():
    data = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 8.0, 12.0],
        ],
        dtype=np.float64,
    )
    result = subtract_mean_background(data)
    expected = np.array(
        [
            [-1.0, 0.0, 1.0],
            [-4.0, 0.0, 4.0],
        ],
        dtype=np.float32,
    )

    assert result.dtype == np.float32
    assert np.allclose(result, expected)


def test_subtract_background_median_per_row():
    data = np.array(
        [
            [1.0, 2.0, 100.0],
            [5.0, 7.0, 9.0],
        ],
        dtype=np.float32,
    )
    result = subtract_background(data, method="median")
    expected = np.array(
        [
            [-1.0, 0.0, 98.0],
            [-2.0, 0.0, 2.0],
        ],
        dtype=np.float32,
    )
    assert np.allclose(result, expected)


def test_convert_digits_to_db_uses_cold_baseline():
    data = np.array([[10.0, 20.0]], dtype=np.float32)
    out = convert_digits_to_db(data, cold_digits=5.0)
    scale = 2500.0 / 256.0 / 25.4
    expected = (data - 5.0) * scale
    assert np.allclose(out, expected)


def test_build_unique_output_png_path_avoids_overwrite(tmp_path: Path):
    (tmp_path / "demo.png").write_bytes(b"x")
    (tmp_path / "demo_1.png").write_bytes(b"x")

    out_path = build_unique_output_png_path(str(tmp_path), "demo.fit.gz")
    assert out_path.endswith("demo_2.png")


def test_save_background_subtracted_png_writes_file(tmp_path: Path):
    data = np.array(
        [
            [1.0, 2.0, 3.0, 4.0],
            [2.0, 3.0, 4.0, 5.0],
            [3.0, 4.0, 5.0, 6.0],
        ],
        dtype=np.float32,
    )
    freqs = np.array([90.0, 80.0, 70.0], dtype=float)
    time = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
    out = tmp_path / "result.png"

    save_background_subtracted_png(
        data=data,
        freqs=freqs,
        time=time,
        output_path=str(out),
        title="demo-Background Subtracted",
        cmap_name="Custom",
        ut_start_sec=3600.0,
        cold_digits=1.5,
    )

    assert out.exists()
    assert out.stat().st_size > 0


def test_save_background_subtracted_png_ut_fallback_when_missing_start(tmp_path: Path):
    data = np.array([[1.0, 2.0], [2.0, 3.0]], dtype=np.float32)
    freqs = np.array([90.0, 80.0], dtype=float)
    time = np.array([0.0, 1.0], dtype=float)
    out = tmp_path / "result_ut_fallback.png"
    save_background_subtracted_png(
        data=data,
        freqs=freqs,
        time=time,
        output_path=str(out),
        title="ut-fallback",
        cmap_name="inferno",
        ut_start_sec=None,
        cold_digits=0.0,
    )
    assert out.exists()
    assert out.stat().st_size > 0
