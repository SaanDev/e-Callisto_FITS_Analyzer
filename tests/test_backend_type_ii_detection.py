from __future__ import annotations

import numpy as np
import pytest

from src.Backend.type_ii_detection import (
    TypeIIDetectionCancelled,
    TypeIIDetectionSettings,
    build_type_ii_isolation_mask,
    detect_type_ii_bursts,
    type_ii_candidate_from_mask,
)


def _spectrum(*, paired: bool = False, seed: int = 4):
    rng = np.random.default_rng(seed)
    freqs = np.linspace(220.0 if paired else 110.0, 20.0, 240 if paired else 160)
    times = np.linspace(0.0, 180.0, 721)
    data = rng.normal(0.0, 1.0, (freqs.size, times.size)).astype(np.float32)
    for column, time_s in enumerate(times):
        fundamental = 95.0 - 0.22 * time_s
        data[:, column] += 9.0 * np.exp(-0.5 * np.square((freqs - fundamental) / 1.2))
        if paired:
            harmonic = 190.0 - 0.44 * time_s
            data[:, column] += 8.0 * np.exp(-0.5 * np.square((freqs - harmonic) / 1.5))
    return data, freqs, times


def test_detects_slow_downward_type_ii_and_returns_full_mask():
    data, freqs, times = _spectrum()
    before = data.copy()

    result = detect_type_ii_bursts(data, freqs, times)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.drift_mhz_s == pytest.approx(-0.22, abs=0.02)
    assert candidate.confidence >= 0.75
    assert candidate.mask.shape == data.shape
    assert np.any(candidate.mask)
    assert np.array_equal(data, before)
    assert np.array_equal(build_type_ii_isolation_mask(candidate), candidate.mask)


def test_pairs_fundamental_and_harmonic_but_keeps_separate_masks():
    data, freqs, times = _spectrum(paired=True)

    result = detect_type_ii_bursts(data, freqs, times)

    assert len(result.candidates) == 2
    by_band = {candidate.suggested_band: candidate for candidate in result.candidates}
    assert set(by_band) == {"fundamental", "harmonic"}
    fundamental = by_band["fundamental"]
    harmonic = by_band["harmonic"]
    assert fundamental.paired_candidate_id == harmonic.candidate_id
    assert harmonic.paired_candidate_id == fundamental.candidate_id
    assert not np.any(fundamental.mask & harmonic.mask)


def test_merges_compatible_fragmented_type_ii_segments():
    data, freqs, times = _spectrum()
    gap_columns = (times >= 72.0) & (times <= 80.0)
    rng = np.random.default_rng(91)
    data[:, gap_columns] = rng.normal(
        0.0,
        1.0,
        (data.shape[0], int(np.count_nonzero(gap_columns))),
    )

    result = detect_type_ii_bursts(data, freqs, times)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.start_time_s < 10.0
    assert candidate.end_time_s > 150.0
    assert candidate.drift_mhz_s == pytest.approx(-0.22, abs=0.03)


def test_close_parallel_split_lanes_remain_one_emission_band():
    rng = np.random.default_rng(105)
    freqs = np.linspace(125.0, 20.0, 210)
    times = np.linspace(0.0, 160.0, 641)
    data = rng.normal(0.0, 1.0, (freqs.size, times.size)).astype(np.float32)
    for column, time_s in enumerate(times):
        lower = 88.0 - 0.20 * time_s
        upper = 98.0 - 0.20 * time_s
        data[:, column] += 8.5 * np.exp(-0.5 * np.square((freqs - lower) / 0.9))
        data[:, column] += 8.0 * np.exp(-0.5 * np.square((freqs - upper) / 0.9))

    result = detect_type_ii_bursts(data, freqs, times)

    assert len(result.candidates) == 1
    assert result.candidates[0].paired_candidate_id is None
    assert result.candidates[0].frequency_span_mhz > 30.0


def test_curved_partial_type_ii_and_reversed_frequency_axis():
    rng = np.random.default_rng(204)
    freqs = np.linspace(125.0, 20.0, 210)
    times = np.linspace(0.0, 180.0, 721)
    data = rng.normal(0.0, 1.0, (freqs.size, times.size)).astype(np.float32)
    for column, time_s in enumerate(times):
        if 20.0 <= time_s <= 155.0:
            center = 108.0 - 0.11 * time_s - 0.00065 * time_s * time_s
            data[:, column] += 8.5 * np.exp(-0.5 * np.square((freqs - center) / 1.1))

    normal = detect_type_ii_bursts(data, freqs, times)
    reversed_axis = detect_type_ii_bursts(data[::-1], freqs[::-1], times)

    assert len(normal.candidates) == 1
    assert len(reversed_axis.candidates) == 1
    assert normal.candidates[0].fit_quality > 0.95
    assert reversed_axis.candidates[0].drift_mhz_s == pytest.approx(
        normal.candidates[0].drift_mhz_s,
        abs=0.02,
    )


def test_rejects_broadband_vertical_impulses():
    rng = np.random.default_rng(300)
    freqs = np.linspace(120.0, 20.0, 180)
    times = np.linspace(0.0, 120.0, 481)
    data = rng.normal(0.0, 1.0, (freqs.size, times.size)).astype(np.float32)
    for column in (80, 200, 360):
        data[:, column : column + 2] += 12.0

    result = detect_type_ii_bursts(data, freqs, times)

    assert result.candidates == ()


@pytest.mark.parametrize("drift", [0.0, 7.0])
def test_rejects_stationary_and_type_iii_like_signals(drift):
    rng = np.random.default_rng(9)
    freqs = np.linspace(120.0, 20.0, 180)
    times = np.linspace(0.0, 90.0, 361)
    data = rng.normal(0.0, 1.0, (freqs.size, times.size)).astype(np.float32)
    for column, time_s in enumerate(times):
        center = 90.0 - drift * time_s
        if freqs.min() <= center <= freqs.max():
            data[:, column] += 10.0 * np.exp(-0.5 * np.square((freqs - center) / 1.0))

    result = detect_type_ii_bursts(data, freqs, times)

    assert result.candidates == ()


def test_visible_roi_and_cancellation():
    data, freqs, times = _spectrum()
    roi = {
        "time_start_s": 30.0,
        "time_end_s": 100.0,
        "frequency_min_mhz": 55.0,
        "frequency_max_mhz": 100.0,
    }
    result = detect_type_ii_bursts(data, freqs, times, roi=roi)
    assert result.roi["time_start_s"] >= 30.0
    assert result.roi["time_end_s"] <= 100.0
    assert result.candidates

    with pytest.raises(TypeIIDetectionCancelled):
        detect_type_ii_bursts(data, freqs, times, cancel_cb=lambda: True)


def test_manual_mask_uses_same_feature_schema():
    data, freqs, times = _spectrum()
    detected = detect_type_ii_bursts(data, freqs, times).candidates[0]

    manual = type_ii_candidate_from_mask(
        data,
        freqs,
        times,
        detected.mask,
        settings=TypeIIDetectionSettings(),
    )

    assert manual.feature_vector.shape == detected.feature_vector.shape
    assert manual.drift_mhz_s == pytest.approx(detected.drift_mhz_s, abs=0.03)
    assert manual.snr_data is not None
