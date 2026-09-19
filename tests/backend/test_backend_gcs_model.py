"""
e-CALLISTO FITS Analyzer
Unit tests for the GCS CME model (src/backend/gcs/gcs_model.py).

The important tests here are the ones that pin the *science*: the hand-rolled
projection is only worth its speed if it agrees with sunpy, and the parameter
relations are only worth reporting if they mean what the papers say they mean.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.backend.gcs.gcs_model import (
    ARCSEC_PER_RADIAN,
    DEFAULT_N_CIRCLE,
    MIN_MODEL_RADIUS_RSUN,
    MIN_USEFUL_SEPARATION_DEG,
    PARAMETER_NAMES,
    WEAKLY_CONSTRAINED,
    GCSParameters,
    GCSViewpoint,
    ObserverGeometry,
    angular_widths_deg,
    apex_arcsec,
    apex_cross_section_radius_rsun,
    apex_height_rsun,
    apply_apex_drag,
    direction_from_arcsec,
    free_parameters,
    gcs_mesh,
    handle_positions_arcsec,
    has_independent_viewpoints,
    height_for_apex_radius,
    interpolate_parameters,
    join_polylines,
    leg_height_rsun,
    observer_separation_deg,
    orientation_matrix,
    project_points_to_arcsec,
    project_to_arcsec,
    refine_gcs,
    refine_plan,
    shell_centre_distance_rsun,
    staged_parameters,
    wireframe_arcsec,
)

# A front-side CME and two well-separated observers, reused throughout.
TRUTH = GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32)
VIEW_A = ObserverGeometry(
    lon_deg=0.0, lat_deg=-4.0, dsun_rsun=215.0, rsun_arcsec=960.0, label="LASCO C3"
)
VIEW_B = ObserverGeometry(
    lon_deg=62.0, lat_deg=2.0, dsun_rsun=213.0, rsun_arcsec=968.0, label="STEREO-A COR2"
)


@pytest.fixture(scope="module")
def disk_map():
    """A synthetic Earth-view helioprojective map, for the sunpy cross-checks."""
    units = pytest.importorskip("astropy.units")
    pytest.importorskip("sunpy.map")
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import get_earth
    from sunpy.map.header_helper import make_fitswcs_header

    obstime = "2012-07-12T16:00:00"
    data = np.zeros((256, 256))
    ref = SkyCoord(
        0 * units.arcsec,
        0 * units.arcsec,
        obstime=obstime,
        observer=get_earth(obstime),
        frame="helioprojective",
    )
    header = make_fitswcs_header(data, ref, scale=[32, 32] * units.arcsec / units.pix)
    return sunpy.map.Map(data, header)


# --- Parameters and derived radii ------------------------------------------


def test_parameters_round_trip_through_an_array():
    assert GCSParameters.from_array(TRUTH.as_array()) == TRUTH
    assert tuple(PARAMETER_NAMES) == (
        "lon_deg",
        "lat_deg",
        "tilt_deg",
        "height_rsun",
        "alpha_deg",
        "kappa",
    )


def test_interpolated_parameters_are_exact_at_the_ends_and_linear_between():
    first = GCSParameters(10.0, -5.0, 20.0, 4.0, 30.0, 0.30)
    second = GCSParameters(30.0, 5.0, 40.0, 8.0, 40.0, 0.40)
    assert interpolate_parameters(first, second, 0.0) is first
    assert interpolate_parameters(first, second, 1.0) is second
    assert interpolate_parameters(first, second, -0.5) is first
    assert interpolate_parameters(first, second, 1.5) is second
    quarter = interpolate_parameters(first, second, 0.25)
    assert quarter.as_array() == pytest.approx([15.0, -2.5, 25.0, 5.0, 32.5, 0.325])


def test_interpolated_angles_take_the_short_way_round():
    def shell(lon, tilt):
        return GCSParameters(lon, 0.0, tilt, 6.0, 30.0, 0.3)

    # Across the far side of the Sun, not back through Earth's direction.
    lon = interpolate_parameters(shell(170.0, 0.0), shell(-170.0, 0.0), 0.5).lon_deg
    assert math.cos(math.radians(lon)) == pytest.approx(-1.0)
    # Tilt is only defined modulo 180 degrees, so 85 to -85 is a 10-degree turn.
    tilt = interpolate_parameters(shell(0.0, 85.0), shell(0.0, -85.0), 0.5).tilt_deg
    assert tilt % 180.0 == pytest.approx(90.0)
    # Continued from the first value, so a playback readout does not jump.
    assert interpolate_parameters(shell(0.0, 85.0), shell(0.0, -85.0), 0.25).tilt_deg == pytest.approx(87.5)


@pytest.mark.parametrize(
    "params",
    [
        GCSParameters(0.0, 0.0, 0.0, 9.0, 32.0, 1.0),  # kappa is singular at 1
        GCSParameters(0.0, 0.0, 0.0, 9.0, 32.0, 0.0),
        GCSParameters(0.0, 0.0, 0.0, -1.0, 32.0, 0.3),
        GCSParameters(0.0, 0.0, 0.0, 9.0, 95.0, 0.3),
        GCSParameters(0.0, 120.0, 0.0, 9.0, 32.0, 0.3),
        GCSParameters(float("nan"), 0.0, 0.0, 9.0, 32.0, 0.3),
        GCSParameters(0.0, 0.0, float("inf"), 9.0, 32.0, 0.3),
        GCSParameters(0.0, 0.0, 0.0, float("inf"), 32.0, 0.3),
    ],
)
def test_unphysical_parameters_are_rejected(params):
    assert not params.is_physical


def test_height_is_the_leading_edge_not_the_leg_height():
    """The sense of ``height`` is easy to invert, so pin it against the mesh."""
    for height in (4.0, 9.0, 20.0):
        for alpha, kappa in ((32.0, 0.32), (20.0, 0.5), (45.0, 0.15)):
            params = GCSParameters(0.0, 0.0, 0.0, height, alpha, kappa)
            mesh = gcs_mesh(params, n_circle=361)
            largest = float(np.linalg.norm(mesh.points, axis=0).max())
            # Discretisation only, so it converges from below as n_circle grows.
            assert largest == pytest.approx(height, rel=1e-4)
            assert apex_height_rsun(params) == pytest.approx(height)
            # The leg junction sits well inside the leading edge.
            assert 0.0 < leg_height_rsun(params) < height


def test_derived_radii_follow_the_thernisien_relations():
    params = GCSParameters(0.0, 0.0, 0.0, 9.0, 32.0, 0.32)
    alpha = math.radians(32.0)
    expected_leg = 9.0 * (1.0 - 0.32) * math.cos(alpha) / (1.0 + math.sin(alpha))
    assert leg_height_rsun(params) == pytest.approx(expected_leg)
    expected_rapex = (
        0.32 * (expected_leg / math.cos(alpha) + expected_leg * math.tan(alpha)) / (1.0 - 0.32**2)
    )
    assert apex_cross_section_radius_rsun(params) == pytest.approx(expected_rapex)
    # T2011 eq. 20: the apex circle centre sits one cross-section inside the front.
    assert shell_centre_distance_rsun(params) == pytest.approx(9.0 - expected_rapex)


def test_a_narrower_shell_has_a_smaller_cross_section():
    wide = GCSParameters(0.0, 0.0, 0.0, 9.0, 32.0, 0.6)
    narrow = GCSParameters(0.0, 0.0, 0.0, 9.0, 32.0, 0.1)
    assert apex_cross_section_radius_rsun(narrow) < apex_cross_section_radius_rsun(wide)


def test_intrinsic_widths_include_the_shell_cross_section():
    params = TRUTH.replace_values(alpha_deg=30.0, kappa=0.5)
    face_on, edge_on = angular_widths_deg(params)
    assert face_on == pytest.approx(120.0)
    assert edge_on == pytest.approx(60.0)


# --- Mesh and orientation --------------------------------------------------


def test_mesh_matches_pythea_default_resolution():
    mesh = gcs_mesh(TRUTH)
    assert mesh.size == 5130
    assert mesh.n_circle == DEFAULT_N_CIRCLE
    assert mesh.points.shape == (3, 5130)


def test_mesh_does_not_depend_on_orientation():
    """The whole fast path rests on this: a lon/lat/tilt drag reuses the mesh."""
    base = gcs_mesh(TRUTH)
    turned = gcs_mesh(TRUTH.replace_values(lon_deg=-170.0, lat_deg=80.0, tilt_deg=-33.0))
    np.testing.assert_allclose(base.points, turned.points)


def test_orientation_matrix_is_a_rotation():
    matrix = orientation_matrix(TRUTH)
    np.testing.assert_allclose(matrix @ matrix.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(matrix) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "lon,lat,tilt",
    [(0.0, 0.0, 0.0), (35.0, -12.0, 25.0), (-90.0, 45.0, 60.0), (179.0, -89.0, -175.0)],
)
def test_apex_points_where_the_parameters_say_and_tilt_does_not_move_it(lon, lat, tilt):
    """Getting the Euler order wrong files tilt as longitude, silently."""
    params = GCSParameters(lon, lat, tilt, 9.0, 32.0, 0.32)
    apex = orientation_matrix(params) @ np.array([0.0, 0.0, 9.0])
    radius = float(np.linalg.norm(apex))
    assert radius == pytest.approx(9.0)
    got_lon = math.degrees(math.atan2(apex[1], apex[0]))
    got_lat = math.degrees(math.asin(apex[2] / radius))
    assert ((got_lon - lon + 180.0) % 360.0) - 180.0 == pytest.approx(0.0, abs=1e-9)
    assert got_lat == pytest.approx(lat, abs=1e-9)


# --- Projection: the agreement that justifies the fast path -----------------


def _sunpy_projection(points_hgs, disk_map):
    """Project via SkyCoord.transform_to — the slow path, used as the oracle."""
    import astropy.units as u
    from astropy.coordinates import CartesianRepresentation, SkyCoord
    from sunpy.coordinates import frames

    frame_coord = disk_map.coordinate_frame
    coords = SkyCoord(
        CartesianRepresentation(points_hgs * u.R_sun),
        frame=frames.HeliographicStonyhurst,
        obstime=frame_coord.obstime,
        observer=frame_coord.observer,
    )
    hpc = coords.transform_to(frame_coord)
    return (
        np.asarray(hpc.Tx.to_value(u.arcsec)),
        np.asarray(hpc.Ty.to_value(u.arcsec)),
        np.asarray(hpc.is_visible(), dtype=bool),
    )


@pytest.mark.parametrize(
    "params",
    [
        GCSParameters(40.0, -15.0, 30.0, 9.0, 32.0, 0.32),
        GCSParameters(0.0, 0.0, 0.0, 4.0, 20.0, 0.5),
        GCSParameters(170.0, 60.0, -40.0, 20.0, 45.0, 0.15),
    ],
)
def test_fast_projection_agrees_with_sunpy(disk_map, params):
    """The fast projector must be exact, not merely close — else it is a bug."""
    observer = ObserverGeometry.from_frame(disk_map)
    assert observer is not None
    mesh = gcs_mesh(params)
    oriented = orientation_matrix(params) @ mesh.points

    fast_tx, fast_ty, occulted = project_points_to_arcsec(oriented, observer)
    slow_tx, slow_ty, visible = _sunpy_projection(oriented, disk_map)

    drawn = ~occulted
    assert drawn.any()
    np.testing.assert_allclose(fast_tx[drawn], slow_tx[drawn], atol=1e-6)
    np.testing.assert_allclose(fast_ty[drawn], slow_ty[drawn], atol=1e-6)
    # Everything we draw, sunpy agrees is on the near side.
    assert visible[drawn].all()


def test_sub_photospheric_mesh_points_are_never_drawn(disk_map):
    """The GCS legs are anchored at Sun centre, so the mesh really does reach r=0."""
    observer = ObserverGeometry.from_frame(disk_map)
    mesh = gcs_mesh(TRUTH)
    oriented = orientation_matrix(TRUTH) @ mesh.points
    radius = np.linalg.norm(oriented, axis=0)
    assert radius.min() < MIN_MODEL_RADIUS_RSUN  # the situation is real

    _, _, occulted = project_points_to_arcsec(oriented, observer)
    inside = radius < MIN_MODEL_RADIUS_RSUN
    assert occulted[inside].all()


def test_apparent_solar_radius_matches_sunpy(disk_map):
    observer = ObserverGeometry.from_frame(disk_map)
    assert observer.rsun_arcsec == pytest.approx(disk_map.rsun_obs.value, abs=1e-8)


def test_projection_masks_the_coronagraph_occulter():
    mesh = gcs_mesh(TRUTH)
    unmasked = project_to_arcsec(mesh, TRUTH, VIEW_A)
    masked = project_to_arcsec(mesh, TRUTH, VIEW_A, fov_rsun=(3.7, 30.0))
    drawn_unmasked = np.count_nonzero(np.isfinite(masked.tx_arcsec))
    assert drawn_unmasked < np.count_nonzero(np.isfinite(unmasked.tx_arcsec))
    # Nothing survives inside the C3 occulter.
    radius = np.hypot(masked.tx_arcsec, masked.ty_arcsec) / VIEW_A.rsun_arcsec
    assert np.nanmin(radius) >= 3.7 - 1e-9


def test_an_earth_directed_cme_projects_symmetrically():
    """A CME aimed at the observer is centred on the disk, by symmetry."""
    observer = ObserverGeometry(
        lon_deg=17.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0
    )
    params = GCSParameters(17.0, 0.0, 0.0, 9.0, 32.0, 0.32)
    projection = project_to_arcsec(gcs_mesh(params), params, observer)
    assert projection.apex_tx == pytest.approx(0.0, abs=1e-6)
    assert projection.apex_ty == pytest.approx(0.0, abs=1e-6)
    finite = np.isfinite(projection.tx_arcsec)
    assert np.nanmean(projection.tx_arcsec[finite]) == pytest.approx(0.0, abs=1.0)


def test_a_limb_cme_projects_its_full_height():
    """At the limb there is no foreshortening, so the sky radius is the height."""
    observer = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    params = GCSParameters(90.0, 0.0, 0.0, 9.0, 32.0, 0.32)
    tx, ty = apex_arcsec(params, observer)
    assert math.hypot(tx, ty) / observer.rsun_arcsec == pytest.approx(9.0, rel=2e-3)


def test_tilt_does_not_move_the_projected_apex():
    first = apex_arcsec(TRUTH, VIEW_A)
    second = apex_arcsec(TRUTH.replace_values(tilt_deg=TRUTH.tilt_deg + 90.0), VIEW_A)
    assert first == pytest.approx(second, abs=1e-6)


# --- Observer geometry -----------------------------------------------------


def test_observer_geometry_reads_a_real_map(disk_map):
    observer = ObserverGeometry.from_frame(disk_map)
    assert observer is not None
    assert observer.dsun_rsun == pytest.approx(215.0, rel=0.05)
    assert observer.rsun_arcsec == pytest.approx(945.0, rel=0.02)
    assert abs(observer.lat_deg) < 10.0


def test_observer_geometry_degrades_for_a_frame_without_coordinates():
    """Cropped/composited frames lose their WCS; the tool must hide, not crash."""

    class Bare:
        data = np.zeros((4, 4))

    class NoObserver:
        class coordinate_frame:  # noqa: N801 - mimicking a sunpy attribute
            observer = None

    assert ObserverGeometry.from_frame(Bare()) is None
    assert ObserverGeometry.from_frame(NoObserver()) is None


def test_observer_separation_is_symmetric_and_bounded():
    assert observer_separation_deg(VIEW_A, VIEW_B) == pytest.approx(
        observer_separation_deg(VIEW_B, VIEW_A)
    )
    # Haversine keeps full precision at zero separation, where acos(dot) would
    # only manage ~1e-6 degrees.
    assert observer_separation_deg(VIEW_A, VIEW_A) == pytest.approx(0.0, abs=1e-12)
    opposite = ObserverGeometry(
        lon_deg=180.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0
    )
    origin = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    assert observer_separation_deg(origin, opposite) == pytest.approx(180.0)


# --- Wireframe -------------------------------------------------------------


def test_wireframe_is_one_nan_separated_array():
    x, y = join_polylines([(np.arange(3.0), np.arange(3.0)), (np.arange(2.0), np.arange(2.0))])
    assert x.size == y.size == 6
    assert np.isnan(x[3]) and np.isnan(y[3])


def test_wireframe_arcsec_returns_drawable_arrays():
    x, y, projection = wireframe_arcsec(TRUTH, VIEW_A)
    assert x.size == y.size > 0
    assert np.isfinite(x).any()
    assert 0.0 < projection.visible_fraction <= 1.0


def test_wireframe_is_sparser_than_the_full_mesh():
    """Painting all 5130 points per update is the cost this avoids."""
    x, _, _ = wireframe_arcsec(TRUTH, VIEW_A)
    assert x.size < gcs_mesh(TRUTH).size


def test_empty_polylines_give_empty_arrays():
    x, y = join_polylines([])
    assert x.size == 0 and y.size == 0


# --- Inverse geometry for dragging ----------------------------------------


def test_direction_inverse_round_trips_the_projection():
    for lon, lat in ((35.0, -12.0), (-60.0, 40.0), (5.0, 0.0)):
        params = GCSParameters(lon, lat, 0.0, 9.0, 32.0, 0.32)
        tx, ty = apex_arcsec(params, VIEW_A)
        got = direction_from_arcsec(tx, ty, radius_rsun=9.0, observer=VIEW_A)
        assert got is not None
        assert got[0] == pytest.approx(lon, abs=1e-6)
        assert got[1] == pytest.approx(lat, abs=1e-6)


def test_direction_inverse_agrees_with_sunpy_on_the_surface(disk_map):
    """At 1 R_sun this is the geometry solar_grid.point_lonlat gets from sunpy."""
    from src.backend.solar.solar_grid import point_lonlat

    observer = ObserverGeometry.from_frame(disk_map)
    for tx, ty in ((100.0, 200.0), (-400.0, 150.0), (0.0, 0.0)):
        expected = point_lonlat(tx, ty, disk_map, frame_key="HGS")
        got = direction_from_arcsec(tx, ty, radius_rsun=1.0, observer=observer)
        if expected is None:
            continue
        assert got is not None
        assert got[0] == pytest.approx(expected[0], abs=0.05)
        assert got[1] == pytest.approx(expected[1], abs=0.05)


def test_direction_inverse_returns_none_when_the_sight_line_misses():
    assert direction_from_arcsec(1.0e6, 1.0e6, radius_rsun=1.0, observer=VIEW_A) is None
    assert direction_from_arcsec(float("nan"), 0.0, radius_rsun=9.0, observer=VIEW_A) is None


def test_apex_drag_lands_exactly_under_the_cursor():
    apex = apex_arcsec(TRUTH, VIEW_A)
    for dx, dy in ((300.0, 200.0), (-800.0, -400.0), (150.0, -1200.0)):
        target = (apex[0] + dx, apex[1] + dy)
        moved = apply_apex_drag(TRUTH, VIEW_A, target)
        got = apex_arcsec(moved, VIEW_A)
        assert math.hypot(got[0] - target[0], got[1] - target[1]) < 1.0


def test_radial_drag_changes_height_and_leaves_the_direction_alone():
    """Dragging outward must raise the CME, not swing it around the limb."""
    apex = apex_arcsec(TRUTH, VIEW_A)
    out = apply_apex_drag(TRUTH, VIEW_A, (apex[0] * 1.3, apex[1] * 1.3))
    assert out.height_rsun > TRUTH.height_rsun + 1.0
    # Direction/height are weakly coupled, so two passes leave ~1e-4 deg of
    # iteration residual — about 0.07 arcsec on the sky, far inside one pixel.
    assert out.lon_deg == pytest.approx(TRUTH.lon_deg, abs=1e-3)
    assert out.lat_deg == pytest.approx(TRUTH.lat_deg, abs=1e-3)

    inward = apply_apex_drag(TRUTH, VIEW_A, (apex[0] * 0.7, apex[1] * 0.7))
    assert inward.height_rsun < TRUTH.height_rsun - 1.0


def test_tangential_drag_changes_direction_and_leaves_height_alone():
    apex = apex_arcsec(TRUTH, VIEW_A)
    radius = math.hypot(*apex)
    angle = math.atan2(apex[1], apex[0]) + math.radians(25.0)
    moved = apply_apex_drag(
        TRUTH, VIEW_A, (radius * math.cos(angle), radius * math.sin(angle))
    )
    assert moved.height_rsun == pytest.approx(TRUTH.height_rsun, rel=1e-3)
    assert (moved.lon_deg, moved.lat_deg) != (TRUTH.lon_deg, TRUTH.lat_deg)


@pytest.mark.parametrize("lock,changes,holds", [("radial", "height_rsun", "lon_deg"),
                                               ("tangential", "lat_deg", "height_rsun")])
def test_drag_locks_constrain_the_gesture(lock, changes, holds):
    apex = apex_arcsec(TRUTH, VIEW_A)
    moved = apply_apex_drag(
        TRUTH, VIEW_A, (apex[0] * 1.25, apex[1] * 0.9), lock=lock
    )
    assert getattr(moved, changes) != pytest.approx(getattr(TRUTH, changes))
    assert getattr(moved, holds) == pytest.approx(getattr(TRUTH, holds), rel=1e-6)


def test_height_solve_refuses_when_the_cme_points_at_the_observer():
    """Along the line of sight the sky radius barely responds to height."""
    observer = ObserverGeometry(lon_deg=35.0, lat_deg=-12.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    unchanged = height_for_apex_radius(TRUTH, observer, 5000.0)
    assert unchanged == pytest.approx(TRUTH.height_rsun)


def test_handles_sit_on_the_model():
    handles = handle_positions_arcsec(TRUTH, VIEW_A)
    assert set(handles) == {"apex", "flank", "rim"}
    assert handles["apex"] == pytest.approx(apex_arcsec(TRUTH, VIEW_A), abs=1e-9)
    for position in handles.values():
        assert all(math.isfinite(value) for value in position)


# --- Refinement ------------------------------------------------------------


def _synthetic_clicks(params, observer, *, n=25, noise=20.0, seed=7):
    """Points a user might click along the projected front, plus noise."""
    rng = np.random.default_rng(seed)
    projection = project_to_arcsec(gcs_mesh(params), params, observer)
    usable = np.nonzero(np.isfinite(projection.tx_arcsec))[0]
    radius = np.hypot(projection.tx_arcsec[usable], projection.ty_arcsec[usable])
    outer = usable[radius > np.percentile(radius, 70.0)]
    picked = rng.choice(outer, n, replace=False)
    points = np.column_stack((projection.tx_arcsec[picked], projection.ty_arcsec[picked]))
    return points + rng.normal(scale=noise, size=points.shape)


@pytest.fixture(scope="module")
def two_viewpoints():
    return [
        GCSViewpoint(VIEW_A, _synthetic_clicks(TRUTH, VIEW_A, seed=7), "A"),
        GCSViewpoint(VIEW_B, _synthetic_clicks(TRUTH, VIEW_B, seed=8), "B"),
    ]


def test_refine_improves_a_realistic_manual_seed(two_viewpoints):
    """The headline behaviour: a polish of a hand fit, not a fit from nothing."""
    seed = GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27)
    result = refine_gcs(two_viewpoints, seed)

    assert result.converged
    assert result.rms_arcsec < result.seed_rms_arcsec / 2.0
    # Direction and height recover; alpha/kappa are the documented weak pair.
    assert abs(result.parameters.lon_deg - TRUTH.lon_deg) < 4.0
    assert abs(result.parameters.lat_deg - TRUTH.lat_deg) < 4.0
    assert abs(result.parameters.height_rsun - TRUTH.height_rsun) < 0.5
    assert result.n_points == 50
    assert result.seed == seed
    assert result.weakly_constrained == WEAKLY_CONSTRAINED


def test_refine_reports_finite_errors_and_names_the_weak_parameters(two_viewpoints):
    result = refine_gcs(two_viewpoints, GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27))
    assert result.covariance is not None
    assert result.covariance.shape == (len(result.free), len(result.free))
    for name in result.free:
        assert math.isfinite(result.sigma[name])
        assert result.sigma[name] > 0.0
    assert "weakly constrained" in result.message
    # The message is shown verbatim in the status bar, so it must say it is formal.
    assert "formal" in result.message


def test_refine_flags_the_worst_click(two_viewpoints):
    result = refine_gcs(two_viewpoints, GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27))
    assert 0 <= result.worst_index < result.n_points
    assert result.max_residual_arcsec == pytest.approx(
        float(np.max(np.abs(result.residuals_arcsec)))
    )


def test_one_viewpoint_holds_the_direction_fixed(two_viewpoints):
    """A single view cannot separate direction from height and width."""
    assert free_parameters(two_viewpoints[:1]) == (
        "tilt_deg",
        "height_rsun",
        "alpha_deg",
        "kappa",
    )
    seed = GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27)
    result = refine_gcs(two_viewpoints[:1], seed)
    assert result.parameters.lon_deg == pytest.approx(seed.lon_deg)
    assert result.parameters.lat_deg == pytest.approx(seed.lat_deg)
    assert "one effective viewpoint" in result.message


def test_two_nearby_viewpoints_are_treated_as_one():
    nearby = ObserverGeometry(
        lon_deg=VIEW_A.lon_deg + MIN_USEFUL_SEPARATION_DEG / 2.0,
        lat_deg=VIEW_A.lat_deg,
        dsun_rsun=215.0,
        rsun_arcsec=960.0,
    )
    views = [
        GCSViewpoint(VIEW_A, _synthetic_clicks(TRUTH, VIEW_A, seed=1)),
        GCSViewpoint(nearby, _synthetic_clicks(TRUTH, nearby, seed=2)),
    ]
    assert "lon_deg" not in free_parameters(views)


@pytest.mark.parametrize("empty", [np.empty((0, 2)), np.array([[np.nan, np.nan]])])
def test_an_empty_second_view_does_not_release_direction(two_viewpoints, empty):
    views = [two_viewpoints[0], GCSViewpoint(VIEW_B, empty)]
    assert not has_independent_viewpoints(views)
    result = refine_gcs(views, TRUTH)
    assert result.n_viewpoints == 1
    assert "lon_deg" not in result.free
    assert result.parameters.lon_deg == TRUTH.lon_deg
    assert result.parameters.lat_deg == TRUTH.lat_deg


def test_near_opposite_views_remain_geometrically_degenerate():
    opposite = ObserverGeometry(
        lon_deg=VIEW_A.lon_deg + 178.0, lat_deg=-VIEW_A.lat_deg,
        dsun_rsun=215.0, rsun_arcsec=960.0,
    )
    views = [GCSViewpoint(view, _synthetic_clicks(TRUTH, view)) for view in (VIEW_A, opposite)]
    assert not has_independent_viewpoints(views)
    assert "lon_deg" not in free_parameters(views)
    views.append(GCSViewpoint(VIEW_B, _synthetic_clicks(TRUTH, VIEW_B)))
    assert has_independent_viewpoints(views)


def test_reported_residuals_are_arcseconds_for_each_observer():
    from scipy.spatial import cKDTree

    close = ObserverGeometry(
        lon_deg=62.0, lat_deg=2.0, dsun_rsun=40.0,
        rsun_arcsec=math.asin(1.0 / 40.0) * ARCSEC_PER_RADIAN,
    )
    views = [GCSViewpoint(view, _synthetic_clicks(TRUTH, view, n=12)) for view in (VIEW_A, close)]
    result = refine_gcs(views, TRUTH, free=["height_rsun"], max_nfev=8)
    expected = []
    for view in views:
        projection = project_to_arcsec(gcs_mesh(result.parameters), result.parameters, view.observer)
        finite = np.isfinite(projection.tx_arcsec) & np.isfinite(projection.ty_arcsec)
        mesh_points = np.column_stack((projection.tx_arcsec[finite], projection.ty_arcsec[finite]))
        expected.extend(cKDTree(mesh_points).query(view.clicks_arcsec)[0])
    np.testing.assert_allclose(result.residuals_arcsec, expected)
    assert result.rms_arcsec == pytest.approx(np.sqrt(np.mean(np.square(expected))))


def test_refinement_respects_the_detector_occulter():
    from scipy.spatial import cKDTree

    clicks = _synthetic_clicks(TRUTH, VIEW_A, n=12)
    view = GCSViewpoint(VIEW_A, clicks, fov_rsun=(6.0, 30.0))
    result = refine_gcs([view], TRUTH, free=["height_rsun"], max_nfev=1)
    projection = project_to_arcsec(gcs_mesh(result.parameters), result.parameters, VIEW_A, fov_rsun=view.fov_rsun)
    finite = np.isfinite(projection.tx_arcsec) & np.isfinite(projection.ty_arcsec)
    mesh_points = np.column_stack((projection.tx_arcsec[finite], projection.ty_arcsec[finite]))
    expected = cKDTree(mesh_points).query(clicks)[0]
    np.testing.assert_allclose(result.residuals_arcsec, expected)


def test_repeated_identical_clicks_do_not_produce_precise_uncertainties():
    click = _synthetic_clicks(TRUTH, VIEW_A, n=1)[0]
    view = GCSViewpoint(VIEW_A, np.tile(click, (12, 1)))
    result = refine_gcs([view], TRUTH, free=["height_rsun", "kappa"])
    assert result.covariance is None
    assert math.isnan(result.sigma["height_rsun"])
    assert math.isnan(result.sigma["kappa"])
    assert "uncertainties unavailable" in result.message


def test_refinement_requires_a_visible_manual_seed():
    view = GCSViewpoint(VIEW_A, _synthetic_clicks(TRUTH, VIEW_A), fov_rsun=(3.7, 30.0))
    with pytest.raises(ValueError, match="outside the visible field"):
        refine_gcs([view], TRUTH.replace_values(height_rsun=1.1))


def test_refinement_does_not_silently_clip_an_imported_seed(two_viewpoints):
    with pytest.raises(ValueError, match="outside the refinement range"):
        refine_gcs(two_viewpoints, TRUTH.replace_values(height_rsun=40.0))


def test_refine_raises_with_user_facing_text(two_viewpoints):
    """Matches fit_circle/fit_height_time so the controller can show it verbatim."""
    seed = GCSParameters(43.0, -18.0, 34.0, 7.9, 37.0, 0.27)
    with pytest.raises(ValueError, match="at least one viewpoint"):
        refine_gcs([], seed)
    with pytest.raises(ValueError, match="Click along the CME front"):
        refine_gcs([GCSViewpoint(VIEW_A, np.empty((0, 2)))], seed)
    with pytest.raises(ValueError, match="at least"):
        refine_gcs([GCSViewpoint(VIEW_A, np.zeros((2, 2)))], seed)
    with pytest.raises(ValueError, match="Unknown GCS parameter"):
        refine_gcs(two_viewpoints, seed, free=["nonsense"])
    with pytest.raises(ValueError, match="valid range"):
        refine_gcs(two_viewpoints, seed.replace_values(kappa=1.0))
    for free in ([], ["height_rsun", "height_rsun"]):
        with pytest.raises(ValueError, match="distinct parameter"):
            refine_gcs(two_viewpoints, seed, free=free)
    with pytest.raises(ValueError, match="positive finite"):
        refine_gcs(two_viewpoints, seed, click_tolerance_arcsec=float("nan"))


def test_refine_ignores_non_finite_clicks():
    clicks = np.array([[100.0, 200.0], [np.nan, 5.0], [300.0, 400.0], [1.0, np.inf]])
    view = GCSViewpoint(VIEW_A, clicks)
    assert view.n_clicks == 4
    result = refine_gcs(
        [view], GCSParameters(35.0, -12.0, 25.0, 9.0, 32.0, 0.32), free=["height_rsun"]
    )
    assert result.n_points == 2


# --- The smoothness contract ----------------------------------------------


def test_a_full_parameter_update_stays_far_under_a_frame_budget():
    """Guards the premise of the whole design: no SkyCoord in the hot path.

    Measured at ~0.3 ms; the ceiling is loose so the test is not flaky on a busy
    machine, but tight enough that routing this through ``transform_to`` (~3.2 ms
    for one viewpoint) would fail it.
    """
    import time

    params = TRUTH
    wireframe_arcsec(params, VIEW_A)  # warm up
    started = time.perf_counter()
    iterations = 20
    for index in range(iterations):
        moved = params.replace_values(
            lon_deg=35.0 + index * 0.1, height_rsun=9.0 + index * 0.01
        )
        wireframe_arcsec(moved, VIEW_A)
    elapsed = (time.perf_counter() - started) / iterations
    assert elapsed < 5.0e-3, f"a full GCS update took {elapsed * 1000:.2f} ms"


def test_reusing_the_mesh_is_cheaper_than_rebuilding_it():
    """What makes an orientation drag essentially free."""
    import time

    mesh = gcs_mesh(TRUTH)

    def rebuild():
        wireframe_arcsec(TRUTH, VIEW_A)

    def reuse():
        wireframe_arcsec(TRUTH, VIEW_A, mesh=mesh)

    for function in (rebuild, reuse):
        function()
    timings = []
    for function in (rebuild, reuse):
        started = time.perf_counter()
        for _ in range(50):
            function()
        timings.append(time.perf_counter() - started)
    assert timings[1] < timings[0]


def test_arcsec_per_radian_constant():
    assert ARCSEC_PER_RADIAN == pytest.approx(206264.806, rel=1e-6)


def test_handles_hide_when_their_anchor_is_behind_the_sun():
    """A handle you cannot see must not be draggable — the inverse is near-side
    only, so dragging a far-side apex would mirror the CME without saying so."""
    far_side = GCSParameters(180.0, 0.0, 0.0, 1.4, 32.0, 0.32)
    observer = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    handles = handle_positions_arcsec(far_side, observer)
    assert all(math.isnan(value) for value in handles["apex"])

    near_side = GCSParameters(90.0, 0.0, 0.0, 9.0, 32.0, 0.32)
    assert all(math.isfinite(value) for value in handle_positions_arcsec(near_side, observer)["apex"])


@pytest.mark.parametrize("lon", [35.0, 90.0, 103.0, 150.0])
def test_a_radial_drag_holds_the_direction_even_past_the_limb(lon):
    """A sight line crosses the shell's sphere twice, and the two roots are
    different CMEs. Past the limb the apex sits on the far root, so a near-side-
    only inverse would flip the eruption to the other side of the Sun mid-drag.
    """
    observer = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    params = GCSParameters(lon, 10.0, 0.0, 12.0, 32.0, 0.32)
    apex = apex_arcsec(params, observer)
    target = (apex[0] * 1.15, apex[1] * 1.15)
    moved = apply_apex_drag(params, observer, target)

    # ~2e-3 deg of coupling residual is a fixed point of the two-step decomposition,
    # not incomplete convergence (identical at 2, 3 and 4 passes). That is ~0.3
    # arcsec at 12 R_sun, far under one coronagraph pixel; lock="radial" pins it.
    assert moved.lon_deg == pytest.approx(lon, abs=5e-3)
    assert moved.lat_deg == pytest.approx(10.0, abs=5e-3)
    assert moved.height_rsun > params.height_rsun
    landed = apex_arcsec(moved, observer)
    assert math.hypot(landed[0] - target[0], landed[1] - target[1]) < 1.0


def test_the_sphere_inverse_offers_the_root_nearest_the_current_fit():
    observer = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    params = GCSParameters(140.0, 0.0, 0.0, 12.0, 32.0, 0.32)
    tx, ty = apex_arcsec(params, observer)

    near = direction_from_arcsec(tx, ty, radius_rsun=12.0, observer=observer)
    far = direction_from_arcsec(
        tx, ty, radius_rsun=12.0, observer=observer, prefer=(140.0, 0.0)
    )
    assert far is not None and far[0] == pytest.approx(140.0, abs=1e-6)
    # Without a preference the near-side root is a genuinely different CME.
    assert near is not None and abs(near[0] - 140.0) > 10.0


def test_a_locked_radial_drag_holds_the_direction_exactly():
    """The escape hatch for the coupling residual above."""
    observer = ObserverGeometry(lon_deg=0.0, lat_deg=0.0, dsun_rsun=215.0, rsun_arcsec=960.0)
    params = GCSParameters(103.0, 10.0, 0.0, 12.0, 32.0, 0.32)
    apex = apex_arcsec(params, observer)
    moved = apply_apex_drag(params, observer, (apex[0] * 1.15, apex[1] * 1.15), lock="radial")
    assert moved.lon_deg == pytest.approx(103.0, abs=1e-12)
    assert moved.lat_deg == pytest.approx(10.0, abs=1e-12)
    assert moved.height_rsun > params.height_rsun


# --- Staged refinement ---------------------------------------------------------------


def test_refine_frees_parameters_in_stages_as_points_accumulate():
    """Refine is useful from the second point on: height first, direction, tilt, then α and κ."""
    allowed = PARAMETER_NAMES
    stages = (("height_rsun",), ("lon_deg", "lat_deg"), ("tilt_deg",), ("alpha_deg",), ("kappa",))
    expected = {
        1: (),
        2: ("height_rsun",),
        3: ("height_rsun",),  # the direction arrives as a pair
        4: ("lon_deg", "lat_deg", "height_rsun"),
        5: ("lon_deg", "lat_deg", "tilt_deg", "height_rsun"),
        6: ("lon_deg", "lat_deg", "tilt_deg", "height_rsun", "alpha_deg"),
        7: PARAMETER_NAMES,
        40: PARAMETER_NAMES,
    }
    for points, free in expected.items():
        assert staged_parameters(allowed, stages, points, PARAMETER_NAMES)[0] == free, points
    assert staged_parameters(allowed, stages, 3, PARAMETER_NAMES)[1] == ("lon_deg", "lat_deg")
    assert staged_parameters(allowed, stages, 7, PARAMETER_NAMES)[1] == ()


def test_refine_plan_holds_the_direction_without_two_separated_views():
    one_view = [GCSViewpoint(VIEW_A, _synthetic_clicks(TRUTH, VIEW_A, n=6), "A")]
    free, upcoming, points = refine_plan(one_view)
    assert points == 6 and "lon_deg" not in free and "lat_deg" not in free
    assert free == ("tilt_deg", "height_rsun", "alpha_deg", "kappa") and upcoming == ()


def test_refine_plan_with_many_points_frees_what_free_parameters_allows(two_viewpoints):
    free, upcoming, points = refine_plan(two_viewpoints)
    assert free == free_parameters(two_viewpoints) and upcoming == () and points == 50


def test_a_two_point_refine_fits_the_height_alone():
    """Clicks outside a too-small shell pull its height out to the front; nothing else moves."""
    views = [GCSViewpoint(VIEW_A, _synthetic_clicks(TRUTH, VIEW_A, n=2, noise=0.0), "A")]
    seed = TRUTH.replace_values(height_rsun=TRUTH.height_rsun - 1.5)
    free, _upcoming, _points = refine_plan(views)
    result = refine_gcs(views, seed, free=free)
    assert result.free == ("height_rsun",)
    assert result.parameters.height_rsun > seed.height_rsun
    for name in ("lon_deg", "lat_deg", "tilt_deg", "alpha_deg", "kappa"):
        assert getattr(result.parameters, name) == getattr(seed, name)
