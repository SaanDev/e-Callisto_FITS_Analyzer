"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Instrument classification for the multi-mission Solar Image Analysis window.

Every supported observable falls into one of four science classes that decide
which analysis tools make sense:

* ``disk_euv`` — full-disk EUV/UV imagers (SDO/AIA, STEREO/EUVI, GOES/SUVI):
  active-region detection, HEK labels, RGB composites, light curves.
* ``coronagraph`` — occulted white-light imagers (SOHO/LASCO C2/C3,
  STEREO/COR1/COR2): NRGF radial filtering, CME height-time measurement.
* ``heliospheric`` — wide-field heliospheric imagers (STEREO/HI1/HI2):
  background subtraction, time-elongation J-maps.
* ``magnetograph`` — SDO/HMI products: vector-field overlay, polarity
  composites.

Pure functions over plain strings/objects (no Qt, no sunpy) so both the UI
gating and the tests stay trivial.
"""

from __future__ import annotations

from typing import Any


DISK_EUV = "disk_euv"
CORONAGRAPH = "coronagraph"
HELIOSPHERIC = "heliospheric"
MAGNETOGRAPH = "magnetograph"
UNKNOWN = "unknown"

_CORONAGRAPH_DETECTORS = ("C2", "C3", "COR1", "COR2")
_DISK_EUV_INSTRUMENT_TOKENS = ("AIA", "SUVI", "SOLAR ULTRAVIOLET IMAGER", "SWAP")


def classify_observable(instrument: str, value: Any) -> str:
    """Science class for a Solar Image Analysis observable selection.

    ``instrument``/``value`` follow the window's observable userData convention:
    ("AIA", wavelength), ("HMI", product), ("LASCO", detector),
    ("SECCHI", (spacecraft, detector, wavelength_or_None)), ("SUVI", wavelength).
    """
    inst = str(instrument or "").strip().upper()
    if inst in ("AIA", "SUVI"):
        return DISK_EUV
    if inst == "HMI":
        return MAGNETOGRAPH
    if inst == "LASCO":
        return CORONAGRAPH
    if inst == "SECCHI":
        detector = ""
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            detector = str(value[1] or "").strip().upper()
        if detector == "EUVI":
            return DISK_EUV
        if detector in ("COR1", "COR2"):
            return CORONAGRAPH
        if detector.startswith("HI"):
            return HELIOSPHERIC
        return UNKNOWN
    return UNKNOWN


def classify_frame(frame: Any) -> str:
    """Science class of a loaded map frame, from its attrs/metadata.

    The detector is the most specific signal (a SECCHI frame is only meaningful
    per detector), so it is checked before the instrument name. Falls back to
    FITS meta keys the way the plot windows already do.
    """
    detector = _text(getattr(frame, "detector", None)) or _meta_text(frame, "detector")
    instrument = _text(getattr(frame, "instrument", None)) or _meta_text(frame, "instrume", "instrument")

    det = detector.upper()
    if det in _CORONAGRAPH_DETECTORS:
        return CORONAGRAPH
    if det.startswith("HI") and any(ch.isdigit() for ch in det):
        return HELIOSPHERIC
    if det == "EUVI":
        return DISK_EUV

    inst = instrument.upper()
    if any(token in inst for token in _DISK_EUV_INSTRUMENT_TOKENS):
        return DISK_EUV
    if "HMI" in inst:
        return MAGNETOGRAPH
    if "LASCO" in inst:
        return CORONAGRAPH
    return UNKNOWN


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _meta_text(frame: Any, *keys: str) -> str:
    meta = getattr(frame, "meta", None)
    if not meta:
        return ""
    try:
        lowered = {str(k).strip().lower(): v for k, v in dict(meta).items()}
    except Exception:
        return ""
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return _text(value)
    return ""
