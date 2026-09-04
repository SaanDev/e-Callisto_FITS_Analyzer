"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import copy

import numpy as np


DEFAULT_FREQUENCY_DIRECTION = -1
_STEP_EPS = 1e-9


def dominant_frequency_direction(freqs: np.ndarray, default: int = DEFAULT_FREQUENCY_DIRECTION) -> int:
    arr = np.asarray(freqs, dtype=float).ravel()
    if arr.size < 2:
        return int(default)

    diffs = np.diff(arr)
    diffs = diffs[np.isfinite(diffs)]
    diffs = diffs[np.abs(diffs) > _STEP_EPS]
    if diffs.size == 0:
        return int(default)
    return -1 if float(np.nanmedian(diffs)) < 0.0 else 1


def orient_frequency_rows(
    data: np.ndarray,
    freqs: np.ndarray,
    *,
    direction: int = DEFAULT_FREQUENCY_DIRECTION,
) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data)
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    target = -1 if int(direction) < 0 else 1
    current = dominant_frequency_direction(freq_arr, default=target)
    if current == target:
        return arr, freq_arr
    return arr[::-1, ...], freq_arr[::-1]


def orient_frequency_axis(freqs: np.ndarray, *, direction: int = DEFAULT_FREQUENCY_DIRECTION) -> np.ndarray:
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    target = -1 if int(direction) < 0 else 1
    current = dominant_frequency_direction(freq_arr, default=target)
    if current == target:
        return freq_arr
    return freq_arr[::-1]


def frequency_step_mhz(freqs: np.ndarray, default: float = 1.0) -> float:
    arr = np.asarray(freqs, dtype=float).ravel()
    if arr.size < 2:
        return float(default)
    diffs = np.diff(arr)
    diffs = np.abs(diffs[np.isfinite(diffs)])
    diffs = diffs[diffs > _STEP_EPS]
    if diffs.size == 0:
        return float(default)
    return float(np.nanmedian(diffs))


def axis_edges(values: np.ndarray, default_step: float = 1.0) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        return np.empty(0, dtype=float)

    if arr.size == 1:
        half = 0.5 * float(default_step)
        return np.array([float(arr[0]) + half, float(arr[0]) - half], dtype=float)

    edges = np.empty(arr.size + 1, dtype=float)
    edges[1:-1] = 0.5 * (arr[:-1] + arr[1:])
    edges[0] = float(arr[0]) + 0.5 * float(arr[0] - arr[1])
    edges[-1] = float(arr[-1]) + 0.5 * float(arr[-1] - arr[-2])
    return edges


def frequency_edges(freqs: np.ndarray, default_step: float = 1.0) -> np.ndarray:
    return axis_edges(freqs, default_step=default_step)


def frequency_gap_spans(
    freqs: np.ndarray,
    gap_row_mask: np.ndarray | None,
    *,
    default_step: float = 1.0,
) -> list[tuple[float, float]]:
    freq_arr = np.asarray(freqs, dtype=float).ravel()
    if gap_row_mask is None or freq_arr.size == 0:
        return []

    mask = np.asarray(gap_row_mask, dtype=bool).ravel()
    if mask.shape[0] != freq_arr.size or not np.any(mask):
        return []

    edges = frequency_edges(freq_arr, default_step=default_step)
    if edges.size != freq_arr.size + 1:
        return []

    spans: list[tuple[float, float]] = []
    idx = 0
    while idx < mask.size:
        if not bool(mask[idx]):
            idx += 1
            continue

        start = idx
        while idx < mask.size and bool(mask[idx]):
            idx += 1
        end = idx

        lo = min(float(edges[start]), float(edges[end]))
        hi = max(float(edges[start]), float(edges[end]))
        spans.append((lo, hi))

    return spans


def time_bounds(time: np.ndarray) -> tuple[float, float]:
    arr = np.asarray(time, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("Time axis cannot be empty.")
    x0 = float(arr[0])
    x1 = float(arr[-1])
    if abs(x1 - x0) < _STEP_EPS:
        x1 = x0 + 1.0
    return x0, x1


def matplotlib_extent(freqs: np.ndarray, time: np.ndarray, default_step: float | None = None) -> list[float]:
    step = float(frequency_step_mhz(freqs, default=1.0) if default_step is None else default_step)
    edges = frequency_edges(freqs, default_step=step)
    if edges.size < 2:
        raise ValueError("Frequency axis cannot be empty.")
    x0, x1 = time_bounds(time)
    return [x0, x1, float(edges[-1]), float(edges[0])]


def pyqtgraph_extent(freqs: np.ndarray, time: np.ndarray, default_step: float | None = None) -> list[float]:
    step = float(frequency_step_mhz(freqs, default=1.0) if default_step is None else default_step)
    edges = frequency_edges(freqs, default_step=step)
    if edges.size < 2:
        raise ValueError("Frequency axis cannot be empty.")
    x0, x1 = time_bounds(time)
    return [x0, x1, float(edges[0]), float(edges[-1])]


def invalid_row_mask(data: np.ndarray, gap_row_mask: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(data)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")
    mask = ~np.any(np.isfinite(arr), axis=1)
    if gap_row_mask is not None:
        gap_mask = np.asarray(gap_row_mask, dtype=bool).ravel()
        if gap_mask.shape[0] == arr.shape[0]:
            mask = np.logical_or(mask, gap_mask)
    return mask


def finite_data_limits(data: np.ndarray) -> tuple[float, float] | tuple[None, None]:
    arr = np.asarray(data, dtype=float)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return None, None
    vmin = float(np.nanmin(arr))
    vmax = float(np.nanmax(arr))
    if vmax <= vmin:
        vmax = vmin + 1e-6
    return vmin, vmax


def percentile_data_limits(
    data: np.ndarray,
    lower_percentile: float = 5.0,
    upper_percentile: float = 98.0,
) -> tuple[float, float] | tuple[None, None]:
    arr = np.asarray(data, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None, None

    lo_pct = float(np.clip(lower_percentile, 0.0, 100.0))
    hi_pct = float(np.clip(upper_percentile, 0.0, 100.0))
    if lo_pct > hi_pct:
        lo_pct, hi_pct = hi_pct, lo_pct

    vmin = float(np.percentile(finite, lo_pct))
    vmax = float(np.percentile(finite, hi_pct))
    if vmax < vmin:
        vmin, vmax = vmax, vmin
    return vmin, vmax


def masked_display_data(data: np.ndarray) -> np.ma.MaskedArray:
    return np.ma.masked_invalid(np.asarray(data, dtype=float))


def transparent_bad_cmap(cmap):
    if cmap is None:
        return None
    try:
        out = copy.copy(cmap)
        if hasattr(out, "copy"):
            out = out.copy()
    except Exception:
        out = cmap

    try:
        out.set_bad((0.0, 0.0, 0.0, 0.0))
    except Exception:
        pass
    return out


def format_frequency_mhz(value) -> str:
    """Tick label for a frequency in MHz, with only the digits it needs."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(v):
        return ""

    magnitude = abs(v)
    if magnitude >= 100.0:
        return f"{v:.0f}"
    if magnitude >= 10.0:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    if magnitude >= 1.0:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if magnitude > 0.0:
        return f"{v:.4g}"
    return "0"


#: Mantissa sets for labelled log ticks, coarse to fine.  The finest set that
#: still fits inside the label budget wins, so a narrow band gets 20, 30, 40 ...
#: while a three-decade band falls back to 1, 2, 5 per decade.
_LOG_LABEL_LADDER = (
    (1.0,),
    (1.0, 3.0),
    (1.0, 2.0, 5.0),
    (1.0, 2.0, 3.0, 5.0, 7.0),
    (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
)
_LOG_MINOR_MANTISSAS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
_LOG_MINOR_FINE = tuple(1.0 + 0.5 * i for i in range(18))
_MAX_MINOR_TICKS = 60
_NICE_LINEAR_STEPS = (1.0, 2.0, 2.5, 5.0)


def _clean(value: float) -> float:
    """Drop the float noise in ``2.0 * 10 ** -2`` so 0.02 stays 0.02."""
    return float(f"{float(value):.10g}")


def _decade_ticks(lo: float, hi: float, mantissas) -> list[float]:
    """Every ``mantissa x 10**k`` inside ``[lo, hi]``, ascending."""
    lo_exp = int(np.floor(np.log10(lo)))
    hi_exp = int(np.ceil(np.log10(hi)))
    low = lo * (1.0 - 1e-9)
    high = hi * (1.0 + 1e-9)

    out: list[float] = []
    for exponent in range(lo_exp, hi_exp + 1):
        scale = 10.0 ** exponent
        for mantissa in mantissas:
            value = _clean(mantissa * scale)
            if low <= value <= high:
                out.append(value)
    return sorted(set(out))


def _nice_linear_ticks(lo: float, hi: float, max_labels: int) -> list[float]:
    """Round ticks for a band too narrow to hold even one decade subdivision.

    They are still placed logarithmically; only their *choice* is linear, which
    is what any plotting library falls back to over a fraction of a decade.
    """
    span = float(hi) - float(lo)
    if span <= 0.0:
        return []

    base_exp = int(np.floor(np.log10(span / max(2, max_labels - 1))))
    for exponent in (base_exp, base_exp + 1, base_exp - 1, base_exp + 2):
        for factor in _NICE_LINEAR_STEPS:
            step = factor * (10.0 ** exponent)
            if step <= 0.0:
                continue
            first = np.ceil(lo / step) * step
            ticks = [_clean(v) for v in np.arange(first, hi + step * 1e-6, step)]
            ticks = [v for v in ticks if lo - step * 1e-6 <= v <= hi + step * 1e-6]
            if 3 <= len(ticks) <= max_labels:
                return ticks
    return [_clean(v) for v in np.linspace(lo, hi, min(max_labels, 5))]


def log_frequency_ticks_mhz(
    lo_mhz: float,
    hi_mhz: float,
    *,
    max_labels: int = 10,
) -> tuple[list[float], list[float]]:
    """Base-10 tick frequencies for a log axis spanning ``[lo_mhz, hi_mhz]``.

    Returns ``(labelled, unlabelled)`` in MHz.  Both renderers use this so the
    matplotlib and pyqtgraph axes cannot disagree about where a tick belongs.
    """
    try:
        lo = float(lo_mhz)
        hi = float(hi_mhz)
    except (TypeError, ValueError):
        return [], []
    if lo > hi:
        lo, hi = hi, lo
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo <= 0.0 or hi <= lo:
        return [], []

    budget = max(2, int(max_labels))

    labelled: list[float] = []
    for mantissas in _LOG_LABEL_LADDER:
        ticks = _decade_ticks(lo, hi, mantissas)
        if len(ticks) > budget:
            break
        if len(ticks) >= 2:
            labelled = ticks

    if not labelled:
        decades = _decade_ticks(lo, hi, (1.0,))
        if len(decades) > budget:
            # More decades than labels allowed: keep every n-th one.
            stride = int(np.ceil(len(decades) / budget))
            labelled = decades[::stride]
        else:
            # Less than one subdivided decade: round numbers, log-placed.
            labelled = _nice_linear_ticks(lo, hi, budget)

    labels = set(labelled)
    minor = [v for v in _decade_ticks(lo, hi, _LOG_MINOR_MANTISSAS) if v not in labels]
    if not minor:
        minor = [v for v in _decade_ticks(lo, hi, _LOG_MINOR_FINE) if v not in labels]
    if len(minor) > _MAX_MINOR_TICKS:
        minor = minor[:: int(np.ceil(len(minor) / _MAX_MINOR_TICKS))]

    return labelled, minor


def log_frequency_rows(freqs: np.ndarray, *, count: int | None = None) -> np.ndarray:
    """Uniform log10(frequency) row centres spanning ``freqs``.

    Row order follows the input, so a descending frequency axis (the CALLISTO
    convention) yields descending log rows.
    """
    arr = np.asarray(freqs, dtype=float).ravel()
    finite = arr[np.isfinite(arr) & (arr > 0.0)]
    if finite.size < 2:
        raise ValueError("A log frequency axis needs at least two positive frequencies.")

    lo = float(np.log10(finite.min()))
    hi = float(np.log10(finite.max()))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        raise ValueError("Frequency axis does not span a usable range for a log scale.")

    rows = int(count) if count else int(arr.size)
    rows = max(2, rows)
    grid = np.linspace(lo, hi, rows)
    descending = arr.size >= 2 and float(arr[0]) > float(arr[-1])
    return grid[::-1] if descending else grid


def log_frequency_bin_rows(
    lo_mhz: float,
    hi_mhz: float,
    count: int,
    *,
    descending: bool = True,
) -> np.ndarray:
    """Centres of ``count`` equal log10 bins filling ``[lo_mhz, hi_mhz]``.

    Rows chosen this way tile the band exactly, so the image drawn from them
    can be placed on the band's own edges instead of half a row outside them --
    which is what lets the accelerated renderer frame precisely the span the
    matplotlib axis frames.
    """
    lo, hi = float(lo_mhz), float(hi_mhz)
    if lo > hi:
        lo, hi = hi, lo
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo <= 0.0 or hi <= lo:
        raise ValueError("A log frequency band needs a positive, increasing range.")

    rows = max(2, int(count))
    edges = np.linspace(np.log10(lo), np.log10(hi), rows + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    return centres[::-1] if descending else centres


def resample_rows_to_log_frequency(
    data: np.ndarray,
    freqs: np.ndarray,
    *,
    count: int | None = None,
    bounds: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Map a spectrogram onto a uniformly log-spaced frequency grid.

    Returns ``(resampled, log_rows)`` where ``log_rows`` holds log10(frequency)
    centres in the same order as the input rows.  Renderers that can only place
    an image on a linear axis (pyqtgraph's ImageItem) need this to show a log
    frequency scale; matplotlib warps the image itself and does not.

    ``bounds`` gives the band the rows must fill, in MHz -- normally the
    channel edges, so the result covers the same span the data does.  Without
    it the rows run from the lowest to the highest channel centre.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D data, got ndim={arr.ndim}.")

    source = np.asarray(freqs, dtype=float).ravel()
    if source.size != arr.shape[0]:
        raise ValueError("Frequency axis length does not match the data rows.")

    if bounds is None:
        log_rows = log_frequency_rows(source, count=count)
    else:
        log_rows = log_frequency_bin_rows(
            bounds[0],
            bounds[1],
            int(count) if count else int(source.size),
            descending=source.size >= 2 and float(source[0]) > float(source[-1]),
        )

    # np.interp needs an ascending sample axis.
    valid = np.isfinite(source) & (source > 0.0)
    if valid.sum() < 2:
        raise ValueError("A log frequency axis needs at least two positive frequencies.")
    src_log = np.log10(source[valid])
    block = arr[valid, :]
    order = np.argsort(src_log)
    src_log = src_log[order]
    block = block[order, :]

    out = np.empty((log_rows.size, arr.shape[1]), dtype=arr.dtype)
    for column in range(arr.shape[1]):
        out[:, column] = np.interp(log_rows, src_log, block[:, column])
    return out, log_rows


def log_frequency_row_indices(
    freqs: np.ndarray,
    *,
    count: int | None = None,
    bounds: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """``(log_rows, nearest_source_row)`` for a log-resampled spectrogram.

    Anything indexed by row rather than interpolated — a gap mask, a channel
    flag — has to follow the rows to their new places, or it ends up describing
    the wrong frequencies once the image is re-sampled.
    """
    source = np.asarray(freqs, dtype=float).ravel()
    if bounds is None:
        log_rows = log_frequency_rows(source, count=count)
    else:
        log_rows = log_frequency_bin_rows(
            bounds[0],
            bounds[1],
            int(count) if count else int(source.size),
            descending=source.size >= 2 and float(source[0]) > float(source[-1]),
        )

    usable = np.flatnonzero(np.isfinite(source) & (source > 0.0))
    if usable.size < 2:
        raise ValueError("A log frequency axis needs at least two positive frequencies.")

    src_log = np.log10(source[usable])
    order = np.argsort(src_log)
    src_log = src_log[order]
    src_index = usable[order]

    right = np.clip(np.searchsorted(src_log, log_rows), 1, src_log.size - 1)
    left = right - 1
    take_right = (log_rows - src_log[left]) > (src_log[right] - log_rows)
    return log_rows, np.where(take_right, src_index[right], src_index[left])


def resample_row_mask_to_log_frequency(
    mask: np.ndarray | None,
    freqs: np.ndarray,
    *,
    count: int | None = None,
    bounds: tuple[float, float] | None = None,
) -> np.ndarray | None:
    """Carry a per-row boolean mask onto the log-resampled rows."""
    if mask is None:
        return None
    flags = np.asarray(mask, dtype=bool).ravel()
    source = np.asarray(freqs, dtype=float).ravel()
    if flags.size != source.size:
        return None
    _rows, nearest = log_frequency_row_indices(source, count=count, bounds=bounds)
    return flags[nearest]
