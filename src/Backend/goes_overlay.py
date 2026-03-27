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


GOES_OVERLAY_CHANNEL_ORDER: tuple[str, ...] = ("xrsa", "xrsb")
GOES_OVERLAY_CHANNEL_LABELS: dict[str, str] = {
    "xrsa": "Long(XRS-A)",
    "xrsb": "Short(XRS-B)",
}


@dataclass(slots=True)
class GoesOverlaySeries:
    channel_key: str
    display_label: str
    channel_label: str
    satellite_number: int
    x_seconds: np.ndarray
    flux_wm2: np.ndarray


@dataclass(slots=True)
class GoesOverlayPayload:
    start_utc: datetime
    end_utc: datetime
    base_utc: datetime
    satellite_number: int
    satellite_numbers: tuple[int, ...]
    series: dict[str, GoesOverlaySeries]
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


def pick_goes_short_channel(columns: list[str]) -> str | None:
    for col in columns:
        lowered = str(col or "").strip().lower()
        if any(token in lowered for token in ("xrsa", "short", "0.5", "4.0")):
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


def _extract_goes_overlay_series(
    frame,
    *,
    column: str,
    channel_key: str,
    base_utc: datetime,
    start_utc: datetime,
    end_utc: datetime,
    satellite_number: int,
) -> GoesOverlaySeries:
    index = getattr(frame, "index", None)
    to_pydatetime = getattr(index, "to_pydatetime", None)
    if not callable(to_pydatetime):
        raise RuntimeError("GOES/XRS frame index does not provide timestamps.")

    base_utc = _ensure_utc(base_utc)
    start_utc = _ensure_utc(start_utc)
    end_utc = _ensure_utc(end_utc)
    times_utc = [_ensure_utc(item) for item in list(to_pydatetime())]
    flux = np.asarray(frame[column], dtype=float)

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
        raise RuntimeError(f"GOES/XRS overlay contains no valid {channel_key.upper()} samples in range.")

    selected_times = [times_utc[i] for i in np.nonzero(mask)[0]]
    selected_flux = np.asarray(flux[mask], dtype=float)
    x_seconds = np.asarray([(item - base_utc).total_seconds() for item in selected_times], dtype=float)

    if x_seconds.size == 0:
        raise RuntimeError("GOES/XRS overlay did not produce any plottable timestamps.")

    order = np.argsort(x_seconds, kind="mergesort")
    return GoesOverlaySeries(
        channel_key=str(channel_key),
        display_label=str(GOES_OVERLAY_CHANNEL_LABELS.get(channel_key, channel_key.upper())),
        channel_label=str(column),
        satellite_number=int(satellite_number),
        x_seconds=np.asarray(x_seconds[order], dtype=float),
        flux_wm2=np.asarray(selected_flux[order], dtype=float),
    )


def _best_channel_key(series_map: dict[str, GoesOverlaySeries]) -> str:
    for key in ("xrsb", "xrsa"):
        if key in series_map:
            return key
    return next(iter(series_map))


def _payload_sample_count(payload: GoesOverlayPayload) -> int:
    total = 0
    for series in (payload.series or {}).values():
        try:
            total += int(np.asarray(series.x_seconds, dtype=float).size)
        except Exception:
            continue
    return total


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

    series_map: dict[str, GoesOverlaySeries] = {}
    short_col = pick_goes_short_channel(numeric_cols)
    long_col = pick_goes_long_channel(numeric_cols)

    if short_col:
        try:
            series_map["xrsa"] = _extract_goes_overlay_series(
                frame,
                column=short_col,
                channel_key="xrsa",
                base_utc=base_utc,
                start_utc=start_utc,
                end_utc=end_utc,
                satellite_number=satellite_number,
            )
        except Exception:
            pass

    if long_col:
        try:
            series_map["xrsb"] = _extract_goes_overlay_series(
                frame,
                column=long_col,
                channel_key="xrsb",
                base_utc=base_utc,
                start_utc=start_utc,
                end_utc=end_utc,
                satellite_number=satellite_number,
            )
        except Exception:
            pass

    if not series_map:
        raise RuntimeError("GOES/XRS overlay does not provide usable XRS-A or XRS-B samples in range.")

    primary_key = _best_channel_key(series_map)
    primary_series = series_map[primary_key]
    return GoesOverlayPayload(
        start_utc=_ensure_utc(start_utc),
        end_utc=_ensure_utc(end_utc),
        base_utc=_ensure_utc(base_utc),
        satellite_number=int(primary_series.satellite_number),
        satellite_numbers=(int(primary_series.satellite_number),),
        series=dict(series_map),
        x_seconds=np.asarray(primary_series.x_seconds, dtype=float),
        flux_wm2=np.asarray(primary_series.flux_wm2, dtype=float),
        channel_label=str(primary_series.channel_label),
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
        try:
            search_result = search(spec)
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: search failed: {exc}")
            continue
        if not search_result.rows:
            continue
        search_hits += len(search_result.rows)

        try:
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
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: fetch failed: {exc}")
            continue
        if fetch_result.errors:
            fetch_errors.extend([f"GOES-{sat}: {msg}" for msg in fetch_result.errors])
        if not fetch_result.paths:
            continue

        if progress_cb is not None:
            progress_cb(search_end, f"Loading GOES-{sat} XRS time series...")

        try:
            load_result = load_downloaded(fetch_result.paths, data_kind=DATA_KIND_TIMESERIES)
            ts = load_result.maps_or_timeseries
            to_dataframe = getattr(ts, "to_dataframe", None)
            if not callable(to_dataframe):
                fetch_errors.append(f"GOES-{sat}: loaded data does not provide a dataframe.")
                continue
            frame = to_dataframe()
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: load failed: {exc}")
            continue
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
            "No usable GOES/XRS overlay channels could be loaded across GOES-16 to GOES-19."
            + (f"\n\nDetails:\n{details}{more}" if details else "")
        )

    best_series: dict[str, GoesOverlaySeries] = {}
    for payload in payload_candidates:
        for key, series in (payload.series or {}).items():
            current = best_series.get(key)
            score = (int(np.asarray(series.x_seconds, dtype=float).size), -int(series.satellite_number))
            current_score = (
                (-1, 0)
                if current is None
                else (int(np.asarray(current.x_seconds, dtype=float).size), -int(current.satellite_number))
            )
            if current is None or score > current_score:
                best_series[key] = series

    if not best_series:
        details = "\n".join(fetch_errors[:6])
        more = "" if len(fetch_errors) <= 6 else f"\n...and {len(fetch_errors) - 6} more."
        raise RuntimeError(
            "No usable GOES/XRS overlay channels could be loaded across GOES-16 to GOES-19."
            + (f"\n\nDetails:\n{details}{more}" if details else "")
        )

    ordered_series: dict[str, GoesOverlaySeries] = {}
    for key in GOES_OVERLAY_CHANNEL_ORDER:
        if key in best_series:
            ordered_series[key] = best_series[key]
    for key in best_series:
        if key not in ordered_series:
            ordered_series[key] = best_series[key]

    primary_key = _best_channel_key(ordered_series)
    primary_series = ordered_series[primary_key]
    used_satellites = tuple(sorted({int(series.satellite_number) for series in ordered_series.values()}))
    payload = GoesOverlayPayload(
        start_utc=_ensure_utc(start_utc),
        end_utc=_ensure_utc(end_utc),
        base_utc=_ensure_utc(base_utc),
        satellite_number=int(primary_series.satellite_number),
        satellite_numbers=used_satellites,
        series=ordered_series,
        x_seconds=np.asarray(primary_series.x_seconds, dtype=float),
        flux_wm2=np.asarray(primary_series.flux_wm2, dtype=float),
        channel_label=str(primary_series.channel_label),
    )
    if progress_cb is not None:
        sat_text = ", ".join(f"GOES-{sat}" for sat in payload.satellite_numbers) or f"GOES-{int(payload.satellite_number)}"
        progress_cb(100, f"Loaded {_payload_sample_count(payload)} GOES/XRS samples from {sat_text}.")
    return payload
