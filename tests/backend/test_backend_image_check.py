"""
e-CALLISTO FITS Analyzer
Unit tests for the load-time pixel-geometry check (src/backend/solar/image_check.py):
size class, limb measured from the pixels, stale binned headers.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

sunpy_map = pytest.importorskip("sunpy.map")

from src.backend.solar.image_check import (  # noqa: E402
    CENTRE_MIN_PX,
    CHECK_KEY,
    ERROR_KEY,
    FIX_KEY,
    _header_disc_centre,
    apply_check,
    check_frame,
    fit_disc,
)

DSUN = 1.4746e11
RSUN_REF = 696_000_000.0
RSUN_ARCSEC = math.degrees(math.asin(RSUN_REF / DSUN)) * 3600.0  # ~973.6"


def _continuum(n: int, cdelt: float, *, header_cdelt: float | None = None, centre=None, noise=0.01, instrument="HMI",
               aia4500: bool = False, plage: bool = False):
    """A limb-darkened continuum disc as HMI stores it (faint finite ring to ~1.05 R, NaN beyond).

    ``aia4500`` mimics the real AIA 4500 A frames instead: a wide scattered-light
    halo, a +-30% brightness tilt and a diagonal streak across the disc (what
    pulled the first disc fit ~25 px off at 1024 and ~100 px at 4096).
    ``plage`` adds bright patches just inside the limb with edges steeper than
    the limb itself (AIA 1700).
    """
    cx, cy = centre if centre is not None else ((n - 1) / 2.0 + 3.3, (n - 1) / 2.0 - 2.1)
    r_px = RSUN_ARCSEC / cdelt
    yy, xx = np.mgrid[0:n, 0:n]
    rr = np.hypot(xx - cx, yy - cy) / r_px
    mu = np.sqrt(np.clip(1 - rr**2, 0, 1))
    image = 50000.0 * (1 - 0.88 * (1 - mu) + 0.23 * (1 - mu**2))
    if aia4500:
        tilt = 1.0 + 0.3 * ((xx - cx) - 0.5 * (yy - cy)) / r_px
        streak = 1.0 + 0.25 * np.exp(-((xx - cx) - (yy - cy)) ** 2 / (2 * (0.05 * r_px) ** 2))
        image = image * tilt * streak
        # The real halo is lopsided (brighter to the upper left), which is what
        # drags a thresholded centroid off the disc centre.
        # Like the real frame, the halo stays below the disc's own edge.
        # The real halo stays below the disc's own edge (~0.65x), is brightest
        # beside the brightest part of the disc and fades over ~0.3 R.
        lopsided = 1.0 + 0.5 * np.cos(np.arctan2(yy - cy, xx - cx) + 0.46)
        halo = 0.2 * 50000.0 * lopsided * np.exp(-np.clip(rr - 1.0, 0, None) / 0.3)
        image = np.where(rr <= 1.0, image, halo)
    else:
        image = np.where(rr <= 1.0, image, 300.0)
        image[rr > 1.05] = np.nan
    if plage:
        for ang in (0.3, 2.2, 3.6, 5.0):
            px, py = cx + 0.93 * r_px * math.cos(ang), cy + 0.93 * r_px * math.sin(ang)
            image[np.hypot(xx - px, yy - py) < 0.05 * r_px] *= 3.0
    image *= 1 + noise * np.random.default_rng(1).standard_normal(image.shape)
    hc = header_cdelt if header_cdelt is not None else cdelt
    meta = {
        "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": "arcsec", "CUNIT2": "arcsec",
        "CDELT1": hc, "CDELT2": hc, "CRPIX1": cx + 1.0, "CRPIX2": cy + 1.0, "CRVAL1": 0.0, "CRVAL2": 0.0,
        "DATE-OBS": "2025-12-03T00:00:00", "DSUN_OBS": DSUN, "RSUN_REF": RSUN_REF, "RSUN_OBS": RSUN_ARCSEC,
        "HGLN_OBS": 0.0, "HGLT_OBS": 0.6, "WAVELNTH": 6173.0 if instrument == "HMI" else 171.0,
        "TELESCOP": "SDO/" + instrument, "INSTRUME": "HMI_FRONT2" if instrument == "HMI" else "AIA_3",
        "CONTENT": "CONTINUUM INTENSITY" if instrument == "HMI" else "",
        "WAVEUNIT": "angstrom",
    }
    if header_cdelt is not None and header_cdelt != cdelt:
        f = cdelt / header_cdelt  # header left at the finer, unbinned grid
        meta["CRPIX1"] = (cx + 0.5) * f + 0.5
        meta["CRPIX2"] = (cy + 0.5) * f + 0.5
    return sunpy_map.Map(image.astype(np.float32), meta)


@pytest.mark.parametrize("n, cdelt", [(1024, 2.016), (2048, 1.008)])
def test_good_frames_pass_and_the_limb_is_found_in_the_pixels(n, cdelt):
    frame = _continuum(n, cdelt)
    check = check_frame(frame)
    assert check.ok and check.binning_fix is None
    assert check.size_class.startswith(f"{n}²")
    assert check.measured.radius_px == pytest.approx(RSUN_ARCSEC / cdelt, rel=0.003)
    assert check.summary().endswith("✓")
    assert apply_check(frame, check) is frame
    assert frame.meta[CHECK_KEY] == check.summary()


def test_the_faint_ring_beyond_the_limb_is_not_taken_for_the_disc():
    frame = _continuum(1024, 2.016)
    disc = fit_disc(frame.data)
    finite_radius = math.sqrt(np.isfinite(frame.data).sum() / math.pi)
    assert finite_radius / disc.radius_px == pytest.approx(1.05, abs=0.01)  # NaN edge is 5% out
    assert disc.radius_px == pytest.approx(RSUN_ARCSEC / 2.016, rel=0.003)
    assert disc.centre_x == pytest.approx(511.5 + 3.3, abs=0.6)
    assert disc.centre_y == pytest.approx(511.5 - 2.1, abs=0.6)


@pytest.mark.parametrize("factor, n", [(2, 2048), (4, 1024)])
def test_stale_binned_header_is_corrected(factor, n):
    true_cdelt = 0.504 * factor
    frame = _continuum(n, true_cdelt, header_cdelt=0.504)  # pixels binned, header left at 0.504"/px
    check = check_frame(frame)
    assert check.binning_fix == factor and check.ok
    fixed = apply_check(frame, check)
    assert fixed is not frame
    assert float(fixed.scale.axis1.value) == pytest.approx(true_cdelt)
    assert FIX_KEY in fixed.meta
    # The corrected header puts the disc centre where the pixels have it.
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    centre = fixed.world_to_pixel(SkyCoord(0 * u.arcsec, 0 * u.arcsec, frame=fixed.coordinate_frame))
    assert float(centre[0].value) == pytest.approx(check.measured.centre_x, abs=1.0)
    assert float(centre[1].value) == pytest.approx(check.measured.centre_y, abs=1.0)
    assert check_frame(fixed).ok and check_frame(fixed).binning_fix is None


def test_an_unexplained_scale_mismatch_is_an_error_not_a_guess():
    frame = _continuum(1024, 2.016, header_cdelt=2.016 * 1.3)
    check = check_frame(frame)
    assert not check.ok
    assert "puts the limb at" in check.error
    out = apply_check(frame, check)
    assert out is frame
    assert ERROR_KEY in frame.meta


def test_euv_frames_are_checked_by_field_of_view():
    good = _continuum(1024, 2.4, instrument="AIA")
    assert check_frame(good).ok
    stale = _continuum(1024, 2.4, header_cdelt=0.6, instrument="AIA")
    check = check_frame(stale)
    assert check.measured is None  # no sharp limb in EUV: header-only check
    assert check.binning_fix == 4


def test_cutouts_are_not_judged_against_full_disc_sizes():
    frame = _continuum(1024, 2.016)
    cut = sunpy_map.Map(np.asarray(frame.data)[100:400, 200:700], dict(frame.meta))
    check = check_frame(cut)
    assert check.size_class.startswith("500×300")
    assert check.ok and check.binning_fix is None


@pytest.mark.parametrize("n, cdelt", [(1024, 2.4), (2048, 1.2)])
def test_halo_and_brightness_tilt_do_not_pull_the_disc_off_centre(n, cdelt):
    """AIA 4500 (real 2025-12-03 frames: the first fit was 25 px off at 1024², 100 px at 4096²).

    2048² also exercises the binned fit used for frames larger than 1024².
    """
    centre = ((n - 1) / 2.0 + 0.004 * n, (n - 1) / 2.0 - 0.003 * n)
    frame = _continuum(n, cdelt, centre=centre, instrument="AIA", aia4500=True)
    frame.meta["WAVELNTH"] = 4500.0
    disc = fit_disc(frame.data)
    r_px = RSUN_ARCSEC / cdelt
    assert disc.radius_px == pytest.approx(r_px, rel=0.004)
    assert math.hypot(disc.centre_x - centre[0], disc.centre_y - centre[1]) < 0.002 * r_px
    check = check_frame(frame)
    assert check.ok, check.error


def test_bright_plage_at_the_limb_is_not_taken_for_the_limb():
    """AIA 1700: plage edges inside the limb are steeper than the limb."""
    frame = _continuum(1024, 2.4, instrument="AIA", plage=True)
    frame.meta["WAVELNTH"] = 1700.0
    disc = fit_disc(frame.data)
    assert disc.radius_px == pytest.approx(RSUN_ARCSEC / 2.4, rel=0.004)
    assert disc.centre_x == pytest.approx(511.5 + 3.3, abs=0.6)
    assert disc.centre_y == pytest.approx(511.5 - 2.1, abs=0.6)
    assert check_frame(frame).ok


@pytest.mark.parametrize("shift, ok", [(1.0, True), (8.0, False), (25.0, False)])
def test_a_wrong_reference_pixel_is_an_error(shift, ok):
    frame = _continuum(1024, 2.016)
    meta = dict(frame.meta)
    meta["crpix1"] = float(meta["crpix1"]) + shift
    check = check_frame(sunpy_map.Map(np.asarray(frame.data), meta))
    assert check.ok is ok
    if not ok:
        assert "disc centre" in check.error
        assert check.binning_fix is None


def test_header_disc_centre_follows_crval_and_rotation():
    """Raw HMI: CROTA2 ~180 and the reference pixel is not the Sun's centre."""
    meta = {"CRPIX1": 2048.5, "CRPIX2": 2048.5, "CDELT1": 0.504, "CDELT2": 0.504,
            "CRVAL1": 5.04, "CRVAL2": -10.08, "CUNIT1": "arcsec", "CUNIT2": "arcsec", "CROTA2": 180.0}
    x, y = _header_disc_centre(meta)
    # Rotated 180 deg, +10 px in x of the world is -10 px in the image: the centre
    # sits at pixel offset (+10, -20) from CRPIX (0-based 2047.5).
    assert x == pytest.approx(2047.5 + 10.0, abs=1e-6)
    assert y == pytest.approx(2047.5 - 20.0, abs=1e-6)
    pc = {**meta, "PC1_1": -1.0, "PC1_2": 0.0, "PC2_1": 0.0, "PC2_2": -1.0}
    assert _header_disc_centre(pc) == pytest.approx((x, y))
    # And a SunPy map agrees.
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    m = sunpy_map.Map(np.zeros((4096, 4096), dtype=np.float32), {
        **meta, "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "DATE-OBS": "2025-12-03T00:00:00",
        "DSUN_OBS": DSUN, "HGLN_OBS": 0.0, "HGLT_OBS": 0.6})
    px, py = m.world_to_pixel(SkyCoord(0 * u.arcsec, 0 * u.arcsec, frame=m.coordinate_frame))
    assert float(px.value) == pytest.approx(x, abs=0.01)
    assert float(py.value) == pytest.approx(y, abs=0.01)


def test_a_stale_binned_header_with_a_wrong_centre_is_not_silently_fixed():
    frame = _continuum(1024, 2.016, header_cdelt=0.504)
    meta = dict(frame.meta)
    meta["crpix1"] = float(meta["crpix1"]) + 60.0  # 15 binned px
    check = check_frame(sunpy_map.Map(np.asarray(frame.data), meta))
    assert not check.ok and check.binning_fix is None
    assert "disc centre" in check.error
    assert CENTRE_MIN_PX > 0
