from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from src.Backend.batch_processing import (
    build_unique_output_png_path,
    list_fit_files,
    save_background_subtracted_png,
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
    )

    assert out.exists()
    assert out.stat().st_size > 0
