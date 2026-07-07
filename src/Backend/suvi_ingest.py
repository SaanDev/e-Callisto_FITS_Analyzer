"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

GOES/SUVI (Solar Ultraviolet Imager) L1b/L2 ingestion.

NOAA's SUVI L1b FITS files carry a perfectly valid Helioprojective WCS
(``HPLN/HPLT-TAN``, ``arcsec``, 2.5"/pix) but also embed a long string value
using the HEASARC ``CONTINUE`` convention with a continuation card that astropy
cannot parse under strict verification. Because ``sunpy.map.Map`` verifies the
whole header, it aborts with an ``Unparsable card (CONTINUE)`` ``OSError`` before
it ever reaches the image data. (This is the real defect behind the earlier
"SUVI files have no WCS" impression — the WCS is fine; the header just will not
fully parse.)

This module reads the science HDU directly and rebuilds a clean FITS header from
the parseable cards, dropping only the malformed continuation. That is enough for
sunpy to construct a valid ``SUVIMap`` (which also supplies the built-in
``goes-rsuvi{wave}`` colormaps). Well-formed L2 products load through the
ordinary loader untouched.

Pure astropy/numpy with an injectable base loader so it can be unit tested
without network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np


# INSTRUME/TELESCOP/ORIGIN tokens that identify a GOES/SUVI frame.
_SUVI_TOKENS = ("SUVI", "SOLAR ULTRAVIOLET IMAGER", "GOES-R")


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


def is_suvi_header(header: Any) -> bool:
    """Return True when a FITS header looks like a GOES/SUVI image."""
    text = _header_text(header, "INSTRUME", "TELESCOP", "ORIGIN")
    return any(token in text for token in _SUVI_TOKENS)


def sanitize_fits_header(header: Any) -> Any:
    """Return a new astropy ``Header`` containing only the parseable cards.

    SUVI L1b headers embed a malformed ``CONTINUE`` long-string card; parsing the
    whole header (as sunpy's Map reader and astropy's ``verify`` both do) raises
    ``VerifyError``. Copying card-by-card and skipping any card whose keyword or
    value cannot be parsed drops just the offending continuation while keeping the
    WCS and observer metadata intact. Safe to call on any header (a clean header
    is copied verbatim).
    """
    from astropy.io import fits

    clean = fits.Header()
    try:
        cards = list(header.cards)
    except Exception:
        return clean

    for card in cards:
        try:
            keyword = card.keyword
        except Exception:
            continue
        # Blank and CONTINUE cards carry no standalone value; the CONTINUE card is
        # exactly the one that fails to parse, so never copy it directly.
        if keyword in ("", "CONTINUE"):
            continue
        try:
            value = card.value
            comment = card.comment
        except Exception:
            # Malformed card (the bad SUVI CONTINUE parent) — skip it.
            continue
        try:
            if keyword == "HISTORY":
                clean.add_history(value)
            elif keyword == "COMMENT":
                clean.add_comment(value)
            else:
                clean[keyword] = (value, comment)
        except Exception:
            continue
    return clean


def patch_suvi_l1b_header(header: Any) -> Any:
    """Sanitize a SUVI header and backfill WCS/wavelength units if absent.

    The WCS in current L1b files is already valid, so this mostly sanitizes the
    malformed ``CONTINUE`` card. It additionally supplies conservative defaults
    for the few unit keywords sunpy needs when a file omits them: ``WAVEUNIT``
    (SUVIMap multiplies ``WAVELNTH`` by it to pick the colormap) and ``CUNIT1/2``.
    """
    clean = sanitize_fits_header(header)

    if "WAVELNTH" in clean and "WAVEUNIT" not in clean:
        clean["WAVEUNIT"] = "Angstrom"
    for axis in ("1", "2"):
        if f"CTYPE{axis}" in clean and f"CUNIT{axis}" not in clean:
            clean[f"CUNIT{axis}"] = "arcsec"
    return clean


def _pick_image_hdu(hdul: Any) -> tuple[Any, Any]:
    """Return ``(data, header)`` for the SUVI science-image HDU.

    Prefers the first 2-D HDU that also carries a WCS (``CTYPE1``); the L1b product
    keeps the image in the primary HDU and a data-quality mask in a second
    ImageHDU, so the WCS check disambiguates them.
    """
    fallback: Any | None = None
    for hdu in hdul:
        data = getattr(hdu, "data", None)
        if data is None or getattr(data, "ndim", 0) != 2:
            continue
        if fallback is None:
            fallback = hdu
        try:
            if "CTYPE1" in hdu.header:
                return data, hdu.header
        except Exception:
            continue
    if fallback is not None:
        return fallback.data, fallback.header
    return None, None


def load_suvi_map(path: str | Path, *, base_loader: Callable[..., Any] | None = None) -> Any:
    """Load one GOES/SUVI FITS file into a sunpy Map, repairing L1b headers.

    Tries the ordinary sunpy loader first (works for well-formed L2 products and
    any file whose header parses); on the SUVI L1b parse failure it reads the
    science HDU directly and rebuilds a clean header.
    """
    file_path = str(Path(path).expanduser())
    if base_loader is None:
        from sunpy.map import Map as _Map

        base_loader = _Map

    try:
        return base_loader(file_path)
    except Exception:
        # Fall through to the salvage path (malformed CONTINUE card in L1b).
        pass

    from astropy.io import fits

    with fits.open(file_path) as hdul:
        data, header = _pick_image_hdu(hdul)
        if data is None:
            # Nothing salvageable — re-raise the original loader error for context.
            return base_loader(file_path)
        clean = patch_suvi_l1b_header(header)
        array = np.asarray(data, dtype=np.float32)
    return base_loader((array, clean))


# Each SUVI header is repaired individually, so callers (e.g.
# sunpy_archive.load_downloaded) must map this over the file list one at a time
# rather than handing the loader a whole sequence.
load_suvi_map.per_file = True  # type: ignore[attr-defined]
