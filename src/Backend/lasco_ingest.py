"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

SOHO/LASCO C2 & C3 coronagraph ingestion.

Many LASCO FITS products in the archive (both the calibrated level-1 and the
near-real-time level-0.5 "raw" frames) omit the ``CUNIT1``/``CUNIT2`` axis-unit
keywords. ``sunpy``'s :class:`~sunpy.map.sources.soho.LASCOMap` reads them
unconditionally::

    return SpatialPair(u.Unit(self.meta.get('cunit1').lower()), ...)

so a missing keyword makes ``self.meta.get('cunit1')`` return ``None`` and the
map construction aborts inside ``_validate_meta`` with::

    AttributeError: 'NoneType' object has no attribute 'lower'

before any image is ever plotted. LASCO axes are always in arcsec, so this
module reads the science HDU directly and backfills the missing unit keywords,
then rebuilds a valid ``LASCOMap``. Well-formed files load through the ordinary
loader untouched.

Pure astropy/numpy with an injectable base loader so it can be unit tested
without network access — the same shape as ``suvi_ingest``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np


# INSTRUME/TELESCOP/DETECTOR tokens that identify a SOHO/LASCO frame.
_LASCO_TOKENS = ("LASCO", "SOHO")


def _header_text(header: Any, *keys: str) -> str:
    """Uppercased concatenation of the given header keys (missing keys ignored)."""
    parts: list[str] = []
    for key in keys:
        try:
            if key in header:
                parts.append(str(header[key]))
        except Exception:
            continue
    return " ".join(parts).upper()


def is_lasco_header(header: Any) -> bool:
    """Return True when a FITS header looks like a SOHO/LASCO coronagraph image."""
    text = _header_text(header, "INSTRUME", "TELESCOP", "DETECTOR")
    return "LASCO" in text or ("SOHO" in text and "C2" in text) or ("SOHO" in text and "C3" in text)


def patch_lasco_header(header: Any) -> Any:
    """Return a copy of a LASCO header with the axis-unit keywords backfilled.

    LASCO frames are always in arcsec; when ``CUNIT1``/``CUNIT2`` are absent (or
    blank) ``LASCOMap.spatial_units`` crashes. Only the missing keywords are
    filled — a file that already declares its units is left untouched. Safe to
    call on any header.
    """
    clean = header.copy()
    for axis in ("1", "2"):
        key = f"CUNIT{axis}"
        try:
            value = clean.get(key)
        except Exception:
            value = None
        if value is None or not str(value).strip():
            clean[key] = "arcsec"
    return clean


def _pick_image_hdu(hdul: Any) -> tuple[Any, Any]:
    """Return ``(data, header)`` for the LASCO science-image HDU.

    LASCO keeps the coronagraph image in the primary HDU; this returns the first
    2-D HDU found (primary first), which is that image.
    """
    for hdu in hdul:
        data = getattr(hdu, "data", None)
        if data is not None and getattr(data, "ndim", 0) == 2:
            return data, hdu.header
    return None, None


def load_lasco_map(path: str | Path, *, base_loader: Callable[..., Any] | None = None) -> Any:
    """Load one SOHO/LASCO FITS file into a sunpy Map, repairing missing units.

    Tries the ordinary sunpy loader first (works for any file that already
    declares ``CUNIT1``/``CUNIT2``); on the missing-unit failure it reads the
    science HDU directly, backfills the arcsec unit keywords and rebuilds the
    map from ``(array, header)``.
    """
    file_path = str(Path(path).expanduser())
    if base_loader is None:
        from sunpy.map import Map as _Map

        base_loader = _Map

    try:
        return base_loader(file_path)
    except Exception:
        # Fall through to the salvage path (missing CUNIT on LASCO level-0.5/1).
        pass

    from astropy.io import fits

    with fits.open(file_path) as hdul:
        data, header = _pick_image_hdu(hdul)
        if data is None:
            # Nothing salvageable — re-raise the original loader error for context.
            return base_loader(file_path)
        clean = patch_lasco_header(header)
        array = np.asarray(data, dtype=np.float32)
    return base_loader((array, clean))


# Each LASCO header is repaired individually, so callers (e.g.
# sunpy_archive.load_downloaded) must map this over the file list one at a time
# rather than handing the loader a whole sequence.
load_lasco_map.per_file = True  # type: ignore[attr-defined]
