"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from astropy.io import fits

from src.Backend.multi_station_comparison import (
    COLOR_SCALE_MANUAL,
    COLOR_SCALE_PER_STATION,
    COLOR_SCALE_SHARED,
    TIME_ALIGNMENT_UT,
    aligned_time_axes,
    compute_color_limits,
    export_comparison_png,
    load_comparison_dataset,
    render_comparison_figure,
)


def _write_fit(path: Path, *, label: str, time_obs: str | None = "12:00:00", base: float = 0.0) -> None:
    data = (np.arange(12, dtype=np.float32).reshape(3, 4) + float(base)).astype(np.float32)
    hdu = fits.PrimaryHDU(data=data)
    hdr = hdu.header
    hdr["CRVAL1"] = 0.0
    hdr["CDELT1"] = 1.0
    hdr["CRPIX1"] = 1.0
    hdr["CRVAL2"] = 100.0
    hdr["CDELT2"] = -5.0
    hdr["CRPIX2"] = 1.0
    hdr["INSTRUME"] = label
    if time_obs is not None:
        hdr["TIME-OBS"] = time_obs
    hdu.writeto(path, overwrite=True)


def test_load_multiple_comparison_datasets(tmp_path: Path):
    a = tmp_path / "A_20260101_120000.fit"
    b = tmp_path / "B_20260101_120100.fit"
    _write_fit(a, label="Station A", time_obs="12:00:00")
    _write_fit(b, label="Station B", time_obs="12:01:00")

    datasets = [load_comparison_dataset(str(a)), load_comparison_dataset(str(b))]

    assert [item.label for item in datasets] == ["Station A", "Station B"]
    assert datasets[0].data.shape == (3, 4)
    assert datasets[0].ut_start_sec == pytest.approx(12 * 3600)
    assert datasets[1].ut_start_sec == pytest.approx(12 * 3600 + 60)


def test_ut_alignment_maps_different_time_obs_to_shared_clock(tmp_path: Path):
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A", time_obs="12:00:00")
    _write_fit(b, label="B", time_obs="12:01:00")
    datasets = [load_comparison_dataset(str(a)), load_comparison_dataset(str(b))]

    axes, mode, warnings = aligned_time_axes(datasets, TIME_ALIGNMENT_UT)

    assert mode == TIME_ALIGNMENT_UT
    assert warnings == ()
    assert axes[0][0] == pytest.approx(12 * 3600)
    assert axes[1][0] == pytest.approx(12 * 3600 + 60)


def test_midnight_crossing_ut_alignment_unwraps_next_day(tmp_path: Path):
    before = tmp_path / "before.fit"
    after = tmp_path / "after.fit"
    _write_fit(before, label="Before", time_obs="23:59:00")
    _write_fit(after, label="After", time_obs="00:01:00")
    datasets = [load_comparison_dataset(str(before)), load_comparison_dataset(str(after))]

    axes, mode, _warnings = aligned_time_axes(datasets, TIME_ALIGNMENT_UT)

    assert mode == TIME_ALIGNMENT_UT
    assert axes[0][0] == pytest.approx(23 * 3600 + 59 * 60)
    assert axes[1][0] == pytest.approx(24 * 3600 + 60)
    assert axes[1][0] > axes[0][-1]


def test_color_scale_modes_compute_expected_limits(tmp_path: Path):
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A", base=0.0)
    _write_fit(b, label="B", base=100.0)
    datasets = [load_comparison_dataset(str(a)), load_comparison_dataset(str(b))]

    shared = compute_color_limits(datasets, {"use_db": False}, COLOR_SCALE_SHARED)
    per_station = compute_color_limits(datasets, {"use_db": False}, COLOR_SCALE_PER_STATION)
    manual = compute_color_limits(datasets, {"use_db": False}, COLOR_SCALE_MANUAL, manual_limits=(-2.0, 8.0))

    assert np.asarray(shared) == pytest.approx(np.asarray(((0.0, 111.0), (0.0, 111.0))))
    assert np.asarray(per_station) == pytest.approx(np.asarray(((0.0, 11.0), (100.0, 111.0))))
    assert np.asarray(manual) == pytest.approx(np.asarray(((-2.0, 8.0), (-2.0, 8.0))))


def test_render_and_export_comparison_png_applies_exact_locked_axes(monkeypatch, tmp_path: Path):
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    out = tmp_path / "comparison.png"
    _write_fit(a, label="A", time_obs="12:00:00")
    _write_fit(b, label="B", time_obs="12:01:00")
    datasets = [load_comparison_dataset(str(a)), load_comparison_dataset(str(b))]
    display_range = {
        "time_start_s": 100.0,
        "time_stop_s": 200.0,
        "freq_min_mhz": 80.0,
        "freq_max_mhz": 140.0,
    }
    captured = {}

    def fake_savefig(fig, path, *args, **kwargs):
        captured["xlim"] = fig.axes[0].get_xlim()
        captured["ylim"] = fig.axes[0].get_ylim()
        Path(path).write_bytes(b"png")

    monkeypatch.setattr("matplotlib.figure.Figure.savefig", fake_savefig)

    result = export_comparison_png(
        datasets,
        str(out),
        alignment_mode="seconds",
        display_range=display_range,
        visual={"use_db": False, "use_utc": False, "cmap": "viridis"},
    )

    assert result.xlim == pytest.approx((100.0, 200.0))
    assert result.ylim == pytest.approx((80.0, 140.0))
    assert captured["xlim"] == pytest.approx((100.0, 200.0))
    assert captured["ylim"] == pytest.approx((80.0, 140.0))
    assert out.exists()


def test_render_warns_when_locked_range_is_outside_file(tmp_path: Path):
    a = tmp_path / "a.fit"
    _write_fit(a, label="A", time_obs="12:00:00")
    dataset = load_comparison_dataset(str(a))

    result = render_comparison_figure(
        [dataset],
        alignment_mode="seconds",
        display_range={"time_start_s": 100.0, "time_stop_s": 200.0, "freq_min_mhz": 80.0, "freq_max_mhz": 140.0},
        visual={"use_db": False, "use_utc": False},
    )

    assert any("no data inside" in warning for warning in result.warnings)
