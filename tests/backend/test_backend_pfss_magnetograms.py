"""
e-CALLISTO FITS Analyzer
Unit tests for synoptic magnetogram sourcing (src/backend/solar/pfss_magnetograms.py).

Nothing here touches the network: searches run against an injected fake Fido
client, and every map is synthesised locally. The normalisation pipeline is where
the real risk lives, so most of these tests are about it.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.backend.solar.pfss_magnetograms import (
    DEFAULT_PFSS_SHAPE,
    HMI_SYNOPTIC_SERIES,
    MAGNETOGRAM_SOURCES,
    SOURCE_ADAPT,
    SOURCE_DESCRIPTIONS,
    SOURCE_GONG,
    SOURCE_HMI,
    SOURCE_LABELS,
    SOURCE_LOCAL,
    MagnetogramError,
    MagnetogramRow,
    carrington_rotation,
    carrington_rotation_from_name,
    describe_magnetogram,
    load_adapt_realizations,
    load_magnetogram,
    magnetogram_cache_dir,
    search_magnetograms,
    source_label,
)

OBSTIME = "2020-09-01T13:00:00"


# --------------------------------------------------------------------------- #
# Synthetic maps
# --------------------------------------------------------------------------- #

def _cea_map(nlon: int = 360, nlat: int = 180, nan_rows: int = 0):
    """A full-Sun Carrington map in cylindrical equal area, as GONG/HMI supply."""
    pytest.importorskip("sunkit_magex")
    import sunpy.map
    from sunkit_magex.pfss import utils

    header = utils.carr_cea_wcs_header(OBSTIME, (nlon, nlat))
    sin_lat = np.linspace(-1, 1, nlat)[:, None]
    lon = np.linspace(0, 360, nlon, endpoint=False)[None, :]
    data = 10.0 * sin_lat + 8.0 * np.sin(np.radians(3 * lon)) * np.sqrt(1 - sin_lat**2)
    data = np.broadcast_to(data, (nlat, nlon)).copy()
    if nan_rows:
        # GONG leaves NaN over the poles, where a synoptic map is most foreshortened.
        data[:nan_rows, :] = np.nan
        data[-nan_rows:, :] = np.nan
    return sunpy.map.Map(data, header)


def _car_map(nlon: int = 180, nlat: int = 90):
    """A plate-carree synoptic map: uniform in latitude, as ADAPT supplies."""
    u = pytest.importorskip("astropy.units")
    import sunpy.map
    from astropy.constants import R_sun
    from astropy.coordinates import SkyCoord
    from sunpy.map.header_helper import make_fitswcs_header

    reference = SkyCoord(
        0 * u.deg, 0 * u.deg, R_sun,
        frame="heliographic_carrington", obstime=OBSTIME, observer="self",
    )
    header = make_fitswcs_header(
        (nlat, nlon), reference,
        scale=[360 / nlon, 180 / nlat] * u.deg / u.pix,
        reference_pixel=[(nlon / 2) - 0.5, (nlat / 2) - 0.5] * u.pix,
        projection_code="CAR",
    )
    lat = np.linspace(-90, 90, nlat)[:, None]
    data = 10.0 * np.sin(np.radians(lat))
    return sunpy.map.Map(np.broadcast_to(data, (nlat, nlon)).copy(), header)


@pytest.fixture
def written(tmp_path):
    """Save a synthetic map to disk and hand back the path."""
    def _write(map_obj, name="synoptic.fits"):
        path = tmp_path / name
        map_obj.save(str(path), overwrite=True)
        return path
    return _write


class _FakeFido:
    """Stands in for Fido: records queries, returns canned rows."""

    def __init__(self, rows=(), fetched=()):
        self._rows = list(rows)
        self._fetched = list(fetched)
        self.searches: list[tuple] = []
        self.fetches: list[dict] = []

    def search(self, *query):
        self.searches.append(query)
        return [_FakeTable(self._rows)]

    def fetch(self, _query, **kwargs):
        self.fetches.append(kwargs)
        return list(self._fetched)


class _FakeTable:
    def __init__(self, rows):
        self._rows = list(rows)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return _FakeTable(self._rows[item])
        return self._rows[item]


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def test_every_source_has_a_label_and_a_description():
    assert set(MAGNETOGRAM_SOURCES) == set(SOURCE_LABELS) == set(SOURCE_DESCRIPTIONS)
    for source in MAGNETOGRAM_SOURCES:
        assert SOURCE_LABELS[source].strip()
        assert SOURCE_DESCRIPTIONS[source].strip()


def test_source_label_falls_back_to_the_raw_key():
    assert source_label(SOURCE_GONG) == SOURCE_LABELS[SOURCE_GONG]
    assert source_label("mystery") == "mystery"


def test_carrington_rotation_is_plausible():
    # CR 2234 covers 2020-09; the numbering is monotonic since 1853.
    assert carrington_rotation(OBSTIME) == 2234
    assert carrington_rotation("not a time") is None


def test_cache_dir_is_created_under_the_given_root(tmp_path):
    created = magnetogram_cache_dir(tmp_path)
    assert created.is_dir()
    assert created.parent == tmp_path


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def test_local_source_cannot_be_searched():
    with pytest.raises(MagnetogramError, match="file browser"):
        search_magnetograms(SOURCE_LOCAL, OBSTIME)


def test_unknown_source_is_rejected():
    pytest.importorskip("sunpy.net")
    with pytest.raises(MagnetogramError, match="Unknown magnetogram source"):
        search_magnetograms("nonsense", OBSTIME, fido_client=_FakeFido())


def test_hmi_can_be_searched_without_an_email():
    """A JSOC search is an anonymous metadata query.

    Verified against the live archive: availability can be browsed with no JSOC
    account at all. Only the export staged at fetch time needs a registered
    address, so demanding one here would block a user from even seeing whether
    the rotation they want exists.
    """
    pytest.importorskip("sunpy.net")
    fake = _FakeFido()
    search_magnetograms(SOURCE_HMI, OBSTIME, email="", fido_client=fake)

    rendered = " ".join(repr(part) for part in fake.searches[0])
    assert HMI_SYNOPTIC_SERIES in rendered
    assert "Notify" not in rendered, "no email was given, so none should be sent"


def test_hmi_fetch_without_an_email_is_refused_with_the_registration_link():
    from src.backend.solar.pfss_magnetograms import fetch_magnetogram

    row = MagnetogramRow(source=SOURCE_HMI, url="x.fits", query_result=object())
    with pytest.raises(MagnetogramError, match="register_email"):
        fetch_magnetogram(row, "/tmp", email="", fido_client=_FakeFido())


def test_gong_search_asks_for_synoptic_extent():
    """Without ExtentType the GONG client also returns non-synoptic products."""
    pytest.importorskip("sunpy.net")
    fake = _FakeFido()
    search_magnetograms(SOURCE_GONG, OBSTIME, fido_client=fake)

    assert len(fake.searches) == 1
    rendered = " ".join(repr(part) for part in fake.searches[0]).lower()
    assert "gong" in rendered
    assert "synoptic" in rendered


def test_hmi_search_uses_the_pole_filled_series_and_the_rotation():
    pytest.importorskip("sunpy.net")
    fake = _FakeFido()
    search_magnetograms(SOURCE_HMI, OBSTIME, email="someone@example.org", fido_client=fake)

    rendered = " ".join(repr(part) for part in fake.searches[0])
    assert HMI_SYNOPTIC_SERIES in rendered
    assert "2234" in rendered
    assert "someone@example.org" in rendered


def test_adapt_search_requests_the_carrington_longitude_frame():
    pytest.importorskip("sunpy.net")
    fake = _FakeFido()
    search_magnetograms(SOURCE_ADAPT, OBSTIME, fido_client=fake)
    rendered = " ".join(repr(part) for part in fake.searches[0]).lower()
    assert "adapt" in rendered


def test_search_rows_are_sorted_by_closeness_to_the_requested_time():
    pytest.importorskip("sunpy.net")
    rows = [
        {"Start Time": "2020-09-04T00:00:00", "url": "far.fits"},
        {"Start Time": "2020-09-01T12:00:00", "url": "near.fits"},
    ]
    found = search_magnetograms(SOURCE_GONG, OBSTIME, fido_client=_FakeFido(rows))
    assert [row.url for row in found] == ["near.fits", "far.fits"]


def test_search_failure_is_wrapped_with_context():
    pytest.importorskip("sunpy.net")

    class Broken(_FakeFido):
        def search(self, *query):
            raise RuntimeError("archive offline")

    with pytest.raises(MagnetogramError, match="archive offline"):
        search_magnetograms(SOURCE_GONG, OBSTIME, fido_client=Broken())


def test_adapt_rows_advertise_their_realizations():
    pytest.importorskip("sunpy.net")
    rows = [{"Start Time": OBSTIME, "url": "adapt.fts.gz"}]
    found = search_magnetograms(SOURCE_ADAPT, OBSTIME, fido_client=_FakeFido(rows))
    assert found[0].realizations == 12


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #

def test_local_row_returns_its_own_path(written):
    from src.backend.solar.pfss_magnetograms import fetch_magnetogram

    path = written(_cea_map(72, 36))
    row = MagnetogramRow(source=SOURCE_LOCAL, path=str(path))
    assert fetch_magnetogram(row, path.parent) == path


def test_missing_local_file_is_reported(tmp_path):
    from src.backend.solar.pfss_magnetograms import fetch_magnetogram

    row = MagnetogramRow(source=SOURCE_LOCAL, path=str(tmp_path / "absent.fits"))
    with pytest.raises(MagnetogramError, match="not found"):
        fetch_magnetogram(row, tmp_path)


def test_an_already_cached_file_is_not_downloaded_again(tmp_path):
    from src.backend.solar.pfss_magnetograms import fetch_magnetogram

    cache = magnetogram_cache_dir(tmp_path)
    (cache / "mrzqs.fits.gz").write_bytes(b"cached")
    fake = _FakeFido()
    row = MagnetogramRow(
        source=SOURCE_GONG, url="https://example.org/mrzqs.fits.gz", query_result=object()
    )

    result = fetch_magnetogram(row, tmp_path, fido_client=fake)
    assert result == cache / "mrzqs.fits.gz"
    assert fake.fetches == [], "a cache hit must not hit the network"


def test_a_download_returning_nothing_is_an_error(tmp_path):
    from src.backend.solar.pfss_magnetograms import fetch_magnetogram

    row = MagnetogramRow(source=SOURCE_GONG, url="https://example.org/x.fits", query_result=object())
    with pytest.raises(MagnetogramError, match="no files"):
        fetch_magnetogram(row, tmp_path, fido_client=_FakeFido(fetched=[]))


def test_a_row_without_a_query_handle_cannot_be_fetched(tmp_path):
    from src.backend.solar.pfss_magnetograms import fetch_magnetogram

    row = MagnetogramRow(source=SOURCE_GONG, url="https://example.org/x.fits")
    with pytest.raises(MagnetogramError, match="no query handle"):
        fetch_magnetogram(row, tmp_path, fido_client=_FakeFido())


# --------------------------------------------------------------------------- #
# Normalisation -- the part that actually breaks
# --------------------------------------------------------------------------- #

def test_a_clean_cea_map_passes_through_unchanged(written):
    path = written(_cea_map(360, 180))
    magnetogram, provenance = load_magnetogram(path, source=SOURCE_GONG, frame_time=OBSTIME)

    assert provenance["projection"] == "CEA"
    assert provenance["shape"] == (180, 360)
    assert provenance["non_finite_pixels"] == 0
    assert np.all(np.isfinite(magnetogram.data))


def test_polar_nan_gaps_are_filled_so_the_solver_accepts_the_map(written):
    """The single most important normalisation step.

    ``pfss.Input`` raises ``ValueError`` on any non-finite pixel, and real GONG
    synoptic maps carry NaN over both poles -- so without this the default
    magnetogram source would fail every time.
    """
    pytest.importorskip("sunkit_magex")
    from sunkit_magex import pfss

    raw = _cea_map(72, 36, nan_rows=3)
    assert np.any(~np.isfinite(raw.data))
    with pytest.raises(ValueError, match="finite"):
        pfss.Input(raw, 10, 2.5)

    magnetogram, provenance = load_magnetogram(
        written(raw, "gaps.fits"), source=SOURCE_GONG, frame_time=OBSTIME
    )
    assert provenance["non_finite_pixels"] > 0
    assert np.all(np.isfinite(magnetogram.data))
    pfss.Input(magnetogram, 10, 2.5)  # must not raise


def test_filled_gaps_add_no_net_flux(written):
    """Zero is the honest filler for an unmeasured pixel."""
    magnetogram, _ = load_magnetogram(
        written(_cea_map(72, 36, nan_rows=3), "gaps.fits"), source=SOURCE_GONG
    )
    filled = np.asarray(magnetogram.data)
    assert filled[0, :].tolist() == [0.0] * filled.shape[1]


def test_an_oversampled_map_is_resampled_to_the_pfss_grid(written):
    """HMI synoptic maps are 3600x1440; the solve does not benefit from that."""
    path = written(_cea_map(1080, 540), "big.fits")
    _magnetogram, provenance = load_magnetogram(path, source=SOURCE_HMI, frame_time=OBSTIME)

    assert provenance["original_shape"] == (540, 1080)
    assert provenance["shape"] == (DEFAULT_PFSS_SHAPE[1], DEFAULT_PFSS_SHAPE[0])


def test_a_small_map_is_left_alone(written):
    path = written(_cea_map(72, 36), "small.fits")
    _magnetogram, provenance = load_magnetogram(path, source=SOURCE_GONG)
    assert provenance["shape"] == (36, 72)
    assert "resampled_to" not in provenance


def test_a_plate_carree_map_is_reprojected_to_equal_area(written):
    """ADAPT is plate-carree; PFSS requires equal steps in sin(latitude)."""
    pytest.importorskip("sunkit_magex")
    from sunkit_magex.pfss import utils

    path = written(_car_map(180, 90), "car.fits")
    magnetogram, provenance = load_magnetogram(path, source=SOURCE_LOCAL, frame_time=OBSTIME)

    assert provenance["projection"] == "CAR"
    assert provenance["reprojected_to_cea"] is True
    assert utils.is_cea_map(magnetogram)
    assert utils.is_full_sun_synoptic_map(magnetogram)


def test_provenance_records_the_offset_from_the_frame(written):
    path = written(_cea_map(72, 36))
    _magnetogram, provenance = load_magnetogram(
        path, source=SOURCE_GONG, frame_time="2020-09-08T13:00:00"
    )
    assert provenance["frame_offset_hours"] == pytest.approx(168.0, abs=1.0)


def test_provenance_reports_net_flux_without_correcting_it(written):
    """pfss() excludes the monopole itself, so this is a diagnostic only."""
    path = written(_cea_map(72, 36))
    magnetogram, provenance = load_magnetogram(path, source=SOURCE_GONG)

    assert "net_flux_ratio" in provenance
    assert provenance["net_flux_mean"] == pytest.approx(float(np.nanmean(magnetogram.data)))


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(MagnetogramError, match="not found"):
        load_magnetogram(tmp_path / "absent.fits")


# --------------------------------------------------------------------------- #
# ADAPT realizations
# --------------------------------------------------------------------------- #

@pytest.fixture
def adapt_file(tmp_path):
    """An ADAPT-shaped file: 12 realizations stacked on a third axis."""
    from astropy.io import fits

    base = _cea_map(72, 36)
    cube = np.stack([np.asarray(base.data) * (1.0 + 0.05 * index) for index in range(12)])
    path = tmp_path / "adapt_synthetic.fts"
    fits.PrimaryHDU(cube, header=fits.Header(dict(base.meta))).writeto(str(path), overwrite=True)
    return path


def test_all_twelve_realizations_are_split_out(adapt_file):
    """sunpy.map.Map cannot read the 3-D HDU directly, so they are paired by hand."""
    assert len(load_adapt_realizations(adapt_file)) == 12


def test_the_requested_realization_is_the_one_returned(adapt_file):
    peaks = []
    for index in (0, 5, 11):
        magnetogram, provenance = load_magnetogram(
            adapt_file, source=SOURCE_ADAPT, realization=index
        )
        assert provenance["realization"] == index
        assert provenance["realizations"] == 12
        peaks.append(float(np.nanmax(magnetogram.data)))
    assert peaks == sorted(peaks), "realizations were built with increasing amplitude"


def test_an_out_of_range_realization_is_clamped(adapt_file):
    _magnetogram, provenance = load_magnetogram(
        adapt_file, source=SOURCE_ADAPT, realization=99
    )
    assert provenance["realization"] == 11


def test_a_two_dimensional_file_still_reads_as_one_realization(written):
    assert len(load_adapt_realizations(written(_cea_map(72, 36)))) == 1


# --------------------------------------------------------------------------- #
# The sidebar hint
# --------------------------------------------------------------------------- #

def test_description_is_empty_without_provenance():
    assert "No magnetogram" in describe_magnetogram({})


def test_description_leads_with_the_source_and_carries_the_staleness(written):
    path = written(_cea_map(72, 36))
    _magnetogram, provenance = load_magnetogram(
        path, source=SOURCE_GONG, frame_time="2020-09-11T13:00:00"
    )
    text = describe_magnetogram(provenance)

    assert text.startswith(SOURCE_LABELS[SOURCE_GONG])
    assert "CR 2234" in text
    assert "d from frame" in text, "the age of the map is the number that matters"


def test_description_surfaces_filled_gaps(written):
    path = written(_cea_map(72, 36, nan_rows=3), "gaps.fits")
    _magnetogram, provenance = load_magnetogram(path, source=SOURCE_GONG)
    assert "zero-filled" in describe_magnetogram(provenance)


# --------------------------------------------------------------------------- #
# Carrington rotation from a filename
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "filename,expected",
    [
        # GONG results carry no CAR_ROT column, but the name encodes it: the
        # c2272 here is the rotation and _281 the central-meridian longitude.
        ("mrzqs230615t1204c2272_281.fits.gz", 2272),
        ("mrzqs200901t1304c2234_022.fits.gz", 2234),
        ("", None),
        ("nothing.fits", None),
        # Out of the plausible numbering range: a coincidental digit run, not a
        # rotation. Carrington numbering passed 1600 in 1975.
        ("random_c9999_file.fits", None),
        ("old_c0042_file.fits", None),
    ],
)
def test_carrington_rotation_is_parsed_from_gong_filenames(filename, expected):
    assert carrington_rotation_from_name(filename) == expected


def test_search_rows_recover_the_rotation_the_archive_omitted():
    pytest.importorskip("sunpy.net")
    rows = [{"Start Time": OBSTIME, "url": "/mrzqs200901/mrzqs200901t1304c2234_022.fits.gz"}]
    found = search_magnetograms(SOURCE_GONG, OBSTIME, fido_client=_FakeFido(rows))
    assert found[0].carrington_rotation == 2234
    assert "CR 2234" in found[0].label


# --------------------------------------------------------------------------- #
# Source-specific quirks found against the live archives
# --------------------------------------------------------------------------- #

def test_adapt_bypasses_fido_so_older_dates_are_not_silently_empty():
    """sunpy's ADAPTClient picks a filename pattern by date and the pre-2024
    one expects a letter where the re-versioned NSO archive has a digit, so a
    plain Fido search returns zero rows for any date before 2024-09-28 without
    raising. _search_adapt forces the working pattern."""
    import inspect

    from src.backend.solar import pfss_magnetograms as module

    source = inspect.getsource(module._search_adapt)
    assert "adapt_use_new_pattern=True" in source
    # Must degrade to the ordinary path if that internal keyword ever goes away.
    assert "except Exception" in source


def test_an_injected_client_still_drives_the_adapt_path():
    """The bypass must not defeat the test seam."""
    pytest.importorskip("sunpy.net")
    fake = _FakeFido([{"Start Time": OBSTIME, "url": "adapt.fts.gz"}])
    rows = search_magnetograms(SOURCE_ADAPT, OBSTIME, fido_client=fake)
    assert fake.searches, "an injected client must be used, not bypassed"
    assert rows and rows[0].realizations == 12


def test_the_rotation_falls_back_to_the_observation_time():
    """ADAPT filenames carry no rotation code, and a synoptic map is identified
    by its rotation first."""
    pytest.importorskip("sunpy.net")
    rows = search_magnetograms(
        SOURCE_ADAPT, OBSTIME,
        fido_client=_FakeFido([{"Start Time": OBSTIME, "url": "adapt40311_044012_x.fts.gz"}]),
    )
    assert rows[0].carrington_rotation == 2234


def test_a_row_with_no_usable_time_leads_with_its_rotation():
    """JSOC reports this series' time columns as 'Invalid KeyLink'."""
    pytest.importorskip("sunpy.net")
    rows = search_magnetograms(
        SOURCE_HMI, OBSTIME, email="a@b.c",
        fido_client=_FakeFido([{"CAR_ROT": 2272}]),
    )
    assert rows[0].label.endswith("CR 2272")
    assert "unknown time" not in rows[0].label


def test_reprojection_preserves_the_field_unit(written):
    """car_to_cea builds a fresh header and drops BUNIT, which would leave every
    reprojected map's field strength unlabelled in the provenance and on the
    diagnostics colorbar."""
    pytest.importorskip("sunkit_magex")

    car = _car_map(180, 90)
    car.meta["bunit"] = "Gauss"
    _magnetogram, provenance = load_magnetogram(
        written(car, "car_units.fits"), source=SOURCE_LOCAL
    )
    assert provenance["reprojected_to_cea"] is True
    assert provenance["bunit"] == "Gauss"


def test_a_real_hmi_synoptic_header_is_normalised(tmp_path):
    """The HMI synoptic header really is non-compliant, in specific ways.

    Taken from a live JSOC export of hmi.synoptic_mr_polfil_720s CR 2272:
    ``CUNIT2 = 'Sine Latitude'`` is not a parseable unit, and ``CRVAL1`` is an
    accumulated Carrington longitude in the hundreds of thousands of degrees.
    Both are why pfss.utils.fix_hmi_meta exists, and why loading one of these
    without it fails rather than merely looking odd.
    """
    pytest.importorskip("sunkit_magex")
    import sunpy.map
    from astropy.io import fits

    # A miniature of the real thing: same header quirks, 1/10th the size.
    data = np.tile(np.linspace(-20, 20, 144)[:, None], (1, 360)).astype(float)
    header = fits.Header()
    header["NAXIS"] = 2
    header["CTYPE1"] = "CRLN-CEA"
    header["CTYPE2"] = "CRLT-CEA"
    header["CUNIT1"] = "degree"
    header["CUNIT2"] = "Sine Latitude"
    header["CDELT1"] = -1.0
    header["CDELT2"] = 2.0 / 144
    header["CRVAL1"] = 817740.0
    header["CRVAL2"] = 0.0
    header["CRPIX1"] = 180.0
    header["CRPIX2"] = 72.5
    header["CAR_ROT"] = 2272
    header["BUNIT"] = "Mx/cm^2"
    header["DATE-OBS"] = "2023-06-27T16:46:00"

    path = tmp_path / "hmi_synoptic.fits"
    fits.PrimaryHDU(data, header=header).writeto(str(path), overwrite=True)

    magnetogram, provenance = load_magnetogram(
        path, source=SOURCE_HMI, frame_time="2023-06-15T12:00:00"
    )
    assert provenance["hmi_meta_fixed"] is True
    assert provenance["carrington_rotation"] == 2272
    assert np.all(np.isfinite(magnetogram.data))

    # A synoptic map is dated at the end of its rotation, so it can sit well
    # over a week from the frame even when it is the correct rotation. Surfacing
    # that offset is the whole point of the provenance line.
    assert provenance["frame_offset_hours"] < 0
    assert abs(provenance["frame_offset_hours"]) > 24 * 7


def test_repairing_sine_latitude_also_rescales_the_latitude_spacing():
    """Relabelling CUNIT2 without rescaling CDELT2 is a silent scientific error.

    The header's CDELT2 is a spacing in sin(latitude); calling the axis degrees
    without the 180/pi correction (Thompson 2006, sec. 5.5) leaves the map with
    a latitude scale wrong by that factor, which still *looks* like a valid map.
    """
    from astropy.io import fits

    from src.backend.solar.pfss_magnetograms import _repair_sine_latitude_header

    header = fits.Header()
    header["CUNIT1"] = "Degree"
    header["CUNIT2"] = "Sine Latitude"
    header["CDELT1"] = -0.1
    header["CDELT2"] = 2.0 / 1440

    assert _repair_sine_latitude_header(header) is True
    assert header["CUNIT1"] == "deg"
    assert header["CUNIT2"] == "deg"
    assert header["CDELT1"] == pytest.approx(0.1), "CDELT1 must become positive"
    assert header["CDELT2"] == pytest.approx(np.degrees(2.0 / 1440))

    # A full-Sun CEA map spans 180/pi * 2 degrees in the scaled latitude axis.
    assert 1440 * header["CDELT2"] == pytest.approx(np.degrees(2.0))


def test_a_header_with_nothing_to_repair_is_left_alone():
    from astropy.io import fits

    from src.backend.solar.pfss_magnetograms import _repair_sine_latitude_header

    header = fits.Header()
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    header["CDELT1"] = 1.0
    header["CDELT2"] = 1.0
    assert _repair_sine_latitude_header(header) is False
    assert header["CDELT2"] == 1.0


def test_a_repaired_hmi_map_is_a_solvable_pfss_input(tmp_path):
    """The real proof that the rescale is right: the solver accepts it."""
    pytest.importorskip("sunkit_magex")
    from astropy.io import fits
    from sunkit_magex import pfss
    from sunkit_magex.pfss import utils

    nlat, nlon = 144, 360
    data = np.tile(np.linspace(-20, 20, nlat)[:, None], (1, nlon)).astype(float)
    header = fits.Header()
    header["CTYPE1"] = "CRLN-CEA"
    header["CTYPE2"] = "CRLT-CEA"
    header["CUNIT1"] = "Degree"
    header["CUNIT2"] = "Sine Latitude"
    header["CDELT1"] = -360.0 / nlon
    header["CDELT2"] = 2.0 / nlat
    header["CRVAL1"] = 817740.0
    header["CRVAL2"] = 0.0
    header["CRPIX1"] = nlon / 2 + 0.5
    header["CRPIX2"] = nlat / 2 + 0.5
    header["CAR_ROT"] = 2272
    header["BUNIT"] = "Mx/cm^2"
    header["DATE-OBS"] = "2023-06-27T16:46:00"

    path = tmp_path / "hmi_like.fits"
    fits.PrimaryHDU(data, header=header).writeto(str(path), overwrite=True)

    magnetogram, provenance = load_magnetogram(path, source=SOURCE_HMI, target_shape=None)
    assert provenance.get("header_units_repaired") is True
    assert utils.is_cea_map(magnetogram)
    assert utils.is_full_sun_synoptic_map(magnetogram)
    pfss.Input(magnetogram, 10, 2.5)   # must not raise
