"""
e-CALLISTO FITS Analyzer
Unit tests for instrument science-class classification
(src/Backend/instrument_profiles.py).
"""

from __future__ import annotations

import pytest

from src.Backend.instrument_profiles import (
    CORONAGRAPH,
    DISK_EUV,
    HELIOSPHERIC,
    MAGNETOGRAPH,
    UNKNOWN,
    classify_frame,
    classify_observable,
)


@pytest.mark.parametrize(
    "instrument, value, expected",
    [
        ("AIA", 193.0, DISK_EUV),
        ("AIA", 1600.0, DISK_EUV),
        ("HMI", "magnetogram", MAGNETOGRAPH),
        ("HMI", "continuum", MAGNETOGRAPH),
        ("LASCO", "C2", CORONAGRAPH),
        ("LASCO", "C3", CORONAGRAPH),
        ("SECCHI", ("STEREO_A", "EUVI", 195.0), DISK_EUV),
        ("SECCHI", ("STEREO_B", "EUVI", 304.0), DISK_EUV),
        ("SECCHI", ("STEREO_A", "COR1", None), CORONAGRAPH),
        ("SECCHI", ("STEREO_A", "COR2", None), CORONAGRAPH),
        ("SECCHI", ("STEREO_B", "COR2", None), CORONAGRAPH),
        ("SECCHI", ("STEREO_A", "HI1", None), HELIOSPHERIC),
        ("SECCHI", ("STEREO_B", "HI2", None), HELIOSPHERIC),
        ("SUVI", 171.0, DISK_EUV),
        ("SUVI", 304.0, DISK_EUV),
        ("XRS", None, UNKNOWN),
        ("", None, UNKNOWN),
    ],
)
def test_classify_observable(instrument, value, expected):
    assert classify_observable(instrument, value) == expected


class _Frame:
    def __init__(self, *, instrument="", detector="", meta=None):
        self.instrument = instrument
        self.detector = detector
        self.meta = meta or {}


@pytest.mark.parametrize(
    "frame, expected",
    [
        (_Frame(instrument="AIA_4"), DISK_EUV),
        (_Frame(instrument="GOES-R Series Solar Ultraviolet Imager"), DISK_EUV),
        (_Frame(instrument="SECCHI", detector="EUVI"), DISK_EUV),
        (_Frame(instrument="SECCHI", detector="COR2"), CORONAGRAPH),
        (_Frame(instrument="SECCHI", detector="COR1"), CORONAGRAPH),
        (_Frame(instrument="LASCO", detector="C2"), CORONAGRAPH),
        (_Frame(instrument="", detector="C3", meta={"instrume": "LASCO"}), CORONAGRAPH),
        (_Frame(instrument="SECCHI", detector="HI1"), HELIOSPHERIC),
        (_Frame(instrument="SECCHI", detector="HI1A"), HELIOSPHERIC),
        (_Frame(instrument="SECCHI", detector="HI2"), HELIOSPHERIC),
        (_Frame(instrument="HMI_SIDE1"), MAGNETOGRAPH),
        (_Frame(instrument="SWAP"), DISK_EUV),
        # Meta-only fallbacks (real sunpy maps expose attrs; local FITS may not).
        (_Frame(meta={"instrume": "SECCHI", "detector": "COR2"}), CORONAGRAPH),
        (_Frame(meta={"instrume": "AIA_3"}), DISK_EUV),
        (_Frame(), UNKNOWN),
        (_Frame(instrument="SECCHI"), UNKNOWN),  # SECCHI without a detector is ambiguous
    ],
)
def test_classify_frame(frame, expected):
    assert classify_frame(frame) == expected


def test_classify_frame_detector_beats_instrument():
    # A COR2 detector must win even when the instrument string mentions EUV-ish
    # tokens — the detector is the physically meaningful discriminator.
    frame = _Frame(instrument="SECCHI EUVI-COR SUITE", detector="COR2")
    assert classify_frame(frame) == CORONAGRAPH
