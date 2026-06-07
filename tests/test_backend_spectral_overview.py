"""
Tests for full-day station spectral overview processing and rendering.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from src.Backend.batch_processing import PLOTUTIL_DB_SCALE, PLOTUTIL_DISPLAY_LIMITS
from src.Backend.spectral_overview import (
    SPECTRAL_OVERVIEW_FIGURE_SIZE,
    SpectralOverviewCancelled,
    SpectralOverviewSource,
    build_spectral_overview,
    render_spectral_overview_figure,
)
from tests.helpers_learmonth import write_test_callisto_fit


def _source(path, observed_at, focus="01"):
    return SpectralOverviewSource(
        path=str(path),
        station="TEST",
        observed_at_utc=observed_at,
        focus_code=focus,
        filename=path.name,
    )


def test_build_spectral_overview_uses_one_day_wide_median(tmp_path):
    freqs = np.array([30.0, 20.0])
    time = np.array([0.0, 1.0])
    first = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.array([[0, 10], [10, 20]], dtype=np.uint8),
        freqs=freqs,
        time=time,
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    second = write_test_callisto_fit(
        tmp_path / "TEST_20240101_001500_01.fit",
        data=np.array([[20, 30], [30, 40]], dtype=np.uint8),
        freqs=freqs,
        time=time,
        date_obs="2024/01/01",
        time_obs="00:15:00",
    )

    result = build_spectral_overview(
        [
            _source(first, datetime(2024, 1, 1, 0, 0)),
            _source(second, datetime(2024, 1, 1, 0, 15)),
        ],
        temp_dir=str(tmp_path),
        panel_render_columns=30000,
    )

    assert result.loaded_sources == 2
    assert len(result.segments) == 2
    assert np.allclose(
        result.segments[0].data_db,
        np.array([[-15.0, -5.0], [-15.0, -5.0]], dtype=np.float32) * PLOTUTIL_DB_SCALE,
    )
    assert np.allclose(
        result.segments[1].data_db,
        np.array([[5.0, 15.0], [5.0, 15.0]], dtype=np.float32) * PLOTUTIL_DB_SCALE,
    )


def test_build_spectral_overview_handles_frequency_changes_independently(tmp_path):
    time = np.array([0.0, 1.0])
    first = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.array([[0, 10], [10, 20]], dtype=np.uint8),
        freqs=np.array([30.0, 20.0]),
        time=time,
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    second = write_test_callisto_fit(
        tmp_path / "TEST_20240101_010000_01.fit",
        data=np.array([[100, 110], [110, 120]], dtype=np.uint8),
        freqs=np.array([50.0, 40.0]),
        time=time,
        date_obs="2024/01/01",
        time_obs="01:00:00",
    )

    result = build_spectral_overview(
        [
            _source(first, datetime(2024, 1, 1, 0, 0)),
            _source(second, datetime(2024, 1, 1, 1, 0)),
        ],
        temp_dir=str(tmp_path),
        panel_render_columns=30000,
    )

    assert {segment.frequency_group for segment in result.segments} == {0, 1}
    for segment in result.segments:
        assert np.allclose(
            segment.data_db,
            np.array([[-5.0, 5.0], [-5.0, 5.0]], dtype=np.float32) * PLOTUTIL_DB_SCALE,
        )


def test_build_spectral_overview_cancellation_cleans_work_files(tmp_path):
    source_path = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=np.array([30.0, 20.0]),
        time=np.array([0.0, 1.0]),
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    work_parent = tmp_path / "work"
    work_parent.mkdir()

    with pytest.raises(SpectralOverviewCancelled):
        build_spectral_overview(
            [_source(source_path, datetime(2024, 1, 1, 0, 0))],
            temp_dir=str(work_parent),
            cancel_check=lambda: True,
        )

    assert list(work_parent.iterdir()) == []


def test_build_spectral_overview_closes_memmap_before_temp_cleanup(monkeypatch, tmp_path):
    source_path = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=np.array([30.0, 20.0]),
        time=np.array([0.0, 1.0]),
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    created_memmaps = []
    real_memmap = np.memmap

    def tracked_memmap(*args, **kwargs):
        mapped = real_memmap(*args, **kwargs)
        created_memmaps.append(mapped)
        return mapped

    monkeypatch.setattr("src.Backend.spectral_overview.np.memmap", tracked_memmap)

    build_spectral_overview(
        [_source(source_path, datetime(2024, 1, 1, 0, 0))],
        temp_dir=str(tmp_path),
    )

    assert created_memmaps
    assert all(mapped._mmap.closed for mapped in created_memmaps)


def test_build_spectral_overview_continues_after_unreadable_source(tmp_path):
    valid_path = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=np.array([30.0, 20.0]),
        time=np.array([0.0, 1.0]),
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    missing_path = tmp_path / "TEST_20240101_001500_01.fit"

    result = build_spectral_overview(
        [
            _source(valid_path, datetime(2024, 1, 1, 0, 0)),
            _source(missing_path, datetime(2024, 1, 1, 0, 15)),
        ],
        temp_dir=str(tmp_path),
    )

    assert result.total_sources == 2
    assert result.loaded_sources == 1
    assert len(result.segments) == 1
    assert any(missing_path.name in warning for warning in result.warnings)


def test_build_spectral_overview_rejects_mixed_station_sources(tmp_path):
    source_path = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.ones((2, 2), dtype=np.uint8),
        freqs=np.array([30.0, 20.0]),
        time=np.array([0.0, 1.0]),
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    first = _source(source_path, datetime(2024, 1, 1, 0, 0))
    second = SpectralOverviewSource(
        path=str(source_path),
        station="OTHER",
        observed_at_utc=datetime(2024, 1, 1, 0, 15),
        focus_code="01",
        filename=source_path.name,
    )

    with pytest.raises(ValueError, match="one station"):
        build_spectral_overview([first, second], temp_dir=str(tmp_path))


def test_render_spectral_overview_builds_six_fixed_scale_panels(tmp_path):
    source_path = write_test_callisto_fit(
        tmp_path / "TEST_20240101_000000_01.fit",
        data=np.array([[0, 10], [10, 20]], dtype=np.uint8),
        freqs=np.array([30.0, 20.0]),
        time=np.array([0.0, 1.0]),
        date_obs="2024/01/01",
        time_obs="00:00:00",
    )
    result = build_spectral_overview(
        [_source(source_path, datetime(2024, 1, 1, 0, 0))],
        temp_dir=str(tmp_path),
    )

    figure = render_spectral_overview_figure(result)

    panels = figure.axes[:6]
    assert tuple(figure.get_size_inches()) == pytest.approx(SPECTRAL_OVERVIEW_FIGURE_SIZE)
    assert len(panels) == 6
    assert panels[0].images[0].get_clim() == pytest.approx(PLOTUTIL_DISPLAY_LIMITS)
    assert any(text.get_text() == "No data" for text in panels[1].texts)
    assert "TEST" in figure._suptitle.get_text()
    assert "Focus 01" in figure._suptitle.get_text()
    assert "\n" not in figure._suptitle.get_text()
    assert panels[0].get_position().y1 < figure._suptitle.get_position()[1]
