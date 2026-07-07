"""
e-CALLISTO FITS Analyzer
Unit tests for multi-viewpoint reprojection (src/Backend/multiview.py).

Builds small synthetic sunpy maps with different heliographic observer
longitudes (mimicking Earth vs STEREO views) and checks that reprojection places
one onto the other's grid.
"""

from __future__ import annotations

import numpy as np
import pytest

sunpy_map = pytest.importorskip("sunpy.map")

import astropy.units as u  # noqa: E402
from astropy.coordinates import SkyCoord  # noqa: E402
from sunpy.coordinates import HeliographicStonyhurst, frames  # noqa: E402

from src.Backend.multiview import (  # noqa: E402
    CoalignedView,
    blink_pair,
    coalign_to_reference,
    observer_separation_deg,
    reproject_map_to,
)

DATE = "2012-07-12T16:00:00"


def make_view_map(lon_deg: float, *, shape=(48, 48), scale: float = 25.0) -> "sunpy_map.GenericMap":
    """A uniform disk-scale map seen from a given Stonyhurst longitude."""
    data = np.ones(shape, dtype=float)
    observer = SkyCoord(
        lon_deg * u.deg, 0 * u.deg, 1 * u.AU, obstime=DATE, frame=HeliographicStonyhurst
    )
    ref_coord = SkyCoord(
        0 * u.arcsec, 0 * u.arcsec, obstime=DATE, observer=observer, frame=frames.Helioprojective
    )
    header = sunpy_map.make_fitswcs_header(
        data, ref_coord, scale=[scale, scale] * u.arcsec / u.pix
    )
    return sunpy_map.Map(data, header)


def test_reproject_identity_preserves_grid():
    m = make_view_map(0.0)
    out = reproject_map_to(m, m)
    assert out.data.shape == m.data.shape
    # WCS pixel scale is preserved onto the same grid.
    assert np.allclose(out.wcs.wcs.crpix, m.wcs.wcs.crpix)
    assert np.isfinite(out.data).mean() > 0.5


def test_reproject_cross_observer_matches_target_grid():
    earth = make_view_map(0.0, shape=(48, 48), scale=25.0)
    stereo = make_view_map(60.0, shape=(40, 40), scale=30.0)

    out = reproject_map_to(stereo, earth)
    # Output lives on the Earth-view (target) pixel grid.
    assert out.data.shape == earth.data.shape
    # Some on-disk pixels survive the cross-viewpoint transform.
    assert np.isfinite(out.data).any()


def test_blink_pair_returns_reference_and_reprojected():
    earth = make_view_map(0.0)
    stereo = make_view_map(45.0)
    a, b = blink_pair(earth, stereo)
    assert a is earth
    assert b.data.shape == earth.data.shape


def test_coalign_to_reference():
    earth = make_view_map(0.0)
    stereo_a = make_view_map(60.0)
    stereo_b = make_view_map(-60.0)
    view = coalign_to_reference(earth, [stereo_a, stereo_b])
    assert isinstance(view, CoalignedView)
    assert view.reference is earth
    assert len(view.reprojected) == 2
    assert len(view.all_maps) == 3
    for m in view.reprojected:
        assert m.data.shape == earth.data.shape


def test_observer_separation_deg():
    earth = make_view_map(0.0)
    stereo = make_view_map(72.0)
    sep = observer_separation_deg(earth, stereo)
    assert sep == pytest.approx(72.0, abs=1.0)


def test_reproject_map_to_rejects_non_map():
    earth = make_view_map(0.0)
    with pytest.raises(TypeError):
        reproject_map_to(object(), earth)
