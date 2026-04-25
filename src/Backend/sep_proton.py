"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import cftime
import netCDF4 as nc
import numpy as np
import requests

BASE_URL_TMPL = (
    "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/"
    "goes/goes{goes_num}/l2/data/sgps-l2-avg5m"
)
CANDIDATE_VERSIONS: tuple[str, ...] = ("v3-0-2", "v3-0-1", "v3-0-0", "v2-0-0", "v1-0-1", "v1-0-0")
AUTO_SPACECRAFT_ORDER: tuple[int, ...] = (19, 18, 17, 16)

CACHE_DIR = tempfile.mkdtemp(prefix="goes_sep_cache_")


@atexit.register
def _cleanup_cache_dir() -> None:
    shutil.rmtree(CACHE_DIR, ignore_errors=True)


class SepProtonDataError(RuntimeError):
    """Raised when GOES SEP proton data cannot be loaded or parsed."""


@dataclass(frozen=True)
class SepProtonRangeData:
    times: tuple[datetime, ...]
    low_flux: tuple[float, ...]
    high_flux: tuple[float, ...]
    low_channel_label: str
    high_channel_label: str
    units: str
    spacecraft: str
    source_files: tuple[str, ...]

    @classmethod
    def empty(
        cls,
        *,
        low_channel_label: str = "P(low)",
        high_channel_label: str = "P(high)",
        units: str = "",
        spacecraft: str = "",
        source_files: tuple[str, ...] = (),
    ) -> "SepProtonRangeData":
        return cls(
            times=(),
            low_flux=(),
            high_flux=(),
            low_channel_label=low_channel_label,
            high_channel_label=high_channel_label,
            units=units,
            spacecraft=spacecraft,
            source_files=tuple(source_files),
        )


@dataclass(frozen=True)
class _DailySliceData:
    times: np.ndarray
    low_flux: np.ndarray
    high_flux: np.ndarray
    low_channel_label: str
    high_channel_label: str
    units: str


def spacecraft_label(goes_num: int) -> str:
    return f"GOES-{int(goes_num)}"


def normalize_spacecraft_selection(spacecraft: int | str | None) -> Optional[int]:
    if spacecraft is None:
        return None
    if isinstance(spacecraft, str):
        token = spacecraft.strip()
        if not token or token.lower() == "auto":
            return None
        if token.upper().startswith("GOES-"):
            token = token.split("-", 1)[1]
        try:
            goes_num = int(token)
        except ValueError as exc:
            raise ValueError(f"Unsupported GOES spacecraft selection: {spacecraft}") from exc
    else:
        goes_num = int(spacecraft)

    if goes_num not in AUTO_SPACECRAFT_ORDER:
        raise ValueError(f"Unsupported GOES spacecraft selection: {spacecraft_label(goes_num)}")
    return goes_num


def iter_utc_dates(start_dt: datetime, end_dt: datetime) -> tuple[date, ...]:
    if end_dt < start_dt:
        return ()
    current = start_dt.date()
    last = end_dt.date()
    days: list[date] = []
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def build_filename(goes_num: int, year: int, month: int, day: int, version: str) -> str:
    return f"sci_sgps-l2-avg5m_g{goes_num:02d}_d{year:04d}{month:02d}{day:02d}_{version}.nc"


def build_url(goes_num: int, year: int, month: int, day: int, version: str) -> str:
    base = BASE_URL_TMPL.format(goes_num=goes_num)
    return f"{base}/{year:04d}/{month:02d}/{build_filename(goes_num, year, month, day, version)}"


def get_local_path(filename: str) -> str:
    return os.path.join(CACHE_DIR, filename)


def _find_time_variable(ds: nc.Dataset):
    if "L2_SciData_TimeStamp" in ds.variables:
        return ds.variables["L2_SciData_TimeStamp"]
    if "time" in ds.variables:
        return ds.variables["time"]
    for key in ds.variables.keys():
        if "time" in key.lower():
            return ds.variables[key]
    raise KeyError("Cannot find a time variable (L2_SciData_TimeStamp/time) in file.")


def _as_float_array(values) -> np.ndarray:
    array = np.array(values)
    if np.ma.isMaskedArray(array):
        array = np.ma.filled(array, np.nan)
    return np.asarray(array, dtype=float)


def pick_sgps_channel_indices(lower_e: np.ndarray, upper_e: np.ndarray) -> tuple[int, int]:
    lower_e = np.asarray(lower_e, dtype=float).ravel()
    upper_e = np.asarray(upper_e, dtype=float).ravel()
    if lower_e.size == 0 or upper_e.size == 0 or lower_e.size != upper_e.size:
        raise ValueError("GOES SGPS energy bounds are missing or inconsistent.")

    midpoint = 0.5 * (lower_e + upper_e)

    def _pick_target(target: float) -> int:
        inside = np.where((lower_e <= target) & (upper_e >= target))[0]
        if inside.size > 0:
            return int(inside[0])
        return int(np.nanargmin(np.abs(midpoint - target)))

    return _pick_target(10.0), _pick_target(100.0)


def channel_label_for_range(idx: int, lower_e: np.ndarray, upper_e: np.ndarray, prefix: str = "P") -> str:
    try:
        lower = float(np.asarray(lower_e).ravel()[idx])
        upper = float(np.asarray(upper_e).ravel()[idx])
        return f"{prefix}{idx + 1} ({lower:g}-{upper:g} MeV)"
    except Exception:
        return f"{prefix}{idx + 1}"


def _parse_time_values(time_var) -> np.ndarray:
    raw_time = time_var[:]
    units = getattr(time_var, "units", None)
    if units:
        time_values = cftime.num2pydate(raw_time, units)
    else:
        base = datetime(2000, 1, 1, 12, 0, 0)
        time_values = [base + timedelta(seconds=int(value)) for value in np.asarray(raw_time, dtype=np.int64)]

    normalized: list[datetime] = []
    for item in time_values:
        if isinstance(item, datetime):
            normalized.append(item.replace(tzinfo=None))
        else:
            normalized.append(
                datetime(
                    item.year,
                    item.month,
                    item.day,
                    item.hour,
                    item.minute,
                    item.second,
                    int(getattr(item, "microsecond", 0) or 0),
                )
            )
    return np.asarray(normalized, dtype=object)


def download_daily_file(
    goes_num: int,
    day: date,
    *,
    session=None,
    progress_cb=None,
) -> str:
    session_obj = session or requests
    last_exc: Exception | None = None
    for version in CANDIDATE_VERSIONS:
        filename = build_filename(goes_num, day.year, day.month, day.day, version)
        url = build_url(goes_num, day.year, day.month, day.day, version)
        local_path = get_local_path(filename)

        if os.path.exists(local_path):
            if progress_cb:
                progress_cb(None, f"Using cached GOES-{goes_num} SEP file: {filename}")
            return local_path

        try:
            if progress_cb:
                progress_cb(None, f"Downloading GOES-{goes_num} SEP file: {filename}")
            response = session_obj.get(url, stream=True, timeout=60)
            response.raise_for_status()

            total = int(response.headers.get("content-length", 0) or 0)
            downloaded = 0
            with open(local_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress = max(5, min(95, 5 + int((downloaded / total) * 90.0)))
                        progress_cb(progress, f"Downloading GOES-{goes_num} SEP data...")
            return local_path
        except Exception as exc:
            last_exc = exc
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except OSError:
                pass

    tried = ", ".join(CANDIDATE_VERSIONS)
    message = (
        f"{spacecraft_label(goes_num)} SGPS file for {day:%Y-%m-%d} is not available on the NOAA archive. "
        f"Tried versions: {tried}."
    )
    if last_exc is None:
        raise FileNotFoundError(message)
    raise FileNotFoundError(message) from last_exc


def load_daily_sgps_slice(local_nc_path: str, start_dt: datetime, end_dt: datetime) -> _DailySliceData:
    with nc.Dataset(local_nc_path) as ds:
        time_var = _find_time_variable(ds)
        times = _parse_time_values(time_var)

        if "AvgDiffProtonFlux" not in ds.variables:
            raise KeyError("Cannot find 'AvgDiffProtonFlux' in file.")
        flux_var = ds.variables["AvgDiffProtonFlux"]
        flux = flux_var[:]
        flux_units = str(getattr(flux_var, "units", "") or "")

        lower_name = "DiffProtonLowerEnergy" if "DiffProtonLowerEnergy" in ds.variables else None
        upper_name = "DiffProtonUpperEnergy" if "DiffProtonUpperEnergy" in ds.variables else None
        lower_e = ds.variables[lower_name][:] if lower_name else None
        upper_e = ds.variables[upper_name][:] if upper_name else None

    flux_array = _as_float_array(flux)
    if flux_array.ndim == 3:
        flux_channels = np.nanmean(flux_array, axis=1)
    elif flux_array.ndim == 2:
        flux_channels = flux_array
    else:
        raise ValueError(f"Unexpected AvgDiffProtonFlux dimensions: {flux_array.shape}")

    if flux_channels.ndim != 2 or flux_channels.shape[1] == 0:
        raise ValueError("GOES SGPS proton flux does not provide usable channel data.")

    if lower_e is not None and upper_e is not None:
        low_bounds = _as_float_array(lower_e)
        high_bounds = _as_float_array(upper_e)
        if low_bounds.ndim == 2 and low_bounds.shape[0] in (1, 2):
            low_bounds = low_bounds[0, :]
            high_bounds = high_bounds[0, :]
        idx_low, idx_high = pick_sgps_channel_indices(low_bounds, high_bounds)
        low_label = channel_label_for_range(idx_low, low_bounds, high_bounds)
        high_label = channel_label_for_range(idx_high, low_bounds, high_bounds)
    else:
        idx_low = 0
        idx_high = max(0, flux_channels.shape[1] - 1)
        low_label = f"P{idx_low + 1}"
        high_label = f"P{idx_high + 1}"

    low_flux = np.asarray(flux_channels[:, idx_low], dtype=float)
    high_flux = np.asarray(flux_channels[:, idx_high], dtype=float)
    mask = (times >= start_dt) & (times <= end_dt)

    return _DailySliceData(
        times=times[mask],
        low_flux=low_flux[mask],
        high_flux=high_flux[mask],
        low_channel_label=low_label,
        high_channel_label=high_label,
        units=flux_units,
    )


def _combine_daily_slices(
    goes_num: int,
    slices: list[_DailySliceData],
    source_files: list[str],
) -> SepProtonRangeData:
    low_label = "P(low)"
    high_label = "P(high)"
    units = ""
    rows: list[tuple[datetime, float, float]] = []

    for slice_data in slices:
        if slice_data.low_channel_label:
            low_label = slice_data.low_channel_label
        if slice_data.high_channel_label:
            high_label = slice_data.high_channel_label
        if slice_data.units:
            units = slice_data.units
        rows.extend(
            (timestamp, float(low_value), float(high_value))
            for timestamp, low_value, high_value in zip(
                slice_data.times,
                slice_data.low_flux,
                slice_data.high_flux,
            )
        )

    if not rows:
        return SepProtonRangeData.empty(
            low_channel_label=low_label,
            high_channel_label=high_label,
            units=units,
            spacecraft=spacecraft_label(goes_num),
            source_files=tuple(source_files),
        )

    rows.sort(key=lambda item: item[0])
    deduped: list[tuple[datetime, float, float]] = []
    previous_timestamp: datetime | None = None
    for row in rows:
        timestamp = row[0]
        if previous_timestamp is not None and timestamp == previous_timestamp:
            continue
        deduped.append(row)
        previous_timestamp = timestamp

    times, low_flux, high_flux = zip(*deduped)
    return SepProtonRangeData(
        times=tuple(times),
        low_flux=tuple(float(value) for value in low_flux),
        high_flux=tuple(float(value) for value in high_flux),
        low_channel_label=low_label,
        high_channel_label=high_label,
        units=units,
        spacecraft=spacecraft_label(goes_num),
        source_files=tuple(source_files),
    )


def _load_spacecraft_range(
    goes_num: int,
    start_dt: datetime,
    end_dt: datetime,
    *,
    session=None,
    progress_cb=None,
) -> SepProtonRangeData:
    days = iter_utc_dates(start_dt, end_dt)
    if not days:
        return SepProtonRangeData.empty(spacecraft=spacecraft_label(goes_num))

    slices: list[_DailySliceData] = []
    source_files: list[str] = []

    for index, day in enumerate(days, start=1):
        if progress_cb:
            progress_cb(None, f"Loading {spacecraft_label(goes_num)} SEP proton data ({index}/{len(days)})...")

        try:
            local_path = download_daily_file(goes_num, day, session=session, progress_cb=progress_cb)
            day_start = start_dt if day == start_dt.date() else datetime(day.year, day.month, day.day, 0, 0, 0)
            day_end = end_dt if day == end_dt.date() else datetime(day.year, day.month, day.day, 23, 59, 59)
            slice_data = load_daily_sgps_slice(local_path, day_start, day_end)
        except FileNotFoundError as exc:
            raise SepProtonDataError(str(exc)) from exc
        except Exception as exc:
            raise SepProtonDataError(
                f"Could not load {spacecraft_label(goes_num)} SEP proton data for {day:%Y-%m-%d}: {exc}"
            ) from exc

        source_files.append(os.path.basename(local_path))
        slices.append(slice_data)

    return _combine_daily_slices(goes_num, slices, source_files)


def load_sep_proton_range(
    start_dt: datetime,
    end_dt: datetime,
    *,
    spacecraft: int | str | None = "auto",
    session=None,
    progress_cb=None,
) -> SepProtonRangeData:
    if end_dt <= start_dt:
        raise ValueError("End time must be after start time.")

    selected = normalize_spacecraft_selection(spacecraft)
    candidates = AUTO_SPACECRAFT_ORDER if selected is None else (selected,)
    errors: list[str] = []

    for goes_num in candidates:
        try:
            result = _load_spacecraft_range(goes_num, start_dt, end_dt, session=session, progress_cb=progress_cb)
            if progress_cb:
                progress_cb(100, f"Loaded {len(result.times)} SEP proton samples from {result.spacecraft}.")
            return result
        except SepProtonDataError as exc:
            if selected is not None:
                raise
            errors.append(f"{spacecraft_label(goes_num)}: {exc}")

    details = "\n".join(errors) if errors else "No candidate GOES spacecraft could satisfy the request."
    raise SepProtonDataError(
        "Could not load GOES SEP proton flux data for the selected UTC range.\n"
        f"Details:\n{details}"
    )
