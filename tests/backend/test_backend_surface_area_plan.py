"""
e-CALLISTO FITS Analyzer
Tests from the HMI sunspot-area implementation plan, section 10.1
(src/backend/solar/surface_measure.py: mask_surface_area with a mu cutoff,
status and exclusion counts, line-of-sight projected area, the orthographic
comparison method, WCS corner solid angles and subpixel sampling).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.backend.solar.coronagraph import RSUN_KM
from src.backend.solar.surface_measure import (
    DEFAULT_MU_MIN,
    SurfaceGeometry,
    km2_to_msh,
    mask_surface_area,
    msh_to_km2,
    pixel_solid_angles,
    project_to_sphere,
    wcs_pixel_solid_angles,
)

RSUN = 960.0
ARCSEC = math.pi / 648000.0


def _single_pixel_at(tx: float, ty: float = 0.0, scale: float = 0.01, **kw) -> tuple[np.ndarray, SurfaceGeometry]:
    """A 1x1 image whose only pixel centre looks along (tx, ty)."""
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_arcsec=tx, y_ref_arcsec=ty,
                        x_scale_arcsec_per_pix=scale, y_scale_arcsec_per_pix=scale, **kw)
    return np.ones((1, 1), dtype=bool), g


def _alpha_for_q(q: float, g: SurfaceGeometry) -> float:
    """Arcsec from Sun centre where the impact parameter D sin(alpha) = q R."""
    return math.degrees(math.asin(q / g.observer_distance_rsun)) * 3600.0


def test_default_cutoff_is_the_plans():
    assert DEFAULT_MU_MIN == pytest.approx(0.30)


def test_disc_centre_pixel():
    mask, g = _single_pixel_at(0.0)
    a = mask_surface_area(mask, g, mu_min=DEFAULT_MU_MIN)
    big_d = g.observer_distance_rsun
    omega = (0.01 * ARCSEC) ** 2
    expected = (big_d - 1.0) ** 2 * omega * RSUN_KM**2
    assert a.status == "ok"
    assert a.min_mu == pytest.approx(1.0)
    assert a.on_disk_km2 == pytest.approx(expected, rel=1e-9)
    assert a.projected_km2 == pytest.approx(expected, rel=1e-9)
    assert a.correction_factor == pytest.approx(1.0)


def test_impact_parameter_point_eight_gives_mu_point_six():
    probe = SurfaceGeometry(rsun_arcsec=RSUN)
    mask, g = _single_pixel_at(_alpha_for_q(0.8, probe))
    a = mask_surface_area(mask, g, mu_min=DEFAULT_MU_MIN)
    assert a.status == "ok"
    assert a.min_mu == pytest.approx(0.6, abs=1e-6)
    assert a.correction_factor == pytest.approx(5.0 / 3.0, rel=1e-6)
    # And the plan's d = D cos(alpha) - R mu.
    alpha = _alpha_for_q(0.8, probe) * ARCSEC
    proj = project_to_sphere(_alpha_for_q(0.8, probe), 0.0, probe)
    assert float(proj.distance) == pytest.approx(probe.observer_distance_rsun * math.cos(alpha) - 0.6, rel=1e-9)


def test_off_disc_ray_is_excluded_and_reported():
    mask, g = _single_pixel_at(1.2 * RSUN)
    a = mask_surface_area(mask, g, mu_min=DEFAULT_MU_MIN)
    assert a.status == "invalid"
    assert a.off_disc_px == pytest.approx(1.0)
    assert a.valid_px == 0.0
    assert a.projected_km2 is None and a.correction_factor is None
    assert not a.is_valid


def test_tangent_ray_is_rejected_without_dividing_by_zero():
    probe = SurfaceGeometry(rsun_arcsec=RSUN)
    mask, g = _single_pixel_at(_alpha_for_q(1.0, probe), scale=1e-6)
    with np.errstate(all="raise"):
        a = mask_surface_area(mask, g, mu_min=DEFAULT_MU_MIN)
    assert a.status == "invalid"
    assert a.valid_px == 0.0
    assert a.off_disc_px + a.near_limb_px == pytest.approx(1.0)


def test_pixels_below_the_cutoff_are_excluded_and_marked_partial():
    probe = SurfaceGeometry(rsun_arcsec=RSUN)
    inside = _alpha_for_q(0.5, probe)  # mu 0.87
    outside = _alpha_for_q(0.98, probe)  # mu 0.2
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=0.0, x_ref_arcsec=inside,
                        x_scale_arcsec_per_pix=outside - inside, y_scale_arcsec_per_pix=1.0)
    a = mask_surface_area(np.ones((1, 2), dtype=bool), g, mu_min=DEFAULT_MU_MIN)
    assert a.status == "partial"
    assert a.near_limb_px == pytest.approx(1.0)
    assert a.valid_px == pytest.approx(1.0)
    assert a.min_mu == pytest.approx(math.sqrt(1 - 0.25), abs=1e-3)
    # The valid part alone is what is reported.
    only_inside = mask_surface_area(np.array([[True, False]]), g, mu_min=DEFAULT_MU_MIN)
    assert a.on_disk_km2 == pytest.approx(only_inside.on_disk_km2)


def test_empty_and_all_invalid_selections():
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=1.5, y_ref_pix=1.5, x_scale_arcsec_per_pix=2000.0, y_scale_arcsec_per_pix=2000.0)
    empty = mask_surface_area(np.zeros((4, 4), dtype=bool), g, mu_min=DEFAULT_MU_MIN)
    assert empty.status == "empty" and not empty.is_valid
    corners = np.zeros((4, 4), dtype=bool)
    corners[0, 0] = corners[3, 3] = True  # +-3000" from centre: sky
    sky = mask_surface_area(corners, g, mu_min=DEFAULT_MU_MIN)
    assert sky.status == "invalid"
    assert sky.off_disc_px == pytest.approx(2.0)


def test_fractional_coverage_counts_half_a_pixel_as_half():
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=4.5, y_ref_pix=4.5, x_scale_arcsec_per_pix=2.0, y_scale_arcsec_per_pix=2.0)
    full = np.zeros((10, 10))
    full[5, 5] = 1.0
    half = full * 0.5
    a_full = mask_surface_area(full, g, mu_min=DEFAULT_MU_MIN)
    a_half = mask_surface_area(half, g, mu_min=DEFAULT_MU_MIN)
    assert a_half.on_disk_km2 == pytest.approx(0.5 * a_full.on_disk_km2)
    assert a_half.selected_weight == pytest.approx(0.5)
    with pytest.raises(ValueError):
        mask_surface_area(full * 2.0, g)


def test_unit_conversions():
    one_msh = 2 * math.pi * RSUN_KM**2 / 1e6
    assert msh_to_km2(1.0) == pytest.approx(one_msh)
    assert km2_to_msh(one_msh) == pytest.approx(1.0)
    assert one_msh == pytest.approx(3.041e6, rel=1e-3)
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=4.5, y_ref_pix=4.5, x_scale_arcsec_per_pix=2.0, y_scale_arcsec_per_pix=2.0)
    a = mask_surface_area(np.ones((10, 10), dtype=bool), g, mu_min=DEFAULT_MU_MIN)
    assert a.on_disk_mm2 == pytest.approx(a.on_disk_km2 / 1e6)
    assert a.on_disk_msh == pytest.approx(km2_to_msh(a.on_disk_km2))


def test_corner_solid_angles_follow_unequal_and_rotated_scales():
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_scale_arcsec_per_pix=0.5, y_scale_arcsec_per_pix=0.7, rotation_deg=30.0)
    omega = pixel_solid_angles(g, np.array([0.0, 100.0]), np.array([0.0, -40.0]))
    np.testing.assert_allclose(omega, 0.5 * 0.7 * ARCSEC**2, rtol=1e-6)


def test_fast_solid_angle_path_agrees_with_the_wcs_corner_method_over_the_hmi_disc():
    """Plan acceptance: the fast |s_x s_y| path within 0.1% of WCS corners, or off by default."""
    pytest.importorskip("astropy")
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["HPLN-TAN", "HPLT-TAN"]
    w.wcs.cunit = ["deg", "deg"]
    w.wcs.crpix = [2048.5, 2048.5]
    w.wcs.crval = [0.0, 0.0]
    scale = 0.504 / 3600.0
    r = math.radians(179.93)  # raw HMI orientation
    w.wcs.cd = [[scale * math.cos(r), -scale * math.sin(r)], [scale * math.sin(r), scale * math.cos(r)]]
    cols = np.array([2047.5, 100.0, 3990.0, 2047.0, 300.0, 3700.0])
    rows = np.array([2047.5, 2048.0, 2048.0, 120.0, 400.0, 3800.0])
    corners = wcs_pixel_solid_angles(w, cols, rows)
    fast = (0.504 * ARCSEC) ** 2
    np.testing.assert_allclose(corners, fast, rtol=1e-3)
    # Skewed WCS: the corner method follows the determinant, not the diagonal.
    w.wcs.cd = [[scale, 0.3 * scale], [0.0, scale]]
    skewed = wcs_pixel_solid_angles(w, np.array([2047.5]), np.array([2047.5]))
    assert float(skewed[0]) == pytest.approx(fast, rel=1e-6)


def _cap_weights(g: SurfaceGeometry, n_pix: int, beta_deg: float, sub: int) -> np.ndarray:
    """Coverage of the cap rho <= beta about the sub-observer point, sub x sub samples per pixel."""
    offs = (np.arange(sub) + 0.5) / sub - 0.5
    idx = np.arange(n_pix, dtype=float)
    cols = (idx[None, :, None, None] + offs[None, None, None, :])
    rows = (idx[:, None, None, None] + offs[None, None, :, None])
    tx, ty = g.pixel_to_arcsec(cols, rows)
    proj = project_to_sphere(tx, ty, g)
    inside = proj.on_disk & (np.nan_to_num(proj.z, nan=-2.0) >= math.cos(math.radians(beta_deg)))
    return inside.mean(axis=(2, 3))


def test_spherical_cap_within_a_tenth_of_a_percent_with_refined_coverage():
    n_pix = 300
    beta = 20.0
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=(n_pix - 1) / 2, y_ref_pix=(n_pix - 1) / 2,
                        x_scale_arcsec_per_pix=2.4, y_scale_arcsec_per_pix=2.4)
    truth_msh = (1 - math.cos(math.radians(beta))) * 1e6
    a = mask_surface_area(_cap_weights(g, n_pix, beta, 8), g, mu_min=DEFAULT_MU_MIN)
    assert a.status == "ok"
    assert a.on_disk_msh == pytest.approx(truth_msh, rel=1e-3)


def test_subpixel_sampling_converges():
    n_pix = 120
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=-200.0, y_ref_pix=(n_pix - 1) / 2,
                        x_scale_arcsec_per_pix=2.0, y_scale_arcsec_per_pix=2.0)  # a patch near the limb
    mask = np.ones((n_pix, n_pix), dtype=bool)
    areas = [mask_surface_area(mask, g, mu_min=0.05, subsamples=k).on_disk_msh for k in (1, 2, 4, 8)]
    steps = [abs(areas[i + 1] - areas[i]) for i in range(3)]
    assert steps[2] < steps[1] < steps[0]
    assert steps[2] / areas[3] < 5e-3  # the plan's convergence target


def test_full_disc_visible_cap_with_the_cutoff_disabled():
    n_pix = 1024
    g = SurfaceGeometry(rsun_arcsec=RSUN, x_ref_pix=(n_pix - 1) / 2, y_ref_pix=(n_pix - 1) / 2,
                        x_scale_arcsec_per_pix=2.4, y_scale_arcsec_per_pix=2.4)
    a = mask_surface_area(np.ones((n_pix, n_pix), dtype=bool), g, mu_min=0.0)
    big_d = g.observer_distance_rsun
    assert a.on_disk_msh == pytest.approx(1e6 * (1 - 1 / big_d), rel=5e-4)


def test_orthographic_comparison_differs_as_the_geometry_says():
    mask, g = _single_pixel_at(0.0)
    finite = mask_surface_area(mask, g, mu_min=DEFAULT_MU_MIN, method="finite_distance")
    ortho = mask_surface_area(mask, g, mu_min=DEFAULT_MU_MIN, method="orthographic")
    big_d = g.observer_distance_rsun
    rho = RSUN * ARCSEC
    # At disc centre: (D - R)^2 Omega against Omega R^2 / rho^2.
    assert finite.on_disk_km2 / ortho.on_disk_km2 == pytest.approx(((big_d - 1.0) * rho) ** 2, rel=1e-6)
    assert ortho.method == "orthographic"
    with pytest.raises(ValueError):
        mask_surface_area(mask, g, method="guess")
