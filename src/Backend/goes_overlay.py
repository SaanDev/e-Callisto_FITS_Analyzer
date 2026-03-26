"""
e-CALLISTO FITS Analyzer
Version 2.3.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from src.Backend.sunpy_archive import (
    DATA_KIND_TIMESERIES,
    SunPyQuerySpec,
    fetch,
    load_downloaded,
    search,
)


@dataclass(slots=True)
class GoesOverlayPayload:
    start_utc: datetime
    end_utc: datetime
    base_utc: datetime
    satellite_number: int
    x_seconds: np.ndarray
    flux_wm2: np.ndarray
    channel_label: str


GOES_CLASS_LEVELS: tuple[tuple[float, str], ...] = (
    (1.0e-8, "A"),
    (1.0e-7, "B"),
    (1.0e-6, "C"),
    (1.0e-5, "M"),
    (1.0e-4, "X"),
)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pick_goes_long_channel(columns: list[str]) -> str | None:
    for col in columns:
        lowered = str(col or "").strip().lower()
        if any(token in lowered for token in ("xrsb", "long", "1.0", "8.0")):
            return str(col)
    return None


def normalize_goes_satellite_numbers(values: Sequence[int] | int | None) -> tuple[int, ...]:
    if values is None:
        return (16, 17, 18, 19)
    if isinstance(values, (int, np.integer)):
        values = [int(values)]

    out: list[int] = []
    for item in values:
        try:
            sat = int(item)
        except Exception:
            continue
        if sat < 1 or sat in out:
            continue
        out.append(sat)
    return tuple(out) if out else (16, 17, 18, 19)


def goes_flux_axis_limits(flux) -> tuple[float, float] | None:
    vals = np.asarray(flux, dtype=float)
    vals = vals[np.isfinite(vals) & (vals > 0.0)]
    if vals.size == 0:
        return None

    min_flux = float(np.min(vals))
    max_flux = float(np.max(vals))
    lower_class = GOES_CLASS_LEVELS[0][0]
    upper_class = GOES_CLASS_LEVELS[-1][0]

    for value, _label in GOES_CLASS_LEVELS:
        if value <= min_flux:
            lower_class = value
        if value >= max_flux:
            upper_class = value
            break

    lower = min(min_flux / 1.6, lower_class / 1.6)
    upper = max(max_flux * 1.6, upper_class * 1.6)
    if upper <= lower:
        upper = lower * 10.0
    return float(lower), float(upper)


def goes_class_ticks_for_limits(ymin: float, ymax: float) -> list[tuple[float, str]]:
    try:
        lo = float(min(ymin, ymax))
        hi = float(max(ymin, ymax))
    except Exception:
        return [(value, label) for value, label in GOES_CLASS_LEVELS]

    ticks = [(value, label) for value, label in GOES_CLASS_LEVELS if lo <= value <= hi]
    if ticks:
        return ticks
    if hi < GOES_CLASS_LEVELS[0][0]:
        return [GOES_CLASS_LEVELS[0]]
    return [GOES_CLASS_LEVELS[-1]]


def build_goes_overlay_payload(
    frame,
    *,
    base_utc: datetime,
    start_utc: datetime,
    end_utc: datetime,
    satellite_number: int = 16,
) -> GoesOverlayPayload:
    if frame is None or len(frame) == 0:
        raise RuntimeError("Loaded GOES/XRS time series has no data.")

    numeric_cols = [str(c) for c in frame.columns if np.issubdtype(frame[c].dtype, np.number)]
    if not numeric_cols:
        raise RuntimeError("GOES/XRS TimeSeries has no numeric columns.")

    long_col = pick_goes_long_channel(numeric_cols)
    if not long_col:
        raise RuntimeError("GOES/XRS long channel (XRS-B) is unavailable.")

    index = getattr(frame, "index", None)
    to_pydatetime = getattr(index, "to_pydatetime", None)
    if not callable(to_pydatetime):
        raise RuntimeError("GOES/XRS frame index does not provide timestamps.")

    base_utc = _ensure_utc(base_utc)
    start_utc = _ensure_utc(start_utc)
    end_utc = _ensure_utc(end_utc)
    times_utc = [_ensure_utc(item) for item in list(to_pydatetime())]
    flux = np.asarray(frame[long_col], dtype=float)

    if flux.size != len(times_utc):
        raise RuntimeError("GOES/XRS frame shape is inconsistent.")

    mask = np.array(
        [
            (start_utc <= item <= end_utc)
            and np.isfinite(val)
            and float(val) > 0.0
            for item, val in zip(times_utc, flux)
        ],
        dtype=bool,
    )
    if not np.any(mask):
        raise RuntimeError("GOES/XRS overlay contains no valid long-channel samples in range.")

    selected_times = [times_utc[i] for i in np.nonzero(mask)[0]]
    selected_flux = np.asarray(flux[mask], dtype=float)
    x_seconds = np.asarray([(item - base_utc).total_seconds() for item in selected_times], dtype=float)

    if x_seconds.size == 0:
        raise RuntimeError("GOES/XRS overlay did not produce any plottable timestamps.")

    order = np.argsort(x_seconds, kind="mergesort")
    return GoesOverlayPayload(
        start_utc=start_utc,
        end_utc=end_utc,
        base_utc=base_utc,
        satellite_number=int(satellite_number),
        x_seconds=np.asarray(x_seconds[order], dtype=float),
        flux_wm2=np.asarray(selected_flux[order], dtype=float),
        channel_label=str(long_col),
    )


def fetch_goes_overlay(
    *,
    start_utc: datetime,
    end_utc: datetime,
    base_utc: datetime,
    cache_dir: str | Path,
    satellite_numbers: Sequence[int] | int | None = None,
    max_records: int = 128,
    progress_cb: Callable[[int | None, str | None], None] | None = None,
) -> GoesOverlayPayload:
    satellites = normalize_goes_satellite_numbers(satellite_numbers)
    if progress_cb is not None:
        sat_text = ", ".join(f"GOES-{sat}" for sat in satellites)
        progress_cb(5, f"Searching {sat_text} XRS archives...")

    payload_candidates: list[GoesOverlayPayload] = []
    search_hits = 0
    fetch_errors: list[str] = []
    n_sats = max(1, len(satellites))

    for idx, sat in enumerate(satellites, start=1):
        search_start = 5 + int(((idx - 1) / n_sats) * 70)
        search_end = 5 + int((idx / n_sats) * 70)
        spec = SunPyQuerySpec(
            start_dt=_ensure_utc(start_utc),
            end_dt=_ensure_utc(end_utc),
            spacecraft="GOES",
            instrument="XRS",
            satellite_number=int(sat),
            max_records=max(1, int(max_records)),
        )

        if progress_cb is not None:
            progress_cb(search_start, f"Searching GOES-{sat} XRS archive...")
        search_result = search(spec)
        if not search_result.rows:
            continue
        search_hits += len(search_result.rows)

        fetch_result = fetch(
            search_result,
            cache_dir,
            selected_rows=list(range(len(search_result.rows))),
            progress_cb=(
                None
                if progress_cb is None
                else lambda value, text, sat=sat, start=search_start, end=search_end: progress_cb(
                    start + int(max(0, min(100, int(value or 0))) * max(1, end - start) / 100.0),
                    f"GOES-{sat}: {text}",
                )
            ),
        )
        if fetch_result.errors:
            fetch_errors.extend([f"GOES-{sat}: {msg}" for msg in fetch_result.errors])
        if not fetch_result.paths:
            continue

        if progress_cb is not None:
            progress_cb(search_end, f"Loading GOES-{sat} XRS time series...")

        load_result = load_downloaded(fetch_result.paths, data_kind=DATA_KIND_TIMESERIES)
        ts = load_result.maps_or_timeseries
        to_dataframe = getattr(ts, "to_dataframe", None)
        if not callable(to_dataframe):
            continue
        frame = to_dataframe()
        try:
            payload = build_goes_overlay_payload(
                frame,
                base_utc=base_utc,
                start_utc=start_utc,
                end_utc=end_utc,
                satellite_number=sat,
            )
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: {exc}")
            continue
        payload_candidates.append(payload)

    if not payload_candidates:
        details = "\n".join(fetch_errors[:6])
        more = "" if len(fetch_errors) <= 6 else f"\n...and {len(fetch_errors) - 6} more."
        if search_hits == 0:
            raise RuntimeError("No GOES/XRS files were found across GOES-16 to GOES-19 for the selected FITS observation window.")
        raise RuntimeError(
            "No usable GOES/XRS long-channel data could be loaded across GOES-16 to GOES-19."
            + (f"\n\nDetails:\n{details}{more}" if details else "")
        )

    payload = max(
        payload_candidates,
        key=lambda item: (int(item.x_seconds.size), -int(item.satellite_number)),
    )
    if progress_cb is not None:
        progress_cb(100, f"Loaded {int(payload.x_seconds.size)} GOES/XRS samples from GOES-{int(payload.satellite_number)}.")
    return payload
