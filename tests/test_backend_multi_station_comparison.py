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

pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from astropy.io import fits

from src.Backend.multi_station_comparison import (
    COLOR_SCALE_MANUAL,
    COLOR_SCALE_PER_STATION,
    COLOR_SCALE_SHARED,
    NOISE_METHOD_CLIP,
    NOISE_METHOD_MEAN,
    NOISE_METHOD_MEDIAN,
    NOISE_METHOD_ROBUST,
    TIME_ALIGNMENT_UT,
    ComparisonNoiseSettings,
    aligned_time_axes,
    apply_comparison_noise,
    comparison_grid_dimensions,
    comparison_panel_payloads,
    compute_color_limits,
    combined_comparison_dataset_from_paths,
    combined_comparison_datasets_from_paths,
    export_comparison_grid,
    export_comparison_png,
    load_comparison_dataset,
    render_comparison_grid_figure,
    render_comparison_figure,
)


def _write_fit(
    path: Path,
    *,
    label: str,
    time_obs: str | None = "12:00:00",
    base: float = 0.0,
    freq_start: float = 100.0,
) -> None:
    data = (np.arange(12, dtype=np.float32).reshape(3, 4) + float(base)).astype(np.float32)
    hdu = fits.PrimaryHDU(data=data)
    hdr = hdu.header
    hdr["CRVAL1"] = 0.0
    hdr["CDELT1"] = 1.0
    hdr["CRPIX1"] = 1.0
    hdr["CRVAL2"] = float(freq_start)
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

    assert [item.label for item in datasets] == ["Station A - 2026-01-01", "Station B - 2026-01-01"]
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


def test_comparison_noise_methods_transform_data_without_mutating_source():
    data = np.array([[1.0, 2.0, 7.0], [4.0, 8.0, 12.0]], dtype=np.float32)
    original = data.copy()

    mean = apply_comparison_noise(data, ComparisonNoiseSettings(method=NOISE_METHOD_MEAN))
    median = apply_comparison_noise(data, ComparisonNoiseSettings(method=NOISE_METHOD_MEDIAN))
    robust = apply_comparison_noise(data, ComparisonNoiseSettings(method=NOISE_METHOD_ROBUST))
    clipped = apply_comparison_noise(data, ComparisonNoiseSettings(method=NOISE_METHOD_CLIP, clip_low=-1.0, clip_high=2.0))

    assert mean == pytest.approx(data - np.nanmean(data, axis=1, keepdims=True))
    assert median == pytest.approx(data - np.nanmedian(data, axis=1, keepdims=True))
    assert robust == pytest.approx(data - np.nanpercentile(data, 25.0, axis=1, keepdims=True))
    assert clipped == pytest.approx(np.clip(robust, -1.0, 2.0))
    assert np.array_equal(data, original)


def test_comparison_noise_defaults_and_normalization_start_at_zero():
    data = np.array([[1.0, 2.0, 7.0]], dtype=np.float32)
    default = ComparisonNoiseSettings()
    clipped = apply_comparison_noise(data, {"method": NOISE_METHOD_CLIP})

    assert default.clip_low == pytest.approx(0.0)
    assert default.clip_high == pytest.approx(0.0)
    assert clipped == pytest.approx(np.zeros_like(data))


def test_comparison_noise_preserves_gap_rows_as_nan():
    data = np.array([[1.0, 2.0, 3.0], [10.0, 11.0, 12.0]], dtype=np.float32)

    result = apply_comparison_noise(
        data,
        ComparisonNoiseSettings(method=NOISE_METHOD_MEDIAN),
        gap_row_mask=np.array([False, True]),
    )

    assert result[0] == pytest.approx([-1.0, 0.0, 1.0])
    assert np.all(np.isnan(result[1]))


def test_color_limits_use_noise_processed_data(tmp_path: Path):
    a = tmp_path / "a.fit"
    b = tmp_path / "b.fit"
    _write_fit(a, label="A", base=0.0)
    _write_fit(b, label="B", base=100.0)
    datasets = [load_comparison_dataset(str(a)), load_comparison_dataset(str(b))]

    limits = compute_color_limits(
        datasets,
        {"use_db": False},
        COLOR_SCALE_SHARED,
        noise_settings=[ComparisonNoiseSettings(method=NOISE_METHOD_MEAN), ComparisonNoiseSettings(method=NOISE_METHOD_MEAN)],
    )

    assert np.asarray(limits) == pytest.approx(np.asarray(((-1.5, 1.5), (-1.5, 1.5))))


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


@pytest.mark.parametrize(
    ("panel_count", "columns", "expected"),
    [
        (1, None, (1, 1)),
        (2, None, (1, 2)),
        (4, None, (2, 3)),
        (9, None, (3, 3)),
        (10, None, (3, 4)),
        (5, 2, (3, 2)),
    ],
)
def test_comparison_grid_dimensions(panel_count: int, columns: int | None, expected: tuple[int, int]):
    assert comparison_grid_dimensions(panel_count, columns) == expected


def test_grid_render_preserves_panel_order_shared_time_and_native_frequency_ranges(tmp_path: Path):
    datasets = []
    for index in range(5):
        path = tmp_path / f"S{index}_20260101_120000.fit"
        _write_fit(
            path,
            label=f"Station {index}",
            base=float(index * 20),
            freq_start=100.0 + float(index * 20),
        )
        datasets.append(load_comparison_dataset(str(path)))
    payloads, mode, _warnings = comparison_panel_payloads(
        datasets,
        alignment_mode="seconds",
        visual={"use_db": False, "cmap": "jet"},
        color_scale_mode=COLOR_SCALE_SHARED,
    )
    display_range = {
        "time_start_s": 20.0,
        "time_stop_s": 80.0,
        "freq_min_mhz": 45.0,
        "freq_max_mhz": 150.0,
    }

    result = render_comparison_grid_figure(
        payloads,
        alignment_mode=mode,
        display_range=display_range,
        visual={"use_db": False, "cmap": "jet"},
        color_scale_mode=COLOR_SCALE_SHARED,
        columns=3,
        title="Grid Export",
    )

    assert [payload.dataset.label for payload in result.panel_payloads] == [dataset.label for dataset in datasets]
    assert len(result.axes) == 5
    assert all(ax.get_xlim() == pytest.approx((20.0, 80.0)) for ax in result.axes)
    for ax, payload in zip(result.axes, payloads):
        assert ax.get_ylim() == pytest.approx(tuple(sorted(payload.mpl_extent[2:4])))
    assert len({tuple(round(value, 3) for value in ax.get_ylim()) for ax in result.axes}) == 5
    assert result.ylim is None
    assert result.axes[2].get_xlabel() == "Time [s]"
    assert result.axes[3].get_xlabel() == "Time [s]"
    assert result.axes[4].get_xlabel() == "Time [s]"
    assert result.axes[0].get_title() == datasets[0].label
    assert len(result.axes[0].patches) == 0
    assert result.figure.axes[5].get_visible() is False
    assert len(result.figure.axes) == 7  # Six grid cells plus one shared colorbar.


def test_grid_ignores_locked_frequency_range_and_warns_only_for_time_miss(tmp_path: Path):
    path = tmp_path / "station.fit"
    _write_fit(path, label="Station", freq_start=100.0)
    dataset = load_comparison_dataset(str(path))
    payloads, mode, _warnings = comparison_panel_payloads([dataset], alignment_mode="seconds", visual={"use_db": False})

    result = render_comparison_grid_figure(
        payloads,
        alignment_mode=mode,
        display_range={
            "time_start_s": 0.0,
            "time_stop_s": 2.0,
            "freq_min_mhz": 500.0,
            "freq_max_mhz": 600.0,
        },
        visual={"use_db": False},
    )

    assert result.axes[0].get_xlim() == pytest.approx((0.0, 2.0))
    assert result.axes[0].get_ylim() == pytest.approx(tuple(sorted(payloads[0].mpl_extent[2:4])))
    assert not any("no data inside" in warning for warning in result.warnings)


@pytest.mark.parametrize("output_format", ["png", "pdf", "eps", "svg", "tiff"])
def test_grid_export_supports_main_output_formats(monkeypatch, tmp_path: Path, output_format: str):
    path = tmp_path / "station.fit"
    output = tmp_path / f"comparison.{output_format}"
    _write_fit(path, label="Station")
    payloads, mode, _warnings = comparison_panel_payloads(
        [load_comparison_dataset(str(path))],
        alignment_mode="seconds",
        visual={"use_db": False},
    )
    captured = {}

    def fake_savefig(_figure, file_path, *args, **kwargs):
        captured["format"] = kwargs.get("format")
        Path(file_path).write_bytes(b"export")

    monkeypatch.setattr("matplotlib.figure.Figure.savefig", fake_savefig)

    export_comparison_grid(
        payloads,
        str(output),
        alignment_mode=mode,
        visual={"use_db": False},
        output_format=output_format,
    )

    assert captured["format"] == output_format
    assert output.exists()


def test_combined_comparison_dataset_from_time_combinable_paths(tmp_path: Path):
    a = tmp_path / "STAT_20260101_120000_A.fit"
    b = tmp_path / "STAT_20260101_121500_A.fit"
    _write_fit(a, label="STAT", time_obs="12:00:00", base=1.0)
    _write_fit(b, label="STAT", time_obs="12:15:00", base=20.0)

    combined = combined_comparison_dataset_from_paths([str(a), str(b)])

    assert combined is not None
    assert combined.combine_type == "time"
    assert combined.label == "STAT - 2026-01-01"
    assert combined.data.shape == (3, 8)
    assert combined.time[0] == pytest.approx(0.0)
    assert combined.time[-1] == pytest.approx(7.0)
    assert combined.sources == (str(a), str(b))


def test_grouped_time_combinable_paths_return_one_combined_dataset_per_station(tmp_path: Path):
    sta_a = tmp_path / "STA_20260101_120000_A.fit"
    sta_b = tmp_path / "STA_20260101_121500_A.fit"
    stb_a = tmp_path / "STB_20260101_120000_A.fit"
    stb_b = tmp_path / "STB_20260101_121500_A.fit"
    _write_fit(sta_a, label="STA", time_obs="12:00:00", base=1.0)
    _write_fit(sta_b, label="STA", time_obs="12:15:00", base=10.0)
    _write_fit(stb_a, label="STB", time_obs="12:00:00", base=100.0)
    _write_fit(stb_b, label="STB", time_obs="12:15:00", base=200.0)

    grouped = combined_comparison_datasets_from_paths([str(sta_a), str(sta_b), str(stb_a), str(stb_b)])

    assert [dataset.label for dataset in grouped] == ["STA - 2026-01-01", "STB - 2026-01-01"]
    assert [dataset.combine_type for dataset in grouped] == ["time", "time"]
    assert [dataset.data.shape for dataset in grouped] == [(3, 8), (3, 8)]


def test_grouped_frequency_combinable_paths_return_one_combined_dataset_per_station(tmp_path: Path):
    sta_a = tmp_path / "STA_20260101_120000_A.fit"
    sta_b = tmp_path / "STA_20260101_120000_B.fit"
    stb_a = tmp_path / "STB_20260101_120000_A.fit"
    stb_b = tmp_path / "STB_20260101_120000_B.fit"
    _write_fit(sta_a, label="STA", time_obs="12:00:00", base=1.0, freq_start=100.0)
    _write_fit(sta_b, label="STA", time_obs="12:00:00", base=10.0, freq_start=85.0)
    _write_fit(stb_a, label="STB", time_obs="12:00:00", base=100.0, freq_start=100.0)
    _write_fit(stb_b, label="STB", time_obs="12:00:00", base=200.0, freq_start=85.0)

    grouped = combined_comparison_datasets_from_paths([str(sta_a), str(sta_b), str(stb_a), str(stb_b)])

    assert [dataset.label for dataset in grouped] == ["STA - 2026-01-01", "STB - 2026-01-01"]
    assert [dataset.combine_type for dataset in grouped] == ["frequency", "frequency"]
    assert [dataset.data.shape for dataset in grouped] == [(6, 4), (6, 4)]
