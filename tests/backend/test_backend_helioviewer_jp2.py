"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for src/backend/solar/helioviewer_jp2.py.

The traps these pin were each found in real Helioviewer files: a NUL byte after
the XML, LASCO JP2s pre-rotated to north up but still carrying the original
CROTA, LASCO headers with no observer, COR2 sequences alternating between 2048
and 1024 pixels, and sub-pixel pointing jitter between frames.
"""

from __future__ import annotations

import io
import struct
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sunpy.map")
pytest.importorskip("PIL")

from src.backend.solar import helioviewer_jp2 as hv


# --- Fixtures ---------------------------------------------------------------


def _xml(fits: dict, *, helioviewer: dict | None = None, trailing_nul: bool = True) -> bytes:
    cards = "\n".join(f"<{k}>{v}</{k}>" for k, v in fits.items())
    hv_section = ""
    if helioviewer is not None:
        hv_cards = "\n".join(f"<{k}>{v}</{k}>" for k, v in helioviewer.items())
        hv_section = f"\n<helioviewer>\n{hv_cards}\n</helioviewer>"
    text = f'<?xml version="1.0" encoding="UTF-8"?>\n<meta>\n<fits>\n{cards}\n</fits>{hv_section}\n</meta>\n'
    return text.encode() + (b"\x00" if trailing_nul else b"")


def make_jp2(pixels: np.ndarray, xml: bytes) -> bytes:
    """A real JPEG2000 file with an ``xml `` box spliced in before the codestream,
    the layout Helioviewer uses."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.asarray(pixels, dtype=np.uint8)).save(buffer, format="JPEG2000", irreversible=False)
    raw = buffer.getvalue()
    position = 0
    while position + 8 <= len(raw):
        length, kind = struct.unpack(">I4s", raw[position : position + 8])
        if kind == b"jp2c":
            box = struct.pack(">I4s", 8 + len(xml), b"xml ") + xml
            return raw[:position] + box + raw[position:]
        position += length
    raise AssertionError("no codestream box")


def lasco_fits(**overrides) -> dict:
    """A LASCO C2 quicklook header as Helioviewer ships it — warts included."""
    fits = {
        "TELESCOP": "SOHO",
        "INSTRUME": "LASCO",
        "DETECTOR": "C2",
        "NAXIS": 2,
        "NAXIS1": 64,
        "NAXIS2": 64,
        "DATE_OBS": "2012/07/12",  # date only …
        "TIME_OBS": "17:00:07.671",  # … the time lives here
        "EXPTIME": 25.0933,
        "CRPIX1": 32.5,
        "CRPIX2": 32.5,
        "CRVAL1": 0.0,
        "CRVAL2": 0.0,
        "CROTA": -173.53821,
        "CROTA1": -173.53821,
        "CROTA2": -173.538,
        "CTYPE1": "SOLAR-X",
        "CTYPE2": "SOLAR-Y",
        "CUNIT1": "ARCSEC",
        "CUNIT2": "ARCSEC",
        "CDELT1": 190.4,
        "CDELT2": 190.4,
        "RSUN": 0.0,  # not a radius
        "BUNIT": 0,
    }
    for key, value in overrides.items():
        if value is None:
            fits.pop(key, None)
        else:
            fits[key] = value
    return fits


HV_LASCO = {"HV_OBSERVATORY": "SOHO", "HV_INSTRUMENT": "LASCO", "HV_DETECTOR": "C2", "HV_QUICKLOOK": "TRUE"}


def sky_map(size: int, *, lon_obs: float = 0.0, crpix_shift=(0.0, 0.0), scale=None, offset=0.0, when=None):
    """A synthetic coronagraph map: bright ring on a zero background, like a JP2."""
    import astropy.units as u
    import sunpy.map
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import frames
    from sunpy.map.header_helper import make_fitswcs_header

    stamp = (when or datetime(2012, 7, 12, 17, 0, 0)).isoformat()
    observer = SkyCoord(lon_obs * u.deg, 0 * u.deg, 1.0 * u.AU, frame=frames.HeliographicStonyhurst, obstime=stamp)
    arcsec_per_px = scale if scale is not None else 40000.0 / size
    yy, xx = np.mgrid[0:size, 0:size]
    centre = (size - 1) / 2.0
    radius = np.hypot(xx - centre - crpix_shift[0], yy - centre - crpix_shift[1]) * arcsec_per_px / 960.0
    data = np.where((radius > 2.5) & (radius < 12.0), 100.0 / np.maximum(radius, 1e-6) + offset, 0.0).astype(np.float32)
    ref = SkyCoord(0 * u.arcsec, 0 * u.arcsec, obstime=stamp, observer=observer, frame="helioprojective")
    header = make_fitswcs_header(
        data,
        ref,
        reference_pixel=[centre + crpix_shift[0], centre + crpix_shift[1]] * u.pix,
        scale=[arcsec_per_px, arcsec_per_px] * u.arcsec / u.pix,
        instrument="SECCHI",
        detector="COR2",
        observatory="STEREO_A",  # real COR2 JP2s carry OBSRVTRY; sunpy's CORMap indexes it
    )
    header["rsun_ref"] = hv.RSUN_REF_M
    return sunpy.map.Map(data, header)


class _Resp:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code = status
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _Session:
    """Routes by endpoint and records calls; ``script`` items can be exceptions."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        endpoint = url.rsplit("/", 2)[-2]
        self.calls.append((endpoint, dict(params or {})))
        script = self.routes[endpoint]
        item = script.pop(0) if isinstance(script, list) else script
        if isinstance(item, BaseException):
            raise item
        return item


# --- Sources ----------------------------------------------------------------


def test_stereo_b_is_unavailable_after_it_was_lost():
    cor2b = hv.source_by_key("COR2-B")
    assert cor2b.available_on(date(2012, 7, 12))
    assert not cor2b.available_on(date(2015, 1, 1))


def test_the_default_triad_is_stereo_b_soho_stereo_a_whatever_the_date():
    """Fixed left to right; STEREO-B being lost in 2014 does not change the order."""
    assert [source.key for source in hv.default_viewpoints()] == ["COR2-B", "LASCO C2", "COR2-A"]
    assert list(hv.DEFAULT_VIEWPOINT_KEYS) == ["COR2-B", "LASCO C2", "COR2-A"]


def test_only_coronagraphs_are_offered_for_gcs():
    assert {source.detector for source in hv.GCS_JP2_SOURCES} == {"COR1", "COR2", "C2", "C3"}


# --- Search -------------------------------------------------------------------


def test_frame_times_come_from_one_getjpx_call():
    stamps = [1342108800 + 900 * k for k in (2, 0, 1)]  # out of order on purpose
    session = _Session({"getJPX": _Resp(payload={"frames": stamps, "uri": "jpip://x", "message": ""})})
    source = hv.source_by_key("COR2-A")
    times = hv.list_frame_times(
        source, datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 18), session=session
    )
    assert len(session.calls) == 1
    endpoint, params = session.calls[0]
    assert endpoint == "getJPX"
    assert params["verbose"] == params["linked"] == params["jpip"] == "true"
    assert params["sourceId"] == source.source_id
    assert times == sorted(times) and len(times) == 3
    assert times[0] == datetime.fromtimestamp(stamps[1], timezone.utc).replace(tzinfo=None)


def test_picking_keeps_the_frames_nearest_the_target_in_time_order():
    base = datetime(2012, 7, 12, 16, 0)
    times = [base + timedelta(minutes=12 * k) for k in range(10)]
    target = base + timedelta(minutes=50)
    picked = hv.pick_frames_near(times, target, 3)
    assert picked == sorted(picked)
    assert picked == [base + timedelta(minutes=36), base + timedelta(minutes=48), base + timedelta(minutes=60)]


def test_a_dropped_connection_is_retried_then_succeeds():
    import requests

    session = _Session(
        {"getJPX": [requests.ConnectionError("Remote end closed connection"), _Resp(payload={"frames": [1342108800]})]}
    )
    response = hv._get(session, "getJPX/", {}, retry_delays=(0.0, 0.0))
    assert response.json()["frames"] == [1342108800]
    assert len(session.calls) == 2


def test_a_bad_request_fails_at_once_without_retrying():
    session = _Session({"getJPX": [_Resp(status=400), _Resp(payload={})]})
    with pytest.raises(hv.HelioviewerJP2Error):
        hv._get(session, "getJPX/", {}, retry_delays=(0.0, 0.0))
    assert len(session.calls) == 1


def test_a_server_that_never_recovers_gives_a_clear_error():
    import requests

    session = _Session({"getJPX": [requests.ConnectionError("down")] * 3})
    with pytest.raises(hv.HelioviewerJP2Error, match="did not respond after 3 attempts"):
        hv._get(session, "getJPX/", {}, retry_delays=(0.0, 0.0))


def test_cancelling_stops_a_retry_loop():
    import requests

    session = _Session({"getJPX": [requests.ConnectionError("down")] * 5})
    with pytest.raises(hv.HelioviewerJP2Cancelled):
        hv._get(session, "getJPX/", {}, retry_delays=(5.0, 5.0), cancel_cb=lambda: len(session.calls) >= 1)
    assert len(session.calls) == 1


# --- Download -----------------------------------------------------------------


def test_a_cached_jp2_is_served_without_a_request(tmp_path: Path):
    source = hv.source_by_key("LASCO C2")
    when = datetime(2012, 7, 12, 17, 0, 8)
    raw = make_jp2(np.zeros((8, 8)), _xml(lasco_fits()))
    hv.cache_path(tmp_path, source, when).write_bytes(raw)
    session = _Session({})
    assert hv.download_jp2(source, when, cache_dir=tmp_path, session=session) == raw
    assert session.calls == []


def test_a_download_is_written_to_the_cache(tmp_path: Path):
    source = hv.source_by_key("LASCO C2")
    when = datetime(2012, 7, 12, 17, 0, 8)
    raw = make_jp2(np.zeros((8, 8)), _xml(lasco_fits()))
    session = _Session({"getJP2Image": _Resp(content=raw)})
    hv.download_jp2(source, when, cache_dir=tmp_path, session=session)
    assert hv.cache_path(tmp_path, source, when).read_bytes() == raw
    assert session.calls[0][1]["date"] == "2012-07-12T17:00:08Z"


def test_a_non_jp2_response_is_rejected(tmp_path: Path):
    source = hv.source_by_key("LASCO C2")
    session = _Session({"getJP2Image": _Resp(content=b'{"error":"no image"}')})
    with pytest.raises(hv.HelioviewerJP2Error, match="did not return a JPEG2000"):
        hv.download_jp2(source, datetime(2012, 7, 12), cache_dir=tmp_path, session=session)
    # Nothing bogus cached. (tmp_path is shared with conftest's QSettings file,
    # so look for image files specifically rather than an empty directory.)
    assert not list(tmp_path.glob("*.jp2")) and not list(tmp_path.glob("*.part"))


# --- Decoding -----------------------------------------------------------------


def test_the_xml_box_is_found_by_walking_boxes():
    raw = make_jp2(np.zeros((8, 8)), _xml({"INSTRUME": "LASCO"}))
    assert "<INSTRUME>LASCO</INSTRUME>" in hv.jp2_xml_text(raw)


def test_the_trailing_nul_helioviewer_writes_does_not_break_parsing():
    header = hv.parse_jp2_header(_xml(lasco_fits(), trailing_nul=True).decode())
    assert header["instrume"] == "LASCO"
    assert header["crota2"] == pytest.approx(-173.538)


def test_a_malformed_history_card_falls_back_to_pattern_parsing():
    text = _xml({"INSTRUME": "LASCO", "CDELT1": 11.9}).decode().replace(
        "</fits>", "<HISTORY>a < b & c</HISTORY></fits>"
    )
    header = hv.parse_jp2_header(text)
    assert header["instrume"] == "LASCO"
    assert header["cdelt1"] == pytest.approx(11.9)


def test_the_helioviewer_section_survives_parsing_as_sunpy_expects():
    """LASCOMap decides whether to ignore CROTA by looking for this very key."""
    header = hv.parse_jp2_header(_xml(lasco_fits(), helioviewer=HV_LASCO).decode())
    assert "helioviewer" in header
    assert header["hv_detector"] == "C2"


def test_pixels_are_flipped_into_fits_row_order():
    stored = np.zeros((8, 8), dtype=np.uint8)
    stored[0, :] = 200  # top row as the JP2 stores it
    pixels = hv.decode_jp2_pixels(make_jp2(stored, _xml({})))
    assert pixels.dtype == np.float32
    assert np.all(pixels[-1, :] == 200) and np.all(pixels[0, :] == 0)


def test_lasco_gets_an_l1_observer_and_its_date_rejoined():
    import astropy.units as u
    from sunpy.coordinates import get_earth

    header = hv.normalise_header(hv.parse_jp2_header(_xml(lasco_fits()).decode()))
    earth = get_earth("2012-07-12T17:00:07.671")
    assert header["hgln_obs"] == 0.0
    assert header["hglt_obs"] == pytest.approx(earth.lat.to_value(u.deg))
    assert header["dsun_obs"] == pytest.approx(earth.radius.to_value(u.m) - hv.L1_OFFSET_M)
    assert "rsun" not in header  # RSUN = 0 is not a radius


def test_an_existing_observer_is_left_alone():
    fits = lasco_fits(HGLN_OBS=1.5, HGLT_OBS=2.5, DSUN_OBS=1.4e11)
    header = hv.normalise_header(hv.parse_jp2_header(_xml(fits).decode()))
    assert header["hgln_obs"] == pytest.approx(1.5)
    assert header["dsun_obs"] == pytest.approx(1.4e11)


def test_lasco_jp2_crota_is_ignored_because_helioviewer_already_rotated_it():
    """Honouring CROTA here would draw LASCO upside down under the wireframe."""
    raw = make_jp2(np.zeros((64, 64)), _xml(lasco_fits(), helioviewer=HV_LASCO))
    frame = hv.jp2_to_map(raw)
    assert np.allclose(frame.rotation_matrix, np.eye(2))
    assert frame.date.isot.startswith("2012-07-12T17:00:07")


def test_crota_still_counts_when_the_file_is_not_from_helioviewer():
    """Guards the key the rule depends on: without the helioviewer section the
    same header must keep its roll."""
    raw = make_jp2(np.zeros((64, 64)), _xml(lasco_fits(), helioviewer=None))
    frame = hv.jp2_to_map(raw)
    assert not np.allclose(frame.rotation_matrix, np.eye(2))


def test_a_decoded_lasco_frame_supports_the_gcs_projection():
    from src.backend.gcs.gcs_model import ObserverGeometry

    frame = hv.jp2_to_map(make_jp2(np.zeros((64, 64)), _xml(lasco_fits(), helioviewer=HV_LASCO)))
    geometry = ObserverGeometry.from_frame(frame)
    assert geometry is not None
    assert geometry.lon_deg == pytest.approx(0.0, abs=1e-6)
    assert 210 < geometry.dsun_rsun < 222


# --- Harmonising --------------------------------------------------------------


def test_mixed_2048_and_1024_frames_share_one_grid_without_dropping():
    """Consecutive COR2 JP2s really do alternate resolution."""
    frames = [sky_map(128), sky_map(64), sky_map(128), sky_map(64)]
    harmonised, dropped = hv.harmonise_sequence(frames, max_px=64)
    assert dropped == 0
    assert {tuple(frame.data.shape) for frame in harmonised} == {(64, 64)}


def test_a_single_small_outlier_does_not_downsample_the_sequence():
    frames = [sky_map(64), sky_map(64), sky_map(64), sky_map(32)]
    harmonised, dropped = hv.harmonise_sequence(frames, max_px=1024)
    assert dropped == 1
    assert {tuple(frame.data.shape) for frame in harmonised} == {(64, 64)}


def test_frames_above_the_size_cap_are_block_averaged_down():
    harmonised, dropped = hv.harmonise_sequence([sky_map(128), sky_map(128)], max_px=64)
    assert dropped == 0 and harmonised[0].data.shape == (64, 64)


def test_rebinning_keeps_the_sun_centre_on_the_same_sky_position():
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    frame = sky_map(128, crpix_shift=(3.3, -2.1))
    binned = hv._rebinned(frame, 2)
    # Same sky point, both grids: the world coordinate of the binned Sun centre
    # must still be (0, 0).
    x, y = hv._sun_centre_pixel(binned)
    world = binned.pixel_to_world(x * u.pix, y * u.pix)
    assert world.Tx.to_value(u.arcsec) == pytest.approx(0.0, abs=1e-3)
    assert world.Ty.to_value(u.arcsec) == pytest.approx(0.0, abs=1e-3)
    assert binned.scale.axis1.to_value(u.arcsec / u.pix) == pytest.approx(
        2 * frame.scale.axis1.to_value(u.arcsec / u.pix)
    )


def test_pointing_jitter_is_removed_by_aligning_sun_centres():
    frames = [sky_map(64), sky_map(64, crpix_shift=(0.5, -0.5))]
    harmonised, _ = hv.harmonise_sequence(frames)
    first = hv._sun_centre_pixel(harmonised[0])
    second = hv._sun_centre_pixel(harmonised[1])
    assert second[0] == pytest.approx(first[0], abs=1e-6)
    assert second[1] == pytest.approx(first[1], abs=1e-6)


def test_aligned_frames_difference_to_near_zero_where_nothing_changed():
    """The point of alignment: without it, every edge becomes a bright/dark pair."""
    frames = [sky_map(96), sky_map(96, crpix_shift=(0.5, 0.5))]
    unaligned = np.abs(frames[1].data - frames[0].data)
    harmonised, _ = hv.harmonise_sequence(frames)
    aligned = hv.difference_image(harmonised[1], harmonised[0], smooth_sigma_px=0)
    assert np.percentile(np.abs(aligned), 99) < 0.5 * np.percentile(unaligned, 99)


def test_one_extra_frame_is_brought_onto_a_harmonised_grid():
    sequence, _ = hv.harmonise_sequence([sky_map(64), sky_map(64)])
    extra = hv.align_to_grid(sky_map(128, crpix_shift=(1.0, -1.0)), sequence[0])
    assert extra.data.shape == (64, 64)
    assert hv._sun_centre_pixel(extra) == pytest.approx(hv._sun_centre_pixel(sequence[0]), abs=1e-6)
    assert hv.difference_image(extra, sequence[0]).shape == (64, 64)
    # Never upsampled: a smaller frame cannot join the grid.
    assert hv.align_to_grid(sky_map(32), sequence[0]) is None


# --- Difference images --------------------------------------------------------


def test_a_difference_is_zero_outside_the_field():
    a, b = sky_map(64), sky_map(64, offset=3.0)
    diff = hv.difference_image(b, a)
    outside = np.asarray(a.data) == 0
    assert np.all(diff[outside] == 0)


def test_a_global_offset_between_frames_is_removed():
    """A 1024-px and a 2048-px JP2 of the same corona can sit several counts apart."""
    a, b = sky_map(64), sky_map(64, offset=5.0)
    diff = hv.difference_image(b, a, smooth_sigma_px=0)
    inside = diff != 0
    assert abs(float(np.median(diff[inside]))) < 1e-3


def test_differences_are_taken_on_the_bytes_not_divided_by_exposure():
    """JP2 pixels are display values; dividing by EXPTIME would invent change."""
    a, b = sky_map(64), sky_map(64)
    a.meta["exptime"], b.meta["exptime"] = 6.0, 25.0
    diff = hv.difference_image(b, a, smooth_sigma_px=0)
    assert np.allclose(diff, 0.0, atol=1e-4)


def test_a_difference_of_misaligned_grids_is_refused():
    with pytest.raises(ValueError, match="share one grid"):
        hv.difference_image(sky_map(64), sky_map(32))


def test_the_symmetric_clip_ignores_the_masked_background():
    image = np.zeros((10, 10), dtype=np.float32)
    image[4:6, 4:6] = [[-4.0, 2.0], [3.0, -1.0]]
    # With the zeros included the 50th percentile would be 0.
    assert hv.symmetric_clip(image, 50.0) == pytest.approx(2.5)
    assert hv.symmetric_clip(np.zeros((4, 4)), 99.0) == 1.0


# --- Date ranges ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_listing_cache():
    """The listing cache is module-wide; tests must not see each other's entries."""
    hv._LISTING_CACHE.clear()
    yield
    hv._LISTING_CACHE.clear()


def test_a_range_must_run_forwards_and_stay_within_the_limit():
    start = datetime(2012, 7, 12, 16)
    assert hv.validate_range(start, start + timedelta(hours=2)) is None
    assert "after its start" in hv.validate_range(start, start)
    assert "after its start" in hv.validate_range(start, start - timedelta(minutes=1))
    assert "narrow it" in hv.validate_range(start, start + hv.MAX_RANGE + timedelta(minutes=1))


def test_a_range_under_the_cap_keeps_every_consecutive_frame():
    base = datetime(2012, 7, 12, 16)
    times = [base + timedelta(minutes=12 * k) for k in range(8)]
    assert hv.pick_frames_evenly(list(reversed(times)), 60) == times


def test_a_range_over_the_cap_is_thinned_evenly_keeping_both_ends():
    base = datetime(2012, 7, 12, 0)
    times = [base + timedelta(minutes=12 * k) for k in range(120)]
    picked = hv.pick_frames_evenly(times, 10)
    assert len(picked) == 10
    assert picked[0] == times[0] and picked[-1] == times[-1]
    gaps = {round((b - a).total_seconds() / 60) for a, b in zip(picked, picked[1:])}
    assert max(gaps) - min(gaps) <= 12  # even to within one native step


def test_fetch_range_loads_every_listed_frame_onto_one_grid(tmp_path: Path):
    source = hv.source_by_key("LASCO C2")
    base = datetime(2012, 7, 12, 16, 0, 6)
    stamps = [int((base + timedelta(minutes=12 * k)).replace(tzinfo=timezone.utc).timestamp()) for k in range(5)]
    raw = make_jp2(np.full((64, 64), 40, dtype=np.uint8), _xml(lasco_fits(), helioviewer=HV_LASCO))
    session = _Session({"getJPX": _Resp(payload={"frames": stamps}), "getJP2Image": _Resp(content=raw)})
    progress: list[str] = []
    sequence = hv.fetch_range(
        source,
        datetime(2012, 7, 12, 16),
        datetime(2012, 7, 12, 17),
        cache_dir=tmp_path,
        session=session,
        progress_cb=progress.append,
    )
    assert sequence.listed == 5 and len(sequence.frames) == 5
    assert {frame.data.shape for frame in sequence.frames} == {(64, 64)}
    assert sequence.previous is None  # nothing listed before the range
    assert sum(1 for endpoint, _ in session.calls if endpoint == "getJPX") == 1
    assert any("Listing" in line for line in progress)

    # A second fetch of the same, long-past range needs no network at all.
    session.calls.clear()
    again = hv.fetch_range(
        source, datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17), cache_dir=tmp_path, session=session
    )
    assert len(again.frames) == 5 and again.from_cache == 5
    assert session.calls == []


class _DatedSession(_Session):
    """Lists ``stamps`` and serves each JP2 with the requested time in its header."""

    def __init__(self, stamps, make_jp2_for):
        super().__init__({"getJPX": _Resp(payload={"frames": stamps})})
        self._make_jp2_for = make_jp2_for

    def get(self, url, params=None, timeout=None):
        endpoint = url.rsplit("/", 2)[-2]
        if endpoint != "getJP2Image":
            return super().get(url, params=params, timeout=timeout)
        self.calls.append((endpoint, dict(params or {})))
        return _Resp(content=self._make_jp2_for(datetime.strptime(params["date"], "%Y-%m-%dT%H:%M:%SZ")))


def _dated_lasco_jp2(when: datetime, size: int = 64) -> bytes:
    fits = lasco_fits(
        NAXIS1=size, NAXIS2=size, CRPIX1=size / 2 + 0.5, CRPIX2=size / 2 + 0.5,
        CDELT1=190.4 * 64 / size, CDELT2=190.4 * 64 / size,
        DATE_OBS=f"{when:%Y/%m/%d}", TIME_OBS=f"{when:%H:%M:%S}.000",
    )
    return make_jp2(np.full((size, size), 40, dtype=np.uint8), _xml(fits, helioviewer=HV_LASCO))


def _unix(times) -> list[int]:
    return [int(when.replace(tzinfo=timezone.utc).timestamp()) for when in times]


def test_fetch_range_loads_the_frame_before_the_range_as_its_first_reference(tmp_path: Path):
    """The first frame has no running-difference reference inside the range."""
    start, end = datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17)
    base = datetime(2012, 7, 12, 16, 0, 6)
    times = [base + timedelta(minutes=12 * k) for k in range(-3, 5)]  # three before the range
    # The reference arrives at double resolution, as consecutive COR2 JP2s do.
    session = _DatedSession(_unix(times), lambda when: _dated_lasco_jp2(when, 128 if when < start else 64))
    sequence = hv.fetch_range(hv.source_by_key("LASCO C2"), start, end, cache_dir=tmp_path, session=session)

    listings = [params for endpoint, params in session.calls if endpoint == "getJPX"]
    assert len(listings) == 1  # the look-back rides on the one listing call
    assert listings[0]["startTime"] == hv._iso_z(start - hv.PREVIOUS_FRAME_LOOKBACK)
    assert sequence.listed == 5 and len(sequence.frames) == 5
    assert sequence.frames[0].date.datetime == base
    # The latest frame before the range, not an older one, and only that one downloaded.
    assert sequence.previous.date.datetime == times[2]
    downloads = sorted(params["date"] for endpoint, params in session.calls if endpoint == "getJP2Image")
    assert downloads == sorted(hv._iso_z(when) for when in times[2:])
    assert sequence.previous.data.shape == sequence.frames[0].data.shape
    assert hv._sun_centre_pixel(sequence.previous) == pytest.approx(hv._sun_centre_pixel(sequence.frames[0]), abs=1e-6)


def test_a_reference_that_fails_to_load_is_not_a_failed_frame(tmp_path: Path):
    start, end = datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17)
    base = datetime(2012, 7, 12, 16, 0, 6)
    times = [base + timedelta(minutes=12 * k) for k in range(-1, 5)]
    session = _DatedSession(_unix(times), lambda when: b"not a jp2" if when < start else _dated_lasco_jp2(when))
    sequence = hv.fetch_range(hv.source_by_key("LASCO C2"), start, end, session=session)
    assert sequence.previous is None
    assert sequence.skipped == ()
    assert len(sequence.frames) == 5


def test_fetch_range_thins_a_long_range_to_the_cap(tmp_path: Path):
    source = hv.source_by_key("LASCO C2")
    base = datetime(2012, 7, 12, 0, 0, 6)
    stamps = [int((base + timedelta(minutes=12 * k)).replace(tzinfo=timezone.utc).timestamp()) for k in range(40)]
    raw = make_jp2(np.full((32, 32), 40, dtype=np.uint8), _xml(lasco_fits(NAXIS1=32, NAXIS2=32, CRPIX1=16.5, CRPIX2=16.5), helioviewer=HV_LASCO))
    session = _Session({"getJPX": _Resp(payload={"frames": stamps}), "getJP2Image": _Resp(content=raw)})
    sequence = hv.fetch_range(
        source, datetime(2012, 7, 12, 0), datetime(2012, 7, 12, 8), max_frames=6, cache_dir=tmp_path, session=session
    )
    assert sequence.listed == 40
    assert len(sequence.frames) == 6
    assert sum(1 for endpoint, _ in session.calls if endpoint == "getJP2Image") == 6


def test_fetch_range_refuses_a_backwards_range_without_a_request():
    session = _Session({})
    with pytest.raises(hv.HelioviewerJP2Error, match="after its start"):
        hv.fetch_range(hv.source_by_key("COR2-A"), datetime(2012, 7, 12, 18), datetime(2012, 7, 12, 16), session=session)
    assert session.calls == []


def test_an_empty_range_says_which_channel_and_when():
    session = _Session({"getJPX": _Resp(payload={"frames": []})})
    with pytest.raises(hv.HelioviewerJP2Error, match="no STEREO-A COR2 frames between 2012-07-12 16:00"):
        hv.fetch_range(hv.source_by_key("COR2-A"), datetime(2012, 7, 12, 16), datetime(2012, 7, 12, 17), session=session)
