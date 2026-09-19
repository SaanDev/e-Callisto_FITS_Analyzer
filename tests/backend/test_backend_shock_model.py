"""
e-CALLISTO FITS Analyzer
Unit tests for the spheroid and ellipsoid shock models (src/backend/gcs/shock_model.py).

The tests that matter most pin the *conventions*: the parameters only mean what
PyThea's do if the semi-axes, the eccentricity sign and the rotation order all
match, and a fit is only worth refining against the outline if the outline
really is the edge of the projected surface.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.backend.gcs.gcs_model import GCSParameters, GCSViewpoint, ObserverGeometry, apex_arcsec, project_points_to_arcsec
from src.backend.gcs.shock_model import (
    ELLIPSOID,
    SHOCK_PARAMETER_NAMES,
    SPHEROID,
    ShockParameters,
    _distance_to_curve,
    apply_shock_apex_drag,
    interpolate_shock_parameters,
    refine_shock,
    shock_apex_arcsec,
    shock_free_parameters,
    shock_handle_positions_arcsec,
    shock_mesh_hgs,
    shock_orientation_matrix,
    shock_parameters_from_axes,
    shock_refine_plan,
    shock_semi_axes,
    shock_silhouette_arcsec,
    shock_silhouette_hgs,
    shock_wireframe_arcsec,
)


def _observer(lon: float, distance: float = 215.0, label: str = "") -> ObserverGeometry:
    return ObserverGeometry(
        lon_deg=lon,
        lat_deg=0.0,
        dsun_rsun=distance,
        rsun_arcsec=math.degrees(math.asin(1.0 / distance)) * 3600.0,
        label=label,
    )


EARTH = _observer(0.0, 213.0, "C3")
STEREO_A = _observer(120.0, 207.0, "COR2-A")
STEREO_B = _observer(-115.0, 219.0, "COR2-B")
SPHERE_SHOCK = ShockParameters(lon_deg=60.0, lat_deg=-10.0, height_rsun=10.0, kappa=0.6, epsilon=0.3)
ELLIPSOID_SHOCK = ShockParameters(60.0, -10.0, 10.0, 0.6, -0.4, tilt_deg=30.0, alpha=1.25, model=ELLIPSOID)


def _local(params: ShockParameters, points_hgs: np.ndarray) -> np.ndarray:
    axes = shock_semi_axes(params)
    return shock_orientation_matrix(params).T @ points_hgs - np.array([[axes.rcenter_rsun], [0.0], [0.0]])


def _ellipsoid_value(params: ShockParameters, points_hgs: np.ndarray) -> np.ndarray:
    axes = shock_semi_axes(params)
    local = _local(params, points_hgs)
    return (
        (local[0] / axes.radaxis_rsun) ** 2
        + (local[1] / axes.orthoaxis1_rsun) ** 2
        + (local[2] / axes.orthoaxis2_rsun) ** 2
    )


# --- PyThea's parameter conventions ---------------------------------------------


@pytest.mark.parametrize("epsilon", [0.6, 0.0, -0.6])
def test_semi_axes_follow_pytheas_self_similar_relations(epsilon):
    params = ShockParameters(10.0, 5.0, 8.0, 0.7, epsilon, tilt_deg=20.0, alpha=1.4, model=ELLIPSOID)
    axes = shock_semi_axes(params)
    b = 0.7 * (8.0 - 1.0)  # kappa = b / (height - 1 R_sun)
    assert axes.orthoaxis1_rsun == pytest.approx(b)
    assert axes.orthoaxis2_rsun == pytest.approx(b / 1.4)  # alpha = b / c
    assert axes.rcenter_rsun + axes.radaxis_rsun == pytest.approx(8.0)  # height = r_centre + a
    ratio = axes.radaxis_rsun / axes.orthoaxis1_rsun
    if epsilon > 0:  # radially elongated: epsilon = +sqrt(1 - (b/a)^2)
        assert ratio > 1 and math.sqrt(1 - (1 / ratio) ** 2) == pytest.approx(epsilon)
    elif epsilon < 0:  # flattened: epsilon = -sqrt(1 - (a/b)^2)
        assert ratio < 1 and -math.sqrt(1 - ratio**2) == pytest.approx(epsilon)
    else:
        assert ratio == pytest.approx(1.0)


@pytest.mark.parametrize("model", [SPHEROID, ELLIPSOID])
@pytest.mark.parametrize("epsilon", [0.8, 0.25, 0.0, -0.25, -0.8])
def test_parameters_round_trip_through_centre_and_semi_axes(model, epsilon):
    params = ShockParameters(-40.0, 12.0, 6.5, 1.3, epsilon, tilt_deg=45.0, alpha=0.8, model=model).as_model(model)
    axes = shock_semi_axes(params)
    back = shock_parameters_from_axes(
        params.lon_deg, params.lat_deg, axes.rcenter_rsun, axes.radaxis_rsun, axes.orthoaxis1_rsun,
        axes.orthoaxis2_rsun, tilt_deg=params.tilt_deg, model=model,
    )
    for name in SHOCK_PARAMETER_NAMES:
        assert getattr(back, name) == pytest.approx(getattr(params, name), abs=1e-12)
    assert back.model == model


def test_a_spheroid_has_no_tilt_or_second_aspect_ratio():
    ellipsoid = ShockParameters(0.0, 0.0, 5.0, 1.0, 0.2, tilt_deg=35.0, alpha=1.3, model=ELLIPSOID)
    spheroid = ellipsoid.as_model(SPHEROID)
    assert (spheroid.tilt_deg, spheroid.alpha, spheroid.model) == (0.0, 1.0, SPHEROID)
    axes = shock_semi_axes(spheroid)
    assert axes.orthoaxis2_rsun == axes.orthoaxis1_rsun
    # Kept: the direction, the apex and the radial/lateral shape the user set.
    assert shock_semi_axes(ellipsoid).orthoaxis1_rsun == axes.orthoaxis1_rsun
    assert shock_semi_axes(ellipsoid).radaxis_rsun == axes.radaxis_rsun
    # Tilt has no effect on a spheroid even if a stray value reaches it.
    assert np.allclose(
        shock_mesh_hgs(spheroid.replace_values(tilt_deg=70.0)), shock_mesh_hgs(spheroid)
    )


@pytest.mark.parametrize(
    "params",
    [
        ShockParameters(0.0, 0.0, 1.0, 1.0, 0.0),  # apex on the photosphere
        ShockParameters(0.0, 0.0, 5.0, 0.0, 0.0),
        ShockParameters(0.0, 0.0, 5.0, 1.0, 1.0),  # epsilon = 1 has no radial axis
        ShockParameters(0.0, 0.0, 5.0, 1.0, -1.0),
        ShockParameters(0.0, 95.0, 5.0, 1.0, 0.0),
        ShockParameters(0.0, 0.0, 5.0, 1.0, 0.0, alpha=0.0, model=ELLIPSOID),
        ShockParameters(float("nan"), 0.0, 5.0, 1.0, 0.0),
        ShockParameters(0.0, 0.0, 5.0, 1.0, 0.0, model="cone"),
    ],
)
def test_unphysical_shocks_are_rejected(params):
    assert not params.is_physical


def test_axes_that_put_the_apex_below_the_photosphere_are_refused():
    with pytest.raises(ValueError, match="above the photosphere"):
        shock_parameters_from_axes(0.0, 0.0, 0.2, 0.5, 1.0)
    with pytest.raises(ValueError, match="positive"):
        shock_parameters_from_axes(0.0, 0.0, 2.0, 0.0, 1.0)


# --- Orientation and surface -------------------------------------------------------


def test_orientation_is_pytheas_extrinsic_xyz_rotation():
    from scipy.spatial.transform import Rotation

    rng = np.random.default_rng(1)
    for _ in range(50):
        params = ShockParameters(
            rng.uniform(-180, 180), rng.uniform(-89, 89), 5.0, 1.0, 0.0,
            tilt_deg=rng.uniform(-90, 90), alpha=1.2, model=ELLIPSOID,
        )
        expected = Rotation.from_euler(
            "xyz", [params.tilt_deg, -params.lat_deg, params.lon_deg], degrees=True
        ).as_matrix()
        assert np.allclose(shock_orientation_matrix(params), expected, atol=1e-12)


def test_the_radial_axis_points_at_lon_lat_and_tilt_zero_keeps_b_equatorial():
    params = ShockParameters(30.0, 20.0, 5.0, 1.0, 0.0, tilt_deg=0.0, alpha=1.2, model=ELLIPSOID)
    rotation = shock_orientation_matrix(params)
    lon, lat = math.radians(30.0), math.radians(20.0)
    assert rotation[:, 0] == pytest.approx([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    assert rotation[2, 1] == pytest.approx(0.0, abs=1e-12)  # b parallel to the solar equator
    assert rotation[2, 2] > 0.0  # c in the meridional plane, towards north
    tilted = shock_orientation_matrix(params.replace_values(tilt_deg=90.0))
    assert tilted[:, 1] == pytest.approx(rotation[:, 2])  # tilt turns b onto c


def test_the_mesh_lies_on_the_surface_with_the_apex_first():
    mesh = shock_mesh_hgs(ELLIPSOID_SHOCK)
    assert np.allclose(_ellipsoid_value(ELLIPSOID_SHOCK, mesh.reshape(3, -1)), 1.0)
    apex = mesh[:, 0, 0]
    assert np.linalg.norm(apex) == pytest.approx(ELLIPSOID_SHOCK.height_rsun)
    assert np.allclose(mesh[:, 0, :], apex[:, None])  # the apex row is one point
    assert np.allclose(mesh[:, :, 0], mesh[:, :, -1])  # rings are closed


def test_tilt_never_moves_the_apex():
    first = shock_apex_arcsec(ELLIPSOID_SHOCK, EARTH)
    second = shock_apex_arcsec(ELLIPSOID_SHOCK.replace_values(tilt_deg=-60.0), EARTH)
    assert first == pytest.approx(second)


def test_the_apex_projects_where_the_gcs_apex_does():
    """The apex drag reuses the GCS solver, which is only valid if both agree."""
    proxy = GCSParameters(SPHERE_SHOCK.lon_deg, SPHERE_SHOCK.lat_deg, 40.0, SPHERE_SHOCK.height_rsun, 30.0, 0.3)
    for observer in (EARTH, STEREO_A, STEREO_B):
        assert shock_apex_arcsec(SPHERE_SHOCK, observer) == pytest.approx(apex_arcsec(proxy, observer), abs=1e-9)


def test_the_projection_agrees_with_sunpy():
    units = pytest.importorskip("astropy.units")
    pytest.importorskip("sunpy.coordinates")
    from astropy.coordinates import CartesianRepresentation, SkyCoord
    from sunpy.coordinates import frames

    obstime = "2012-07-12T16:00:00"
    observer = SkyCoord(120.0 * units.deg, 0.0 * units.deg, 207.0 * units.R_sun,
                        frame=frames.HeliographicStonyhurst, obstime=obstime)
    rsun_arcsec = math.degrees(math.asin(1.0 / 207.0)) * 3600.0
    geometry = ObserverGeometry(lon_deg=120.0, lat_deg=0.0, dsun_rsun=207.0, rsun_arcsec=rsun_arcsec)
    outline = shock_silhouette_hgs(ELLIPSOID_SHOCK, geometry, samples=60)
    tx, ty, _ = project_points_to_arcsec(outline, geometry)
    points = SkyCoord(CartesianRepresentation(*(outline * units.R_sun)),
                      frame=frames.HeliographicStonyhurst, obstime=obstime)
    helioprojective = points.transform_to(frames.Helioprojective(observer=observer, obstime=obstime))
    assert tx == pytest.approx(helioprojective.Tx.to_value(units.arcsec), abs=1e-3)
    assert ty == pytest.approx(helioprojective.Ty.to_value(units.arcsec), abs=1e-3)


# --- The outline --------------------------------------------------------------------


@pytest.mark.parametrize("params", [SPHERE_SHOCK, ELLIPSOID_SHOCK])
@pytest.mark.parametrize("observer", [EARTH, STEREO_A, STEREO_B])
def test_the_outline_is_where_sight_lines_graze_the_surface(params, observer):
    outline = shock_silhouette_hgs(params, observer)
    assert np.allclose(_ellipsoid_value(params, outline), 1.0)
    assert np.allclose(outline[:, 0], outline[:, -1])  # closed
    axes = shock_semi_axes(params)
    local = _local(params, outline)
    eye = _local(params, observer.dsun_rsun * observer.projection_matrix[2][:, None])
    normal = local / np.array([[axes.radaxis_rsun**2], [axes.orthoaxis1_rsun**2], [axes.orthoaxis2_rsun**2]])
    sight = local - eye
    cosine = np.einsum("in,in->n", normal, sight) / (np.linalg.norm(normal, axis=0) * np.linalg.norm(sight, axis=0))
    assert np.abs(cosine).max() < 1e-9


@pytest.mark.parametrize("params", [SPHERE_SHOCK, ELLIPSOID_SHOCK])
def test_the_outline_encloses_the_whole_projected_surface(params):
    from matplotlib.path import Path

    x, y, _ = project_points_to_arcsec(shock_silhouette_hgs(params, STEREO_A, samples=2000), STEREO_A)
    mesh = shock_mesh_hgs(params, n_polar=121, n_azimuth=241).reshape(3, -1)
    mx, my, _ = project_points_to_arcsec(mesh, STEREO_A)
    edge = Path(np.column_stack((x, y)))
    points = np.column_stack((mx, my))
    outside = points[~edge.contains_points(points, radius=2.0) & ~edge.contains_points(points, radius=-2.0)]
    distance = _distance_to_curve(outside, x, y) if len(outside) else np.zeros(1)
    assert distance.max() < 0.5  # arcsec: nothing on the surface projects beyond the outline


def test_an_observer_inside_the_shock_sees_no_outline():
    engulfing = ShockParameters(0.0, 0.0, 250.0, 1.0, 0.0)
    assert shock_silhouette_hgs(engulfing, EARTH) is None
    x, y = shock_silhouette_arcsec(engulfing, EARTH)
    assert x.size == y.size == 0


def test_the_occulter_and_the_disk_hide_the_outline():
    x, _ = shock_silhouette_arcsec(SPHERE_SHOCK, EARTH)
    masked, _ = shock_silhouette_arcsec(SPHERE_SHOCK, EARTH, fov_rsun=(3.7, 6.0))
    radius = np.hypot(*shock_silhouette_arcsec(SPHERE_SHOCK, EARTH)) / EARTH.rsun_arcsec
    visible = np.isfinite(x)
    assert np.all(np.isnan(masked[visible & ((radius < 3.7) | (radius > 6.0))]))
    assert np.any(np.isfinite(masked))
    low = ShockParameters(90.0, 0.0, 1.6, 1.2, 0.0)  # a low dome on the west limb: half inside the Sun
    x_low, _ = shock_silhouette_arcsec(low, EARTH)
    assert np.any(np.isnan(x_low)) and np.any(np.isfinite(x_low))


def test_the_wireframe_draws_rings_meridians_and_the_outline():
    x, y = shock_wireframe_arcsec(SPHERE_SHOCK, EARTH, ring_stride=4, n_meridians=8)
    assert x.shape == y.shape and np.any(np.isfinite(x))
    outline_x, _ = shock_silhouette_arcsec(SPHERE_SHOCK, EARTH)
    assert np.isin(outline_x[np.isfinite(outline_x)], x).all()
    dense, _ = shock_wireframe_arcsec(SPHERE_SHOCK, EARTH, ring_stride=1, n_meridians=11)
    assert np.count_nonzero(np.isfinite(dense)) > np.count_nonzero(np.isfinite(x))


def test_distance_to_a_broken_curve_uses_only_drawn_segments():
    x = np.array([0.0, 10.0, np.nan, 20.0, 30.0])
    y = np.zeros_like(x)
    distance = _distance_to_curve(np.array([[5.0, 3.0], [15.0, 0.0], [-4.0, 3.0]]), x, y)
    assert distance == pytest.approx([3.0, 5.0, 5.0])
    assert _distance_to_curve(np.array([[0.0, 0.0]]), np.array([np.nan, 1.0]), np.array([0.0, np.nan])) is None


# --- Direct manipulation and playback -------------------------------------------------


def test_the_apex_drag_lands_under_the_cursor_and_expands_self_similarly():
    target = (-3.0 * EARTH.rsun_arcsec * 0.9, -2.0 * EARTH.rsun_arcsec * 0.9)
    moved = apply_shock_apex_drag(ELLIPSOID_SHOCK, EARTH, target)
    assert shock_apex_arcsec(moved, EARTH) == pytest.approx(target, abs=0.5)
    assert (moved.kappa, moved.epsilon, moved.alpha, moved.tilt_deg, moved.model) == (
        ELLIPSOID_SHOCK.kappa, ELLIPSOID_SHOCK.epsilon, ELLIPSOID_SHOCK.alpha, ELLIPSOID_SHOCK.tilt_deg, ELLIPSOID
    )
    radial = apply_shock_apex_drag(SPHERE_SHOCK, EARTH, tuple(2.0 * v for v in shock_apex_arcsec(SPHERE_SHOCK, EARTH)),
                                   lock="radial")
    assert (radial.lon_deg, radial.lat_deg) == (SPHERE_SHOCK.lon_deg, SPHERE_SHOCK.lat_deg)
    assert radial.height_rsun > SPHERE_SHOCK.height_rsun


def test_a_hidden_apex_offers_no_handle():
    behind = ShockParameters(180.0, 0.0, 1.5, 0.5, 0.0)  # far side, low
    assert all(math.isnan(value) for value in shock_handle_positions_arcsec(behind, EARTH)["apex"])
    visible = shock_handle_positions_arcsec(SPHERE_SHOCK, EARTH)["apex"]
    assert visible == pytest.approx(shock_apex_arcsec(SPHERE_SHOCK, EARTH))


def test_interpolation_is_exact_at_the_ends_and_linear_between():
    later = SPHERE_SHOCK.replace_values(height_rsun=14.0, kappa=0.8, epsilon=0.1, lon_deg=70.0)
    assert interpolate_shock_parameters(SPHERE_SHOCK, later, 0.0) is SPHERE_SHOCK
    assert interpolate_shock_parameters(SPHERE_SHOCK, later, 1.0) is later
    middle = interpolate_shock_parameters(SPHERE_SHOCK, later, 0.5)
    assert (middle.height_rsun, middle.kappa, middle.epsilon, middle.lon_deg) == pytest.approx((12.0, 0.7, 0.2, 65.0))
    assert middle.model == SPHEROID


def test_interpolation_takes_angles_the_short_way_and_turns_a_spheroid_into_an_ellipsoid():
    west = ShockParameters(170.0, 0.0, 5.0, 1.0, 0.0)
    east = ShockParameters(-170.0, 0.0, 5.0, 1.0, 0.0)
    assert math.cos(math.radians(interpolate_shock_parameters(west, east, 0.5).lon_deg)) == pytest.approx(-1.0)
    first = ShockParameters(0.0, 0.0, 5.0, 1.0, 0.0, tilt_deg=85.0, alpha=1.2, model=ELLIPSOID)
    second = first.replace_values(tilt_deg=-85.0)
    assert interpolate_shock_parameters(first, second, 0.5).tilt_deg % 180.0 == pytest.approx(90.0)
    mixed = interpolate_shock_parameters(west.replace_values(lon_deg=0.0), first, 0.5)
    assert mixed.model == ELLIPSOID
    assert mixed.tilt_deg == pytest.approx(85.0)  # a spheroid's tilt is meaningless, so the ellipsoid's is held
    assert mixed.alpha == pytest.approx(1.1)


# --- Refinement -----------------------------------------------------------------------------

FIELDS = {"COR2-B": (2.5, 15.0), "C3": (3.7, 30.0), "COR2-A": (2.5, 15.0)}


def _views(truth, observers, *, points=15, noise=15.0, seed=3):
    rng = np.random.default_rng(seed)
    views = []
    for observer in observers:
        fov = FIELDS[observer.label]
        x, y = shock_silhouette_arcsec(truth, observer, samples=2000, fov_rsun=fov)
        visible = np.nonzero(np.isfinite(x))[0]
        chosen = rng.choice(visible, size=points, replace=False)
        clicks = np.column_stack((x[chosen], y[chosen])) + rng.normal(0.0, noise, (points, 2))
        views.append(GCSViewpoint(observer, clicks, observer.label, fov_rsun=fov))
    return views


@pytest.mark.parametrize("truth", [SPHERE_SHOCK, ELLIPSOID_SHOCK])
def test_refine_recovers_a_shock_seen_from_three_spacecraft(truth):
    views = _views(truth, (STEREO_B, EARTH, STEREO_A))
    seed = truth.replace_values(
        lon_deg=truth.lon_deg + 6.0, lat_deg=truth.lat_deg - 4.0, height_rsun=truth.height_rsun + 0.8,
        kappa=truth.kappa + 0.08, epsilon=truth.epsilon - 0.12,
    )
    result = refine_shock(views, seed)
    assert result.converged
    assert result.rms_arcsec < 25.0 < result.seed_rms_arcsec
    assert result.n_viewpoints == 3 and result.n_points == 45
    fitted = result.parameters
    assert abs(fitted.lon_deg - truth.lon_deg) < 0.5 and abs(fitted.lat_deg - truth.lat_deg) < 0.5
    assert fitted.height_rsun == pytest.approx(truth.height_rsun, abs=0.05)
    assert fitted.kappa == pytest.approx(truth.kappa, abs=0.01)
    assert fitted.epsilon == pytest.approx(truth.epsilon, abs=0.03)
    assert fitted.model == truth.model
    assert all(math.isfinite(result.sigma[name]) for name in result.free)


def test_one_view_holds_the_direction_and_a_spheroid_has_no_tilt_to_fit():
    views = _views(SPHERE_SHOCK, (EARTH,))
    assert shock_free_parameters(views, SPHEROID) == ("height_rsun", "kappa", "epsilon")
    assert shock_free_parameters(views, ELLIPSOID) == ("tilt_deg", "height_rsun", "kappa", "epsilon", "alpha")
    result = refine_shock(views, SPHERE_SHOCK.replace_values(height_rsun=10.5))
    assert (result.parameters.lon_deg, result.parameters.lat_deg) == (SPHERE_SHOCK.lon_deg, SPHERE_SHOCK.lat_deg)
    assert "direction held fixed" in result.message
    assert "epsilon weakly constrained" in result.message


def test_an_ellipsoid_in_two_views_is_flagged_as_coupled():
    views = _views(ELLIPSOID_SHOCK, (EARTH, STEREO_A))
    result = refine_shock(views, ELLIPSOID_SHOCK.replace_values(height_rsun=10.4))
    assert "prefer a spheroid" in result.message
    assert set(result.weakly_constrained) == {"epsilon", "tilt_deg"}


@pytest.mark.parametrize(
    "views, seed, free, message",
    [
        ([], SPHERE_SHOCK, None, "at least one viewpoint"),
        ([GCSViewpoint(EARTH, np.empty((0, 2)))], SPHERE_SHOCK, None, "Click along the shock front"),
        ([GCSViewpoint(EARTH, np.array([[5000.0, 0.0]] * 3))], SPHERE_SHOCK, None, "needs at least 4"),
        ([GCSViewpoint(EARTH, np.array([[5000.0, 0.0]] * 9))], SPHERE_SHOCK.replace_values(kappa=3.0), None,
         "outside the refinement range"),
        ([GCSViewpoint(EARTH, np.array([[5000.0, 0.0]] * 9))], SPHERE_SHOCK, ("tilt_deg",), "no tilt"),
        ([GCSViewpoint(EARTH, np.array([[5000.0, 0.0]] * 9))], SPHERE_SHOCK, ("speed",), "Unknown shock parameter"),
        ([GCSViewpoint(EARTH, np.array([[5000.0, 0.0]] * 9), "C3", fov_rsun=(28.0, 30.0))], SPHERE_SHOCK, None,
         "not visible in C3"),
    ],
)
def test_refine_refuses_with_user_facing_text(views, seed, free, message):
    with pytest.raises(ValueError, match=message):
        refine_shock(views, seed, free=free)


# --- Staged refinement ---------------------------------------------------------------


def _clicks(params, observer, n):
    x, y = shock_silhouette_arcsec(params, observer, samples=400)
    visible = np.nonzero(np.isfinite(x))[0]
    chosen = visible[np.linspace(0, len(visible) - 1, n).astype(int)]
    return np.column_stack((x[chosen], y[chosen]))


def test_shock_refine_frees_apex_height_then_direction_then_shape():
    def plan(points, params=SPHERE_SHOCK):
        views = [
            GCSViewpoint(EARTH, _clicks(params, EARTH, points - points // 2), "C3"),
            GCSViewpoint(STEREO_A, _clicks(params, STEREO_A, points // 2), "COR2-A"),
        ]
        return shock_refine_plan(views, params.model)

    assert plan(2)[0] == ("height_rsun",)
    assert plan(4)[0] == ("lon_deg", "lat_deg", "height_rsun")
    assert plan(5)[0] == ("lon_deg", "lat_deg", "height_rsun", "kappa")
    assert plan(6)[0] == ("lon_deg", "lat_deg", "height_rsun", "kappa", "epsilon")
    assert plan(6)[1] == ()  # a spheroid has no b/c or tilt to add
    ellipsoid = plan(8, ELLIPSOID_SHOCK)[0]
    assert set(ellipsoid) == set(SHOCK_PARAMETER_NAMES)
    assert plan(7, ELLIPSOID_SHOCK)[1] == ("tilt_deg",)
