"""
Frequency-axis helper tests.
"""

from __future__ import annotations

import numpy as np

from src.Backend.frequency_axis import (
    axis_edges,
    finite_data_limits,
    frequency_edges,
    frequency_gap_spans,
    invalid_row_mask,
    masked_display_data,
    matplotlib_extent,
    pyqtgraph_extent,
)


def test_frequency_edges_descending_axis_cover_full_channel_bounds():
    freqs = np.array([300.0, 290.0, 280.0], dtype=float)

    edges = frequency_edges(freqs, default_step=10.0)

    assert np.allclose(edges, np.array([305.0, 295.0, 285.0, 275.0]))


def test_axis_edges_cover_full_time_channel_bounds():
    times = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)

    edges = axis_edges(times, default_step=1.0)

    assert np.allclose(edges, np.array([0.5, 1.5, 2.5, 3.5, 4.5]))


def test_matplotlib_and_pyqtgraph_extents_use_same_frequency_bounds():
    freqs = np.array([300.0, 290.0, 280.0], dtype=float)
    time = np.array([5.0, 6.0, 7.0], dtype=float)

    mpl_extent = matplotlib_extent(freqs, time, default_step=10.0)
    qt_extent = pyqtgraph_extent(freqs, time, default_step=10.0)

    assert mpl_extent == [5.0, 7.0, 275.0, 305.0]
    assert qt_extent == [5.0, 7.0, 305.0, 275.0]


def test_invalid_row_mask_respects_explicit_gap_rows():
    data = np.array(
        [
            [10.0, 11.0],
            [np.nan, np.nan],
            [20.0, 21.0],
        ],
        dtype=float,
    )
    gap_row_mask = np.array([False, False, True], dtype=bool)

    out = invalid_row_mask(data, gap_row_mask)

    assert np.array_equal(out, np.array([False, True, True], dtype=bool))


def test_frequency_gap_spans_groups_contiguous_gap_rows():
    freqs = np.array([130.0, 120.0, 110.0, 100.0, 90.0], dtype=float)
    gap_row_mask = np.array([False, True, True, False, True], dtype=bool)

    spans = frequency_gap_spans(freqs, gap_row_mask, default_step=10.0)

    assert spans == [(105.0, 125.0), (85.0, 95.0)]


def test_masked_display_data_and_limits_ignore_gap_rows():
    data = np.array(
        [
            [10.0, 11.0],
            [np.nan, np.nan],
            [20.0, 21.0],
        ],
        dtype=float,
    )

    masked = masked_display_data(data)
    vmin, vmax = finite_data_limits(data)

    assert masked.mask[1, 0]
    assert masked.mask[1, 1]
    assert vmin == 10.0
    assert vmax == 21.0
