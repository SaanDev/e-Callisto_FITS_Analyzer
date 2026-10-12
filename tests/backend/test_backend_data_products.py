"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for src/backend/solar/data_products.py: which download products and frame
sizes each Solar Image Analysis observable offers, and where a search goes.
"""

from __future__ import annotations

import pytest

from src.backend.solar import data_products as dp
from src.backend.solar.jsoc_client import SIZE_BIN2, SIZE_BIN4, SIZE_CUTOUT, SIZE_FULL


@pytest.mark.parametrize(
    ("instrument", "value", "expected"),
    [
        ("AIA", 193.0, (dp.PRODUCT_FITS_FULL, dp.PRODUCT_FITS_REDUCED, dp.PRODUCT_JP2)),
        ("HMI", "magnetogram", (dp.PRODUCT_FITS_FULL, dp.PRODUCT_FITS_REDUCED, dp.PRODUCT_JP2)),
        # Helioviewer has no Dopplergram.
        ("HMI", "dopplergram", (dp.PRODUCT_FITS_FULL, dp.PRODUCT_FITS_REDUCED)),
        ("EIT", 195.0, (dp.PRODUCT_FITS_FULL, dp.PRODUCT_JP2)),
        ("LASCO", "C3", (dp.PRODUCT_FITS_FULL, dp.PRODUCT_JP2)),
        ("SECCHI", ("STEREO_A", "COR2", None), (dp.PRODUCT_FITS_FULL, dp.PRODUCT_JP2)),
        ("SECCHI", ("STEREO_B", "EUVI", 304.0), (dp.PRODUCT_FITS_FULL, dp.PRODUCT_JP2)),
        # Neither heliospheric imagers nor SUVI are in Helioviewer.
        ("SECCHI", ("STEREO_A", "HI1", None), (dp.PRODUCT_FITS_FULL,)),
        ("SUVI", 171.0, (dp.PRODUCT_FITS_FULL,)),
    ],
)
def test_each_observable_offers_what_its_archives_hold(instrument, value, expected):
    assert dp.products_for(instrument, value) == expected


def test_aia_reduced_sizes_start_with_the_e_mail_free_synoptic_set():
    assert dp.reduced_sizes_for("AIA", 171.0) == (dp.SIZE_SYNOPTIC, SIZE_BIN2, SIZE_BIN4, SIZE_CUTOUT)
    # 1700 Å is not produced in the synoptic archive.
    assert dp.reduced_sizes_for("AIA", 1700.0) == (SIZE_BIN2, SIZE_BIN4, SIZE_CUTOUT)
    assert dp.reduced_sizes_for("HMI", "continuum") == (SIZE_BIN2, SIZE_BIN4, SIZE_CUTOUT)
    assert dp.reduced_sizes_for("LASCO", "C2") == ()


def test_the_archive_follows_product_and_size():
    assert dp.archive_for(dp.PRODUCT_FITS_FULL, SIZE_FULL) == dp.ARCHIVE_SUNPY
    assert dp.archive_for(dp.PRODUCT_FITS_REDUCED, SIZE_BIN4) == dp.ARCHIVE_SUNPY
    assert dp.archive_for(dp.PRODUCT_FITS_REDUCED, dp.SIZE_SYNOPTIC) == dp.ARCHIVE_AIA_SYNOPTIC
    assert dp.archive_for(dp.PRODUCT_JP2, SIZE_FULL) == dp.ARCHIVE_HELIOVIEWER_JP2
    assert dp.is_direct_archive(dp.ARCHIVE_HELIOVIEWER_JP2)
    assert dp.is_direct_archive(dp.ARCHIVE_AIA_SYNOPTIC)
    assert not dp.is_direct_archive(dp.ARCHIVE_SUNPY)


def test_jp2_sources_match_helioviewer_source_ids():
    assert dp.jp2_source_for("AIA", 94.0).source_id == 8
    assert dp.jp2_source_for("AIA", 1700.0).source_id == 16
    assert dp.jp2_source_for("AIA", 4500.0).source_id == 17
    assert dp.jp2_source_for("HMI", "magnetogram").source_id == 19
    assert dp.jp2_source_for("EIT", 304.0).source_id == 3
    # The window's userData is not always normalised.
    assert dp.jp2_source_for("lasco", "c2").source_id == 4
    assert dp.jp2_source_for("SECCHI", ["stereo_a", "euvi", "195"]).source_id == 21
    cor2_b = dp.jp2_source_for("SECCHI", ("STEREO_B", "COR2", None))
    assert cor2_b.source_id == 31
    assert cor2_b.last_day is not None


def test_a_greyed_out_product_says_why():
    assert "reduced" in dp.unavailable_reason(dp.PRODUCT_FITS_REDUCED, "LASCO", "C2").lower()
    assert "JP2" in dp.unavailable_reason(dp.PRODUCT_JP2, "SUVI", 171.0)
    assert dp.unavailable_reason(dp.PRODUCT_JP2, "AIA", 193.0) == ""


def test_size_estimates_reflect_the_product():
    assert dp.frame_bytes_estimate(dp.PRODUCT_JP2, SIZE_FULL, "LASCO", "C2") < 1_000_000
    assert dp.frame_bytes_estimate(dp.PRODUCT_JP2, SIZE_FULL, "AIA", 193.0) > 500_000
    assert dp.frame_bytes_estimate(dp.PRODUCT_FITS_REDUCED, dp.SIZE_SYNOPTIC, "AIA", 193.0) == dp.SYNOPTIC_FRAME_BYTES
    # JSOC and VSO sizes keep using the JSOC estimate.
    assert dp.frame_bytes_estimate(dp.PRODUCT_FITS_FULL, SIZE_FULL, "AIA", 193.0) is None
    assert dp.format_size_hint(330_000) == "≈322 KB"
    assert dp.format_size_hint(1_080_000) == "≈1.0 MB"


def test_jp2_decode_sizes_are_normalised():
    assert dp.normalize_jp2_max_px("1024") == 1024
    assert dp.normalize_jp2_max_px(0) == 0
    assert dp.normalize_jp2_max_px(777) == dp.DEFAULT_JP2_MAX_PX
    assert dp.normalize_jp2_max_px(None) == dp.DEFAULT_JP2_MAX_PX
