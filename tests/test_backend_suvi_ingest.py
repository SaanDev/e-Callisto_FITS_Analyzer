"""
e-CALLISTO FITS Analyzer
Unit tests for GOES/SUVI L1b ingestion (src/Backend/suvi_ingest.py).

The centrepiece is a synthetic FITS file that faithfully reproduces the real
NOAA SUVI L1b defect: a valid Helioprojective WCS plus a malformed ``CONTINUE``
long-string card that makes ``sunpy.map.Map`` abort with an
``Unparsable card (CONTINUE)`` OSError. The salvage path must repair it offline.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.Backend import suvi_ingest
from src.Backend.suvi_ingest import (
    is_suvi_header,
    load_suvi_map,
    patch_suvi_l1b_header,
    sanitize_fits_header,
)


def _card(text: str) -> str:
    assert len(text) <= 80, (len(text), text)
    return text.ljust(80)


def write_bad_suvi_fits(path: Path, *, shape: tuple[int, int] = (16, 16), wavelength: float = 171.0) -> Path:
    """Write a tiny FITS file that reproduces the malformed SUVI L1b CONTINUE card.

    The header carries a valid arcsec HPLN/HPLT-TAN WCS and SUVI identifiers, then
    opens a long-string value and follows it with an unquoted ``CONTINUE`` card
    that astropy cannot parse under strict verification (exactly what breaks
    ``sunpy.map.Map``).
    """
    from astropy.io import fits  # local import keeps module import cheap

    ny, nx = shape
    cards = [
        _card("SIMPLE  =                    T"),
        _card("BITPIX  =                  -32"),
        _card("NAXIS   =                    2"),
        _card(f"NAXIS1  = {nx:>20d}"),
        _card(f"NAXIS2  = {ny:>20d}"),
        _card("CTYPE1  = 'HPLN-TAN'"),
        _card("CTYPE2  = 'HPLT-TAN'"),
        _card("CUNIT1  = 'arcsec'"),
        _card("CUNIT2  = 'arcsec'"),
        _card("CDELT1  =                  2.5"),
        _card("CDELT2  =                  2.5"),
        _card(f"CRPIX1  = {nx / 2 + 0.5:>20.4f}"),
        _card(f"CRPIX2  = {ny / 2 + 0.5:>20.4f}"),
        _card("CRVAL1  =                  0.0"),
        _card("CRVAL2  =                  0.0"),
        _card("INSTRUME= 'GOES-R Series Solar Ultraviolet Imager'"),
        _card("TELESCOP= 'G16'"),
        _card(f"WAVELNTH= {wavelength:>20.1f}"),
        _card("DATE-OBS= '2021-05-07T18:01:30.658'"),
        _card("DSUN_OBS=      150937127392.798"),
        # Open a long string, then a malformed (unquoted) CONTINUE continuation.
        _card("LONGKEY = 'abcdefghij&'"),
        _card("CONTINUE   this_is_not_quoted_and_should_not_parse"),
        _card("END"),
    ]
    header = "".join(cards)
    header += " " * ((2880 - (len(header) % 2880)) % 2880)

    data = np.linspace(0.0, 10.0, nx * ny, dtype=">f4").reshape(ny, nx)
    raw = header.encode("ascii") + data.tobytes()
    raw += b"\x00" * ((2880 - (len(raw) % 2880)) % 2880)

    path = Path(path)
    path.write_bytes(raw)
    # Sanity: the file really does break the ordinary loader.
    with pytest.raises(Exception):
        fits.open(path).verify("exception")
    return path


def test_plain_map_fails_but_load_suvi_map_salvages(tmp_path):
    import sunpy.map

    bad = write_bad_suvi_fits(tmp_path / "OR_SUVI-L1b-Fe171.fits")

    with pytest.raises(Exception):
        sunpy.map.Map(str(bad))

    m = load_suvi_map(bad)
    assert type(m).__name__ == "SUVIMap"
    assert m.data.shape == (16, 16)
    assert int(round(float(m.wavelength.to("angstrom").value))) == 171
    # sunpy assigns SUVI its own colormap once the header parses.
    assert m.cmap.name == "goes-rsuvi171"


def test_load_suvi_map_prefers_base_loader_when_it_succeeds():
    calls = {"n": 0}

    def fake_loader(arg):
        calls["n"] += 1
        return f"MAP:{arg}"

    out = load_suvi_map("whatever.fits", base_loader=fake_loader)
    assert out == "MAP:whatever.fits"
    assert calls["n"] == 1  # no salvage attempted


def test_load_suvi_map_salvages_with_injected_loader(tmp_path):
    bad = write_bad_suvi_fits(tmp_path / "x.fits")
    seen = {}

    def picky_loader(arg):
        # Reject the path (as the real sunpy loader does for L1b), accept a
        # (data, header) tuple built by the salvage path.
        if isinstance(arg, tuple):
            seen["data"], seen["header"] = arg
            return "SALVAGED"
        raise OSError("Unparsable card (CONTINUE)")

    out = load_suvi_map(bad, base_loader=picky_loader)
    assert out == "SALVAGED"
    assert seen["data"].shape == (16, 16)
    assert "CTYPE1" in seen["header"]
    assert "CONTINUE" not in seen["header"]


def test_sanitize_keeps_wcs_and_emits_no_continue_cards(tmp_path):
    # Read the real malformed header from disk so the CONTINUE card is present in
    # the exact form that breaks strict parsing (an in-memory Header never
    # exposes the broken card the same way).
    from astropy.io import fits

    bad = write_bad_suvi_fits(tmp_path / "x.fits")
    with fits.open(bad) as hdul:
        clean = sanitize_fits_header(hdul[0].header)

    assert clean["CTYPE1"] == "HPLN-TAN"
    assert float(clean["CDELT1"]) == 2.5
    assert clean["INSTRUME"].startswith("GOES-R")
    # The offending continuation must be gone, and the sanitized header must be
    # fully parseable (no exception when iterating every value).
    assert all(card.keyword != "CONTINUE" for card in clean.cards)
    for card in clean.cards:
        _ = card.value  # would raise if a malformed card survived


def test_patch_backfills_waveunit_and_cunit():
    from astropy.io import fits

    header = fits.Header()
    header["CTYPE1"] = "HPLN-TAN"
    header["CTYPE2"] = "HPLT-TAN"
    header["WAVELNTH"] = 171.0
    # No WAVEUNIT / CUNIT1 / CUNIT2 present.

    patched = patch_suvi_l1b_header(header)
    assert patched["WAVEUNIT"] == "Angstrom"
    assert patched["CUNIT1"] == "arcsec"
    assert patched["CUNIT2"] == "arcsec"


def test_patch_does_not_override_existing_units():
    from astropy.io import fits

    header = fits.Header()
    header["CTYPE1"] = "HPLN-TAN"
    header["CUNIT1"] = "deg"
    header["WAVELNTH"] = 171.0
    header["WAVEUNIT"] = "nm"

    patched = patch_suvi_l1b_header(header)
    assert patched["CUNIT1"] == "deg"
    assert patched["WAVEUNIT"] == "nm"


def test_is_suvi_header():
    from astropy.io import fits

    suvi = fits.Header()
    suvi["INSTRUME"] = "GOES-R Series Solar Ultraviolet Imager"
    assert is_suvi_header(suvi) is True

    aia = fits.Header()
    aia["INSTRUME"] = "AIA_4"
    aia["TELESCOP"] = "SDO/AIA"
    assert is_suvi_header(aia) is False


def test_load_downloaded_routes_suvi(tmp_path):
    from src.Backend.sunpy_archive import DATA_KIND_MAP, load_downloaded

    bad = write_bad_suvi_fits(tmp_path / "OR_SUVI-L1b-Fe171.fits")
    result = load_downloaded([bad], DATA_KIND_MAP, instrument="SUVI")

    assert result.data_kind == DATA_KIND_MAP
    assert result.metadata["n_frames"] == 1
    maps = result.maps_or_timeseries
    assert isinstance(maps, list) and len(maps) == 1
    assert type(maps[0]).__name__ == "SUVIMap"


def test_load_suvi_map_has_per_file_marker():
    # sunpy_archive._load_maps relies on this to map the loader over each file.
    assert getattr(suvi_ingest.load_suvi_map, "per_file", False) is True
