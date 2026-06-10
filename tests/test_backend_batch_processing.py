"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from src.Backend.batch_processing import (
    MEDIAN_DB_SCALE,
    MEDIAN_DB_DISPLAY_LIMITS,
    background_method_label,
    build_unique_output_png_path,
    convert_digits_to_db,
    list_fit_files,
    locked_view_overlaps_data,
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


def test_subtract_background_preserves_robust_baseline_support():
    data = np.array([[0.0, 4.0, 8.0, 100.0]], dtype=np.float32)

    result = subtract_background(data, method="robust")

    assert np.allclose(result, data - np.percentile(data, 25.0, axis=1, keepdims=True))


def test_subtract_background_median_db_matches_reference_method():
    data = np.array(
        [
            [10.0, 12.0, 30.0],
            [20.0, 25.0, 35.0],
        ],
        dtype=np.float32,
    )

    dref = data - np.min(data)
    db = (dref / 255.0 * 2500.0) / 25.4
    expected = db - np.median(db, axis=1, keepdims=True)

    result = subtract_background(data, method="median_db")

    assert result.dtype == np.float32
    assert MEDIAN_DB_SCALE == pytest.approx(2500.0 / 255.0 / 25.4)
    assert np.allclose(result, expected)


def test_legacy_background_method_alias_uses_median_db_label():
    assert background_method_label("plotutil_median_db") == "median_dB"


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


def test_save_background_subtracted_png_does_not_reconvert_db_input(monkeypatch, tmp_path: Path):
    data = np.array([[-1.0, 1.0], [-2.0, 2.0]], dtype=np.float32)
    out = tmp_path / "preconverted_db.png"
    captured = {}

    def fake_savefig(fig, output_path, *args, **kwargs):
        captured["display_data"] = np.asarray(fig.axes[0].images[0].get_array())
        Path(output_path).write_bytes(b"png")

    monkeypatch.setattr("matplotlib.figure.Figure.savefig", fake_savefig)

    save_background_subtracted_png(
        data=data,
        freqs=np.array([90.0, 80.0], dtype=float),
        time=np.array([0.0, 1.0], dtype=float),
        output_path=str(out),
        title="preconverted-db",
        cmap_name="magma",
        cold_digits=100.0,
        db_scale=MEDIAN_DB_SCALE,
        data_units="db",
    )

    assert np.allclose(captured["display_data"], data)


def test_save_background_subtracted_png_applies_default_display_limits(monkeypatch, tmp_path: Path):
    out = tmp_path / "default_limits.png"
    captured = {}

    def fake_savefig(fig, output_path, *args, **kwargs):
        captured["clim"] = fig.axes[0].images[0].get_clim()
        Path(output_path).write_bytes(b"png")

    monkeypatch.setattr("matplotlib.figure.Figure.savefig", fake_savefig)

    save_background_subtracted_png(
        data=np.array([[-50.0, 0.0], [8.0, 50.0]], dtype=np.float32),
        freqs=np.array([90.0, 80.0], dtype=float),
        time=np.array([0.0, 1.0], dtype=float),
        output_path=str(out),
        title="default-limits",
        cmap_name="magma",
        data_units="db",
        default_display_limits=MEDIAN_DB_DISPLAY_LIMITS,
    )

    assert captured["clim"] == pytest.approx(MEDIAN_DB_DISPLAY_LIMITS)


def test_save_background_subtracted_png_converts_default_db_limits_for_digit_display(monkeypatch, tmp_path: Path):
    out = tmp_path / "default_limits_digits.png"
    captured = {}

    def fake_savefig(fig, output_path, *args, **kwargs):
        captured["clim"] = fig.axes[0].images[0].get_clim()
        Path(output_path).write_bytes(b"png")

    monkeypatch.setattr("matplotlib.figure.Figure.savefig", fake_savefig)

    save_background_subtracted_png(
        data=np.array([[-1.0, 0.0], [8.0, 16.0]], dtype=np.float32),
        freqs=np.array([90.0, 80.0], dtype=float),
        time=np.array([0.0, 1.0], dtype=float),
        output_path=str(out),
        title="default-limits-digits",
        cmap_name="magma",
        db_scale=MEDIAN_DB_SCALE,
        data_units="db",
        default_display_limits=MEDIAN_DB_DISPLAY_LIMITS,
        view_config={"visual": {"use_db": False}},
    )

    expected = tuple(value / MEDIAN_DB_SCALE for value in MEDIAN_DB_DISPLAY_LIMITS)
    assert captured["clim"] == pytest.approx(expected)


def test_save_background_subtracted_png_applies_locked_axes(monkeypatch, tmp_path: Path):
    data = np.array([[1.0, 2.0], [2.0, 3.0]], dtype=np.float32)
    freqs = np.array([90.0, 80.0], dtype=float)
    time = np.array([0.0, 1.0], dtype=float)
    out = tmp_path / "locked.png"
    captured = {}

    def fake_savefig(fig, output_path, *args, **kwargs):
        ax = fig.axes[0]
        captured["xlim"] = ax.get_xlim()
        captured["ylim"] = ax.get_ylim()
        captured["xlabel"] = ax.get_xlabel()
        captured["ylabel"] = ax.get_ylabel()
        Path(output_path).write_bytes(b"png")

    monkeypatch.setattr("matplotlib.figure.Figure.savefig", fake_savefig)

    save_background_subtracted_png(
        data=data,
        freqs=freqs,
        time=time,
        output_path=str(out),
        title="locked",
        cmap_name="inferno",
        ut_start_sec=0.0,
        cold_digits=0.0,
        view_config={
            "range": {"time_start_s": 10.0, "time_stop_s": 20.0, "freq_min_mhz": 40.0, "freq_max_mhz": 140.0},
            "visual": {"use_db": False, "use_utc": False, "cmap": "inferno"},
        },
    )

    assert captured["xlim"] == pytest.approx((10.0, 20.0))
    assert captured["ylim"] == pytest.approx((40.0, 140.0))
    assert captured["xlabel"] == "Time [s]"
    assert captured["ylabel"] == "Frequency [MHz]"
    assert out.exists()


def test_locked_view_overlap_detection():
    freqs = np.array([90.0, 80.0], dtype=float)
    time = np.array([0.0, 1.0], dtype=float)

    assert locked_view_overlaps_data(
        freqs,
        time,
        {"range": {"time_start_s": 0.2, "time_stop_s": 0.8, "freq_min_mhz": 78.0, "freq_max_mhz": 92.0}},
    ) is True
    assert locked_view_overlaps_data(
        freqs,
        time,
        {"range": {"time_start_s": 10.0, "time_stop_s": 20.0, "freq_min_mhz": 78.0, "freq_max_mhz": 92.0}},
    ) is False
