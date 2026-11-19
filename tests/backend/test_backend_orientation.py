"""
e-CALLISTO FITS Analyzer
Unit tests for turning solar images north-up (src/backend/solar/orientation.py).
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

sunpy_map = pytest.importorskip("sunpy.map")
import astropy.units as u  # noqa: E402
from astropy.coordinates import SkyCoord  # noqa: E402
from astropy.io import fits  # noqa: E402
from sunpy.coordinates import frames  # noqa: E402

from src.backend.solar.orientation import needs_north_up, north_up, rotation_angle_deg  # noqa: E402

_HEADER = {
    "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": "arcsec", "CUNIT2": "arcsec",
    "CRPIX1": 120.3, "CRPIX2": 131.7, "CRVAL1": 0.0, "CRVAL2": 0.0, "CDELT1": 9.0, "CDELT2": 9.0,
    "DATE-OBS": "2024-05-10T12:00:00", "RSUN_REF": 696000000.0, "DSUN_OBS": 1.5115e11,
    "HGLN_OBS": 0.0, "HGLT_OBS": -3.2, "RSUN_OBS": 949.9,
    "TELESCOP": "SDO/HMI", "INSTRUME": "HMI_FRONT2", "WAVELNTH": 6173.0, "CONTENT": "CONTINUUM INTENSITY",
}


def _map(tmp_path, **extra):
    header = dict(_HEADER, **extra)
    data = np.random.default_rng(3).random((256, 256)).astype("float32")
    path = tmp_path / "frame.fits"
    fits.writeto(path, data, fits.Header(header), overwrite=True)
    return sunpy_map.Map(str(path))


def _values_at(m, lons, lats):
    c = SkyCoord(np.asarray(lons) * u.deg, np.asarray(lats) * u.deg, frame=frames.HeliographicStonyhurst,
                 obstime=m.date, observer=m.observer_coordinate, rsun=m.rsun_meters)
    px, py = m.world_to_pixel(c)
    return [float(m.data[int(round(float(y.value))), int(round(float(x.value)))]) for x, y in zip(px, py)]


def test_half_turn_is_an_exact_flip(tmp_path):
    raw = _map(tmp_path, CROTA2=179.93)
    assert rotation_angle_deg(raw) == pytest.approx(179.93)
    assert needs_north_up(raw)
    up = north_up(raw)
    assert up is not raw
    assert rotation_angle_deg(up) == pytest.approx(-0.07, abs=1e-6)
    # The same solar positions carry exactly the same pixel values.
    lons, lats = [-30.0, 0.0, 25.0, 40.0], [-15.0, 0.0, 20.0, -35.0]
    assert _values_at(up, lons, lats) == _values_at(raw, lons, lats)
    # North really is up now: the north pole sits in the upper half of the array.
    pole = SkyCoord(0 * u.deg, 89 * u.deg, frame=frames.HeliographicStonyhurst, obstime=up.date,
                    observer=up.observer_coordinate, rsun=up.rsun_meters)
    assert float(up.world_to_pixel(pole)[1].value) > up.data.shape[0] / 2
    assert float(raw.world_to_pixel(pole)[1].value) < raw.data.shape[0] / 2
    assert np.shares_memory(np.asarray(up.data), np.asarray(raw.data))  # a view, not a copy


def test_half_turn_with_a_pc_matrix(tmp_path):
    r = math.radians(180.0)
    raw = _map(tmp_path, PC1_1=math.cos(r), PC1_2=-math.sin(r), PC2_1=math.sin(r), PC2_2=math.cos(r))
    up = north_up(raw)
    assert abs(rotation_angle_deg(up)) < 1e-6
    assert _values_at(up, [10.0, -20.0], [5.0, 30.0]) == _values_at(raw, [10.0, -20.0], [5.0, 30.0])


def test_small_rotations_are_left_alone(tmp_path):
    aia_like = _map(tmp_path, CROTA2=0.14)
    assert not needs_north_up(aia_like)
    assert north_up(aia_like) is aia_like
    assert north_up(SimpleNamespace(data=np.ones((4, 4)))) is not None  # no rotation info: unchanged
    plain = SimpleNamespace(data=np.ones((4, 4)))
    assert north_up(plain) is plain


def test_other_angles_are_resampled_north_up(tmp_path):
    raw = _map(tmp_path, CROTA2=30.0)
    up = north_up(raw)
    assert abs(rotation_angle_deg(up)) < 1e-6
