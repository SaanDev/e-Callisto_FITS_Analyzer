"""
e-CALLISTO FITS Analyzer
Unit tests for foreshortening-corrected length and area on the solar disk
(src/backend/solar/surface_measure.py).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.backend.solar.coronagraph import RSUN_KM
from src.backend.solar.surface_measure import (
    DEFAULT_PENUMBRA_LEVEL,
    DEFAULT_UMBRA_LEVEL,
    LIMB_WARNING_MU,
    SurfaceGeometry,
    ellipse_mask,
    fit_quiet_sun_profile,
    geometry_from_map,
    geometry_from_meta,
    km2_to_msh,
    mask_surface_area,
    msh_to_km2,
    mu_map,
    normalize_to_quiet_sun,
    polygon_mask,
    polygon_mask_from_arcsec,
    polyline_surface_length,
    project_to_sphere,
    rectangle_mask,
    remove_small_clusters,
    stonyhurst_from_arcsec,
    sunspot_masks,
    threshold_mask,
)

RSUN = 960.0  # arcsec, roughly SDO's view


def _centred(n: int, scale: float, **kwargs) -> SurfaceGeometry:
    return SurfaceGeometry(
        rsun_arcsec=RSUN,
        x_ref_pix=(n - 1) / 2.0,
        y_ref_pix=(n - 1) / 2.0,
        x_scale_arcsec_per_pix=scale,
        y_scale_arcsec_per_pix=scale,
        **kwargs,
    )


def _hgs_to_arcsec(lon_deg, lat_deg, geometry: SurfaceGeometry):
    """Stonyhurst -> helioprojective, written independently of the module under test."""
    lon = np.radians(np.asarray(lon_deg, dtype=float) - geometry.observer_lon_deg)
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    b0 = math.radians(geometry.b0_deg)
    x = np.cos(lat) * np.sin(lon)
    y = np.sin(lat) * math.cos(b0) - np.cos(lat) * np.cos(lon) * math.sin(b0)
    z = np.sin(lat) * math.sin(b0) + np.cos(lat) * np.cos(lon) * math.cos(b0)
    big_d = geometry.observer_distance_rsun
    zeta = big_d - z
    dist = np.sqrt(x * x + y * y + zeta * zeta)
    tx = np.degrees(np.arctan2(x, zeta)) * 3600.0
    ty = np.degrees(np.arcsin(y / dist)) * 3600.0
    return tx, ty


def _cap_mask(geometry: SurfaceGeometry, n: int, lon0: float, lat0: float, radius_deg: float) -> np.ndarray:
    tx, ty = geometry.pixel_to_arcsec(np.arange(n)[None, :], np.arange(n)[:, None])
    lon, lat = stonyhurst_from_arcsec(tx, ty, geometry)
    l1, b1 = np.radians(lon), np.radians(lat)
    cos_d = np.sin(b1) * math.sin(math.radians(lat0)) + np.cos(b1) * math.cos(math.radians(lat0)) * np.cos(
        l1 - math.radians(lon0)
    )
    return np.nan_to_num(cos_d, nan=-2.0) > math.cos(math.radians(radius_deg))


def _cap_msh(radius_deg: float) -> float:
    return (1.0 - math.cos(math.radians(radius_deg))) * 1e6


# --------------------------------------------------------------------- geometry
def test_geometry_rejects_bad_inputs():
    with pytest.raises(ValueError):
        SurfaceGeometry(rsun_arcsec=0.0)
    with pytest.raises(ValueError):
        SurfaceGeometry(rsun_arcsec=float("nan"))
    with pytest.raises(ValueError):
        SurfaceGeometry(rsun_arcsec=RSUN, x_scale_arcsec_per_pix=0.0)


def test_from_axis_transform_round_trips_pixels():
    transform = {
        "x_ref_pix": 2047.5,
        "y_ref_pix": 2047.5,
        "x_scale_arcsec_per_pix": 0.6,
        "y_scale_arcsec_per_pix": 0.6,
        "x_ref_arcsec": 1.5,
        "y_ref_arcsec": -2.0,
    }
    g = SurfaceGeometry.from_axis_transform(transform, rsun_arcsec=RSUN, b0_deg=3.0)
    tx, ty = g.pixel_to_arcsec(100.0, 3000.0)
    assert float(tx) == pytest.approx(1.5 + (100.0 - 2047.5) * 0.6)
    assert float(ty) == pytest.approx(-2.0 + (3000.0 - 2047.5) * 0.6)
    cols, rows = g.arcsec_to_pixel(tx, ty)
    assert float(cols) == pytest.approx(100.0)
    assert float(rows) == pytest.approx(3000.0)
    assert g.b0_deg == 3.0


def test_projection_centre_and_limb():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    centre = project_to_sphere(0.0, 0.0, g)
    assert bool(centre.on_disk)
    assert float(centre.mu) == pytest.approx(1.0)
    assert float(centre.z) == pytest.approx(1.0)
    assert float(centre.distance) == pytest.approx(g.observer_distance_rsun - 1.0)

    inside = project_to_sphere(RSUN - 1e-3, 0.0, g)
    outside = project_to_sphere(RSUN + 1e-3, 0.0, g)
    assert bool(inside.on_disk)
    assert float(inside.mu) < 0.01
    assert not bool(outside.on_disk)
    assert math.isnan(float(outside.x))


def test_stonyhurst_round_trip_with_b0_and_observer_longitude():
    g = SurfaceGeometry(rsun_arcsec=RSUN, b0_deg=-6.5, observer_lon_deg=12.0)
    lons = np.array([-60.0, -10.0, 12.0, 40.0, 75.0])
    lats = np.array([-35.0, 5.0, 0.0, 22.0, 60.0])
    tx, ty = _hgs_to_arcsec(lons, lats, g)
    lon, lat = stonyhurst_from_arcsec(tx, ty, g)
    np.testing.assert_allclose(lon, lons, atol=1e-6)
    np.testing.assert_allclose(lat, lats, atol=1e-6)


def test_stonyhurst_matches_sunpy():
    """The perspective projection must agree with SunPy's own back-projection."""
    pytest.importorskip("sunpy")
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import frames

    obstime = "2024-05-10T12:00:00"
    observer = SkyCoord(
        lon=0.0 * u.deg,
        lat=-3.2 * u.deg,
        radius=0.985 * u.AU,
        frame=frames.HeliographicStonyhurst,
        obstime=obstime,
    )
    rsun_arcsec = math.degrees(math.asin(RSUN_KM / (0.985 * 1.495978707e8))) * 3600.0
    g = SurfaceGeometry(rsun_arcsec=rsun_arcsec, b0_deg=-3.2, rsun_km=RSUN_KM)

    tx = np.array([0.0, -500.0, 700.0, 300.0, -900.0, 950.0])
    ty = np.array([0.0, 300.0, -400.0, 850.0, -100.0, 50.0])
    hpc = SkyCoord(
        tx * u.arcsec,
        ty * u.arcsec,
        frame=frames.Helioprojective(observer=observer, obstime=obstime, rsun=RSUN_KM * u.km),
    )
    hgs = hpc.transform_to(frames.HeliographicStonyhurst(obstime=obstime))
    lon, lat = stonyhurst_from_arcsec(tx, ty, g)
    expected_lon = (hgs.lon.to_value(u.deg) + 180.0) % 360.0 - 180.0
    np.testing.assert_allclose(lon, expected_lon, atol=1e-4)
    np.testing.assert_allclose(lat, hgs.lat.to_value(u.deg), atol=1e-4)


def test_mu_map_is_one_at_centre_and_nan_off_disk():
    n = 101
    g = _centred(n, 25.0)
    mu = mu_map((n, n), g)
    assert mu[50, 50] == pytest.approx(1.0)
    assert math.isnan(mu[0, 0])
    assert np.nanmin(mu) >= 0.0


def test_geometry_from_map_reads_header():
    sunpy_map = pytest.importorskip("sunpy.map")
    import astropy.units as u

    header = {
        "naxis": 2,
        "naxis1": 64,
        "naxis2": 64,
        "ctype1": "HPLN-TAN",
        "ctype2": "HPLT-TAN",
        "cunit1": "arcsec",
        "cunit2": "arcsec",
        "cdelt1": 38.4,
        "cdelt2": 38.4,
        "crpix1": 32.5,
        "crpix2": 32.5,
        "crval1": 0.0,
        "crval2": 0.0,
        "date-obs": "2024-05-10T12:00:00",
        "rsun_obs": 950.0,
        "rsun_ref": 696_000_000.0,
        "hgln_obs": 0.0,
        "hglt_obs": -3.2,
        "dsun_obs": 1.473e11,
    }
    frame = sunpy_map.Map(np.zeros((64, 64)), header)
    transform = {"x_ref_pix": 31.5, "y_ref_pix": 31.5, "x_scale_arcsec_per_pix": 38.4, "y_scale_arcsec_per_pix": 38.4}
    g = geometry_from_map(frame, transform)
    assert g.rsun_arcsec == pytest.approx(frame.rsun_obs.to_value(u.arcsec))
    assert g.b0_deg == pytest.approx(-3.2)
    assert g.rsun_km == pytest.approx(696_000.0)
    assert g.x_scale_arcsec_per_pix == pytest.approx(38.4)


def test_geometry_from_meta_reads_keys_and_defaults():
    transform = {"x_ref_pix": 10.0, "y_ref_pix": 10.0, "x_scale_arcsec_per_pix": 2.0, "y_scale_arcsec_per_pix": 2.0}
    g = geometry_from_meta(
        {"HGLT_OBS": "-4.5", "hgln_obs": 1.25, "RSUN_REF": 696_000_000.0},
        transform,
        rsun_arcsec=950.0,
    )
    assert g.b0_deg == pytest.approx(-4.5)
    assert g.observer_lon_deg == pytest.approx(1.25)
    assert g.rsun_km == pytest.approx(696_000.0)
    assert g.rsun_arcsec == 950.0
    assert g.x_ref_pix == 10.0

    fallback = geometry_from_meta({"crlt_obs": 3.0, "hglt_obs": "n/a"}, transform, rsun_arcsec=950.0)
    assert fallback.b0_deg == pytest.approx(3.0)
    assert fallback.rsun_km == RSUN_KM
    bare = geometry_from_meta(None, transform, rsun_arcsec=950.0)
    assert (bare.b0_deg, bare.observer_lon_deg) == (0.0, 0.0)


def test_geometry_from_map_without_radius_raises():
    class NoRadius:
        @property
        def rsun_obs(self):
            raise AttributeError("no rsun")

    with pytest.raises(ValueError, match="solar radius"):
        geometry_from_map(NoRadius(), {})


# ----------------------------------------------------------------------- length
def test_length_needs_two_points():
    with pytest.raises(ValueError):
        polyline_surface_length([(0.0, 0.0)], SurfaceGeometry(rsun_arcsec=RSUN))


def test_length_along_equator_is_great_circle():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    lons = np.linspace(-80.0, 80.0, 161)
    tx, ty = _hgs_to_arcsec(lons, np.zeros_like(lons), g)
    result = polyline_surface_length(np.column_stack((tx, ty)), g)
    expected = math.radians(160.0) * RSUN_KM
    assert result.total_km == pytest.approx(expected, rel=1e-4)
    assert result.on_disk_km == pytest.approx(result.total_km)
    assert result.off_limb_km == pytest.approx(0.0)
    assert result.plane_of_sky_km < 0.75 * result.total_km  # foreshortened
    assert not result.crosses_limb
    assert result.near_limb  # it reaches 80 deg, mu ~ 0.17
    assert result.start_lonlat == pytest.approx((-80.0, 0.0), abs=1e-6)
    assert result.end_lonlat == pytest.approx((80.0, 0.0), abs=1e-6)
    assert len(result.segment_km) == 160
    assert sum(result.segment_km) == pytest.approx(result.total_km)


def test_short_length_at_disk_centre_matches_plane_of_sky():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    result = polyline_surface_length([(-5.0, 0.0), (5.0, 0.0)], g)
    # The plane-of-sky scale is taken at Sun centre (as the ruler does); the
    # sub-observer point is one solar radius nearer, so it is 1 - 1/D smaller.
    near_factor = 1.0 - 1.0 / g.observer_distance_rsun
    assert result.total_km == pytest.approx(result.plane_of_sky_km * near_factor, rel=1e-4)
    assert result.plane_of_sky_arcsec == pytest.approx(10.0)
    assert result.min_mu == pytest.approx(1.0, abs=1e-4)


def test_length_near_limb_is_longer_than_it_looks():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    # Five degrees of longitude along the equator, centred at 70 deg west.
    tx, ty = _hgs_to_arcsec(np.array([67.5, 72.5]), np.zeros(2), g)
    result = polyline_surface_length(np.column_stack((tx, ty)), g)
    assert result.total_km == pytest.approx(math.radians(5.0) * RSUN_KM, rel=1e-3)
    ratio = result.total_km / result.plane_of_sky_km
    assert ratio == pytest.approx(1.0 / math.cos(math.radians(70.0)), rel=0.02)


def test_length_off_limb_is_plane_of_sky_and_split():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    # From 0.9 R on the equator radially out to 1.2 R.
    result = polyline_surface_length([(0.9 * RSUN, 0.0), (1.2 * RSUN, 0.0)], g)
    assert result.crosses_limb
    assert result.off_limb_km == pytest.approx(0.2 * RSUN * g.km_per_arcsec, rel=1e-3)
    # The on-disk stretch from 0.9 R to the limb is the arc from asin(0.9) to ~90 deg.
    expected_disk = (math.pi / 2.0 - math.asin(0.9)) * RSUN_KM
    assert result.on_disk_km == pytest.approx(expected_disk, rel=0.01)
    assert result.total_km == pytest.approx(result.on_disk_km + result.off_limb_km)
    assert result.end_lonlat is None


def test_fully_off_limb_line_has_no_disk_part():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    result = polyline_surface_length([(1100.0, 0.0), (1100.0, 300.0)], g)
    assert result.on_disk_km == 0.0
    assert result.min_mu is None
    assert result.total_km == pytest.approx(300.0 * g.km_per_arcsec)


def test_repeated_vertex_contributes_nothing():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    a = polyline_surface_length([(0.0, 0.0), (100.0, 0.0)], g)
    b = polyline_surface_length([(0.0, 0.0), (0.0, 0.0), (100.0, 0.0)], g)
    assert b.total_km == pytest.approx(a.total_km)
    assert b.segment_km[0] == 0.0


# ------------------------------------------------------------------------ masks
def test_polygon_mask_square_and_triangle():
    square = polygon_mask((20, 20), [(2, 2), (7, 2), (7, 7), (2, 7)])
    # Centres on the top/right edges are outside under the half-open rule, so a
    # 5 x 5 pixel square holds 5 x 5 centres.
    assert int(square.sum()) == 25
    assert square[2, 2] and square[6, 6] and not square[7, 7]

    triangle = polygon_mask((100, 100), [(0, 0), (80, 0), (0, 80)])
    assert int(triangle.sum()) == pytest.approx(0.5 * 80 * 80, rel=0.03)


def test_polygon_mask_concave_and_degenerate():
    # A "C" shape: the notch must stay empty.
    c_shape = [(0, 0), (30, 0), (30, 10), (10, 10), (10, 20), (30, 20), (30, 30), (0, 30)]
    mask = polygon_mask((40, 40), c_shape)
    assert not mask[15, 20]
    assert mask[15, 5]
    assert int(mask.sum()) == pytest.approx(30 * 30 - 20 * 10, rel=0.05)
    assert not polygon_mask((10, 10), [(1, 1), (5, 5)]).any()
    assert not polygon_mask((10, 10), [(50, 50), (60, 50), (60, 60)]).any()


def test_freehand_lasso_circle_area():
    angles = np.linspace(0.0, 2.0 * math.pi, 400, endpoint=False)
    verts = np.column_stack((50 + 30 * np.cos(angles), 50 + 30 * np.sin(angles)))
    mask = polygon_mask((100, 100), verts)
    assert int(mask.sum()) == pytest.approx(math.pi * 30 * 30, rel=0.01)


def test_ellipse_and_rectangle_masks():
    ellipse = ellipse_mask((200, 200), (100.0, 100.0), (60.0, 20.0), angle_deg=30.0)
    assert int(ellipse.sum()) == pytest.approx(math.pi * 60 * 20, rel=0.01)
    rotated_far = ellipse_mask((200, 200), (100.0, 100.0), (60.0, 20.0), angle_deg=90.0)
    assert rotated_far[150, 100] and not rotated_far[100, 150]
    rect = rectangle_mask((50, 50), (10.2, 40.0), (3.0, 20.0))
    assert int(rect.sum()) == 8 * 21  # centres at cols 3..10, rows 20..40
    assert not ellipse_mask((10, 10), (5, 5), (0.0, 3.0)).any()


def test_polygon_from_arcsec_uses_the_geometry():
    n = 201
    g = _centred(n, 2.0)
    # A 100" x 100" square at disk centre: 50 x 50 pixels.
    mask = polygon_mask_from_arcsec((n, n), [(-50, -50), (50, -50), (50, 50), (-50, 50)], g)
    assert int(mask.sum()) == 50 * 50


# ------------------------------------------------------------------------- area
@pytest.mark.parametrize("n, scale", [(512, 4.8), (1024, 2.4)])
def test_full_disk_area_is_the_visible_fraction(n, scale):
    g = _centred(n, scale)
    area = mask_surface_area(np.ones((n, n), dtype=bool), g)
    visible_msh = 1e6 * (1.0 - 1.0 / g.observer_distance_rsun)
    assert area.on_disk_msh == pytest.approx(visible_msh, rel=5e-4)
    assert area.on_disk_pixels == pytest.approx(math.pi * (RSUN / scale) ** 2, rel=1e-4)
    assert area.has_off_limb
    assert area.off_limb_pixels == pytest.approx(n * n - area.on_disk_pixels)
    assert area.total_arcsec2 == pytest.approx(n * n * scale * scale)
    assert area.centroid_lonlat == pytest.approx((0.0, 0.0), abs=1e-6)


@pytest.mark.parametrize("lon0", [0.0, 45.0, 70.0])
def test_spherical_cap_area_is_independent_of_position(lon0):
    n = 2048
    g = _centred(n, 1.0, b0_deg=5.0)
    mask = _cap_mask(g, n, lon0, 20.0, 5.0)
    area = mask_surface_area(mask, g)
    assert area.on_disk_msh == pytest.approx(_cap_msh(5.0), rel=0.005)
    assert area.off_limb_pixels == pytest.approx(0.0)
    assert area.centroid_lonlat == pytest.approx((lon0, 20.0), abs=0.01)
    # The image shows less area than there is; the ratio is ~1/mu at the cap.
    assert area.on_disk_km2 >= area.on_disk_plane_of_sky_km2
    assert area.near_limb == (lon0 >= 70.0)


def test_cap_near_limb_converges_with_resolution():
    """Pixel masks overestimate slightly at mu ~ 0.02; finer pixels close the gap."""
    errors = []
    for scale in (1.0, 0.5):
        n = int(2000 / scale)
        g = _centred(n, scale, b0_deg=5.0)
        area = mask_surface_area(_cap_mask(g, n, 85.0, 20.0, 5.0), g)
        errors.append(abs(area.on_disk_msh / _cap_msh(5.0) - 1.0))
        assert area.near_limb
        assert area.min_mu < LIMB_WARNING_MU
    assert errors[0] < 0.03
    assert errors[1] < errors[0]


def test_limb_ring_area_is_exact():
    n = 4600
    scale = 0.5
    g = _centred(n, scale)
    tx, ty = g.pixel_to_arcsec(np.arange(n)[None, :], np.arange(n)[:, None])
    ring = np.hypot(tx, ty) >= 0.995 * RSUN
    area = mask_surface_area(ring, g)
    rho = math.radians(RSUN / 3600.0)
    theta = math.radians(0.995 * RSUN / 3600.0)
    big_d = g.observer_distance_rsun
    alpha_in = math.asin(big_d * math.sin(theta)) - theta
    expected_msh = (math.cos(alpha_in) - math.cos(math.pi / 2 - rho)) * 1e6
    assert area.on_disk_msh == pytest.approx(expected_msh, rel=1e-3)


def test_off_limb_region_is_plane_of_sky():
    n = 800
    scale = 4.0
    g = _centred(n, scale)
    c0, r0 = g.arcsec_to_pixel(1100.0, -40.0)
    c0, r0 = int(math.ceil(float(c0))), int(math.ceil(float(r0)))
    mask = rectangle_mask((n, n), (c0, r0), (c0 + 39, r0 + 19))  # 40 x 20 px above the west limb
    assert int(mask.sum()) == 800
    area = mask_surface_area(mask, g)
    assert area.on_disk_pixels == 0.0
    assert area.on_disk_msh == 0.0
    assert area.off_limb_km2 == pytest.approx(800 * scale * scale * g.km_per_arcsec**2)
    assert area.centroid_lonlat is None
    assert area.mean_mu is None


def test_region_straddling_the_limb_is_split():
    n = 800
    scale = 4.0
    g = _centred(n, scale)
    tx, ty = g.pixel_to_arcsec(np.arange(n)[None, :], np.arange(n)[:, None])
    box = (np.abs(ty) < 60.0) & (tx > RSUN - 80.0) & (tx < RSUN + 80.0)
    area = mask_surface_area(box, g)
    assert area.on_disk_pixels > 0.0
    assert area.off_limb_pixels > 0.0
    assert area.on_disk_pixels + area.off_limb_pixels == pytest.approx(area.n_pixels)
    assert area.on_disk_pixels == pytest.approx(area.off_limb_pixels, rel=0.1)
    assert area.near_limb
    lon, lat = area.centroid_lonlat
    assert lon > 60.0
    assert abs(lat) < 2.0


def test_mean_and_total_intensity():
    n = 64
    g = _centred(n, 10.0)
    image = np.full((n, n), 2.0)
    image[0, 0] = np.nan
    mask = np.zeros((n, n), dtype=bool)
    mask[:2, :2] = True
    area = mask_surface_area(mask, g, image=image)
    assert area.mean_intensity == pytest.approx(2.0)
    assert area.total_intensity == pytest.approx(6.0)
    with pytest.raises(ValueError):
        mask_surface_area(mask, g, image=np.zeros((3, 3)))


def test_empty_mask():
    g = _centred(16, 10.0)
    area = mask_surface_area(np.zeros((16, 16), dtype=bool), g)
    assert area.n_pixels == 0
    assert area.on_disk_msh == 0.0
    assert area.centroid_arcsec is None
    assert area.centroid_lonlat is None
    assert area.mean_intensity is None


def test_msh_conversions():
    one_msh_km2 = 2.0 * math.pi * RSUN_KM**2 * 1e-6
    assert msh_to_km2(1.0) == pytest.approx(one_msh_km2)
    assert km2_to_msh(one_msh_km2) == pytest.approx(1.0)
    assert one_msh_km2 == pytest.approx(3.04e6, rel=0.01)  # the textbook ~3 million km^2


def test_area_honours_rsun_km():
    g1 = _centred(256, 4.0)
    g2 = _centred(256, 4.0, rsun_km=2 * RSUN_KM)
    mask = np.zeros((256, 256), dtype=bool)
    mask[120:136, 120:136] = True
    a1 = mask_surface_area(mask, g1)
    a2 = mask_surface_area(mask, g2)
    assert a2.on_disk_msh == pytest.approx(a1.on_disk_msh)
    assert a2.on_disk_km2 == pytest.approx(4.0 * a1.on_disk_km2)


# -------------------------------------------------------------------- quiet Sun
def _limb_darkened_disk(n: int, scale: float, *, spot: bool = True):
    g = _centred(n, scale)
    mu = mu_map((n, n), g)
    u2, v2 = 0.88, -0.23  # quadratic limb darkening, roughly HMI continuum
    image = 50_000.0 * (1.0 - u2 * (1.0 - mu) - v2 * (1.0 - mu * mu))
    image = np.where(np.isfinite(mu), image, 0.0)
    rng = np.random.default_rng(42)
    image = image * (1.0 + 0.01 * rng.standard_normal(image.shape))
    tx, ty = g.pixel_to_arcsec(np.arange(n)[None, :], np.arange(n)[:, None])
    umbra = penumbra = np.zeros((n, n), dtype=bool)
    if spot:
        r = np.hypot(tx - 400.0, ty - 200.0)
        umbra = r < 15.0
        penumbra = (r >= 15.0) & (r < 35.0)
        image[umbra] *= 0.3
        image[penumbra] *= 0.75
    return g, image, mu, umbra, penumbra


def test_quiet_sun_profile_recovers_limb_darkening():
    g, image, mu, _, _ = _limb_darkened_disk(800, 2.6)
    profile = fit_quiet_sun_profile(image, g)
    for m in (1.0, 0.7, 0.4, 0.2):
        truth = 50_000.0 * (1.0 - 0.88 * (1.0 - m) + 0.23 * (1.0 - m * m))
        assert float(profile.level_at(m)) == pytest.approx(truth, rel=0.005)
    assert profile.disk_centre_level == pytest.approx(50_000.0, rel=0.005)
    # Off-limb (NaN mu) and below mu_min both use the limb level.
    assert float(profile.level_at(np.nan)) == pytest.approx(profile.limb_level)
    assert float(profile.level_at(0.01)) == pytest.approx(profile.limb_level)


def test_normalised_quiet_sun_is_flat_and_spot_is_dark():
    g, image, mu, umbra, penumbra = _limb_darkened_disk(800, 2.6)
    profile = fit_quiet_sun_profile(image, g)
    norm = normalize_to_quiet_sun(image, g, profile)
    quiet = np.isfinite(mu) & (mu > 0.2) & ~umbra & ~penumbra
    assert float(np.median(norm[quiet])) == pytest.approx(1.0, abs=0.003)
    assert float(np.median(norm[umbra])) == pytest.approx(0.3, abs=0.02)
    assert float(np.median(norm[penumbra])) == pytest.approx(0.75, abs=0.02)

    region = np.zeros_like(umbra)
    region[:10, :10] = True
    only_region = normalize_to_quiet_sun(image, g, profile, region=region)
    assert np.isnan(only_region[~region]).all()


def test_quiet_sun_fit_needs_a_disk():
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=-5000.0, y_ref_pix=0.0)
    with pytest.raises(ValueError, match="disk"):
        fit_quiet_sun_profile(np.ones((50, 50)), g)


def test_degree_zero_profile_is_the_disk_median():
    g, image, _, _, _ = _limb_darkened_disk(400, 5.2, spot=False)
    profile = fit_quiet_sun_profile(image, g, degree=0)
    assert float(profile.level_at(1.0)) == pytest.approx(float(profile.level_at(0.3)))


# ------------------------------------------------------------------- thresholds
def test_sunspot_masks_recover_umbra_and_penumbra_areas():
    g, image, _, umbra_true, penumbra_true = _limb_darkened_disk(800, 2.6)
    profile = fit_quiet_sun_profile(image, g)
    norm = normalize_to_quiet_sun(image, g, profile)
    tx, ty = g.pixel_to_arcsec(np.arange(800)[None, :], np.arange(800)[:, None])
    region = np.hypot(tx - 400.0, ty - 200.0) < 60.0
    masks = sunspot_masks(norm, region)
    assert masks.umbra_level == DEFAULT_UMBRA_LEVEL
    assert masks.penumbra_level == DEFAULT_PENUMBRA_LEVEL
    assert int(masks.umbra.sum()) == int(umbra_true.sum())
    assert int(masks.penumbra.sum()) == int(penumbra_true.sum())
    assert not (masks.umbra & masks.penumbra).any()
    assert np.array_equal(masks.spot, masks.umbra | masks.penumbra)

    spot_area = mask_surface_area(masks.spot, g)
    umbra_area = mask_surface_area(masks.umbra, g)
    penumbra_area = mask_surface_area(masks.penumbra, g)
    assert spot_area.on_disk_msh == pytest.approx(umbra_area.on_disk_msh + penumbra_area.on_disk_msh)


def test_sunspot_levels_must_be_ordered():
    with pytest.raises(ValueError):
        sunspot_masks(np.ones((4, 4)), umbra_level=0.9, penumbra_level=0.6)


def test_threshold_mask_bounds_region_and_nan():
    values = np.array([[0.5, 1.0, 2.5], [np.nan, 3.0, 0.95]])
    assert threshold_mask(values, below=0.96).tolist() == [[True, False, False], [False, False, True]]
    assert threshold_mask(values, above=2.5).tolist() == [[False, False, True], [False, True, False]]
    region = np.array([[True, True, True], [False, False, False]])
    assert threshold_mask(values, region, above=0.9, below=3.0).tolist() == [
        [False, True, True],
        [False, False, False],
    ]
    with pytest.raises(ValueError):
        threshold_mask(values)


def test_small_clusters_are_removed():
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:6, 2:6] = True  # 16 px
    mask[10, 10] = True  # 1 px noise
    mask[15, 15] = mask[16, 16] = True  # diagonal pair: 8-connected, 2 px
    cleaned = remove_small_clusters(mask, 3)
    assert int(cleaned.sum()) == 16
    assert int(remove_small_clusters(mask, 2).sum()) == 18
    assert np.array_equal(remove_small_clusters(mask, 1), mask)
    assert int(threshold_mask(mask.astype(float), above=0.5, min_cluster_px=3).sum()) == 16
