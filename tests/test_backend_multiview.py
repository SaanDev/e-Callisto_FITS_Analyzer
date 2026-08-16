"""
e-CALLISTO FITS Analyzer
Unit tests for multi-viewpoint reprojection (src/Backend/multiview.py).

Builds small synthetic sunpy maps with different heliographic observer
longitudes (mimicking Earth vs STEREO views) and checks that reprojection places
one onto the other's grid.
"""

from __future__ import annotations

import contextlib

import numpy as np
import pytest

sunpy_map = pytest.importorskip("sunpy.map")

import astropy.units as u  # noqa: E402
from astropy.coordinates import SkyCoord  # noqa: E402
from sunpy.coordinates import HeliographicStonyhurst, frames  # noqa: E402

from src.Backend.multiview import (  # noqa: E402
    SCREEN_PLANAR,
    SCREEN_SPHERICAL,
    SCREEN_SURFACE,
    CoalignedView,
    _screen_context,
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


# --------------------------------------------------------------------------- #
# Screen assumption
#
# sunpy's default reprojection puts emission on the solar surface, which is right
# for disk imagery and wrong for the optically-thin corona. These cover the
# screen= switch that coronagraph overlay layers rely on.
# --------------------------------------------------------------------------- #
def off_disk_ring_map(lon_deg: float, *, shape=(48, 48), scale: float = 60.0):
    """A map whose signal sits well off the disk, like a coronagraph frame."""
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    radius = np.hypot(xx - shape[1] / 2, yy - shape[0] / 2)
    data = np.where((radius > 8) & (radius < 20), 1.0, 0.0)
    observer = SkyCoord(
        lon_deg * u.deg, 0 * u.deg, 1 * u.AU, obstime=DATE, frame=HeliographicStonyhurst
    )
    ref_coord = SkyCoord(
        0 * u.arcsec, 0 * u.arcsec, obstime=DATE, observer=observer, frame=frames.Helioprojective
    )
    header = sunpy_map.make_fitswcs_header(data, ref_coord, scale=[scale, scale] * u.arcsec / u.pix)
    return sunpy_map.Map(data, header)


def test_screen_context_selects_the_sunpy_screen(monkeypatch):
    import sunpy.coordinates.screens as screens

    entered = []

    class Recorder:
        def __init__(self, name):
            self.name = name

        def __call__(self, observer):
            entered.append(self.name)
            return contextlib.nullcontext()

    monkeypatch.setattr(screens, "SphericalScreen", Recorder("spherical"))
    monkeypatch.setattr(screens, "PlanarScreen", Recorder("planar"))

    m = make_view_map(0.0)
    for token in (SCREEN_SURFACE, "", SCREEN_SPHERICAL, SCREEN_PLANAR):
        with _screen_context(m, token):
            pass
    # "surface" and "" must not enter any screen; the other two must.
    assert entered == ["spherical", "planar"]


def test_default_screen_is_unchanged_behaviour():
    """The default must stay byte-identical, so Compare Viewpoint is untouched."""
    earth = off_disk_ring_map(0.0)
    stereo = off_disk_ring_map(60.0)
    plain = reproject_map_to(stereo, earth)
    explicit = reproject_map_to(stereo, earth, screen=SCREEN_SURFACE)
    assert np.array_equal(np.isnan(plain.data), np.isnan(explicit.data))
    assert np.allclose(plain.data, explicit.data, equal_nan=True)


def test_spherical_screen_recovers_more_off_disk_signal():
    """The surface assumption discards off-disk structure a screen keeps."""
    earth = off_disk_ring_map(0.0)
    stereo = off_disk_ring_map(60.0)
    surface = reproject_map_to(stereo, earth, screen=SCREEN_SURFACE)
    spherical = reproject_map_to(stereo, earth, screen=SCREEN_SPHERICAL)
    assert spherical.data.shape == earth.data.shape
    assert np.isfinite(spherical.data).mean() > np.isfinite(surface.data).mean()


def test_screen_is_centred_on_the_target_observer(monkeypatch):
    """Regression: the screen belongs to the target, not the source.

    ``reproject`` walks the *output* pixels, turns each into a world coordinate
    in the target frame and only then transforms it to the source frame, so the
    2-D-to-3-D assumption is applied in the target's frame. Centring on the
    source observer quietly loses signal as the baseline grows.
    """
    import sunpy.coordinates.screens as screens

    seen = {}

    def recorder(observer, **kwargs):
        seen["lon"] = float(observer.transform_to(
            HeliographicStonyhurst(obstime=DATE)).lon.to_value(u.deg))
        return contextlib.nullcontext()

    monkeypatch.setattr(screens, "SphericalScreen", recorder)
    target = make_view_map(0.0)     # Earth view
    source = make_view_map(60.0)    # STEREO view
    reproject_map_to(source, target, screen=SCREEN_SPHERICAL)
    assert seen["lon"] == pytest.approx(0.0, abs=1.0)   # target's observer, not 60


def test_spherical_screen_survives_a_large_baseline():
    """At STEREO-behind-the-Sun separations the wrong screen returned nothing."""
    earth = off_disk_ring_map(0.0)
    far = off_disk_ring_map(148.0)
    out = reproject_map_to(far, earth, screen=SCREEN_SPHERICAL)
    assert np.isfinite(out.data).mean() > 0.5


def test_screen_falls_back_when_the_observer_is_unusable():
    """A malformed observer must not abort a long batch composite."""

    class NoObserver:
        wcs = "TARGET"
        observer_coordinate = "not-a-coordinate"

        def reproject_to(self, wcs, **kwargs):
            return "REPROJECTED"

    target = NoObserver()
    assert reproject_map_to(NoObserver(), target, screen=SCREEN_SPHERICAL) == "REPROJECTED"


def test_screen_is_forwarded_by_coalign_and_blink(monkeypatch):
    seen = []

    def fake_reproject(source, target, **kwargs):
        seen.append(kwargs.get("screen"))
        return source

    monkeypatch.setattr("src.Backend.multiview.reproject_map_to", fake_reproject)
    earth = make_view_map(0.0)
    coalign_to_reference(earth, [make_view_map(60.0)], screen=SCREEN_SPHERICAL)
    blink_pair(earth, make_view_map(45.0), screen=SCREEN_PLANAR)
    assert seen == [SCREEN_SPHERICAL, SCREEN_PLANAR]
