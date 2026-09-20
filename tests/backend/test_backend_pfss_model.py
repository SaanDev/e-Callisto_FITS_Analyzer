"""
e-CALLISTO FITS Analyzer
Unit tests for the PFSS coronal field model (src/backend/solar/pfss_model.py).

The module is split so that the expensive half (solving and tracing, which needs
the optional ``sunkit_magex`` stack) is separable from the projection half, which
works on plain arrays. Tests are grouped the same way: everything below
``TestWithSolver`` runs with no optional dependency installed at all.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.backend.solar.pfss_model import (
    CLASS_CLOSED,
    CLASS_OPEN_NEGATIVE,
    CLASS_OPEN_POSITIVE,
    DEFAULT_NRHO,
    DEFAULT_RSS,
    MIN_SEED_RADIUS_RSUN,
    PFSS_INSTALL_HINT,
    SEED_MODE_LABELS,
    SEED_MODES,
    PfssOverlay,
    PfssParameters,
    PfssUnavailableError,
    TracedField,
    _format_pfss_dependency_error,
    field_lines_arcsec,
)

OBSTIME = "2020-09-01T13:00:00"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def disk_map():
    """A synthetic Earth-view full-disk AIA map (no network)."""
    u = pytest.importorskip("astropy.units")
    pytest.importorskip("sunpy.map")
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import get_earth
    from sunpy.map.header_helper import make_fitswcs_header

    data = np.zeros((256, 256))
    ref = SkyCoord(
        0 * u.arcsec, 0 * u.arcsec,
        obstime=OBSTIME, observer=get_earth(OBSTIME), frame="helioprojective",
    )
    # An AIA-classed map needs a wavelength: sunpy's AIAMap source reads it on
    # construction to pick a colormap and raises without it.
    header = make_fitswcs_header(
        data, ref, scale=[9.6, 9.6] * u.arcsec / u.pix,
        instrument="AIA", wavelength=193 * u.angstrom,
    )
    return sunpy.map.Map(data, header)


@pytest.fixture(scope="module")
def sub_observer_lon(disk_map):
    """Carrington longitude facing the observer, so tests can aim at a hemisphere."""
    u = pytest.importorskip("astropy.units")
    from sunpy.coordinates import HeliographicCarrington

    carrington = HeliographicCarrington(obstime=OBSTIME, observer="self")
    return float(disk_map.observer_coordinate.transform_to(carrington).lon.to_value(u.deg))


def _radial_line(lon_deg: float, n: int = 12, r0: float = 1.0, r1: float = 2.5):
    """A straight radial 'field line' at one longitude, on the equator."""
    radius = np.linspace(r0, r1, n)
    return np.full(n, float(lon_deg)), np.zeros(n), radius


def _traced(*lines, polarity=None, open_mask=None, obstime: str = OBSTIME) -> TracedField:
    """Assemble a TracedField from (lon, lat, r) triples."""
    lons, lats, radii, offsets = [], [], [], [0]
    for lon, lat, rad in lines:
        lons.append(lon)
        lats.append(lat)
        radii.append(rad)
        offsets.append(offsets[-1] + len(lon))
    count = len(lines)
    return TracedField(
        lon_deg=np.concatenate(lons),
        lat_deg=np.concatenate(lats),
        radius_rsun=np.concatenate(radii),
        offsets=np.asarray(offsets, dtype=np.int64),
        polarity=np.asarray(polarity if polarity is not None else [1.0] * count, dtype=float),
        open_mask=np.asarray(open_mask if open_mask is not None else [True] * count, dtype=bool),
        obstime=obstime,
    )


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #

def test_default_parameters_are_the_conventional_choices():
    params = PfssParameters()
    assert params.nrho == DEFAULT_NRHO == 35
    assert params.rss == DEFAULT_RSS == 2.5
    assert params.is_physical()


def test_seed_modes_all_have_labels():
    assert set(SEED_MODES) == set(SEED_MODE_LABELS)
    assert all(SEED_MODE_LABELS[mode].strip() for mode in SEED_MODES)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"nrho": 0},
        {"rss": 1.0},
        {"seed_density": 0},
        {"seed_radius_rsun": 0.5},
        {"step_size": 0.0},
    ],
)
def test_unphysical_parameters_are_rejected(kwargs):
    assert not PfssParameters(**kwargs).is_physical()


def test_normalized_clamps_into_the_solvable_range():
    params = PfssParameters(nrho=1, rss=0.5, seed_density=0, seed_radius_rsun=0.2, step_size=-1.0)
    fixed = params.normalized()
    assert fixed.is_physical()
    assert fixed.nrho >= 4
    assert fixed.rss > 1.0
    assert fixed.seed_radius_rsun >= MIN_SEED_RADIUS_RSUN


def test_normalized_keeps_the_seed_radius_inside_the_source_surface():
    # A seed above the source surface is outside the solution domain entirely.
    fixed = PfssParameters(rss=1.5, seed_radius_rsun=9.0).normalized()
    assert fixed.seed_radius_rsun < fixed.rss


def test_cache_key_is_stable_and_parameter_sensitive():
    base = PfssParameters()
    assert base.cache_key() == PfssParameters().cache_key()
    assert base.cache_key() != PfssParameters(nrho=36).cache_key()
    assert base.cache_key() != PfssParameters(rss=2.6).cache_key()
    assert base.cache_key() != PfssParameters(seed_density=25).cache_key()


# --------------------------------------------------------------------------- #
# Optional-dependency gating
# --------------------------------------------------------------------------- #

def test_missing_package_gets_the_install_hint():
    message = _format_pfss_dependency_error(ModuleNotFoundError("x", name="sunkit_magex"))
    assert PFSS_INSTALL_HINT in message


def test_missing_compiled_dependency_is_named():
    """streamtracer failing to bundle is the likely packaged-build failure."""
    message = _format_pfss_dependency_error(ModuleNotFoundError("x", name="streamtracer"))
    assert "streamtracer" in message
    assert PFSS_INSTALL_HINT in message


def test_broken_install_does_not_advise_pip():
    """Installed-but-broken means a bundling fault, where pip install is wrong."""
    message = _format_pfss_dependency_error(ImportError("bad symbol"))
    assert PFSS_INSTALL_HINT not in message
    assert "bad symbol" in message


def test_import_error_is_a_pfss_unavailable_error(monkeypatch):
    import src.backend.solar.pfss_model as module

    monkeypatch.setattr(
        module, "import_pfss",
        lambda: (_ for _ in ()).throw(PfssUnavailableError("nope")),
    )
    assert module.pfss_available() is False


# --------------------------------------------------------------------------- #
# Projection: no sunkit_magex required
# --------------------------------------------------------------------------- #

def test_empty_traced_field_projects_to_an_empty_overlay(disk_map):
    empty = TracedField(
        lon_deg=np.zeros(0), lat_deg=np.zeros(0), radius_rsun=np.zeros(0),
        offsets=np.zeros(1, dtype=np.int64), polarity=np.zeros(0), open_mask=np.zeros(0, bool),
    )
    assert field_lines_arcsec(empty, disk_map).is_empty()


def test_overlay_is_empty_without_a_coordinate_frame(disk_map, sub_observer_lon):
    """A cropped frame becomes AiaArrayMap, which can lack a WCS entirely.

    The graticule degrades silently for these rather than raising, and so must
    the PFSS overlay.
    """
    class NoCoordinateFrame:
        data = np.zeros((8, 8))

    traced = _traced(_radial_line(sub_observer_lon))
    assert field_lines_arcsec(traced, NoCoordinateFrame()).is_empty()


def test_near_side_line_projects_onto_the_disk(disk_map, sub_observer_lon):
    u = pytest.importorskip("astropy.units")
    traced = _traced(_radial_line(sub_observer_lon, r0=1.0, r1=1.0, n=3))
    overlay = field_lines_arcsec(traced, disk_map)

    x, _y = overlay.open_positive
    finite = x[np.isfinite(x)]
    assert finite.size > 0
    # A point at the sub-observer longitude sits at disk centre.
    assert np.all(np.abs(finite) < disk_map.rsun_obs.to_value(u.arcsec))


def test_far_side_line_is_hidden_by_default(disk_map, sub_observer_lon):
    """The whole point of near-side masking.

    ``Helioprojective.is_visible`` is an occultation test, not a front/back test:
    a far-side point above the surface projects *beyond* the limb, where nothing
    blocks the sight line, so it reports visible. Drawing it puts a phantom arc
    outside the limb with no footpoint.
    """
    traced = _traced(_radial_line(sub_observer_lon + 100.0))

    hidden = field_lines_arcsec(traced, disk_map, near_side_only=True)
    shown = field_lines_arcsec(traced, disk_map, near_side_only=False)

    assert hidden.open_positive is None, "far-side line must be dropped by default"
    assert shown.open_positive is not None, "clearing the mask must show it again"


def test_near_side_masking_never_removes_near_side_vertices(disk_map, sub_observer_lon):
    traced = _traced(_radial_line(sub_observer_lon))
    masked = field_lines_arcsec(traced, disk_map, near_side_only=True).open_positive
    unmasked = field_lines_arcsec(traced, disk_map, near_side_only=False).open_positive
    assert np.isfinite(masked[0]).sum() == np.isfinite(unmasked[0]).sum()


def test_lines_are_classified_by_openness_and_polarity(disk_map, sub_observer_lon):
    traced = _traced(
        _radial_line(sub_observer_lon - 10.0, r0=1.0, r1=1.2),
        _radial_line(sub_observer_lon, r0=1.0, r1=1.2),
        _radial_line(sub_observer_lon + 10.0, r0=1.0, r1=1.2),
        polarity=[1.0, -1.0, 0.0],
        open_mask=[True, True, False],
    )
    assert traced.class_of(0) == CLASS_OPEN_POSITIVE
    assert traced.class_of(1) == CLASS_OPEN_NEGATIVE
    assert traced.class_of(2) == CLASS_CLOSED

    overlay = field_lines_arcsec(traced, disk_map)
    assert overlay.open_positive is not None
    assert overlay.open_negative is not None
    assert overlay.closed is not None


def test_negative_zero_polarity_counts_as_closed(disk_map, sub_observer_lon):
    """The solver returns polarity as a float and yields -0.0 for closed lines."""
    traced = _traced(
        _radial_line(sub_observer_lon, r0=1.0, r1=1.1),
        polarity=[-0.0], open_mask=[False],
    )
    assert traced.class_of(0) == CLASS_CLOSED


def test_separate_lines_are_nan_separated(disk_map, sub_observer_lon):
    """One curve item per class needs a NaN break between consecutive lines."""
    traced = _traced(
        _radial_line(sub_observer_lon - 5.0, n=4, r0=1.0, r1=1.1),
        _radial_line(sub_observer_lon + 5.0, n=4, r0=1.0, r1=1.1),
        polarity=[1.0, 1.0], open_mask=[True, True],
    )
    x, y = field_lines_arcsec(traced, disk_map).open_positive
    assert x.size == y.size
    # 4 + gap + 4
    assert x.size == 9
    assert np.isnan(x[4]) and np.isnan(y[4])


def test_visibility_toggles_suppress_only_their_own_class(disk_map, sub_observer_lon):
    traced = _traced(
        _radial_line(sub_observer_lon - 5.0, r0=1.0, r1=1.1),
        _radial_line(sub_observer_lon + 5.0, r0=1.0, r1=1.1),
        polarity=[1.0, 0.0], open_mask=[True, False],
    )
    open_only = field_lines_arcsec(traced, disk_map, show_closed=False)
    assert open_only.open_positive is not None
    assert open_only.closed is None

    closed_only = field_lines_arcsec(traced, disk_map, show_open=False)
    assert closed_only.open_positive is None
    assert closed_only.closed is not None

    nothing = field_lines_arcsec(traced, disk_map, show_open=False, show_closed=False)
    assert nothing.is_empty()


def test_projection_agrees_with_a_direct_skycoord_oracle(disk_map, sub_observer_lon):
    """Cross-check against the plain astropy path, as the GCS model does.

    ``src.backend.solar.solar_grid`` uses exactly this recipe for the graticule;
    agreeing with it is what proves the overlay lands on the same pixels as the
    coordinate grid drawn over the same image.
    """
    u = pytest.importorskip("astropy.units")
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import HeliographicCarrington

    lon, lat, radius = _radial_line(sub_observer_lon + 20.0, n=6, r0=1.0, r1=1.0)
    traced = _traced((lon, lat, radius))
    x, y = field_lines_arcsec(traced, disk_map, near_side_only=False).open_positive

    oracle = SkyCoord(
        lon * u.deg, lat * u.deg, radius * u.R_sun,
        frame=HeliographicCarrington(obstime=traced.obstime, observer="self"),
    ).transform_to(disk_map.coordinate_frame)

    np.testing.assert_allclose(x, oracle.Tx.to_value(u.arcsec), rtol=1e-8, atol=1e-6)
    np.testing.assert_allclose(y, oracle.Ty.to_value(u.arcsec), rtol=1e-8, atol=1e-6)


def test_overlay_dataclass_reports_emptiness():
    assert PfssOverlay().is_empty()
    assert not PfssOverlay(closed=(np.zeros(2), np.zeros(2))).is_empty()


# --------------------------------------------------------------------------- #
# Solving and tracing: needs the optional stack
# --------------------------------------------------------------------------- #

class TestWithSolver:
    """Exercised only where ``sunkit_magex`` is installed."""

    @pytest.fixture(scope="class")
    def solution(self):
        pytest.importorskip("sunkit_magex")
        u = pytest.importorskip("astropy.units")
        import sunpy.map
        from sunkit_magex.pfss import utils

        from src.backend.solar.pfss_model import solve_pfss

        nlon, nlat = 72, 36
        # carr_cea_wcs_header takes (nlon, nlat) -- NOT numpy order.
        header = utils.carr_cea_wcs_header(OBSTIME, (nlon, nlat))
        sin_lat = np.linspace(-1, 1, nlat)[:, None]
        lon = np.linspace(0, 360, nlon, endpoint=False)[None, :]
        data = 10.0 * sin_lat + 5.0 * np.sin(np.radians(2 * lon)) * np.sqrt(1 - sin_lat**2)
        magnetogram = sunpy.map.Map(np.broadcast_to(data, (nlat, nlon)).copy(), header)
        return solve_pfss(magnetogram, PfssParameters(nrho=20, seed_density=8))

    def test_solve_records_timing_and_provenance(self, solution):
        assert solution.solve_seconds >= 0.0
        assert solution.provenance["nrho"] == 20
        assert solution.coordinate_frame is not None

    def test_uniform_seeds_are_restricted_to_the_visible_hemisphere(self, solution, disk_map):
        from src.backend.solar.pfss_model import uniform_seeds

        everywhere = uniform_seeds(solution, density=8, frame=None)
        visible = uniform_seeds(solution, density=8, frame=disk_map)
        assert len(np.atleast_1d(visible)) < len(np.atleast_1d(everywhere))

    @pytest.mark.parametrize("mode", SEED_MODES)
    def test_every_seed_mode_builds_seeds(self, solution, disk_map, mode):
        from src.backend.solar.pfss_model import build_seeds

        extra = {}
        if mode == "click":
            extra["clicked_arcsec"] = [(100.0, 150.0), (-200.0, -100.0)]
        if mode == "roi":
            extra["roi_bounds_arcsec"] = (-400.0, -400.0, 400.0, 400.0)
        params = PfssParameters(seed_mode=mode, seed_density=8)
        seeds = build_seeds(solution, frame=disk_map, params=params, **extra)
        assert len(np.atleast_1d(seeds)) > 0

    def test_off_disk_clicks_are_dropped(self, solution, disk_map):
        from src.backend.solar.pfss_model import seeds_from_arcsec

        seeds = seeds_from_arcsec([(0.0, 0.0), (5000.0, 5000.0)], disk_map, solution)
        assert len(np.atleast_1d(seeds)) == 1

    def test_clicking_only_off_disk_raises(self, solution, disk_map):
        from src.backend.solar.pfss_model import seeds_from_arcsec

        with pytest.raises(ValueError):
            seeds_from_arcsec([(9000.0, 9000.0)], disk_map, solution)

    def test_trace_produces_a_projectable_traced_field(self, solution, disk_map):
        from src.backend.solar.pfss_model import build_seeds, trace_field_lines

        seeds = build_seeds(solution, frame=disk_map, params=PfssParameters(seed_density=8))
        traced = trace_field_lines(solution, seeds, with_boundaries=False)

        assert traced.line_count > 0
        assert traced.offsets[-1] == traced.lon_deg.size
        assert traced.polarity.size == traced.line_count
        assert traced.open_mask.size == traced.line_count
        assert np.all(traced.radius_rsun >= 0.99)
        assert not field_lines_arcsec(traced, disk_map).is_empty()

    def test_traced_field_survives_a_round_trip_through_plain_arrays(self, solution, disk_map):
        """The projection half must not depend on live solver objects."""
        from src.backend.solar.pfss_model import build_seeds, trace_field_lines

        seeds = build_seeds(solution, frame=disk_map, params=PfssParameters(seed_density=8))
        traced = trace_field_lines(solution, seeds, with_boundaries=False)

        rebuilt = TracedField(
            lon_deg=traced.lon_deg.copy(),
            lat_deg=traced.lat_deg.copy(),
            radius_rsun=traced.radius_rsun.copy(),
            offsets=traced.offsets.copy(),
            polarity=traced.polarity.copy(),
            open_mask=traced.open_mask.copy(),
            obstime=traced.obstime,
        )
        original = field_lines_arcsec(traced, disk_map).open_negative
        restored = field_lines_arcsec(rebuilt, disk_map).open_negative
        if original is None:
            pytest.skip("no open negative-polarity lines in this synthetic field")
        np.testing.assert_allclose(original[0], restored[0], equal_nan=True)

    def test_source_surface_products_and_stats(self, solution):
        from src.backend.solar.pfss_model import solution_stats, source_surface_products

        ss_br, pils = source_surface_products(solution)
        assert ss_br.data.ndim == 2
        assert isinstance(pils, list)

        stats = solution_stats(solution)
        assert stats["nrho"] == 20
        assert stats["rss"] == pytest.approx(2.5)
        assert "grid" in stats
