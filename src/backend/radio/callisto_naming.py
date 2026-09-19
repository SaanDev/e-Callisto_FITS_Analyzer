"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Canonical parsing of e-CALLISTO archive filenames.

The archive publishes files as ``STATION_YYYYMMDD_HHMMSS[_HHMMSS]_FOCUS.fit[.gz]``
where the station itself may contain underscores (``Malaysia_Banting``).  This
module is the single source of truth for that layout so both the downloader UI
and the on-disk cache derive station/date/focus code the same way.
"""

from __future__ import annotations

from datetime import datetime
import os
import re

_FITS_SUFFIXES = (".fit.gz", ".fits.gz", ".fit", ".fits")
_DATE_RE = re.compile(r"^\d{8}$")
_TIME_RE = re.compile(r"^\d{6}$")


def _strip_fits_suffix(filename: str) -> str:
    stem = os.path.basename(str(filename or "")).strip()
    for suffix in _FITS_SUFFIXES:
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def parse_callisto_archive_filename(filename: str) -> tuple[str, datetime, str]:
    """Return station, UTC timestamp, receiver id parsed from a CALLISTO archive filename."""
    base = os.path.basename(str(filename or "")).strip()
    stem = _strip_fits_suffix(base)
    parts = stem.split("_")

    for idx in range(1, len(parts) - 2):
        if _DATE_RE.match(parts[idx]) and idx + 1 < len(parts) and _TIME_RE.match(parts[idx + 1]):
            station = "_".join(parts[:idx]).strip()
            if not station:
                break
            try:
                observed = datetime.strptime(parts[idx] + parts[idx + 1], "%Y%m%d%H%M%S")
            except ValueError as exc:
                raise ValueError(f"Invalid CALLISTO timestamp in filename: {base}") from exc
            receiver_id = parts[-1].strip()
            if not receiver_id:
                raise ValueError(f"Missing receiver id in CALLISTO filename: {base}")
            return station, observed, receiver_id

    raise ValueError(f"Invalid CALLISTO filename format: {base}")
