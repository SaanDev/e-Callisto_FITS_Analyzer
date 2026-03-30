"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Callable, Sequence

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
    "xrsa": "Short(XRS-A)",
    "xrsb": "Long(XRS-B)",
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
_GOES_STANDARD_TIME_CALENDARS = frozenset({"standard", "gregorian", "proleptic_gregorian"})
_GOES_TIME_UNIT_SECONDS: dict[str, float] = {
    "us": 1.0e-6,
    "usec": 1.0e-6,
    "usecs": 1.0e-6,
    "microsecond": 1.0e-6,
    "microseconds": 1.0e-6,
    "ms": 1.0e-3,
    "msec": 1.0e-3,
    "msecs": 1.0e-3,
    "millisecond": 1.0e-3,
    "milliseconds": 1.0e-3,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
}
_GOES_TIME_UNITS_RE = re.compile(r"^\s*([A-Za-z_]+)\s+since\s+(.+?)\s*$", re.IGNORECASE)


class GoesOverlayCancelled(RuntimeError):
    pass


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pick_goes_long_channel(columns: list[str]) -> str | None:
    return _pick_goes_channel(columns, channel_key="xrsb")


def pick_goes_short_channel(columns: list[str]) -> str | None:
    return _pick_goes_channel(columns, channel_key="xrsa")


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


def _import_goes_netcdf_dependencies():
    try:
        import netCDF4 as nc
        import pandas as pd
    except Exception as exc:
        raise RuntimeError(
            "GOES overlay requires the 'netCDF4' and 'pandas' packages to read downloaded XRS files safely."
        ) from exc
    return nc, pd


def _raise_if_goes_overlay_cancelled(cancel_cb: Callable[[], bool] | None) -> None:
    if cancel_cb is None:
        return
    try:
        cancelled = bool(cancel_cb())
    except Exception:
        cancelled = False
    if cancelled:
        raise GoesOverlayCancelled("GOES overlay request cancelled.")


def _parse_goes_time_reference(units: str) -> tuple[datetime, float] | None:
    match = _GOES_TIME_UNITS_RE.match(str(units or "").strip())
    if match is None:
        return None

    unit_token = str(match.group(1) or "").strip().lower()
    seconds_per_unit = _GOES_TIME_UNIT_SECONDS.get(unit_token)
    if seconds_per_unit is None:
        return None

    anchor_text = str(match.group(2) or "").strip()
    if not anchor_text:
        return None
    anchor_text = anchor_text.replace("Z", "+00:00")
    anchor_text = re.sub(r"\s+(UTC|GMT)$", " +00:00", anchor_text, flags=re.IGNORECASE)
    anchor_text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", anchor_text)
    try:
        anchor_dt = datetime.fromisoformat(anchor_text)
    except Exception:
        return None
    return _ensure_utc(anchor_dt), float(seconds_per_unit)


def _manual_goes_time_utc_values(raw_values, *, units: str, calendar: str) -> list[datetime] | None:
    if str(calendar or "standard").strip().lower() not in _GOES_STANDARD_TIME_CALENDARS:
        return None

    parsed_reference = _parse_goes_time_reference(units)
    if parsed_reference is None:
        return None
    anchor_dt, seconds_per_unit = parsed_reference

    try:
        arr = np.ma.asarray(raw_values)
        arr = np.ma.squeeze(arr)
        if int(getattr(arr, "ndim", 0)) == 0:
            arr = np.ma.asarray([arr.item()])
        if np.ma.isMaskedArray(arr) and np.any(np.ma.getmaskarray(arr)):
            return None
        offsets = np.asarray(np.ma.getdata(arr), dtype=float).ravel()
    except Exception:
        return None

    if offsets.size == 0 or not np.all(np.isfinite(offsets)):
        return None

    out: list[datetime] = []
    for offset in offsets:
        total_microseconds = int(round(float(offset) * seconds_per_unit * 1_000_000.0))
        out.append(anchor_dt + timedelta(microseconds=total_microseconds))
    return out


def _load_goes_overlay_time_values(time_var, *, nc_module) -> list[datetime]:
    units = str(getattr(time_var, "units", "") or "").strip()
    if not units:
        raise RuntimeError("GOES/XRS file is missing time units.")

    calendar = str(getattr(time_var, "calendar", "standard") or "standard").strip()

    raw_values = time_var[:]
    manual_values = _manual_goes_time_utc_values(raw_values, units=units, calendar=calendar)
    if manual_values is not None:
        return manual_values

    try:
        raw_times = nc_module.num2date(
            raw_values,
            units,
            calendar=calendar,
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not convert GOES/XRS timestamps using units '{units}'.") from exc

    time_values = [_coerce_goes_time_utc(item) for item in np.asarray(raw_times, dtype=object).ravel().tolist()]
    if not time_values:
        raise RuntimeError("GOES/XRS file does not contain any timestamps.")
    return time_values


def _coerce_goes_time_utc(value) -> datetime:
    if isinstance(value, datetime):
        return _ensure_utc(value)

    try:
        year = int(getattr(value, "year"))
        month = int(getattr(value, "month"))
        day = int(getattr(value, "day"))
        hour = int(getattr(value, "hour", 0))
        minute = int(getattr(value, "minute", 0))
        sec_raw = float(getattr(value, "second", 0))
        whole_sec = int(sec_raw)
        micro = int(round((sec_raw - whole_sec) * 1_000_000.0))
        extra_micro = int(getattr(value, "microsecond", 0) or 0)
        micro += extra_micro
        whole_sec += micro // 1_000_000
        micro = micro % 1_000_000
        return datetime(year, month, day, hour, minute, whole_sec, micro, tzinfo=timezone.utc)
    except Exception:
        pass

    text = str(value or "").strip()
    if text:
        try:
            return _ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except Exception:
            pass

    raise RuntimeError(f"Could not convert GOES/XRS timestamp '{value}' to UTC datetime.")


def _looks_like_goes_overlay_var(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    return ("flux" in lowered) or any(token in lowered for token in ("xrsa", "xrsb", "short", "long"))


def _goes_channel_score(name: str, *, channel_key: str) -> int:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return -10_000

    score = 0
    if channel_key == "xrsa":
        if lowered == "xrsa_flux":
            score += 160
        if lowered == "a_flux":
            score += 150
        if any(token in lowered for token in ("xrsa", "short", "0.5", "4.0")):
            score += 60
        if lowered.startswith("a_"):
            score += 15
    else:
        if lowered == "xrsb_flux":
            score += 160
        if lowered == "b_flux":
            score += 150
        if any(token in lowered for token in ("xrsb", "long", "1.0", "8.0")):
            score += 60
        if lowered.startswith("b_"):
            score += 15

    if "flux" in lowered:
        score += 80
    if lowered.endswith("_flux"):
        score += 20

    if any(token in lowered for token in ("flag", "flags", "quality", "count", "counts", "num", "primary", "excluded")):
        score -= 220
    if any(token in lowered for token in ("electron", "electrons", "current")):
        score -= 160

    return score


def _pick_goes_channel(columns: Sequence[str], *, channel_key: str) -> str | None:
    best_name = None
    best_score = -10_000
    for col in columns:
        score = _goes_channel_score(str(col), channel_key=channel_key)
        if score > best_score:
            best_name = str(col)
            best_score = score
    if best_name is None or best_score <= 0:
        return None
    return best_name


def _coerce_goes_numeric_series(values, *, expected_size: int) -> np.ndarray | None:
    arr = np.ma.asarray(values)
    arr = np.ma.squeeze(arr)
    if int(getattr(arr, "ndim", -1)) != 1 or int(arr.shape[0]) != int(expected_size):
        return None
    if np.ma.isMaskedArray(arr):
        try:
            data = np.asarray(np.ma.getdata(arr), dtype=float)
            mask = np.asarray(np.ma.getmaskarray(arr), dtype=bool)
        except Exception:
            return None
        if mask.shape != data.shape:
            return None
        if np.any(mask):
            data = np.array(data, dtype=float, copy=True)
            data[mask] = np.nan
        return np.asarray(data, dtype=float)
    try:
        return np.asarray(arr, dtype=float)
    except Exception:
        return None


def _load_goes_overlay_frame_from_netcdf_paths(
    paths: Sequence[str | Path],
    *,
    cancel_cb: Callable[[], bool] | None = None,
):
    nc, pd = _import_goes_netcdf_dependencies()
    frames = []

    for raw_path in paths:
        _raise_if_goes_overlay_cancelled(cancel_cb)
        path = Path(raw_path).expanduser().resolve()
        with nc.Dataset(str(path)) as ds:
            time_var = ds.variables.get("time")
            if time_var is None:
                raise RuntimeError(f"GOES/XRS file '{path.name}' is missing the time variable.")

            try:
                time_values = _load_goes_overlay_time_values(time_var, nc_module=nc)
            except Exception as exc:
                raise RuntimeError(f"Could not parse GOES/XRS timestamps in '{path.name}'.") from exc
            if not time_values:
                raise RuntimeError(f"GOES/XRS file '{path.name}' does not contain any timestamps.")

            time_index = pd.DatetimeIndex(time_values)
            if time_index.tz is None:
                time_index = time_index.tz_localize("UTC")
            else:
                time_index = time_index.tz_convert("UTC")

            columns: dict[str, np.ndarray] = {}
            expected_size = int(time_index.size)
            for name, var in ds.variables.items():
                if str(name) == "time":
                    continue
                lowered = str(name or "").strip().lower()
                units = str(getattr(var, "units", "") or "").strip().lower().replace(" ", "")
                if not (_looks_like_goes_overlay_var(str(name)) or units in {"w/m2", "w/m^2", "wm-2", "wm^-2"}):
                    continue
                try:
                    series = _coerce_goes_numeric_series(var[:], expected_size=expected_size)
                except Exception:
                    continue
                if series is None:
                    continue
                columns[str(name)] = series

            if not columns:
                raise RuntimeError(f"GOES/XRS file '{path.name}' does not contain usable XRS-A/XRS-B variables.")

            frames.append(pd.DataFrame(columns, index=time_index))

    if not frames:
        raise RuntimeError("Downloaded GOES/XRS files could not be parsed.")

    combined = pd.concat(frames, axis=0, sort=False)
    try:
        combined = combined.sort_index()
    except Exception:
        pass
    return combined


def _load_goes_overlay_frame(
    paths: Sequence[str | Path],
    *,
    cancel_cb: Callable[[], bool] | None = None,
):
    normalized = [Path(p).expanduser().resolve() for p in paths if str(p).strip()]
    netcdf_paths = [
        path
        for path in normalized
        if path.exists() and path.suffix.lower() in {".nc", ".nc4", ".cdf"}
    ]
    if netcdf_paths:
        return _load_goes_overlay_frame_from_netcdf_paths(netcdf_paths, cancel_cb=cancel_cb)

    load_result = load_downloaded(normalized, data_kind=DATA_KIND_TIMESERIES)
    ts = load_result.maps_or_timeseries
    to_dataframe = getattr(ts, "to_dataframe", None)
    if not callable(to_dataframe):
        raise RuntimeError("Loaded GOES/XRS data does not provide a dataframe.")
    return to_dataframe()


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


def _collapse_duplicate_samples(x_seconds: np.ndarray, flux_wm2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xs = np.asarray(x_seconds, dtype=float)
    ys = np.asarray(flux_wm2, dtype=float)
    if xs.size <= 1 or ys.size <= 1:
        return xs, ys

    unique_xs, inverse = np.unique(xs, return_inverse=True)
    if unique_xs.size == xs.size:
        return xs, ys

    merged_flux = np.empty(unique_xs.size, dtype=float)
    for idx in range(unique_xs.size):
        merged_flux[idx] = float(np.nanmedian(ys[inverse == idx]))
    return np.asarray(unique_xs, dtype=float), np.asarray(merged_flux, dtype=float)


def _resample_minute_buckets(x_seconds: np.ndarray, flux_wm2: np.ndarray, cadence_seconds: float = 60.0) -> tuple[np.ndarray, np.ndarray]:
    xs = np.asarray(x_seconds, dtype=float)
    ys = np.asarray(flux_wm2, dtype=float)
    if xs.size <= 2 or ys.size <= 2:
        return xs, ys

    diffs = np.diff(xs)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        return xs, ys
    if float(np.nanmedian(diffs)) >= float(cadence_seconds * 0.75):
        return xs, ys

    bucket_ids = np.rint(xs / float(cadence_seconds)).astype(np.int64)
    unique_ids = np.unique(bucket_ids)
    if unique_ids.size == xs.size:
        return xs, ys

    bucket_xs = np.empty(unique_ids.size, dtype=float)
    bucket_flux = np.empty(unique_ids.size, dtype=float)
    for idx, bucket_id in enumerate(unique_ids):
        mask = bucket_ids == bucket_id
        bucket_xs[idx] = float(np.nanmedian(xs[mask]))
        bucket_flux[idx] = float(np.nanmedian(ys[mask]))
    return np.asarray(bucket_xs, dtype=float), np.asarray(bucket_flux, dtype=float)


def _despike_isolated_samples(flux_wm2: np.ndarray, *, threshold_dex: float = 0.65) -> np.ndarray:
    vals = np.asarray(flux_wm2, dtype=float)
    if vals.size < 3:
        return vals

    log_vals = np.log10(vals)
    out = np.array(log_vals, dtype=float, copy=True)
    neighbor_limit = float(threshold_dex) * 0.6

    for idx in range(1, log_vals.size - 1):
        prev_val = float(log_vals[idx - 1])
        cur_val = float(log_vals[idx])
        next_val = float(log_vals[idx + 1])
        if not np.isfinite(prev_val) or not np.isfinite(cur_val) or not np.isfinite(next_val):
            continue
        if abs(prev_val - next_val) > neighbor_limit:
            continue
        upper = max(prev_val, next_val) + float(threshold_dex)
        lower = min(prev_val, next_val) - float(threshold_dex)
        if cur_val > upper or cur_val < lower:
            out[idx] = 0.5 * (prev_val + next_val)

    return np.asarray(np.power(10.0, out), dtype=float)


def normalize_goes_overlay_curve(x_seconds: np.ndarray, flux_wm2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xs = np.asarray(x_seconds, dtype=float)
    ys = np.asarray(flux_wm2, dtype=float)
    if xs.size == 0 or ys.size == 0:
        return xs, ys

    mask = np.isfinite(xs) & np.isfinite(ys) & (ys > 0.0)
    if not np.any(mask):
        return np.empty(0, dtype=float), np.empty(0, dtype=float)
    xs = np.asarray(xs[mask], dtype=float)
    ys = np.asarray(ys[mask], dtype=float)

    order = np.argsort(xs, kind="mergesort")
    xs = np.asarray(xs[order], dtype=float)
    ys = np.asarray(ys[order], dtype=float)

    xs, ys = _collapse_duplicate_samples(xs, ys)
    xs, ys = _resample_minute_buckets(xs, ys)
    ys = _despike_isolated_samples(ys)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


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
    sorted_x = np.asarray(x_seconds[order], dtype=float)
    sorted_flux = np.asarray(selected_flux[order], dtype=float)
    norm_x, norm_flux = normalize_goes_overlay_curve(sorted_x, sorted_flux)
    if norm_x.size == 0 or norm_flux.size == 0:
        raise RuntimeError("GOES/XRS overlay normalization produced no plottable samples.")
    return GoesOverlaySeries(
        channel_key=str(channel_key),
        display_label=str(GOES_OVERLAY_CHANNEL_LABELS.get(channel_key, channel_key.upper())),
        channel_label=str(column),
        satellite_number=int(satellite_number),
        x_seconds=np.asarray(norm_x, dtype=float),
        flux_wm2=np.asarray(norm_flux, dtype=float),
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
    cancel_cb: Callable[[], bool] | None = None,
) -> GoesOverlayPayload:
    _raise_if_goes_overlay_cancelled(cancel_cb)
    satellites = normalize_goes_satellite_numbers(satellite_numbers)
    if progress_cb is not None:
        sat_text = ", ".join(f"GOES-{sat}" for sat in satellites)
        progress_cb(5, f"Searching {sat_text} XRS archives...")

    payload_candidates: list[GoesOverlayPayload] = []
    search_hits = 0
    fetch_errors: list[str] = []
    n_sats = max(1, len(satellites))

    for idx, sat in enumerate(satellites, start=1):
        _raise_if_goes_overlay_cancelled(cancel_cb)
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
        except GoesOverlayCancelled:
            raise
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: search failed: {exc}")
            continue
        if not search_result.rows:
            continue
        _raise_if_goes_overlay_cancelled(cancel_cb)
        search_hits += len(search_result.rows)

        fetch_progress_cb = None
        if progress_cb is not None:
            def fetch_progress_cb(value, text, sat=sat, start=search_start, end=search_end):
                _raise_if_goes_overlay_cancelled(cancel_cb)
                progress_cb(
                    start + int(max(0, min(100, int(value or 0))) * max(1, end - start) / 100.0),
                    f"GOES-{sat}: {text}",
                )

        try:
            fetch_result = fetch(
                search_result,
                cache_dir,
                selected_rows=list(range(len(search_result.rows))),
                progress_cb=fetch_progress_cb,
            )
        except GoesOverlayCancelled:
            raise
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: fetch failed: {exc}")
            continue
        if fetch_result.errors:
            fetch_errors.extend([f"GOES-{sat}: {msg}" for msg in fetch_result.errors])
        if not fetch_result.paths:
            continue
        _raise_if_goes_overlay_cancelled(cancel_cb)

        if progress_cb is not None:
            progress_cb(search_end, f"Loading GOES-{sat} XRS time series...")

        try:
            frame = _load_goes_overlay_frame(fetch_result.paths, cancel_cb=cancel_cb)
        except GoesOverlayCancelled:
            raise
        except Exception as exc:
            fetch_errors.append(f"GOES-{sat}: load failed: {exc}")
            continue
        _raise_if_goes_overlay_cancelled(cancel_cb)
        try:
            payload = build_goes_overlay_payload(
                frame,
                base_utc=base_utc,
                start_utc=start_utc,
                end_utc=end_utc,
                satellite_number=sat,
            )
        except GoesOverlayCancelled:
            raise
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
