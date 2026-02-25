"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class MapAnalysisSummary:
    n_pixels: int
    min: float
    max: float
    mean: float
    median: float
    std: float
    p95: float
    p99: float


@dataclass(frozen=True)
class XRSAnalysisSummary:
    peak_flux: float
    peak_time: datetime | None
    median_flux: float
    rise_seconds: float
    decay_seconds: float
    flare_class: str


def summarize_map_roi(
    data: np.ndarray | Sequence[Sequence[float]],
    roi_bounds: tuple[int, int, int, int] | None = None,
) -> MapAnalysisSummary:
    arr = np.asarray(data, dtype=float)
    if arr.ndim < 2:
        raise ValueError("Map analysis expects at least a 2D array.")

    if roi_bounds is not None:
        x0, x1, y0, y1 = roi_bounds
        x_low, x_high = sorted((int(x0), int(x1)))
        y_low, y_high = sorted((int(y0), int(y1)))
        x_low = max(0, x_low)
        y_low = max(0, y_low)
        x_high = min(arr.shape[1], x_high)
        y_high = min(arr.shape[0], y_high)
        if x_high > x_low and y_high > y_low:
            arr = arr[y_low:y_high, x_low:x_high]

    flat = arr[np.isfinite(arr)]
    if flat.size == 0:
        return MapAnalysisSummary(
            n_pixels=0,
            min=float("nan"),
            max=float("nan"),
            mean=float("nan"),
            median=float("nan"),
            std=float("nan"),
            p95=float("nan"),
            p99=float("nan"),
        )

    return MapAnalysisSummary(
        n_pixels=int(flat.size),
        min=float(np.nanmin(flat)),
        max=float(np.nanmax(flat)),
        mean=float(np.nanmean(flat)),
        median=float(np.nanmedian(flat)),
        std=float(np.nanstd(flat)),
        p95=float(np.nanpercentile(flat, 95)),
        p99=float(np.nanpercentile(flat, 99)),
    )


def summarize_xrs_interval(
    flux_values: Iterable[float],
    times: Sequence[datetime] | None = None,
) -> XRSAnalysisSummary:
    flux = np.asarray(list(flux_values), dtype=float)
    finite_idx = np.where(np.isfinite(flux))[0]
    if finite_idx.size == 0:
        return XRSAnalysisSummary(
            peak_flux=float("nan"),
            peak_time=None,
            median_flux=float("nan"),
            rise_seconds=float("nan"),
            decay_seconds=float("nan"),
            flare_class="Unknown",
        )

    flux = flux[finite_idx]
    time_values = [times[i] for i in finite_idx] if times is not None else []

    peak_i = int(np.nanargmax(flux))
    peak_flux = float(flux[peak_i])
    peak_time = time_values[peak_i] if time_values and peak_i < len(time_values) else None
    median_flux = float(np.nanmedian(flux))

    rise_seconds = float("nan")
    decay_seconds = float("nan")
    if time_values and len(time_values) == len(flux):
        try:
            rise_seconds = max(0.0, float((time_values[peak_i] - time_values[0]).total_seconds()))
            decay_seconds = max(0.0, float((time_values[-1] - time_values[peak_i]).total_seconds()))
        except Exception:
            rise_seconds = float("nan")
            decay_seconds = float("nan")

    return XRSAnalysisSummary(
        peak_flux=peak_flux,
        peak_time=peak_time,
        median_flux=median_flux,
        rise_seconds=rise_seconds,
        decay_seconds=decay_seconds,
        flare_class=classify_goes_flux(peak_flux),
    )


def classify_goes_flux(peak_flux_wm2: float) -> str:
    try:
        value = float(peak_flux_wm2)
    except Exception:
        return "Unknown"
    if not np.isfinite(value) or value <= 0:
        return "Unknown"

    if value < 1e-7:
        base, letter = 1e-8, "A"
    elif value < 1e-6:
        base, letter = 1e-7, "B"
    elif value < 1e-5:
        base, letter = 1e-6, "C"
    elif value < 1e-4:
        base, letter = 1e-5, "M"
    else:
        base, letter = 1e-4, "X"
    return f"{letter}{value / base:.1f}"

