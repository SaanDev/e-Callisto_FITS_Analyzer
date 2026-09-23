"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.backend.radio.ridge_tracking import RidgeSettings, track_ridge

FREQS = np.linspace(200.0, 45.0, 200)  # descending, as CALLISTO stores it
TIME = np.arange(0.0, 120.0, 0.25)
TRUE_FREQ = 150.0 * ((TIME + 20.0) / 20.0) ** -0.6


def _spectrum(*, rfi=True, dropout=True, seed=3):
    rng = np.random.default_rng(seed)
    data = rng.normal(0.0, 1.0, (FREQS.size, TIME.size))
    for column, freq in enumerate(TRUE_FREQ):
        if 40 <= column <= 400 and not (dropout and 200 <= column <= 204):
            data[:, column] += 8.0 * np.exp(-0.5 * ((FREQS - freq) / 1.5) ** 2)
    if rfi:
        data[30, :] += 12.0  # a channel that is bright all the time
    return data


def test_tracks_the_burst_from_its_brightest_point():
    result = track_ridge(_spectrum(), FREQS)

    assert result.time_indices[0] == 40 and result.time_indices[-1] == 400
    error = np.abs(result.freqs_mhz - TRUE_FREQ[result.time_indices])
    assert np.median(error) < 0.5
    assert np.max(error) < 2.0


def test_crosses_a_short_dropout_but_stops_in_the_noise():
    result = track_ridge(_spectrum(), FREQS, settings=RidgeSettings(max_gap=8))

    assert set(range(205, 210)).issubset(result.time_indices.tolist())
    assert not set(range(200, 205)) & set(result.time_indices.tolist())
    assert result.time_indices.max() <= 400


def test_a_seed_picks_which_feature_to_follow():
    data = _spectrum(rfi=False)
    data[150, :] += 25.0  # now the brightest thing is a steady line
    column = 100
    row = int(np.argmin(np.abs(FREQS - TRUE_FREQ[column])))

    result = track_ridge(data, FREQS, seed=(row + 2, column))

    error = np.abs(result.freqs_mhz - TRUE_FREQ[result.time_indices])
    assert np.median(error) < 0.5
    assert result.seed_index[1] == column


def test_falling_drift_keeps_the_ridge_moving_to_lower_frequency():
    result = track_ridge(_spectrum(), FREQS, settings=RidgeSettings(drift="falling"))

    # Sub-channel refinement may nudge a point by under a channel, never more.
    assert np.all(np.diff(result.freqs_mhz) <= FREQS[0] - FREQS[1])


def test_works_on_an_isolated_burst_with_a_flat_background():
    data = _spectrum(rfi=False, dropout=False)
    isolated = np.zeros_like(data)
    for column, freq in enumerate(TRUE_FREQ):
        if 60 <= column <= 300:
            rows = np.abs(FREQS - freq) < 6.0
            isolated[rows, column] = data[rows, column]

    result = track_ridge(isolated, FREQS)

    assert result.time_indices[0] == 60 and result.time_indices[-1] == 300


def test_a_seed_on_empty_sky_is_rejected():
    data = _spectrum(rfi=False)
    with pytest.raises(ValueError, match="not above the background"):
        track_ridge(data, FREQS, seed=(5, 460))


def test_a_lone_noise_spike_past_a_gap_does_not_extend_the_ridge():
    data = _spectrum(rfi=False)
    end_row = int(np.argmin(np.abs(FREQS - TRUE_FREQ[400])))
    data[end_row, 405] += 6.0  # one bright sample five columns after the burst ends

    result = track_ridge(data, FREQS, settings=RidgeSettings(max_gap=8))

    assert result.time_indices.max() == 400
