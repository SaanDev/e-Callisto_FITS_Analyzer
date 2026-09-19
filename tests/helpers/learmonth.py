from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from astropy.io import fits


SAMPLE_COUNT = 802


def make_scan_rows(
    start_dt: datetime,
    offsets: list[float],
    *,
    base_value: int = 0,
) -> list[tuple[datetime, np.ndarray]]:
    rows: list[tuple[datetime, np.ndarray]] = []
    for index, offset in enumerate(offsets):
        dt = start_dt + timedelta(seconds=float(offset))
        value = (int(base_value) + index) % 256
        rows.append((dt, np.full(SAMPLE_COUNT, value, dtype=np.uint8)))
    return rows


def build_test_learmonth_srs(
    path: str | Path,
    scan_rows: list[tuple[datetime, np.ndarray]],
    *,
    start_freq1: int = 25,
    end_freq1: int = 75,
    start_freq2: int = 75,
    end_freq2: int = 180,
    nominal_step: int = 3,
    mode_byte: int = 2,
) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not scan_rows:
        raise ValueError("scan_rows must contain at least one Learmonth scan")

    with out_path.open("wb") as handle:
        first_dt = scan_rows[0][0]
        handle.write(_encode_header(first_dt, nominal_step=nominal_step, mode_byte=mode_byte))
        handle.write(int(start_freq1).to_bytes(2, "big"))
        handle.write(int(end_freq1).to_bytes(2, "big"))
        handle.write(b"\x00" * 4)
        handle.write(int(start_freq2).to_bytes(2, "big"))
        handle.write(int(end_freq2).to_bytes(2, "big"))
        handle.write(b"\x00" * 4)

        for index, (_dt, values) in enumerate(scan_rows):
            row = np.asarray(values, dtype=np.uint8).ravel()
            if row.size != SAMPLE_COUNT:
                raise ValueError(f"Learmonth scan row must have {SAMPLE_COUNT} samples.")
            handle.write(row.tobytes())

            if index + 1 >= len(scan_rows):
                continue

            next_dt = scan_rows[index + 1][0]
            handle.write(_encode_header(next_dt, nominal_step=nominal_step, mode_byte=mode_byte))
            handle.write(int(start_freq1).to_bytes(2, "big"))
            handle.write(int(end_freq1).to_bytes(2, "big"))
            handle.write(b"\x00" * 4)
            handle.write(int(start_freq2).to_bytes(2, "big"))
            handle.write(int(end_freq2).to_bytes(2, "big"))
            handle.write(b"\x00" * 4)

    return out_path


def write_test_callisto_fit(
    path: str | Path,
    *,
    data: np.ndarray,
    freqs: np.ndarray,
    time: np.ndarray,
    date_obs: str,
    time_obs: str,
) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    hdr = fits.Header()
    hdr["DATE-OBS"] = str(date_obs)
    hdr["TIME-OBS"] = str(time_obs)
    hdr["DATE-END"] = str(date_obs)
    hdr["TIME-END"] = str(time_obs)

    primary = fits.PrimaryHDU(data=np.asarray(data, dtype=np.uint8), header=hdr)
    time_col = fits.Column(name="Time", array=np.array([np.asarray(time, dtype=float)]), format=f"{len(time)}D")
    freq_col = fits.Column(name="Frequency", array=np.array([np.asarray(freqs, dtype=float)]), format=f"{len(freqs)}D")
    table = fits.BinTableHDU.from_columns([time_col, freq_col])
    fits.HDUList([primary, table]).writeto(out_path, overwrite=True)
    return out_path


def _encode_header(value: datetime, *, nominal_step: int, mode_byte: int) -> bytes:
    return bytes(
        [
            int(value.year) - 2000,
            int(value.month),
            int(value.day),
            int(value.hour),
            int(value.minute),
            int(value.second),
            int(nominal_step),
            int(mode_byte),
        ]
    )
