"""
e-CALLISTO FITS Analyzer
Unit tests for multi-instrument coronagraph composites
(src/Backend/coronagraph_composite.py).

Everything here is offline: the geometry, colour mapping, blending and
time-matching are plain numpy, so no archive access or sunpy Map is needed.
Reprojection itself is the caller's job (see test_backend_multiview.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from src.Backend.coronagraph_composite import (
    APPROXIMATE_SCREENS,
    SCREEN_SPHERICAL,
    SCREEN_SURFACE,
    LayerSpec,
    composite_frame,
    composite_nickname,
    default_fov_rsun,
    default_gamma_for,
    default_screen_for,
    layer_alpha,
    layer_config_hash,
    layer_rgb,
    match_layer_frames,
    order_layers,
    radial_alpha_mask,
    resolve_detector,
    rsun_pixels,
)

SHAPE = (101, 101)
CENTER = (50.0, 50.0)
RSUN_PX = 10.0


def radial_grid(shape=SHAPE, center=CENTER):
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return np.hypot(xx - center[0], yy - center[1])


def coronal_falloff():
    """A steep radial brightness profile, like a real white-light corona."""
    return 1000.0 / (1.0 + radial_grid())


def bright_disk():
    return np.exp(-((radial_grid() / 12.0) ** 2)) * 900.0 + 5.0


# --------------------------------------------------------------------------- #
# Field of view and screen defaults
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "instrument, value, expected",
    [
        ("LASCO", "C2", (2.2, 6.0)),
        ("LASCO", "C3", (3.7, 30.0)),
        ("SECCHI", ("STEREO_A", "COR1", None), (1.4, 4.0)),
        ("SECCHI", ("STEREO_A", "COR2", None), (2.5, 15.0)),
        ("SECCHI", ("STEREO_A", "EUVI", 195.0), (0.0, 1.7)),
        ("AIA", 193.0, (0.0, 1.28)),
        ("SUVI", 195.0, (0.0, 1.6)),
        ("HMI", "magnetogram", (0.0, 1.0)),
    ],
)
def test_default_fov_rsun_per_instrument(instrument, value, expected):
    assert default_fov_rsun(instrument, value) == expected


def test_default_fov_falls_back_for_unknown_instrument():
    inner, outer = default_fov_rsun("MYSTERY", None)
    assert inner == 0.0 and outer > 0.0


def test_resolve_detector():
    assert resolve_detector("LASCO", "c2") == "C2"
    assert resolve_detector("SECCHI", ("STEREO_B", "cor2", None)) == "COR2"
    assert resolve_detector("AIA", 193.0) is None


def test_default_screen_matches_science_class():
    # Disk emission genuinely sits on the photosphere.
    assert default_screen_for("AIA", 193.0) == SCREEN_SURFACE
    assert default_screen_for("SECCHI", ("STEREO_A", "EUVI", 195.0)) == SCREEN_SURFACE
    assert default_screen_for("HMI", "magnetogram") == SCREEN_SURFACE
    # Optically-thin corona has no unique line-of-sight depth.
    assert default_screen_for("LASCO", "C2") == SCREEN_SPHERICAL
    assert default_screen_for("SECCHI", ("STEREO_A", "COR2", None)) == SCREEN_SPHERICAL


def test_layer_spec_resolves_and_flags_approximation():
    aia = LayerSpec("AIA", 193.0, "AIA 193")
    cor2 = LayerSpec("SECCHI", ("STEREO_A", "COR2", None), "COR2")
    assert aia.resolved_screen() == SCREEN_SURFACE
    assert aia.is_approximate() is False
    assert cor2.resolved_screen() in APPROXIMATE_SCREENS
    assert cor2.is_approximate() is True
    # An explicit screen overrides the automatic choice.
    assert LayerSpec("AIA", 193.0, "x", screen="planar").resolved_screen() == "planar"


def test_layer_spec_explicit_radii_override_defaults():
    spec = LayerSpec("LASCO", "C2", "C2", inner_rsun=1.0, outer_rsun=9.0)
    assert spec.resolved_fov() == (1.0, 9.0)
    # Outer is never allowed below inner.
    assert LayerSpec("AIA", 193.0, "x", inner_rsun=5.0, outer_rsun=1.0).resolved_fov() == (5.0, 5.0)


# --------------------------------------------------------------------------- #
# rsun_pixels
# --------------------------------------------------------------------------- #
class FakeFrame:
    def __init__(self, meta):
        self.meta = dict(meta)


def test_rsun_pixels_from_header():
    # 945" apparent radius at 11.9"/pixel is ~79 pixels (a real LASCO C2 scale).
    assert rsun_pixels(FakeFrame({"rsun_obs": 945.0, "cdelt1": 11.9})) == pytest.approx(79.41, abs=0.05)


def test_rsun_pixels_falls_back_without_headers():
    value = rsun_pixels(FakeFrame({}), data_shape=(1024, 1024))
    assert value > 0.0  # never zero: masks would collapse


def test_rsun_pixels_survives_zero_cdelt():
    assert rsun_pixels(FakeFrame({"rsun_obs": 945.0, "cdelt1": 0.0}), data_shape=(100, 100)) > 0.0


# --------------------------------------------------------------------------- #
# Radial alpha mask
# --------------------------------------------------------------------------- #
def test_radial_alpha_mask_is_half_at_each_boundary():
    # Wide enough to hold C2's full 6 Rsun outer edge at 10 px per Rsun.
    mask = radial_alpha_mask((161, 161), (80.0, 80.0), 10.0, inner_rsun=2.2, outer_rsun=6.0)
    # Feather ramps are centred on the boundary, so alpha is exactly 0.5 there.
    assert mask[80, 80 + 22] == pytest.approx(0.5, abs=0.02)
    assert mask[80, 80 + 60] == pytest.approx(0.5, abs=0.02)
    # Fully opaque mid-annulus, fully transparent inside the occulter and outside.
    assert mask[80, 80 + 40] == pytest.approx(1.0)
    assert mask[80, 80] == pytest.approx(0.0)
    assert mask[80, 80 + 75] == pytest.approx(0.0)
    assert mask.min() >= 0.0 and mask.max() <= 1.0


def test_radial_alpha_mask_disk_has_no_central_hole():
    mask = radial_alpha_mask(SHAPE, CENTER, RSUN_PX, inner_rsun=0.0, outer_rsun=1.28)
    assert mask[50, 50] == pytest.approx(1.0)
    assert mask[50, 50 + 30] == pytest.approx(0.0)


def test_radial_alpha_mask_is_monotone_across_the_outer_edge():
    mask = radial_alpha_mask(SHAPE, CENTER, RSUN_PX, inner_rsun=0.0, outer_rsun=4.0)
    profile = mask[50, 50:]
    assert np.all(np.diff(profile) <= 1e-9)


def test_radial_alpha_mask_honours_an_off_centre_sun():
    mask = radial_alpha_mask(SHAPE, (20.0, 70.0), RSUN_PX, inner_rsun=0.0, outer_rsun=2.0)
    assert mask[70, 20] == pytest.approx(1.0)
    assert mask[50, 50] == pytest.approx(0.0)


def test_radial_alpha_mask_handles_degenerate_shape():
    assert radial_alpha_mask((0, 0), CENTER, RSUN_PX).size == 0


# --------------------------------------------------------------------------- #
# Layer rendering and alpha
# --------------------------------------------------------------------------- #
def test_layer_rgb_returns_unit_float_rgb():
    rgb = layer_rgb(coronal_falloff(), LayerSpec("LASCO", "C2", "C2", colormap="soholasco2"))
    assert rgb.shape == (*SHAPE, 3)
    assert rgb.min() >= 0.0 and rgb.max() <= 1.0


def test_layer_rgb_passes_through_existing_rgb():
    src = np.zeros((4, 4, 3), np.uint8)
    src[..., 0] = 255
    out = layer_rgb(src, LayerSpec("AIA", 193.0, "x"))
    assert out[0, 0, 0] == pytest.approx(1.0)
    assert out[0, 0, 1] == pytest.approx(0.0)


def test_layer_alpha_zeroes_non_finite_pixels():
    data = bright_disk()
    data[50, 50] = np.nan  # a reprojection hole at the very centre
    spec = LayerSpec("AIA", 193.0, "AIA", inner_rsun=0.0, outer_rsun=2.0)
    alpha = layer_alpha(data, spec, center=CENTER, rsun_px=RSUN_PX)
    assert alpha[50, 50] == 0.0
    assert alpha[50, 55] > 0.0


def test_layer_alpha_scales_with_opacity():
    spec = LayerSpec("AIA", 193.0, "AIA", inner_rsun=0.0, outer_rsun=2.0, opacity=0.25)
    alpha = layer_alpha(bright_disk(), spec, center=CENTER, rsun_px=RSUN_PX)
    assert alpha.max() == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# Ordering and blending
# --------------------------------------------------------------------------- #
def test_order_layers_paints_widest_field_of_view_first():
    aia = LayerSpec("AIA", 193.0, "AIA")
    c3 = LayerSpec("LASCO", "C3", "C3")
    cor2 = LayerSpec("SECCHI", ("STEREO_A", "COR2", None), "COR2")
    ordered = [spec.label for spec, _ in order_layers([(aia, None), (c3, None), (cor2, None)])]
    # C3 (30 Rsun) then COR2 (15) then the disk imager on top.
    assert ordered == ["C3", "COR2", "AIA"]


def _base_spec():
    return LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear")


def _aia_spec(**kw):
    return LayerSpec("AIA", 193.0, "AIA", colormap="sdoaia193", scale="linear", **kw)


def test_composite_frame_returns_uint8_rgb():
    out = composite_frame(
        coronal_falloff(), _base_spec(), [(_aia_spec(), bright_disk())],
        center=CENTER, rsun_px=RSUN_PX,
    )
    assert out.dtype == np.uint8
    assert out.shape == (*SHAPE, 3)
    assert out.min() >= 0 and out.max() <= 255


def test_composite_only_changes_pixels_inside_the_layer_field_of_view():
    base = coronal_falloff()
    solo = composite_frame(base, _base_spec(), [], center=CENTER, rsun_px=RSUN_PX)
    with_layer = composite_frame(
        base, _base_spec(), [(_aia_spec(), bright_disk())], center=CENTER, rsun_px=RSUN_PX
    )
    changed = np.any(with_layer.astype(int) != solo.astype(int), axis=2)
    radii = radial_grid()[changed] / RSUN_PX
    # AIA's nominal outer edge is 1.28 Rsun, plus half a feather width.
    assert radii.max() <= 1.28 + 0.15
    # The C2 annulus well outside the disk is untouched.
    assert np.array_equal(with_layer[50, 50 + 40], solo[50, 50 + 40])


def test_zero_opacity_layer_is_a_no_op():
    base = coronal_falloff()
    solo = composite_frame(base, _base_spec(), [], center=CENTER, rsun_px=RSUN_PX)
    faint = composite_frame(
        base, _base_spec(), [(_aia_spec(opacity=0.0), bright_disk())],
        center=CENTER, rsun_px=RSUN_PX,
    )
    assert np.array_equal(faint, solo)


def test_disabled_layer_is_skipped():
    base = coronal_falloff()
    solo = composite_frame(base, _base_spec(), [], center=CENTER, rsun_px=RSUN_PX)
    off = composite_frame(
        base, _base_spec(), [(_aia_spec(enabled=False), bright_disk())],
        center=CENTER, rsun_px=RSUN_PX,
    )
    assert np.array_equal(off, solo)


def test_none_layer_data_is_skipped():
    base = coronal_falloff()
    solo = composite_frame(base, _base_spec(), [], center=CENTER, rsun_px=RSUN_PX)
    out = composite_frame(base, _base_spec(), [(_aia_spec(), None)], center=CENTER, rsun_px=RSUN_PX)
    assert np.array_equal(out, solo)


def test_shape_mismatch_is_skipped_not_raised():
    base = coronal_falloff()
    out = composite_frame(
        base, _base_spec(), [(_aia_spec(), np.ones((7, 7)))], center=CENTER, rsun_px=RSUN_PX
    )
    assert out.shape == (*SHAPE, 3)


def test_all_nan_layer_leaves_the_base_untouched():
    base = coronal_falloff()
    solo = composite_frame(base, _base_spec(), [], center=CENTER, rsun_px=RSUN_PX)
    out = composite_frame(
        base, _base_spec(), [(_aia_spec(), np.full(SHAPE, np.nan))],
        center=CENTER, rsun_px=RSUN_PX,
    )
    assert np.array_equal(out, solo)


def test_opaque_layer_replaces_the_base_inside_its_annulus():
    """A fully opaque layer wins where its mask is 1 — the C2-in-C3 case."""
    inner = LayerSpec("LASCO", "C2", "C2", colormap="sdoaia193", scale="linear",
                      inner_rsun=2.2, outer_rsun=4.5)
    data = coronal_falloff()
    out = composite_frame(np.zeros(SHAPE), _base_spec(), [(inner, data)],
                          center=CENTER, rsun_px=RSUN_PX)
    alpha = layer_alpha(data, inner, center=CENTER, rsun_px=RSUN_PX)
    expected = np.clip(layer_rgb(data, inner, visible=alpha > 0) * 255.0, 0, 255).astype(np.uint8)
    # 3.5 Rsun sits mid-annulus where alpha is exactly 1.
    assert alpha[50, 50 + 35] == pytest.approx(1.0)
    assert np.array_equal(out[50, 50 + 35], expected[50, 50 + 35])


def test_upper_layer_wins_where_two_layers_overlap():
    wide = LayerSpec("LASCO", "C3", "C3", colormap="soholasco3", scale="linear",
                     inner_rsun=0.0, outer_rsun=8.0)
    narrow = LayerSpec("AIA", 193.0, "AIA", colormap="sdoaia193", scale="linear",
                       inner_rsun=0.0, outer_rsun=8.0)
    a, b = coronal_falloff(), bright_disk()
    out = composite_frame(np.zeros(SHAPE), _base_spec(), [(wide, a), (narrow, b)],
                          center=CENTER, rsun_px=RSUN_PX)
    # Equal fields of view: order_layers is stable, so the later layer is on top.
    alpha = layer_alpha(b, narrow, center=CENTER, rsun_px=RSUN_PX)
    expected = np.clip(layer_rgb(b, narrow, visible=alpha > 0) * 255.0, 0, 255).astype(np.uint8)
    assert np.array_equal(out[50, 50 + 20], expected[50, 50 + 20])


def test_layer_rgb_stretches_over_the_visible_band_only():
    """Hidden pixels must not steal the layer's colour range.

    A reprojected coronagraph layer still carries its own occulter and vignetted
    rim. Letting those set the percentile clip spends the colour range on pixels
    the blend discards and flattens the annulus that is actually shown.
    """
    data = coronal_falloff()
    # A bright rim well outside the annulus, as an over-exposed edge would be.
    radius = radial_grid() / RSUN_PX
    data[radius > 4.6] = 5.0e5
    spec = LayerSpec("LASCO", "C2", "C2", colormap="soholasco2", scale="linear",
                     inner_rsun=2.2, outer_rsun=4.5)
    band = layer_alpha(data, spec, center=CENTER, rsun_px=RSUN_PX) > 0

    naive = layer_rgb(data, spec)
    masked = layer_rgb(data, spec, visible=band)
    levels = lambda img: len(np.unique((img[band] * 255).astype(np.uint8)))
    assert levels(masked) > levels(naive)


def test_layer_rgb_ignores_a_mask_that_does_not_apply():
    data = coronal_falloff()
    spec = LayerSpec("LASCO", "C2", "C2", scale="linear")
    plain = layer_rgb(data, spec)
    assert np.array_equal(layer_rgb(data, spec, visible=np.zeros((3, 3), bool)), plain)
    assert np.array_equal(layer_rgb(data, spec, visible=np.zeros(SHAPE, bool)), plain)


# --------------------------------------------------------------------------- #
# Time matching
# --------------------------------------------------------------------------- #
T0 = datetime(2012, 7, 12, 16, 0)


def test_match_layer_frames_picks_the_nearest():
    base = [T0 + timedelta(minutes=12 * i) for i in range(4)]      # 0, 12, 24, 36
    layer = [T0 + timedelta(minutes=5 * i) for i in range(10)]     # every 5 min
    assert match_layer_frames(base, layer) == [0, 2, 5, 7]


def test_match_layer_frames_honours_the_tolerance():
    base = [T0 + timedelta(minutes=12 * i) for i in range(4)]
    layer = [T0 + timedelta(minutes=5 * i) for i in range(10)]
    # 12 min is 2 min from the nearest layer frame -> dropped at a 60 s tolerance.
    assert match_layer_frames(base, layer, max_gap_seconds=60) == [0, None, 5, 7]


def test_match_layer_frames_with_a_slow_layer_repeats_the_same_frame():
    base = [T0 + timedelta(minutes=5 * i) for i in range(4)]
    layer = [T0, T0 + timedelta(hours=1)]
    assert match_layer_frames(base, layer) == [0, 0, 0, 0]


def test_match_layer_frames_handles_missing_times():
    layer = [T0, None, T0 + timedelta(minutes=30)]
    assert match_layer_frames([None, T0], layer) == [None, 0]


def test_match_layer_frames_with_no_layer_times():
    assert match_layer_frames([T0, T0], []) == [None, None]
    assert match_layer_frames([T0, T0], [None, None]) == [None, None]


# --------------------------------------------------------------------------- #
# Cache token and labelling
# --------------------------------------------------------------------------- #
def test_layer_config_hash_is_stable_and_sensitive():
    a = LayerSpec("AIA", 193.0, "AIA")
    b = LayerSpec("LASCO", "C3", "C3")
    assert layer_config_hash([a, b]) == layer_config_hash([a, b])
    assert layer_config_hash([a, b]) != layer_config_hash([b, a])
    assert layer_config_hash([a]) != layer_config_hash([b])
    assert layer_config_hash([a]) != layer_config_hash([a], extra="running")


@pytest.mark.parametrize("field, value", [
    ("colormap", "gray"), ("scale", "linear"), ("clip_low", 5.0),
    ("clip_high", 90.0), ("opacity", 0.5), ("inner_rsun", 3.0),
    ("outer_rsun", 12.0), ("feather_rsun", 0.4), ("screen", "planar"),
    ("enabled", False),
])
def test_layer_config_hash_changes_with_every_style_field(field, value):
    from dataclasses import replace

    base = LayerSpec("AIA", 193.0, "AIA")
    assert layer_config_hash([base]) != layer_config_hash([replace(base, **{field: value})])


def test_composite_nickname_lists_enabled_layers_only():
    aia = LayerSpec("AIA", 193.0, "AIA 193")
    off = LayerSpec("SECCHI", ("STEREO_A", "COR2", None), "COR2", enabled=False)
    assert composite_nickname("LASCO C2", [aia, off]) == "LASCO C2 + AIA 193"


# --------------------------------------------------------------------------- #
# Midtone stretch (gamma)
#
# Measured on a real reprojected STEREO/EUVI frame: the median pixel sits at 13%
# of its own 1-99.5 percentile range, so a straight mapping paints the disk
# almost black. gamma 0.5 lifts that median to 36%.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("instrument, value, expected", [
    ("AIA", 193.0, 0.5),
    ("SECCHI", ("STEREO_A", "EUVI", 195.0), 0.5),
    ("SECCHI", ("STEREO_A", "COR2", None), 0.5),
    ("LASCO", "C2", 0.5),
    ("SUVI", 195.0, 0.5),
    ("HMI", "magnetogram", 1.0),   # signed data wants a linear ramp
])
def test_default_gamma_per_instrument(instrument, value, expected):
    assert default_gamma_for(instrument, value) == expected


def test_gamma_brightens_a_bottom_heavy_layer():
    """The real complaint: a correctly-placed overlay rendered near-black."""
    # A bottom-heavy distribution, like EUV data.
    rng = np.random.default_rng(0)
    data = 800.0 + rng.gamma(shape=1.5, scale=120.0, size=SHAPE)
    spec = LayerSpec("SECCHI", ("STEREO_A", "EUVI", 195.0), "EUVI",
                     colormap="euvi195", scale="log")

    flat = layer_rgb(data, replace_gamma(spec, 1.0))
    lifted = layer_rgb(data, replace_gamma(spec, 0.5))
    assert lifted.mean() > flat.mean() * 1.5
    # Still bounded and still monotone in the data.
    assert 0.0 <= lifted.min() and lifted.max() <= 1.0


def replace_gamma(spec, gamma):
    from dataclasses import replace

    return replace(spec, gamma=gamma)


def test_gamma_of_one_is_a_no_op():
    from src.Backend.solar_data_analysis import array_to_rgb_uint8

    data = coronal_falloff()
    plain = array_to_rgb_uint8(data, percentile_low=1.0, percentile_high=99.0,
                               colormap_name="soholasco2")
    same = array_to_rgb_uint8(data, percentile_low=1.0, percentile_high=99.0,
                              colormap_name="soholasco2", gamma=1.0)
    assert np.array_equal(plain, same)


def test_gamma_is_part_of_the_cache_token():
    base = LayerSpec("AIA", 193.0, "AIA")
    assert layer_config_hash([base]) != layer_config_hash([replace_gamma(base, 0.5)])
