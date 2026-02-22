"""
e-CALLISTO FITS Analyzer
Version 2.1
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

        # Header-based WCS fallback
        if freqs is None:
            freqs = _axis_from_header(header0, 2, data.shape[0])
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
