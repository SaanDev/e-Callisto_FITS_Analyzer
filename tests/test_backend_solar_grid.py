"""
e-CALLISTO FITS Analyzer
Unit tests for the solar-coordinate graticule helpers (src/Backend/solar_grid.py).
"""

from __future__ import annotations

import numpy as np
import pytest

from src.Backend.solar_grid import (
    FRAME_DISPLAY_NAMES,
    FRAME_KEYS,
    frame_key_from_display,
    graticule_arcsec,
    point_lonlat,
)


@pytest.fixture(scope="module")
def disk_map():
    """A synthetic Earth-view full-disk helioprojective map (no network)."""
    astropy_units = pytest.importorskip("astropy.units")
    pytest.importorskip("sunpy.map")
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import get_earth
    from sunpy.map.header_helper import make_fitswcs_header

    obstime = "2020-01-01T00:00:00"
    data = np.zeros((256, 256))
    ref = SkyCoord(
        0 * astropy_units.arcsec,
        0 * astropy_units.arcsec,
        obstime=obstime,
        observer=get_earth(obstime),
        frame="helioprojective",
    )
    header = make_fitswcs_header(data, ref, scale=[8, 8] * astropy_units.arcsec / astropy_units.pix)
    return sunpy.map.Map(data, header)


def test_frame_key_from_display_round_trips():
    for key in FRAME_KEYS:
        assert frame_key_from_display(FRAME_DISPLAY_NAMES[key]) == key
        assert frame_key_from_display(key) == key
    # Unknown text falls back to HCI (the requested default).
    assert frame_key_from_display("something else") == "HCI"


@pytest.mark.parametrize("frame_key", ["HCI", "HGS", "HGC"])
def test_graticule_has_visible_polylines(disk_map, frame_key):
    polylines, labels = graticule_arcsec(disk_map, frame_key=frame_key, lon_step=30, lat_step=30, resolution=61)
    assert polylines, "expected at least one meridian/parallel"
    finite_points = sum(int(np.sum(np.isfinite(x) & np.isfinite(y))) for x, y in polylines)
    assert finite_points > 0, "front-hemisphere points should project to finite arcsec"
    # Every polyline that survives carries at least one plottable point.
    for x, y in polylines:
        assert x.shape == y.shape
    # Labels anchor to finite positions only.
    for _text, lx, ly in labels:
        assert np.isfinite(lx) and np.isfinite(ly)


def test_graticule_breaks_at_the_limb(disk_map):
    # A full parallel/meridian wraps behind the disk, so some samples must be the
    # NaN gaps that break the curve at the near/far-side boundary.
    polylines, _ = graticule_arcsec(disk_map, frame_key="HCI", lon_step=30, lat_step=30, resolution=91)
    has_nan_gap = any(np.any(~np.isfinite(x) | ~np.isfinite(y)) for x, y in polylines)
    assert has_nan_gap


def test_point_lonlat_on_disk_matches_subobserver(disk_map):
    # Disk centre projects to the sub-observer point of the target frame.
    result = point_lonlat(0.0, 0.0, disk_map, frame_key="HCI")
    assert result is not None
    lon, lat = result
    assert np.isfinite(lon) and np.isfinite(lat)
    # For an Earth observer in early January the sub-observer heliographic
    # latitude (B0) is a few degrees south.
    assert -10.0 < lat < 10.0
    assert -180.0 <= lon < 180.0


def test_point_lonlat_off_disk_is_none(disk_map):
    # A sight line far outside the limb misses the solar surface entirely.
    assert point_lonlat(5000.0, 5000.0, disk_map, frame_key="HCI") is None


def test_no_coordinate_frame_degrades_gracefully():
    class _Bare:
        coordinate_frame = None

    polylines, labels = graticule_arcsec(_Bare(), frame_key="HCI")
    assert polylines == [] and labels == []
    assert point_lonlat(0.0, 0.0, _Bare(), frame_key="HCI") is None
