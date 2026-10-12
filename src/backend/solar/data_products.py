"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Download products for the Solar Image Analysis window (src/backend/solar/data_products.py).

A search used to mean one thing: the full-resolution FITS the VSO serves. For a
quick look at an event that is far more data than the question needs, so the
window offers three products for the selected observable:

* **FITS · full resolution** — the archive's own files, as before.
* **FITS · reduced** — smaller FITS. For SDO/AIA that is JSOC's *synoptic*
  archive (1024², level 1.5, no e-mail needed) or a JSOC server-side binning or
  cutout (needs the registered JSOC e-mail). HMI has only the JSOC modes. The
  other missions publish no reduced FITS.
* **JP2 · quicklook** — Helioviewer's JPEG2000 browse images. A few hundred
  kilobytes instead of several megabytes, listed in about a second, and carrying
  the original FITS header — but their pixels are 8-bit *display* values, so
  they are for looking, not for photometry.

This module only answers questions — which products and frame sizes an
observable has, which archive a search runs against, and how big a frame is —
so it stays free of Qt and network code. Observables use the analysis window's
userData tuples: ``("AIA", 193.0)``, ``("HMI", "magnetogram")``,
``("EIT", 195.0)``, ``("LASCO", "C2")``,
``("SECCHI", ("STEREO_A", "COR2", None))`` and ``("SUVI", 171.0)``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.backend.solar.helioviewer_jp2 import JP2Source
from src.backend.solar.jsoc_client import SIZE_BIN2, SIZE_BIN4, SIZE_CUTOUT

# --- Products -------------------------------------------------------------------

PRODUCT_FITS_FULL = "fits_full"
PRODUCT_FITS_REDUCED = "fits_reduced"
PRODUCT_JP2 = "jp2"
PRODUCTS: tuple[str, ...] = (PRODUCT_FITS_FULL, PRODUCT_FITS_REDUCED, PRODUCT_JP2)

PRODUCT_LABELS = {
    PRODUCT_FITS_FULL: "FITS · full resolution",
    PRODUCT_FITS_REDUCED: "FITS · reduced size",
    PRODUCT_JP2: "JP2 · quicklook (Helioviewer)",
}

# --- Archives a search runs against --------------------------------------------

#: SunPy Fido (VSO / NOAA). Full FITS; JSOC binning and cutouts reuse these rows.
ARCHIVE_SUNPY = "sunpy"
#: JSOC's 1024² AIA synoptic FITS, plain HTTP, no e-mail.
ARCHIVE_AIA_SYNOPTIC = "aia_synoptic"
#: Helioviewer's JPEG2000 archive.
ARCHIVE_HELIOVIEWER_JP2 = "helioviewer_jp2"
ARCHIVES: tuple[str, ...] = (ARCHIVE_SUNPY, ARCHIVE_AIA_SYNOPTIC, ARCHIVE_HELIOVIEWER_JP2)

# --- Frame sizes ----------------------------------------------------------------

#: AIA synoptic 1024² FITS. The other reduced sizes are jsoc_client's.
SIZE_SYNOPTIC = "synoptic"
#: Sizes made server-side by a JSOC export, which needs the notify e-mail.
JSOC_SIZES: tuple[str, ...] = (SIZE_BIN2, SIZE_BIN4, SIZE_CUTOUT)
REDUCED_SIZES: tuple[str, ...] = (SIZE_SYNOPTIC, *JSOC_SIZES)

#: Passbands in the AIA synoptic archive (1700 Å is not produced there).
AIA_SYNOPTIC_WAVELENGTHS: tuple[int, ...] = (94, 131, 171, 193, 211, 304, 335, 1600, 4500)

#: Largest side a JP2 frame is decoded to; 0 keeps the native size. JPEG2000
#: decodes at ½, ¼ … of its size almost for free, which keeps a long AIA
#: sequence (4096² natively) from filling memory.
JP2_MAX_PX_OPTIONS: tuple[int, ...] = (0, 2048, 1024)
DEFAULT_JP2_MAX_PX = 2048
JP2_MAX_PX_LABELS = {0: "Native size", 2048: "Up to 2048 px", 1024: "Up to 1024 px"}


def normalize_jp2_max_px(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_JP2_MAX_PX
    return number if number in JP2_MAX_PX_OPTIONS else DEFAULT_JP2_MAX_PX


# --- Helioviewer JP2 sources ------------------------------------------------------

# sourceIds and first/last days from Helioviewer's getDataSources (checked
# Oct 2026). STEREO-B ended in 2014; MDI and SWAP have no observable here.
_STEREO_B_END = date(2014, 9, 27)


def _source(key: str, label: str, source_id: int, instrument: str, detector: str,
            spacecraft: str, first: date, last: date | None = None) -> JP2Source:
    return JP2Source(key, label, source_id, instrument, detector, spacecraft, first, last)


def _build_jp2_sources() -> dict[tuple[str, Any], JP2Source]:
    table: dict[tuple[str, Any], JP2Source] = {}
    aia_first = date(2010, 6, 2)
    for offset, wavelength in enumerate((94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500)):
        first = date(2010, 6, 23) if wavelength == 1700 else aia_first
        table[("AIA", wavelength)] = _source(
            f"AIA {wavelength}", f"SDO/AIA {wavelength} Å", 8 + offset, "AIA", "AIA", "SDO", first
        )
    table[("HMI", "continuum")] = _source(
        "HMI continuum", "SDO/HMI continuum", 18, "HMI", "HMI", "SDO", date(2010, 12, 6)
    )
    table[("HMI", "magnetogram")] = _source(
        "HMI magnetogram", "SDO/HMI magnetogram", 19, "HMI", "HMI", "SDO", date(2010, 12, 6)
    )
    for offset, wavelength in enumerate((171, 195, 284, 304)):
        table[("EIT", wavelength)] = _source(
            f"EIT {wavelength}", f"SOHO/EIT {wavelength} Å", offset, "EIT", "EIT", "SOHO", date(1996, 1, 15)
        )
    table[("LASCO", "C2")] = _source("LASCO C2", "SOHO/LASCO C2", 4, "LASCO", "C2", "SOHO", date(1997, 2, 21))
    table[("LASCO", "C3")] = _source("LASCO C3", "SOHO/LASCO C3", 5, "LASCO", "C3", "SOHO", date(1997, 2, 20))
    for craft, letter, base, last in (("STEREO_A", "A", 20, None), ("STEREO_B", "B", 24, _STEREO_B_END)):
        for offset, wavelength in enumerate((171, 195, 284, 304)):
            first = date(2006, 11, 11) if wavelength in (171, 195) else date(2006, 12, 4)
            table[("SECCHI", (craft, "EUVI", wavelength))] = _source(
                f"EUVI-{letter} {wavelength}", f"STEREO-{letter} EUVI {wavelength} Å", base + offset,
                "SECCHI", "EUVI", f"STEREO-{letter}", first, last,
            )
    table[("SECCHI", ("STEREO_A", "COR1", None))] = _source(
        "COR1-A", "STEREO-A COR1", 28, "SECCHI", "COR1", "STEREO-A", date(2006, 12, 4)
    )
    table[("SECCHI", ("STEREO_A", "COR2", None))] = _source(
        "COR2-A", "STEREO-A COR2", 29, "SECCHI", "COR2", "STEREO-A", date(2006, 12, 29)
    )
    table[("SECCHI", ("STEREO_B", "COR1", None))] = _source(
        "COR1-B", "STEREO-B COR1", 30, "SECCHI", "COR1", "STEREO-B", date(2006, 12, 12), _STEREO_B_END
    )
    table[("SECCHI", ("STEREO_B", "COR2", None))] = _source(
        "COR2-B", "STEREO-B COR2", 31, "SECCHI", "COR2", "STEREO-B", date(2007, 1, 10), _STEREO_B_END
    )
    return table


_JP2_SOURCES = _build_jp2_sources()


def _whole(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def observable_key(instrument: str, value: Any) -> tuple[str, Any]:
    """A hashable, normalised form of an observable's userData."""
    inst = str(instrument or "").strip().upper()
    if inst in ("AIA", "EIT", "SUVI"):
        return inst, _whole(value)
    if inst == "HMI":
        return inst, str(value or "").strip().lower()
    if inst == "LASCO":
        return inst, str(value or "").strip().upper()
    if inst == "SECCHI":
        try:
            craft, detector, wavelength = value
        except (TypeError, ValueError):
            return inst, None
        wl = _whole(wavelength) if wavelength not in (None, "") else None
        return inst, (str(craft or "").strip().upper(), str(detector or "").strip().upper(), wl)
    return inst, value


def jp2_source_for(instrument: str, value: Any) -> JP2Source | None:
    """Helioviewer's JP2 source for an observable, or ``None`` when it has none.

    SUVI, STEREO HI1/HI2 and the HMI Dopplergram are not in Helioviewer.
    """
    return _JP2_SOURCES.get(observable_key(instrument, value))


# --- What an observable offers ----------------------------------------------------


def reduced_sizes_for(instrument: str, value: Any) -> tuple[str, ...]:
    """Reduced FITS sizes an observable can be downloaded at, best first."""
    inst, key = observable_key(instrument, value)
    if inst == "AIA":
        if key in AIA_SYNOPTIC_WAVELENGTHS:
            return REDUCED_SIZES
        return JSOC_SIZES
    if inst == "HMI":
        return JSOC_SIZES
    return ()


def products_for(instrument: str, value: Any) -> tuple[str, ...]:
    """The products available for an observable, in menu order."""
    products = [PRODUCT_FITS_FULL]
    if reduced_sizes_for(instrument, value):
        products.append(PRODUCT_FITS_REDUCED)
    if jp2_source_for(instrument, value) is not None:
        products.append(PRODUCT_JP2)
    return tuple(products)


def unavailable_reason(product: str, instrument: str, value: Any) -> str:
    """Why ``product`` is greyed out for an observable ('' when it is offered)."""
    if product in products_for(instrument, value):
        return ""
    inst = str(instrument or "").strip().upper()
    if product == PRODUCT_FITS_REDUCED:
        return f"{inst or 'This observable'} has no reduced-size FITS product; only SDO/AIA and HMI do."
    if product == PRODUCT_JP2:
        return f"Helioviewer has no JP2 quicklook archive for this {inst or 'observable'} observable."
    return ""


def archive_for(product: str, size_mode: str | None = None) -> str:
    """The archive a search for ``product`` at ``size_mode`` runs against."""
    if product == PRODUCT_JP2:
        return ARCHIVE_HELIOVIEWER_JP2
    if product == PRODUCT_FITS_REDUCED and size_mode == SIZE_SYNOPTIC:
        return ARCHIVE_AIA_SYNOPTIC
    return ARCHIVE_SUNPY


def is_direct_archive(archive: str | None) -> bool:
    """Whether a search result's files are plain URLs (JP2 / synoptic)."""
    return archive in (ARCHIVE_AIA_SYNOPTIC, ARCHIVE_HELIOVIEWER_JP2)


# --- Sizes ------------------------------------------------------------------------

# Measured JP2 sizes (Oct 2026). Helioviewer compresses each source to its own
# quality target, so the coronagraph and EIT files are tiny.
_JP2_BYTES = {
    "AIA": 1_080_000,
    "HMI": 1_055_000,
    "EIT": 70_000,
    "LASCO C2": 330_000,
    "LASCO C3": 500_000,
    "EUVI": 271_000,
    "COR1": 75_000,
    "COR2": 271_000,
}
#: Size of one AIA synoptic FITS (Rice-compressed 1024²).
SYNOPTIC_FRAME_BYTES = 1_170_000


def jp2_frame_bytes(source: JP2Source | None) -> int:
    if source is None:
        return _JP2_BYTES["AIA"]
    if source.instrument == "LASCO":
        return _JP2_BYTES.get(f"LASCO {source.detector}", _JP2_BYTES["LASCO C2"])
    if source.instrument == "SECCHI":
        return _JP2_BYTES.get(source.detector, _JP2_BYTES["COR2"])
    return _JP2_BYTES.get(source.instrument, _JP2_BYTES["AIA"])


def frame_bytes_estimate(product: str, size_mode: str | None, instrument: str, value: Any) -> int | None:
    """Expected size of one frame, or ``None`` to use the JSOC estimate."""
    if product == PRODUCT_JP2:
        return jp2_frame_bytes(jp2_source_for(instrument, value))
    if product == PRODUCT_FITS_REDUCED and size_mode == SIZE_SYNOPTIC:
        return SYNOPTIC_FRAME_BYTES
    return None


def format_size_hint(num_bytes: int) -> str:
    """'≈1.1 MB' style text for a results-table size cell."""
    value = float(max(0, int(num_bytes)))
    if value >= 1024**2:
        return f"≈{value / 1024**2:.1f} MB"
    return f"≈{max(1, round(value / 1024))} KB"

