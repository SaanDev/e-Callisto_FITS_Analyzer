"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Automatic tracking of a burst's emission ridge through a dynamic spectrum.

The per-column maximum picks whatever is brightest at each time, so RFI, a
second burst or plain noise produce scattered points that have to be lassoed
away by hand. Tracking instead starts on the burst and follows it: from each
column it only looks within a few channels of where the ridge was in the
previous one, and it stops once the signal has dropped into the noise for
several columns in a row. Peaks are refined to sub-channel frequency with a
three-point parabola.

Plain numpy; ``data`` is (frequency, time) like the rest of the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

DRIFT_ANY = "any"
DRIFT_FALLING = "falling"
#: Consecutive hits needed to resume tracking after a gap.
CONFIRM_RUN = 2


@dataclass(frozen=True)
class RidgeSettings:
    """How the tracker follows the ridge.

    ``search_channels``: how far (in channels) the ridge may move between two
    consecutive columns. ``threshold_sigma``: a column's peak must stand this
    many robust standard deviations above the background to count.
    ``max_gap``: consecutive columns below threshold before tracking stops.
    ``drift``: ``"falling"`` only lets the ridge move towards lower
    frequency with time, as a type II or III burst does.
    """

    search_channels: int = 3
    threshold_sigma: float = 3.0
    max_gap: int = 8
    drift: str = DRIFT_ANY


@dataclass(frozen=True)
class RidgeResult:
    time_indices: np.ndarray
    freqs_mhz: np.ndarray
    peak_values: np.ndarray
    seed_index: tuple[int, int]
    threshold: float
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return int(self.time_indices.size)


def _background_threshold(values: np.ndarray, threshold_sigma: float) -> float:
    """Background level plus ``threshold_sigma`` robust standard deviations.

    An isolated burst sits on an exactly flat background, where the MAD is
    zero; there anything brighter than the background counts.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("inf")
    baseline = float(np.median(finite))
    sigma = 1.4826 * float(np.median(np.abs(finite - baseline)))
    if not np.isfinite(sigma) or sigma <= 0.0:
        return float(np.nextafter(baseline, np.inf))
    return baseline + float(threshold_sigma) * sigma


def _channel_frequency(freqs: np.ndarray, position: float) -> float:
    """Frequency at a fractional channel index (linear between channels)."""
    lower = int(np.floor(position))
    upper = min(lower + 1, freqs.size - 1)
    weight = position - lower
    return float(freqs[lower] + (freqs[upper] - freqs[lower]) * weight)


def _refined_peak(column: np.ndarray, row: int) -> float:
    """Sub-channel peak position from a parabola through the peak and its neighbours."""
    if row <= 0 or row >= column.size - 1:
        return float(row)
    left, centre, right = column[row - 1], column[row], column[row + 1]
    if not (np.isfinite(left) and np.isfinite(centre) and np.isfinite(right)):
        return float(row)
    curvature = left - 2.0 * centre + right
    if curvature >= 0.0:
        return float(row)
    offset = 0.5 * (left - right) / curvature
    return float(row) + float(np.clip(offset, -0.5, 0.5))


def _seed(data: np.ndarray, seed) -> tuple[int, int] | None:
    """Starting (row, column): the given point snapped to its column's peak, or the brightest sample."""
    rows, cols = data.shape
    finite = np.where(np.isfinite(data), data, -np.inf)
    if seed is None:
        flat = int(np.argmax(finite))
        row, col = divmod(flat, cols)
        return (row, col) if np.isfinite(finite[row, col]) else None
    row, col = int(seed[0]), int(seed[1])
    row = int(np.clip(row, 0, rows - 1))
    col = int(np.clip(col, 0, cols - 1))
    return row, col


def _follow(
    data: np.ndarray,
    start_row: int,
    start_col: int,
    step: int,
    settings: RidgeSettings,
    threshold: float,
    freq_descending: bool,
) -> list[tuple[int, int]]:
    """Walk from ``start_col`` in direction ``step`` (+1 forward, -1 backward)."""
    rows, cols = data.shape
    window = max(1, int(settings.search_channels))
    falling = str(settings.drift).strip().lower() == DRIFT_FALLING
    points: list[tuple[int, int]] = []
    # After a gap, hits are only kept once CONFIRM_RUN of them come in a row:
    # a lone noise sample past the end of the burst must not extend it.
    pending: list[tuple[int, int]] = []
    row = start_row  # last confirmed ridge row
    probe = start_row  # where the next column is searched
    misses = 0
    col = start_col + step
    while 0 <= col < cols and misses <= int(settings.max_gap):
        lo, hi = probe - window, probe + window
        if falling:
            # Rows run high-to-low frequency when the axis is descending, so
            # "lower frequency later" means larger row indices going forward.
            later_is_higher_row = freq_descending
            if (step > 0) == later_is_higher_row:
                lo = probe
            else:
                hi = probe
        lo, hi = max(0, lo), min(rows - 1, hi)
        segment = data[lo : hi + 1, col]
        finite = np.where(np.isfinite(segment), segment, -np.inf)
        best = int(np.argmax(finite))
        value = finite[best]
        if np.isfinite(value) and value > threshold:
            probe = lo + best
            if misses == 0:
                points.append((probe, col))
                row = probe
            else:
                pending.append((probe, col))
                if len(pending) >= CONFIRM_RUN:
                    points.extend(pending)
                    pending = []
                    row = probe
                    misses = 0
        else:
            if pending:
                pending = []
                probe = row
            misses += 1
        col += step
    return points


def track_ridge(
    data,
    freqs,
    *,
    seed: tuple[int, int] | None = None,
    settings: RidgeSettings | None = None,
) -> RidgeResult:
    """Follow the burst ridge through ``data`` (frequency x time).

    ``seed`` is an optional ``(row, column)`` to start from; it is snapped to
    the brightest sample of that column within the search window. Without it,
    tracking starts at the brightest sample of the whole spectrum.
    """
    arr = np.asarray(data, dtype=float)
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    if arr.ndim != 2 or arr.shape[0] != freq_arr.size:
        raise ValueError("Ridge tracking needs (frequency, time) data matching the frequency axis.")
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise ValueError("The spectrum is empty.")
    config = settings or RidgeSettings()

    # Work on each channel's excess over its own median level: a channel that
    # is bright all the time (RFI, an uncorrected receiver band) then no
    # longer outshines the burst. On background-subtracted data it changes
    # almost nothing.
    finite_rows = np.any(np.isfinite(arr), axis=1)
    row_level = np.zeros(arr.shape[0])
    if np.any(finite_rows):
        row_level[finite_rows] = np.nanmedian(arr[finite_rows], axis=1)
    arr = arr - row_level[:, None]

    threshold = _background_threshold(arr, config.threshold_sigma)
    start = _seed(arr, seed)
    if start is None:
        raise ValueError("The spectrum has no finite samples to track.")
    row, col = start
    if seed is not None:
        window = max(1, int(config.search_channels))
        lo, hi = max(0, row - window), min(arr.shape[0] - 1, row + window)
        segment = np.where(np.isfinite(arr[lo : hi + 1, col]), arr[lo : hi + 1, col], -np.inf)
        row = lo + int(np.argmax(segment))
    if not (np.isfinite(arr[row, col]) and arr[row, col] > threshold):
        raise ValueError("The starting point is not above the background; start on the burst itself.")

    freq_descending = freq_arr.size >= 2 and freq_arr[0] > freq_arr[-1]
    backward = _follow(arr, row, col, -1, config, threshold, freq_descending)
    forward = _follow(arr, row, col, +1, config, threshold, freq_descending)
    path = list(reversed(backward)) + [(row, col)] + forward

    columns = np.array([c for _r, c in path], dtype=int)
    positions = np.array([_refined_peak(arr[:, c], r) for r, c in path], dtype=float)
    ridge_freqs = np.array([_channel_frequency(freq_arr, p) for p in positions], dtype=float)
    peaks = np.array([arr[r, c] for r, c in path], dtype=float)

    warnings: list[str] = []
    if columns.size < 3:
        warnings.append("The ridge could only be followed for a few columns; try a larger search window or a lower threshold.")
    return RidgeResult(
        time_indices=columns,
        freqs_mhz=ridge_freqs,
        peak_values=peaks,
        seed_index=(int(row), int(col)),
        threshold=float(threshold),
        warnings=tuple(warnings),
    )
