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
    MAX_BACKGROUND_SPREAD,
    SurfaceGeometry,
    arcsec_from_stonyhurst,
    background_spread,
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
    rotate_points,
    stonyhurst_from_arcsec,
    sunspot_masks,
    synodic_rotation_deg,
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
        # A consistent header: RSUN_OBS = asin(RSUN_REF / DSUN_OBS).
        "rsun_obs": math.degrees(math.asin(696_000_000.0 / 1.473e11)) * 3600.0,
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


# ------------------------------------------------------------- solar rotation
def test_synodic_rotation_matches_sunpy():
    pytest.importorskip("sunpy")
    import astropy.units as u
    from sunpy.sun.models import differential_rotation

    lats = np.array([-60.0, -20.0, 0.0, 15.0, 45.0, 75.0])
    for seconds in (3600.0, 2.5 * 86400.0, -86400.0):
        expected = differential_rotation(seconds * u.s, lats * u.deg, model="howard", frame_time="synodic")
        expected = (expected.to_value(u.deg) + 180.0) % 360.0 - 180.0
        np.testing.assert_allclose(synodic_rotation_deg(lats, seconds), expected, atol=1e-9)
    # Howard's equator: 2.894 urad/s = 14.33 deg/day sidereal, 13.34 deg/day from Earth.
    assert float(synodic_rotation_deg(0.0, 86400.0)) == pytest.approx(13.34, abs=0.01)


def test_rotate_points_moves_disk_points_with_the_sun():
    g = SurfaceGeometry(rsun_arcsec=RSUN, b0_deg=4.0)
    lons = np.array([-30.0, 10.0])
    lats = np.array([20.0, -35.0])
    tx, ty = _hgs_to_arcsec(lons, lats, g)
    seconds = 86400.0
    out = rotate_points(np.column_stack((tx, ty)), g, g, seconds)
    assert out.rotated == (True, True)
    assert not out.any_hidden
    lon2, lat2 = stonyhurst_from_arcsec(np.array(out.points)[:, 0], np.array(out.points)[:, 1], g)
    np.testing.assert_allclose(lat2, lats, atol=1e-6)
    np.testing.assert_allclose(lon2 - lons, synodic_rotation_deg(lats, seconds), atol=1e-6)


def test_rotate_points_keeps_off_limb_and_flags_far_side():
    g = SurfaceGeometry(rsun_arcsec=RSUN)
    near_west_limb = _hgs_to_arcsec(np.array([85.0]), np.array([0.0]), g)
    pts = [(1200.0, 0.0), (float(near_west_limb[0][0]), float(near_west_limb[1][0]))]
    out = rotate_points(pts, g, g, 2 * 86400.0)  # ~26 deg further: behind the limb
    assert out.rotated == (False, False)
    assert out.hidden == (False, True)
    assert out.points[0] == (1200.0, 0.0)
    assert out.points[1] == pts[1]  # kept where it was, flagged


def test_rotate_points_handles_a_different_observer():
    source = SurfaceGeometry(rsun_arcsec=RSUN, b0_deg=0.0)
    target = SurfaceGeometry(rsun_arcsec=RSUN * 1.02, b0_deg=6.0, observer_lon_deg=-10.0)
    tx, ty = _hgs_to_arcsec(np.array([5.0]), np.array([10.0]), source)
    out = rotate_points([(float(tx[0]), float(ty[0]))], source, target, 0.0)
    lon, lat = stonyhurst_from_arcsec(out.points[0][0], out.points[0][1], target)
    assert float(lon) == pytest.approx(5.0, abs=1e-6)
    assert float(lat) == pytest.approx(10.0, abs=1e-6)
    tx2, ty2, visible = arcsec_from_stonyhurst(5.0, 10.0, target)
    assert bool(visible)
    assert float(tx2) == pytest.approx(out.points[0][0])


def test_rotation_agrees_with_sunpy_rotated_frame():
    pytest.importorskip("sunpy")
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import RotatedSunFrame, frames

    t0 = "2024-05-10T00:00:00"
    t1 = "2024-05-11T00:00:00"
    observer0 = SkyCoord(0 * u.deg, 0 * u.deg, 1 * u.AU, frame=frames.HeliographicStonyhurst, obstime=t0)
    observer1 = SkyCoord(0 * u.deg, 0 * u.deg, 1 * u.AU, frame=frames.HeliographicStonyhurst, obstime=t1)
    rsun_arcsec = math.degrees(math.asin(RSUN_KM / 1.495978707e8)) * 3600.0
    g = SurfaceGeometry(rsun_arcsec=rsun_arcsec, rsun_km=RSUN_KM)
    start = SkyCoord(
        -200 * u.arcsec, 300 * u.arcsec,
        frame=frames.Helioprojective(observer=observer0, obstime=t0, rsun=RSUN_KM * u.km),
    )
    rotated = SkyCoord(RotatedSunFrame(base=start, rotated_time=t1)).transform_to(
        frames.Helioprojective(observer=observer1, obstime=t1, rsun=RSUN_KM * u.km)
    )
    out = rotate_points([(-200.0, 300.0)], g, g, 86400.0)
    # SunPy follows Earth's true orbital speed rather than its mean, so the
    # two agree to a few arcsec over a day.
    assert out.points[0][0] == pytest.approx(rotated.Tx.to_value(u.arcsec), abs=3.0)
    assert out.points[0][1] == pytest.approx(rotated.Ty.to_value(u.arcsec), abs=3.0)


# ------------------------------------------------- image rotation and frames
from types import SimpleNamespace

from src.backend.solar.surface_measure import (
    frame_observer_deg,
    frame_rotation_deg,
    geometry_for_frame,
    solar_radius_arcsec_from_meta,
)


def test_rotation_round_trips_about_the_reference_point():
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_arcsec=10.0, y_ref_arcsec=-5.0, rotation_deg=37.0)
    tx, ty = g.to_helioprojective(np.array([110.0, -300.0]), np.array([-5.0, 400.0]))
    back_x, back_y = g.from_helioprojective(tx, ty)
    np.testing.assert_allclose(back_x, [110.0, -300.0], atol=1e-9)
    np.testing.assert_allclose(back_y, [-5.0, 400.0], atol=1e-9)
    # The reference point itself does not move; 100" east of it turns 37 deg CCW.
    rx, ry = g.to_helioprojective(10.0, -5.0)
    assert (float(rx), float(ry)) == pytest.approx((10.0, -5.0))
    ex, ey = g.to_helioprojective(110.0, -5.0)
    assert float(ex) == pytest.approx(10.0 + 100.0 * math.cos(math.radians(37.0)))
    assert float(ey) == pytest.approx(-5.0 + 100.0 * math.sin(math.radians(37.0)))


def test_rotated_image_measures_the_same_and_places_points_correctly():
    upright = SurfaceGeometry(rsun_arcsec=RSUN, b0_deg=3.0)
    flipped = SurfaceGeometry(rsun_arcsec=RSUN, b0_deg=3.0, rotation_deg=180.0)
    tx, ty = _hgs_to_arcsec(np.array([30.0]), np.array([20.0]), upright)
    # In a 180-deg rotated image the same surface point is plotted at (-Tx, -Ty).
    lon, lat = stonyhurst_from_arcsec(-tx, -ty, flipped)
    assert float(lon) == pytest.approx(30.0, abs=1e-6)
    assert float(lat) == pytest.approx(20.0, abs=1e-6)
    ix, iy, visible = arcsec_from_stonyhurst(30.0, 20.0, flipped)
    assert bool(visible)
    assert (float(ix), float(iy)) == pytest.approx((-float(tx[0]), -float(ty[0])))
    line = [(-100.0, 0.0), (-200.0, 150.0)]
    a = polyline_surface_length(line, upright)
    b = polyline_surface_length([(-x, -y) for x, y in line], flipped)
    assert b.total_km == pytest.approx(a.total_km)
    assert b.start_lonlat == pytest.approx(a.start_lonlat)


def test_rotated_full_disk_area_is_unchanged():
    n = 256
    plain = _centred(n, 9.6)
    turned = _centred(n, 9.6, rotation_deg=25.0)
    mask = np.ones((n, n), dtype=bool)
    assert mask_surface_area(mask, turned).on_disk_msh == pytest.approx(
        mask_surface_area(mask, plain).on_disk_msh, rel=2e-3
    )


def test_solar_radius_from_meta_derives_it_from_the_distance():
    assert solar_radius_arcsec_from_meta({"RSUN_OBS": 950.0}) == 950.0
    assert solar_radius_arcsec_from_meta({"rsun_obs": "951.5"}) == 951.5
    au = 1.495978707e11
    derived = solar_radius_arcsec_from_meta({"DSUN_OBS": 0.96 * au, "RSUN_REF": 696_000_000.0})
    assert derived == pytest.approx(math.degrees(math.asin(696e6 / (0.96 * au))) * 3600.0)
    assert derived == pytest.approx(999.6, abs=0.1)  # not Earth's ~960
    # No RSUN_REF: the IAU radius.
    assert solar_radius_arcsec_from_meta({"dsun_obs": au}) == pytest.approx(
        math.degrees(math.asin(RSUN_KM * 1000.0 / au)) * 3600.0
    )
    assert solar_radius_arcsec_from_meta({}) is None
    assert solar_radius_arcsec_from_meta(None) is None


def test_frame_rotation_prefers_the_maps_matrix():
    c, s_ = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    assert frame_rotation_deg(SimpleNamespace(rotation_matrix=[[c, -s_], [s_, c]], meta={"crota2": 99.0})) == pytest.approx(30.0)
    # A map class that ignores CROTA (Helioviewer LASCO) reports identity.
    assert frame_rotation_deg(SimpleNamespace(rotation_matrix=np.eye(2), meta={"crota2": -174.0})) == pytest.approx(0.0)
    assert frame_rotation_deg(SimpleNamespace(meta={"PC1_1": c, "PC2_1": s_})) == pytest.approx(30.0)
    assert frame_rotation_deg(SimpleNamespace(meta={"CROTA2": 179.93})) == pytest.approx(179.93)
    assert frame_rotation_deg(SimpleNamespace(meta={})) == 0.0


def test_frame_observer_reads_map_then_coordinate_frame():
    def coord(lat, lon):
        return SimpleNamespace(lat=SimpleNamespace(to_value=lambda unit: lat), lon=SimpleNamespace(to_value=lambda unit: lon))

    assert frame_observer_deg(SimpleNamespace(observer_coordinate=coord(-3.2, 0.5))) == (-3.2, 0.5)
    crop = SimpleNamespace(coordinate_frame=SimpleNamespace(observer=coord(5.0, -20.0)))
    assert frame_observer_deg(crop) == (5.0, -20.0)
    assert frame_observer_deg(SimpleNamespace()) is None


def _write_map(tmp_path, name, header, n=256):
    pytest.importorskip("sunpy")
    from astropy.io import fits

    path = tmp_path / name
    fits.writeto(path, np.ones((n, n), dtype="float32"), fits.Header(header), overwrite=True)
    import sunpy.map

    return sunpy.map.Map(str(path))


_BASE_HEADER = {
    "CTYPE1": "HPLN-TAN", "CTYPE2": "HPLT-TAN", "CUNIT1": "arcsec", "CUNIT2": "arcsec",
    "CRPIX1": 128.5, "CRPIX2": 128.5, "CRVAL1": 0.0, "CRVAL2": 0.0, "CDELT1": 8.0, "CDELT2": 8.0,
    "DATE-OBS": "2024-05-10T12:00:00", "RSUN_REF": 696000000.0,
}


def test_geometry_for_frame_matches_sunpy_on_a_rotated_map(tmp_path):
    frame = _write_map(tmp_path, "hmi.fits", dict(
        _BASE_HEADER, TELESCOP="SDO/HMI", INSTRUME="HMI_FRONT2", CROTA2=179.93, DSUN_OBS=1.5115e11,
        HGLN_OBS=0.0, HGLT_OBS=-3.2, RSUN_OBS=949.9, WAVELNTH=6173.0,
    ))
    import astropy.units as u

    # The window's linear axis transform (it ignores rotation, as the canvas does).
    transform = {"x_ref_pix": 127.5, "y_ref_pix": 127.5, "x_scale_arcsec_per_pix": 8.0, "y_scale_arcsec_per_pix": 8.0}
    g = geometry_for_frame(frame, transform)
    assert g.rotation_deg == pytest.approx(179.93)
    assert g.b0_deg == pytest.approx(-3.2)
    # The limb follows from R and D; RSUN_OBS (949.9") is a cross-check.
    assert g.rsun_arcsec == pytest.approx(math.degrees(math.asin(696e6 / 1.5115e11)) * 3600.0)
    assert g.rsun_arcsec == pytest.approx(949.9, abs=0.2)
    assert g.dsun_km == pytest.approx(1.5115e8)
    assert g.metadata_valid and g.metadata_notes == ()
    cols = np.array([60.0, 128.0, 190.0])
    rows = np.array([90.0, 140.0, 170.0])
    world = frame.pixel_to_world(cols * u.pix, rows * u.pix)
    from sunpy.coordinates import frames

    hgs = world.transform_to(frames.HeliographicStonyhurst(obstime=frame.date))
    tx, ty = g.pixel_to_arcsec(cols, rows)
    lon, lat = stonyhurst_from_arcsec(tx, ty, g)
    np.testing.assert_allclose(lon, (hgs.lon.to_value(u.deg) + 180) % 360 - 180, atol=0.05)
    np.testing.assert_allclose(lat, hgs.lat.to_value(u.deg), atol=0.05)


def test_geometry_for_frame_without_rsun_obs_uses_the_distance(tmp_path):
    frame = _write_map(tmp_path, "euvi.fits", dict(
        _BASE_HEADER, TELESCOP="STEREO", OBSRVTRY="STEREO_A", INSTRUME="SECCHI", DETECTOR="EUVI",
        WAVELNTH=195.0, DSUN_OBS=0.96 * 1.495978707e11, HGLN_OBS=-20.0, HGLT_OBS=5.0,
    ))
    g = geometry_for_frame(frame, {})
    assert g.rsun_arcsec == pytest.approx(999.6, abs=0.1)
    assert g.observer_lon_deg == pytest.approx(-20.0)
    assert g.b0_deg == pytest.approx(5.0)
    # A plain object with only the header still gets the right radius.
    crop = SimpleNamespace(meta=dict(frame.meta))
    assert geometry_for_frame(crop, {}).rsun_arcsec == pytest.approx(999.6, abs=0.1)
    with pytest.raises(ValueError, match="solar radius"):
        geometry_for_frame(SimpleNamespace(meta={}), {})


def test_disk_interior_mask_drops_sky_and_extreme_limb():
    from src.backend.solar.surface_measure import disk_interior_mask

    n = 401
    g = _centred(n, 6.0)
    everything = np.ones((n, n), dtype=bool)
    inner = disk_interior_mask(everything, g, mu_min=0.1)
    mu = mu_map((n, n), g)
    assert not inner[~np.isfinite(mu)].any()  # no sky
    assert not inner[np.isfinite(mu) & (mu < 0.1)].any()  # no extreme limb
    assert inner[np.isfinite(mu) & (mu >= 0.1)].all()
    part = np.zeros((n, n), dtype=bool)
    part[190:210, 190:210] = True
    assert np.array_equal(disk_interior_mask(part, g, mu_min=0.1), part)


# ------------------------------------------------ standard noise rejection
def test_granulation_dips_are_not_spots_but_pores_are():
    from src.backend.solar.surface_measure import DEFAULT_CORE_DEPTH, reject_shallow_patches

    norm = np.ones((60, 60))
    norm[5:8, 5:8] = 0.88  # granulation dip: below 0.9 but shallow
    norm[20:25, 20:25] = 0.86
    norm[22, 22] = 0.75  # a small pore: one pixel is a real depression
    norm[40:50, 40:50] = 0.7  # penumbra-like
    norm[44:46, 44:46] = 0.4  # with an umbra
    masks = sunspot_masks(norm)
    assert not masks.spot[5:8, 5:8].any()
    assert masks.spot[20:25, 20:25].all()  # the whole pore patch, not just its core
    assert masks.spot[40:50, 40:50].all()
    assert int(masks.umbra.sum()) == 4
    # The rule follows the penumbra level, and can be switched off.
    assert DEFAULT_CORE_DEPTH == pytest.approx(0.10)
    loose = sunspot_masks(norm, core_depth=None)
    assert loose.spot[5:8, 5:8].all()
    shallow = reject_shallow_patches(norm < 0.9, norm, core_below=0.8)
    assert np.array_equal(shallow, masks.spot)
    assert not reject_shallow_patches(np.zeros((4, 4), bool), norm[:4, :4], core_below=0.8).any()


def test_quiet_sun_noise_adds_no_sunspot_area():
    """A spotless limb-darkened disk with HMI-like granulation noise has no spots."""
    n = 512
    g = _centred(n, 4.0)
    mu = mu_map((n, n), g)
    rng = np.random.default_rng(7)
    image = 40_000.0 * (1.0 - 0.88 * (1.0 - mu) + 0.23 * (1.0 - mu * mu))
    # Granulation is spatially correlated (granules span a few pixels), so
    # smooth white noise to that scale and rescale it to the 2.9% rms measured
    # on HMI quiet Sun.
    from scipy.ndimage import gaussian_filter

    granulation = gaussian_filter(rng.standard_normal(image.shape), 1.5)
    granulation *= 0.029 / granulation.std()
    image = image * (1.0 + granulation)
    image = np.where(np.isfinite(mu), image, 0.0)
    profile = fit_quiet_sun_profile(image, g)
    norm = normalize_to_quiet_sun(image, g, profile)
    from src.backend.solar.surface_measure import disk_interior_mask

    inner = disk_interior_mask(np.ones((n, n), dtype=bool), g, mu_min=profile.mu_min)
    noisy = sunspot_masks(norm, inner, core_depth=None, min_cluster_px=3)
    clean = sunspot_masks(norm, inner)
    assert mask_surface_area(noisy.spot, g).on_disk_msh > 0.0  # noise alone dips below 0.9
    assert int(clean.spot.sum()) == 0



# ------------------------------------------------ header geometry checks (plan section 3)
def test_inconsistent_radius_metadata_is_refused(tmp_path):
    frame = _write_map(tmp_path, "bad.fits", dict(
        _BASE_HEADER, TELESCOP="SDO/HMI", INSTRUME="HMI_FRONT2", WAVELNTH=6173.0,
        DSUN_OBS=1.473e11, RSUN_OBS=950.0,  # asin(R/D) is 974.6": 2.6% apart
    ))
    g = geometry_for_frame(frame, {})
    assert not g.metadata_valid
    assert any("inconsistent metadata" in note for note in g.metadata_notes)


def test_missing_header_values_are_recorded_as_fallbacks():
    plain = SimpleNamespace(meta={"RSUN_OBS": 960.0})
    g = geometry_for_frame(plain, {})
    assert g.metadata_valid
    assert g.rsun_km == RSUN_KM
    assert g.dsun_km is None
    assert any("RSUN_REF missing" in note for note in g.metadata_notes)
    assert any("DSUN_OBS missing" in note for note in g.metadata_notes)
    flagged = geometry_for_frame(SimpleNamespace(meta={"RSUN_OBS": 960.0, "QUALITY": 1024}), {})
    assert flagged.quality_flag == 1024
    assert any("QUALITY" in note for note in flagged.metadata_notes)


def test_background_spread_is_small_for_a_radially_symmetric_disk():
    g, image, _, _, _ = _limb_darkened_disk(800, 2.6)  # with a sunspot
    profile = fit_quiet_sun_profile(image, g)
    spread = background_spread(image, g, profile)
    assert spread is not None and spread < 0.01 < MAX_BACKGROUND_SPREAD


def test_background_spread_flags_a_non_radial_background():
    """AIA 4500-like: a brightness gradient across the disk that I_qs(mu) cannot remove.

    On real data HMI continuum measured 0.011-0.017 and AIA 4500 0.28-0.29;
    a +-15% tilt is what made 35% of the 4500 disk read as penumbra.
    """
    g, image, _, _, _ = _limb_darkened_disk(800, 2.6, spot=False)
    tx, ty = g.pixel_to_arcsec(np.arange(800)[None, :], np.arange(800)[:, None])
    tilted = image * (1.0 + 0.15 * (tx + ty) / (2 * RSUN))
    profile = fit_quiet_sun_profile(tilted, g)
    spread = background_spread(tilted, g, profile)
    assert spread is not None and spread > MAX_BACKGROUND_SPREAD
    # Sampling on a stride (large frames) gives the same answer.
    assert background_spread(tilted, g, profile, max_side=200) == pytest.approx(spread, abs=0.02)


def test_background_spread_needs_the_disk():
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=-5000.0, y_ref_pix=0.0)
    _, image, _, _, _ = _limb_darkened_disk(400, 5.2, spot=False)
    g_ok, img_ok, _, _, _ = _limb_darkened_disk(400, 5.2, spot=False)
    profile = fit_quiet_sun_profile(img_ok, g_ok)
    assert background_spread(image, g, profile) is None
