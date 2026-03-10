from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from astropy.io import fits


@dataclass(frozen=True)
class FitsLoadResult:
    data: np.ndarray
    freqs: np.ndarray
    time: np.ndarray
    header0: fits.Header
    ut_start_seconds: float | None


def _col_to_1d(col: Any) -> np.ndarray | None:
    if col is None:
        return None
    try:
        arr = np.array(col)
    except Exception:
        return None
    if arr.ndim == 0:
        return None
    if arr.ndim == 1:
        return arr.astype(float)
    try:
        return arr[0].astype(float)
    except Exception:
        return None


def _get_col(table: Any, desired_names: Sequence[str]) -> Any | None:
    if table is None:
        return None
    names = getattr(table, "names", None)
    if names:
        lowered = {str(n).lower(): str(n) for n in names}
        for desired in desired_names:
            key = lowered.get(str(desired).lower())
            if key is not None:
                try:
                    return table[key]
                except Exception:
                    return None
    if isinstance(table, Mapping):
        lowered = {str(k).lower(): k for k in table.keys()}
        for desired in desired_names:
            key = lowered.get(str(desired).lower())
            if key is not None:
                return table.get(key)
    dtype_names = getattr(getattr(table, "dtype", None), "names", None)
    if dtype_names:
        lowered = {str(n).lower(): str(n) for n in dtype_names}
        for desired in desired_names:
            key = lowered.get(str(desired).lower())
            if key is not None:
                try:
                    return table[key]
                except Exception:
                    return None
    return None


def _axis_from_header(hdr: fits.Header, axis_num: int, length: int) -> np.ndarray | None:
    try:
        crval = hdr.get(f"CRVAL{axis_num}")
        cdelt = hdr.get(f"CDELT{axis_num}")
        crpix = hdr.get(f"CRPIX{axis_num}", 1.0)
        if crval is None or cdelt is None:
            return None
        i = np.arange(int(length), dtype=float) + 1.0
        return float(crval) + (i - float(crpix)) * float(cdelt)
    except Exception:
        return None


def extract_ut_start_sec(hdr: fits.Header | None) -> float | None:
    if hdr is None:
        return None
    try:
        raw = hdr.get("TIME-OBS")
        if not raw:
            return None
        hh, mm, ss = str(raw).strip().split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        return None


def load_callisto_fits(filepath: str, *, memmap: bool = False) -> FitsLoadResult:
    with fits.open(filepath, memmap=memmap) as hdul:
        header0 = hdul[0].header.copy()
        raw = hdul[0].data
        if raw is None:
            raise ValueError("Primary HDU has no data.")

        data = np.asarray(raw, dtype=float)
        data = np.squeeze(data)
        while data.ndim > 2:
            data = data[0]
        if data.ndim != 2:
            raise ValueError(f"Expected 2D data after squeeze, got shape={data.shape}.")

        freqs = None
        time = None
        if len(hdul) > 1:
            for hdu in hdul[1:]:
                table = getattr(hdu, "data", None)
                if table is None:
                    continue
                if freqs is None:
                    freqs = _col_to_1d(_get_col(table, ("frequency", "freq")))
                if time is None:
                    time = _col_to_1d(_get_col(table, ("time", "times")))
                if freqs is not None and time is not None:
                    break

        if freqs is None:
            freqs = _axis_from_header(header0, 2, data.shape[0])
        if time is None:
            time = _axis_from_header(header0, 1, data.shape[1])
        if freqs is None:
            freqs = np.arange(data.shape[0], dtype=float)
        if time is None:
            time = np.arange(data.shape[1], dtype=float)

        freqs = np.asarray(freqs, dtype=float).ravel()
        time = np.asarray(time, dtype=float).ravel()

        if data.shape == (len(freqs), len(time)):
            pass
        elif data.shape == (len(time), len(freqs)):
            data = data.T
        else:
            if len(freqs) != data.shape[0] and len(freqs) == data.shape[1]:
                data = data.T
            if len(freqs) != data.shape[0]:
                freqs = np.arange(data.shape[0], dtype=float)
            if len(time) != data.shape[1]:
                time = np.arange(data.shape[1], dtype=float)

        return FitsLoadResult(
            data=np.asarray(data, dtype=np.float32),
            freqs=np.asarray(freqs, dtype=np.float32),
            time=np.asarray(time, dtype=np.float32),
            header0=header0,
            ut_start_seconds=extract_ut_start_sec(header0),
        )


def header_summary(header: fits.Header, *, limit: int = 12) -> dict[str, str]:
    preferred = [
        "OBSERVAT",
        "DATE-OBS",
        "TIME-OBS",
        "INSTRUME",
        "OBJECT",
        "CONTENT",
        "ORIGIN",
        "TELESCOP",
        "BUNIT",
    ]
    out: dict[str, str] = {}
    for key in preferred:
        value = header.get(key)
        if value not in (None, ""):
            out[key] = str(value)
    if len(out) >= limit:
        return dict(list(out.items())[:limit])
    for key, value in header.items():
        if key in out or value in (None, ""):
            continue
        out[str(key)] = str(value)
        if len(out) >= limit:
            break
    return out

