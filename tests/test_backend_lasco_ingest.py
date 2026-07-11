"""
e-CALLISTO FITS Analyzer
Unit tests for SOHO/LASCO ingestion (src/Backend/lasco_ingest.py).

The centrepiece reproduces the real LASCO defect: a valid arcsec WCS but with the
``CUNIT1``/``CUNIT2`` axis-unit keywords omitted, which makes ``sunpy.map.Map``
abort in ``LASCOMap.spatial_units`` with
``AttributeError: 'NoneType' object has no attribute 'lower'``. The salvage path
must repair it offline.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.Backend import lasco_ingest
from src.Backend.lasco_ingest import (
    is_lasco_header,
    load_lasco_map,
    patch_lasco_header,
)


def write_lasco_fits(
    path: Path,
    *,
    detector: str = "C2",
    shape: tuple[int, int] = (16, 16),
    with_cunit: bool = False,
) -> Path:
    """Write a tiny LASCO FITS file (optionally omitting CUNIT1/2 to reproduce
    the missing-unit crash)."""
    from astropy.io import fits

    ny, nx = shape
    data = np.linspace(0.0, 10.0, nx * ny, dtype="float32").reshape(ny, nx)
    hdr = fits.Header()
    hdr["INSTRUME"] = "LASCO"
    hdr["DETECTOR"] = detector
    hdr["TELESCOP"] = "SOHO"
    hdr["CTYPE1"] = "HPLN-TAN"
    hdr["CTYPE2"] = "HPLT-TAN"
    hdr["CDELT1"] = 11.9
    hdr["CDELT2"] = 11.9
    hdr["CRPIX1"] = nx / 2 + 0.5
    hdr["CRPIX2"] = ny / 2 + 0.5
    hdr["CRVAL1"] = 0.0
    hdr["CRVAL2"] = 0.0
    hdr["DATE-OBS"] = "2024-01-01T00:00:00.000"
    if with_cunit:
        hdr["CUNIT1"] = "arcsec"
        hdr["CUNIT2"] = "arcsec"
    path = Path(path)
    fits.writeto(path, data, hdr, overwrite=True)
    return path


def test_plain_map_fails_but_load_lasco_map_salvages(tmp_path):
    import sunpy.map

    bad = write_lasco_fits(tmp_path / "lasco_c2.fits", detector="C2")

    # The stock loader crashes on the missing CUNIT keywords.
    with pytest.raises(Exception):
        sunpy.map.Map(str(bad))

    m = load_lasco_map(bad)
    assert type(m).__name__ == "LASCOMap"
    assert m.data.shape == (16, 16)
    # sunpy assigns LASCO its detector colormap once the header parses.
    assert m.cmap.name == "soholasco2"


def test_load_lasco_map_c3_detector(tmp_path):
    bad = write_lasco_fits(tmp_path / "lasco_c3.fits", detector="C3")
    m = load_lasco_map(bad)
    assert type(m).__name__ == "LASCOMap"
    assert m.cmap.name == "soholasco3"


def test_load_lasco_map_prefers_base_loader_when_it_succeeds():
    calls = {"n": 0}

    def fake_loader(arg):
        calls["n"] += 1
        return f"MAP:{arg}"

    out = load_lasco_map("whatever.fits", base_loader=fake_loader)
    assert out == "MAP:whatever.fits"
    assert calls["n"] == 1  # no salvage attempted


def test_load_lasco_map_salvages_with_injected_loader(tmp_path):
    bad = write_lasco_fits(tmp_path / "x.fits")
    seen = {}

    def picky_loader(arg):
        # Reject the path (as the real sunpy loader does on a missing unit),
        # accept a (data, header) tuple built by the salvage path.
        if isinstance(arg, tuple):
            seen["data"], seen["header"] = arg
            return "SALVAGED"
        raise AttributeError("'NoneType' object has no attribute 'lower'")

    out = load_lasco_map(bad, base_loader=picky_loader)
    assert out == "SALVAGED"
    assert seen["data"].shape == (16, 16)
    assert seen["header"]["CUNIT1"] == "arcsec"
    assert seen["header"]["CUNIT2"] == "arcsec"


def test_patch_backfills_missing_cunit():
    from astropy.io import fits

    header = fits.Header()
    header["INSTRUME"] = "LASCO"
    header["DETECTOR"] = "C2"
    # No CUNIT1 / CUNIT2 present.

    patched = patch_lasco_header(header)
    assert patched["CUNIT1"] == "arcsec"
    assert patched["CUNIT2"] == "arcsec"


def test_patch_does_not_override_existing_units():
    from astropy.io import fits

    header = fits.Header()
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"

    patched = patch_lasco_header(header)
    assert patched["CUNIT1"] == "deg"
    assert patched["CUNIT2"] == "deg"


def test_is_lasco_header():
    from astropy.io import fits

    lasco = fits.Header()
    lasco["INSTRUME"] = "LASCO"
    lasco["DETECTOR"] = "C2"
    assert is_lasco_header(lasco) is True

    aia = fits.Header()
    aia["INSTRUME"] = "AIA_4"
    aia["TELESCOP"] = "SDO/AIA"
    assert is_lasco_header(aia) is False


def test_load_downloaded_routes_lasco(tmp_path):
    from src.Backend.sunpy_archive import DATA_KIND_MAP, load_downloaded

    bad = write_lasco_fits(tmp_path / "lasco_c2.fits")
    result = load_downloaded([bad], DATA_KIND_MAP, instrument="LASCO")

    assert result.data_kind == DATA_KIND_MAP
    assert result.metadata["n_frames"] == 1
    maps = result.maps_or_timeseries
    assert isinstance(maps, list) and len(maps) == 1
    assert type(maps[0]).__name__ == "LASCOMap"


def test_load_lasco_map_has_per_file_marker():
    # sunpy_archive._load_maps relies on this to map the loader over each file.
    assert getattr(lasco_ingest.load_lasco_map, "per_file", False) is True
