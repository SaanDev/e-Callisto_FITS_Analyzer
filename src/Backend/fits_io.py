"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
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


@dataclass(frozen=True)
class FitsPreviewResult:
    freqs: np.ndarray
    time: np.ndarray
    header0: fits.Header
    data_shape: tuple[int, int]
    freq_source: str
    time_source: str


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
    # If repeated rows, take first row
    try:
        return arr[0].astype(float)
    except Exception:
        return None


def _get_col(table: Any, desired_names: Sequence[str]) -> Any | None:
    if table is None:
        return None

    # Astropy FITS table: has .names and supports table["COL"]
    names = getattr(table, "names", None)
    if names:
        lowered = {str(n).lower(): str(n) for n in names}
        for dn in desired_names:
            key = lowered.get(str(dn).lower())
            if key is not None:
                try:
                    return table[key]
                except Exception:
                    return None

    # Mapping-like fallback (used by some tests / fakes)
    if isinstance(table, Mapping):
        lowered = {str(k).lower(): k for k in table.keys()}
        for dn in desired_names:
            key = lowered.get(str(dn).lower())
            if key is not None:
                return table.get(key)

    # NumPy structured array fallback
    dtype_names = getattr(getattr(table, "dtype", None), "names", None)
    if dtype_names:
        lowered = {str(n).lower(): str(n) for n in dtype_names}
        for dn in desired_names:
            key = lowered.get(str(dn).lower())
            if key is not None:
                try:
                    return table[key]
                except Exception:
                    return None

    return None


def _is_astropy_table_hdu(hdu: Any) -> bool:
    mod = str(getattr(type(hdu), "__module__", "") or "")
    return mod.startswith("astropy.io.fits")


def _raw_table_from_hdu(hdu: Any) -> np.ndarray | None:
    columns = getattr(hdu, "columns", None)
    get_raw_data = getattr(hdu, "_get_raw_data", None)
    if columns is None or not callable(get_raw_data):
        return None

    try:
        dtype = columns.dtype
        if dtype is None or not getattr(dtype, "names", None):
            return None
        dtype = dtype.newbyteorder(">")
        nrows = int(getattr(hdu, "_nrows", 0) or 0)
        data_offset = int(getattr(hdu, "_data_offset", 0) or 0)
        raw = get_raw_data(nrows, dtype, data_offset)
    except Exception:
        return None

    if raw is None:
        return None
    return np.asarray(raw)


def _table_payload_from_hdu(hdu: Any) -> Any | None:
    raw = _raw_table_from_hdu(hdu)
    if raw is not None:
        return raw

    # Fallback for tests/fakes. Avoid Astropy's lazy .data property, which can
    # construct FITS_rec listeners and crash under repeated threaded access.
    if _is_astropy_table_hdu(hdu):
        return None
    return getattr(hdu, "data", None)


def _axis_from_header(hdr: fits.Header, axis_num: int, length: int) -> np.ndarray | None:
    try:
        crval = hdr.get(f"CRVAL{axis_num}", None)
        cdelt = hdr.get(f"CDELT{axis_num}", None)
        crpix = hdr.get(f"CRPIX{axis_num}", 1.0)
        if crval is None or cdelt is None:
            return None
        i = np.arange(int(length), dtype=float) + 1.0  # FITS is 1-based
        return float(crval) + (i - float(crpix)) * float(cdelt)
    except Exception:
        return None


def _frequency_axis_from_range(hdr: fits.Header, length: int) -> np.ndarray | None:
    try:
        count = int(length)
    except Exception:
        return None
    if count <= 0:
        return None

    try:
        lo = hdr.get("FREQMIN", None)
        hi = hdr.get("FREQMAX", None)
        if lo is None or hi is None:
            return None
        lo = float(lo)
        hi = float(hi)
    except Exception:
        return None

    if not np.isfinite(lo) or not np.isfinite(hi):
        return None
    if count == 1:
        return np.array([max(lo, hi)], dtype=float)

    # CALLISTO spectrograms are typically stored with high frequency in the first row.
    return np.linspace(max(lo, hi), min(lo, hi), count, dtype=float)


def preview_callisto_fits(filepath: str, *, memmap: bool = False) -> FitsPreviewResult:
    """
    Load header and axis metadata without reading the primary image data.
    Returns best-effort frequency/time axes and the declared 2D data shape.
    """
    with fits.open(filepath, memmap=memmap) as hdul:
        header0 = hdul[0].header.copy()
        nx = _safe_axis_length(header0.get("NAXIS1", 0))
        ny = _safe_axis_length(header0.get("NAXIS2", 0))

        freqs = None
        time = None
        freq_source = "missing"
        time_source = "missing"

        if len(hdul) > 1:
            for hdu in hdul[1:]:
                table = _table_payload_from_hdu(hdu)
                if table is None:
                    continue
                if freqs is None:
                    freqs = _col_to_1d(_get_col(table, ("frequency", "freq")))
                    if freqs is not None:
                        freq_source = "table"
                if time is None:
                    time = _col_to_1d(_get_col(table, ("time", "times")))
                    if time is not None:
                        time_source = "table"
                if freqs is not None and time is not None:
                    break

        if ny <= 0 and freqs is not None:
            ny = int(np.asarray(freqs).size)
        if nx <= 0 and time is not None:
            nx = int(np.asarray(time).size)

        if freqs is None and ny > 0:
            freqs = _axis_from_header(header0, 2, ny)
            if freqs is not None:
                freq_source = "header"
        if freqs is None and ny > 0:
            freqs = _frequency_axis_from_range(header0, ny)
            if freqs is not None:
                freq_source = "header-range"
        if time is None and nx > 0:
            time = _axis_from_header(header0, 1, nx)
            if time is not None:
                time_source = "header"

        if freqs is None:
            freqs = np.arange(max(ny, 0), dtype=float)
            freq_source = "index"
        if time is None:
            time = np.arange(max(nx, 0), dtype=float)
            time_source = "index"

        freq_arr = np.asarray(freqs, dtype=float).ravel()
        time_arr = np.asarray(time, dtype=float).ravel()

        if ny <= 0:
            ny = int(freq_arr.size)
        if nx <= 0:
            nx = int(time_arr.size)

        return FitsPreviewResult(
            freqs=freq_arr,
            time=time_arr,
            header0=header0,
            data_shape=(int(ny), int(nx)),
            freq_source=str(freq_source),
            time_source=str(time_source),
        )


def load_callisto_fits(filepath: str, *, memmap: bool = False) -> FitsLoadResult:
    """
    Load a CALLISTO-like FITS file and return:
      - data: float ndarray, 2D, oriented as (freq, time)
      - freqs: 1D float ndarray of length data.shape[0]
      - time: 1D float ndarray of length data.shape[1]
      - header0: primary header copy
    """
    with fits.open(filepath, memmap=memmap) as hdul:
        header0 = hdul[0].header.copy()
        raw = hdul[0].data
        if raw is None:
            raise ValueError("Primary HDU has no data.")

        data = np.array(raw, dtype=float, copy=True)
        data = np.squeeze(data)
        while data.ndim > 2:
            data = data[0]
        if data.ndim != 2:
            raise ValueError(f"Expected 2D data after squeeze, got shape={data.shape}.")

        freqs = None
        time = None

        if len(hdul) > 1:
            for hdu in hdul[1:]:
                table = _table_payload_from_hdu(hdu)
                if table is None:
                    continue
                if freqs is None:
                    freqs = _col_to_1d(_get_col(table, ("frequency", "freq")))
                if time is None:
                    time = _col_to_1d(_get_col(table, ("time", "times")))
                if freqs is not None and time is not None:
                    break

        # Header-based WCS fallback
        if freqs is None:
            freqs = _axis_from_header(header0, 2, data.shape[0])
        if freqs is None:
            freqs = _frequency_axis_from_range(header0, data.shape[0])
        if time is None:
            time = _axis_from_header(header0, 1, data.shape[1])

        # If still missing, fall back to indices
        if freqs is None:
            freqs = np.arange(data.shape[0], dtype=float)
        if time is None:
            time = np.arange(data.shape[1], dtype=float)

        freqs = np.asarray(freqs, dtype=float).ravel()
        time = np.asarray(time, dtype=float).ravel()

        # Resolve swapped axes if needed.
        # Desired: data.shape == (len(freqs), len(time))
        if data.shape == (len(freqs), len(time)):
            pass
        elif data.shape == (len(time), len(freqs)):
            data = data.T
        else:
            # If one axis matches and the other doesn't, regenerate the missing one.
            if len(freqs) == data.shape[1] and len(time) == data.shape[1]:
                # ambiguous but likely both came from same axis; reset to indices
                freqs = np.arange(data.shape[0], dtype=float)
                time = np.arange(data.shape[1], dtype=float)
            else:
                if len(freqs) != data.shape[0] and len(freqs) == data.shape[1]:
                    data = data.T
                if len(freqs) != data.shape[0]:
                    freqs = np.arange(data.shape[0], dtype=float)
                if len(time) != data.shape[1]:
                    time = np.arange(data.shape[1], dtype=float)

        return FitsLoadResult(data=data, freqs=freqs, time=time, header0=header0)


def _safe_axis_length(value: Any) -> int:
    try:
        out = int(value)
    except Exception:
        out = 0
    return max(out, 0)


def extract_ut_start_sec(hdr: fits.Header | None) -> float | None:
    if hdr is None:
        return None
    try:
        t = hdr.get("TIME-OBS", None)
        if not t:
            return None
        hh, mm, ss = str(t).strip().split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        return None


def build_combined_header(
    base_header: fits.Header | None,
    *,
    mode: str,
    sources: Sequence[str] | None,
    data_shape: tuple[int, int],
    freqs: np.ndarray | None,
    time: np.ndarray | None,
) -> fits.Header:
    """
    Create an updated primary header for a combined dataset.
    This does not attempt to fully regenerate a standards-compliant CALLISTO header;
    it preserves the base header and adds combination provenance + updated ranges.
    """
    hdr = base_header.copy() if base_header is not None else fits.Header()

    mode_norm = str(mode or "").strip().lower() or "combined"
    sources = list(sources or [])

    # Provenance
    hdr["COMBINED"] = (True, "Data are combined from multiple FITS files")
    hdr["COMBMETH"] = (mode_norm, "Combine method (time|frequency)")
    hdr["NFILES"] = (len(sources), "Number of source FITS files used")

    # Ranges (safe even if caller passed None)
    try:
        if freqs is not None and len(freqs) > 0:
            hdr["FREQMIN"] = (float(np.nanmin(freqs)), "Min frequency (MHz)")
            hdr["FREQMAX"] = (float(np.nanmax(freqs)), "Max frequency (MHz)")
    except Exception:
        pass

    try:
        if time is not None and len(time) > 0:
            hdr["TMIN"] = (float(np.nanmin(time)), "Min time (s)")
            hdr["TMAX"] = (float(np.nanmax(time)), "Max time (s)")
    except Exception:
        pass

    # Update axes sizes (NAXIS* are structural; astropy will fix on write anyway)
    try:
        ny, nx = int(data_shape[0]), int(data_shape[1])
        hdr["NAXIS"] = 2
        hdr["NAXIS1"] = nx
        hdr["NAXIS2"] = ny
    except Exception:
        pass

    # History (keep it readable, not too verbose)
    try:
        hdr.add_history("Combined by e-CALLISTO FITS Analyzer")
        hdr.add_history(f"Combine method: {mode_norm}")
        if sources:
            hdr.add_history(f"First source: {os.path.basename(sources[0])}")
            hdr.add_history(f"Last source: {os.path.basename(sources[-1])}")
    except Exception:
        pass

    return hdr
