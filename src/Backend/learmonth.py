"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests
from astropy.io import fits

from src.Backend.frequency_axis import orient_frequency_rows


BASE_URL = "https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/raw/"
DEFAULT_RECEIVER_ID = "01"
HEADER_SIZE = 8
CHANNEL_HEADER_SIZE = 8
HEADER_BLOCK_SIZE = HEADER_SIZE + (2 * CHANNEL_HEADER_SIZE)
SAMPLE_COUNT = 802
FULL_CHUNK_SCANS = 300
OBS_LATITUDE = -22.24111
OBS_LONGITUDE = 114.0806
OBS_ALTITUDE_M = 594.0


class LearmonthArchiveError(RuntimeError):
    """Raised when the Learmonth archive cannot be accessed or parsed."""


class LearmonthNotFoundError(LearmonthArchiveError):
    """Raised when the requested Learmonth day file does not exist."""


@dataclass(frozen=True)
class LearmonthChunk:
    index: int
    start_dt: datetime
    end_dt: datetime
    scan_count: int
    offset_start: int
    offset_end: int
    is_partial: bool


def build_learmonth_filename(day: date) -> str:
    return f"LM{day:%y%m%d}.srs"


def resolve_learmonth_url(day: date) -> str:
    return f"{BASE_URL}{day:%y}/{build_learmonth_filename(day)}"


def resolve_learmonth_cache_path(day: date, cache_dir: str | os.PathLike[str]) -> Path:
    cache_root = Path(cache_dir).expanduser()
    return cache_root / f"{day:%y}" / build_learmonth_filename(day)


def learmonth_fit_filename(chunk: LearmonthChunk, *, receiver_id: str = DEFAULT_RECEIVER_ID) -> str:
    return f"LEARMONTH_{chunk.start_dt:%Y%m%d}_{chunk.start_dt:%H%M%S}_{receiver_id}.fit"


def download_learmonth_day(
    day: date,
    cache_dir: str | os.PathLike[str],
    *,
    session: Any | None = None,
) -> str:
    destination = resolve_learmonth_cache_path(day, cache_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and destination.stat().st_size > 0:
        return str(destination)

    client = session or requests
    url = resolve_learmonth_url(day)
    temp_path = destination.with_suffix(f"{destination.suffix}.part")

    try:
        with client.get(url, stream=True, timeout=45) as response:
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 404:
                raise LearmonthNotFoundError(f"No Learmonth archive data were found for {day.isoformat()}.")
            if status >= 400:
                raise LearmonthArchiveError(f"Learmonth archive request failed with HTTP {status}.")

            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        handle.write(chunk)
    except LearmonthArchiveError:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise LearmonthArchiveError(f"Could not download Learmonth archive data: {exc}") from exc

    if not temp_path.exists() or temp_path.stat().st_size <= 0:
        raise LearmonthArchiveError("Learmonth archive download completed without writing data.")

    os.replace(temp_path, destination)
    return str(destination)


def list_learmonth_chunks(day_path: str | os.PathLike[str]) -> list[LearmonthChunk]:
    path = Path(day_path)
    if not path.exists():
        raise LearmonthArchiveError(f"Learmonth archive file does not exist: {path}")

    chunks: list[LearmonthChunk] = []

    with path.open("rb") as handle:
        initial_header, _ = _read_prelude(handle)
        nominal_step = _nominal_step_seconds(initial_header)

        current_header = initial_header
        current_dt = _header_to_datetime(current_header)
        chunk_start_dt = current_dt
        chunk_offset_start = handle.tell()
        chunk_last_sample_end = chunk_offset_start
        chunk_index = 0
        chunk_scan_count = 0
        previous_scan_dt: datetime | None = None
        last_positive_step = nominal_step

        while True:
            sample = handle.read(SAMPLE_COUNT)
            if not sample:
                break
            if len(sample) != SAMPLE_COUNT:
                raise LearmonthArchiveError("Learmonth archive ended unexpectedly while reading scan samples.")

            chunk_scan_count += 1
            chunk_last_sample_end = handle.tell()

            next_header = handle.read(HEADER_SIZE)
            if not next_header:
                end_dt = _infer_chunk_end(current_dt, previous_scan_dt, last_positive_step, nominal_step)
                chunks.append(
                    LearmonthChunk(
                        index=chunk_index,
                        start_dt=chunk_start_dt,
                        end_dt=end_dt,
                        scan_count=chunk_scan_count,
                        offset_start=chunk_offset_start,
                        offset_end=chunk_last_sample_end,
                        is_partial=chunk_scan_count < FULL_CHUNK_SCANS,
                    )
                )
                break

            if len(next_header) != HEADER_SIZE:
                raise LearmonthArchiveError("Learmonth archive ended unexpectedly while reading the next scan header.")

            _read_exact(handle, 2 * CHANNEL_HEADER_SIZE, "Learmonth channel headers")
            next_dt = _header_to_datetime(next_header)
            step_seconds = (next_dt - current_dt).total_seconds()
            if step_seconds > 0:
                last_positive_step = float(step_seconds)

            previous_scan_dt = current_dt
            current_dt = next_dt

            if chunk_scan_count >= FULL_CHUNK_SCANS:
                chunks.append(
                    LearmonthChunk(
                        index=chunk_index,
                        start_dt=chunk_start_dt,
                        end_dt=next_dt,
                        scan_count=chunk_scan_count,
                        offset_start=chunk_offset_start,
                        offset_end=chunk_last_sample_end,
                        is_partial=False,
                    )
                )
                chunk_index += 1
                chunk_start_dt = next_dt
                chunk_offset_start = handle.tell()
                chunk_scan_count = 0
                previous_scan_dt = None
                last_positive_step = nominal_step

    return chunks


def write_learmonth_chunk_fit(
    day_path: str | os.PathLike[str],
    chunk: LearmonthChunk,
    out_path: str | os.PathLike[str],
) -> str:
    path = Path(day_path)
    if not path.exists():
        raise LearmonthArchiveError(f"Learmonth archive file does not exist: {path}")

    _initial_header, file_meta = _read_file_header(path)
    scan_times, data = _read_chunk_data(path, chunk)

    if not scan_times or data.size == 0:
        raise LearmonthArchiveError("Learmonth chunk did not contain any scan samples.")

    times = _build_time_axis(scan_times)
    freqs = _build_frequency_axis(file_meta["start_freq1"], file_meta["end_freq2"])
    data, freqs = orient_frequency_rows(data, freqs)
    header = _build_primary_header(
        chunk=chunk,
        day_path=path,
        data=data,
        freqs=freqs,
        times=times,
        nominal_step=float(file_meta["nominal_step"]),
    )

    primary = fits.PrimaryHDU(data=data, header=header)
    time_col = fits.Column(name="Time", array=np.array([times], dtype=np.float64), format=f"{len(times)}D")
    freq_col = fits.Column(name="Frequency", array=np.array([freqs], dtype=np.float64), format=f"{len(freqs)}D")
    table = fits.BinTableHDU.from_columns([time_col, freq_col])

    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    hdul = fits.HDUList([primary, table])
    hdul.writeto(destination, overwrite=True)
    return str(destination)


def _read_file_header(path: str | os.PathLike[str]) -> tuple[bytes, dict[str, Any]]:
    with Path(path).open("rb") as handle:
        return _read_prelude(handle)


def _read_prelude(handle) -> tuple[bytes, dict[str, Any]]:
    initial_header = _read_exact(handle, HEADER_SIZE, "Learmonth initial header")

    start_freq1 = int.from_bytes(_read_exact(handle, 2, "Learmonth channel-1 start frequency"), "big")
    _end_freq1 = int.from_bytes(_read_exact(handle, 2, "Learmonth channel-1 end frequency"), "big")
    _read_exact(handle, 4, "Learmonth channel-1 trailer")

    _start_freq2 = int.from_bytes(_read_exact(handle, 2, "Learmonth channel-2 start frequency"), "big")
    end_freq2 = int.from_bytes(_read_exact(handle, 2, "Learmonth channel-2 end frequency"), "big")
    _read_exact(handle, 4, "Learmonth channel-2 trailer")

    return initial_header, {
        "start_freq1": start_freq1,
        "end_freq2": end_freq2,
        "nominal_step": _nominal_step_seconds(initial_header),
    }


def _read_chunk_data(path: Path, chunk: LearmonthChunk) -> tuple[list[datetime], np.ndarray]:
    with path.open("rb") as handle:
        handle.seek(chunk.offset_start)
        current_dt = chunk.start_dt
        scan_times: list[datetime] = []
        rows: list[np.ndarray] = []

        for scan_index in range(int(chunk.scan_count)):
            sample = _read_exact(handle, SAMPLE_COUNT, "Learmonth scan samples")
            rows.append(np.frombuffer(sample, dtype=np.uint8))
            scan_times.append(current_dt)

            if scan_index >= int(chunk.scan_count) - 1:
                continue

            next_header = _read_exact(handle, HEADER_SIZE, "Learmonth scan header")
            _read_exact(handle, 2 * CHANNEL_HEADER_SIZE, "Learmonth channel headers")
            current_dt = _header_to_datetime(next_header)

    if not rows:
        return [], np.empty((SAMPLE_COUNT, 0), dtype=np.uint8)

    data = np.stack(rows, axis=0).T.astype(np.uint8, copy=False)
    return scan_times, data


def _build_frequency_axis(start_freq1: int, end_freq2: int) -> np.ndarray:
    step = (float(end_freq2) - float(start_freq1)) / float(SAMPLE_COUNT)
    return np.array([float(start_freq1) + (idx * step) for idx in range(SAMPLE_COUNT)], dtype=float)


def _build_time_axis(scan_times: list[datetime]) -> np.ndarray:
    start_dt = scan_times[0]
    return np.array([(dt - start_dt).total_seconds() for dt in scan_times], dtype=float)


def _build_primary_header(
    *,
    chunk: LearmonthChunk,
    day_path: Path,
    data: np.ndarray,
    freqs: np.ndarray,
    times: np.ndarray,
    nominal_step: float,
) -> fits.Header:
    start_dt = chunk.start_dt
    end_dt = chunk.end_dt
    hdr = fits.Header()

    hdr["DATE"] = (start_dt.strftime("%Y-%m-%d"), "Date of observation")
    hdr["CONTENT"] = ("Learmonth radio flux density", "Observation title")
    hdr["INSTRUME"] = ("LEARMONTH", "Name of the instrument")
    hdr["OBJECT"] = ("Sun", "Observed object")
    hdr["DATE-OBS"] = (start_dt.strftime("%Y/%m/%d"), "Date observation starts")
    hdr["TIME-OBS"] = (start_dt.strftime("%H:%M:%S"), "Time observation starts")
    hdr["DATE-END"] = (end_dt.strftime("%Y/%m/%d"), "Date observation ends")
    hdr["TIME-END"] = (end_dt.strftime("%H:%M:%S"), "Time observation ends")
    hdr["ORIGIN"] = ("e-CALLISTO FITS Analyzer", "File creator")
    hdr["BZERO"] = (0.0, "Scaling offset")
    hdr["BSCALE"] = (1.0, "Scaling factor")
    hdr["BUNIT"] = ("sfu", "Z-axis units")
    hdr["DATAMAX"] = (int(np.nanmax(data)), "Maximum data value")
    hdr["DATAMIN"] = (int(np.nanmin(data)), "Minimum data value")
    hdr["CRVAL1"] = (_seconds_of_day(start_dt), "Value on axis 1 [sec of day]")
    hdr["CRPIX1"] = (1, "Reference pixel of axis 1")
    hdr["CTYPE1"] = ("TIME [UT]", "Title of axis 1")
    hdr["CDELT1"] = (_time_step_seconds(times, nominal_step), "Median step in time axis")
    hdr["CRVAL2"] = (float(freqs[0]), "Value on axis 2 [MHz]")
    hdr["CRPIX2"] = (1, "Reference pixel of axis 2")
    hdr["CTYPE2"] = ("Frequency [MHz]", "Title of axis 2")
    hdr["CDELT2"] = (_frequency_step_mhz(freqs), "Median step in frequency axis")
    hdr["FREQMIN"] = (float(np.nanmin(freqs)), "Minimum frequency [MHz]")
    hdr["FREQMAX"] = (float(np.nanmax(freqs)), "Maximum frequency [MHz]")
    hdr["OBS_LAT"] = (OBS_LATITUDE, "Observatory latitude in degree")
    hdr["OBS_LAC"] = ("S", "Observatory latitude code {N, S}")
    hdr["OBS_LON"] = (OBS_LONGITUDE, "Observatory longitude in degree")
    hdr["OBS_LOC"] = ("E", "Observatory longitude code {E, W}")
    hdr["OBS_ALT"] = (OBS_ALTITUDE_M, "Observatory altitude in meter")
    hdr["RAWFILE"] = (day_path.name, "Learmonth source archive file")
    hdr["CHUNKIDX"] = (int(chunk.index), "Learmonth chunk index")
    hdr["PARTIAL"] = (bool(chunk.is_partial), "Chunk ended before 300 scans")
    return hdr


def _read_exact(handle, count: int, label: str) -> bytes:
    data = handle.read(count)
    if len(data) != count:
        raise LearmonthArchiveError(f"{label} could not be read completely from the Learmonth archive file.")
    return data


def _header_to_datetime(header: bytes) -> datetime:
    if len(header) < 6:
        raise LearmonthArchiveError("Learmonth scan header is too short to decode a timestamp.")

    year, month, day, hour, minute, second = [int(value) for value in header[:6]]
    try:
        return datetime(2000 + year, month, day, hour, minute, second)
    except ValueError as exc:
        raise LearmonthArchiveError(f"Invalid Learmonth timestamp in archive header: {list(header[:6])}") from exc


def _nominal_step_seconds(header: bytes) -> float:
    try:
        value = int(header[6])
    except Exception:
        value = 0
    if value <= 0:
        return 3.0
    return float(value)


def _infer_chunk_end(
    current_dt: datetime,
    previous_scan_dt: datetime | None,
    last_positive_step: float,
    nominal_step: float,
) -> datetime:
    step = nominal_step if nominal_step > 0 else last_positive_step
    if step <= 0 and previous_scan_dt is not None:
        inferred = (current_dt - previous_scan_dt).total_seconds()
        if inferred > 0:
            step = float(inferred)
    return current_dt + timedelta(seconds=float(step or 3.0))


def _time_step_seconds(times: np.ndarray, nominal_step: float) -> float:
    if len(times) > 1:
        diffs = np.diff(np.asarray(times, dtype=float))
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size:
            return float(np.median(diffs))
    return float(nominal_step or 3.0)


def _frequency_step_mhz(freqs: np.ndarray) -> float:
    if len(freqs) > 1:
        diffs = np.diff(np.asarray(freqs, dtype=float))
        diffs = diffs[np.isfinite(diffs)]
        if diffs.size:
            return float(np.median(diffs))
    return 0.0


def _seconds_of_day(value: datetime) -> float:
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    return float((value - midnight).total_seconds())
