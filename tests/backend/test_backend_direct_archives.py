"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Tests for src/backend/solar/direct_archives.py: the Helioviewer JP2 and JSOC
AIA-synoptic searches, their downloads and the JP2 load. No network: listings
and downloads are faked, JP2 files are real JPEG2000 written by Pillow.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.backend.services.download_manager import AggregateProgress, DownloadResult
from src.backend.solar import data_products as dp
from src.backend.solar import direct_archives as da
from src.backend.solar import helioviewer_jp2 as hv
from src.backend.solar.sunpy_archive import SunPyQuerySpec

T0 = datetime(2026, 10, 5, 12, 0, 0)


def _spec(archive: str, **overrides) -> SunPyQuerySpec:
    values = dict(
        start_dt=T0,
        end_dt=T0 + timedelta(hours=1),
        spacecraft="SDO",
        instrument="AIA",
        wavelength_angstrom=193.0,
        sample_seconds=None,
        max_records=200,
        archive=archive,
    )
    values.update(overrides)
    return SunPyQuerySpec(**values)


# --- Thinning ------------------------------------------------------------------


def test_thinning_keeps_one_frame_per_cadence_with_slack_for_jitter():
    times = [T0 + timedelta(seconds=s) for s in (0, 12, 24, 118, 130, 240, 300)]
    kept = da.thin_times(times, cadence_seconds=120)
    # 118 s is within the 2% slack of 120 s; 240 is the next one after it.
    assert kept == [T0, T0 + timedelta(seconds=118), T0 + timedelta(seconds=240)]


def test_a_cap_spreads_frames_or_keeps_the_newest():
    times = [T0 + timedelta(minutes=m) for m in range(10)]
    assert da.thin_times(times, max_records=3) == [T0, T0 + timedelta(minutes=4), T0 + timedelta(minutes=9)]
    assert da.thin_times(times, max_records=2, latest=True) == times[-2:]
    assert da.thin_times(times, max_records=1) == [T0]


# --- AIA synoptic --------------------------------------------------------------

DEFINITIVE_LISTING = """
<a href="AIA20261005_1200_0171.fits">AIA20261005_1200_0171.fits</a>      12-Oct-2026 09:13   1162080
<a href="AIA20261005_1200_0193.fits">AIA20261005_1200_0193.fits</a>      12-Oct-2026 09:13   1166400
<a href="AIA20261005_1202_0193.fits">AIA20261005_1202_0193.fits</a>      12-Oct-2026 09:13   1166401
<a href="AIA20261005_1204_0193.fits">AIA20261005_1204_0193.fits</a>      12-Oct-2026 09:13   1166402
"""

NRT_LISTING = """\r
<a href="AIA20261012_080000_0304.fits">AIA20261012_080000_0304.fits</a>    12-Oct-2026 08:15   826560\r
<a href="AIA20261012_080300_0304.fits">AIA20261012_080300_0304.fits</a>    12-Oct-2026 08:18   829440\r
"""


def test_a_listing_yields_the_passband_with_exact_sizes():
    url = "https://jsoc1.stanford.edu/data/aia/synoptic/2026/10/05/H1200/"
    files = da.parse_synoptic_listing(DEFINITIVE_LISTING, url, 193)
    assert [f.filename for f in files] == [
        "AIA20261005_1200_0193.fits",
        "AIA20261005_1202_0193.fits",
        "AIA20261005_1204_0193.fits",
    ]
    assert files[0].when == T0 and files[0].size == 1166400
    assert files[0].url == url + "AIA20261005_1200_0193.fits"


def test_nrt_names_carry_seconds():
    files = da.parse_synoptic_listing(NRT_LISTING, "u/", 304)
    assert [f.when for f in files] == [datetime(2026, 10, 12, 8, 0, 0), datetime(2026, 10, 12, 8, 3, 0)]
    assert files[1].size == 829440


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


def test_synoptic_search_falls_back_to_nrt_and_filters_the_window():
    requested = []

    def getter(url, **_kwargs):
        requested.append(url)
        if "/nrt/" in url:
            return _Resp(200, DEFINITIVE_LISTING)
        return _Resp(404)

    result = da.search_aia_synoptic(
        _spec(dp.ARCHIVE_AIA_SYNOPTIC, end_dt=T0 + timedelta(minutes=3)), getter=getter, max_workers=1
    )
    assert any("/nrt/" in url for url in requested)
    assert [row.fileid for row in result.rows] == ["AIA20261005_1200_0193.fits", "AIA20261005_1202_0193.fits"]
    assert result.rows[0].provider == "JSOC NRT"
    assert result.rows[0].size == "1.1 MB"
    assert [f.size for f in result.raw_response] == [1166400, 1166401]
    assert result.spec.archive == dp.ARCHIVE_AIA_SYNOPTIC


def test_an_unreachable_synoptic_archive_is_an_error_not_an_empty_list():
    def getter(url, **_kwargs):
        raise ConnectionError("down")

    with pytest.raises(RuntimeError, match="could not be reached"):
        da.search_aia_synoptic(_spec(dp.ARCHIVE_AIA_SYNOPTIC), getter=getter, max_workers=1)


def test_synoptic_limits():
    with pytest.raises(ValueError, match="1700"):
        da.search_aia_synoptic(_spec(dp.ARCHIVE_AIA_SYNOPTIC, wavelength_angstrom=1700.0), getter=lambda *a, **k: None)
    with pytest.raises(ValueError, match="at most"):
        da.search_aia_synoptic(
            _spec(dp.ARCHIVE_AIA_SYNOPTIC, end_dt=T0 + timedelta(days=8)), getter=lambda *a, **k: None
        )


# --- Helioviewer JP2 ---------------------------------------------------------------


def test_jp2_search_lists_day_by_day_and_builds_exact_urls(monkeypatch):
    calls = []

    def fake_list(source, start, end, **_kwargs):
        calls.append((source.source_id, start, end))
        return [start + timedelta(minutes=10), start + timedelta(minutes=20)]

    monkeypatch.setattr(hv, "list_frame_times", fake_list)
    progress = []
    spec = _spec(
        dp.ARCHIVE_HELIOVIEWER_JP2,
        spacecraft="SOHO",
        instrument="LASCO",
        wavelength_angstrom=None,
        detector="C2",
        end_dt=T0 + timedelta(days=1, hours=12),
    )
    result = da.search_jp2(spec, progress_cb=lambda v, t: progress.append((v, t)))

    assert [c[0] for c in calls] == [4, 4]
    assert calls[1][1] == T0 + timedelta(days=1)
    assert len(result.rows) == 4
    assert progress and progress[0][0] == 0 and "day 1 of 2" in progress[0][1]
    first = result.raw_response[0]
    assert first.filename == "hv4_20261005T121000.jp2"
    assert first.url.endswith("getJP2Image/?date=2026-10-05T12:10:00Z&sourceId=4")
    assert result.rows[0].provider == "Helioviewer"
    assert result.rows[0].size.startswith("≈")


def test_jp2_search_refuses_what_helioviewer_does_not_hold():
    with pytest.raises(ValueError, match="JP2"):
        da.search_jp2(_spec(dp.ARCHIVE_HELIOVIEWER_JP2, spacecraft="GOES", instrument="SUVI", wavelength_angstrom=171.0))
    with pytest.raises(ValueError, match="ends on 2014"):
        da.search_jp2(
            _spec(
                dp.ARCHIVE_HELIOVIEWER_JP2,
                spacecraft="STEREO_B",
                instrument="SECCHI",
                detector="COR2",
                wavelength_angstrom=None,
            )
        )


def test_find_latest_widens_the_look_back_until_frames_appear():
    from src.backend.solar.sunpy_archive import SunPySearchRow

    windows = []

    def fake_search(spec, *, progress_cb=None, cancel_cb=None, latest=False):
        windows.append(spec.end_dt - spec.start_dt)
        assert latest is True
        if len(windows) < 2:
            return da._result(spec, [], [])
        when = spec.end_dt - timedelta(hours=30)
        rows = [SunPySearchRow(when, when, "SDO", "AIA", "Helioviewer", "a.jp2", "")]
        return da._result(spec, [da.DirectFile("u", "a.jp2", when)], rows)

    now = datetime(2026, 10, 12, 12, 0, 0)
    result = da.find_latest_direct(_spec(dp.ARCHIVE_HELIOVIEWER_JP2), now=now, search_fn=fake_search)
    assert windows[0] == timedelta(hours=6) and windows[1] == timedelta(days=3)
    assert result.spec.start_dt == now - timedelta(hours=30)
    assert result.spec.end_dt > result.spec.start_dt


# --- Download ------------------------------------------------------------------------


class _FakeManager:
    """Writes each item's payload where DownloadManager would."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.items = []

    def download(self, items, *, progress_cb=None, cancel_cb=None):
        self.items = list(items)
        paths = []
        for index, item in enumerate(items):
            Path(item.dest).write_bytes(self.payloads[index])
            paths.append(str(item.dest))
        if progress_cb is not None:
            progress_cb(AggregateProgress(files_total=len(items), files_done=len(items)))
        return DownloadResult(paths=paths, errors=[])


def _jp2_bytes():
    from tests.backend.test_backend_helioviewer_jp2 import _xml, lasco_fits, make_jp2

    return make_jp2(np.zeros((16, 16)), _xml(lasco_fits(NAXIS1=16, NAXIS2=16, CRPIX1=8.5, CRPIX2=8.5)))


def test_a_jp2_download_drops_files_that_are_not_jpeg2000(tmp_path):
    spec = _spec(dp.ARCHIVE_HELIOVIEWER_JP2, spacecraft="SOHO", instrument="LASCO", detector="C2")
    files = [
        da.DirectFile("https://x/a", "hv4_a.jp2", T0),
        da.DirectFile("https://x/b", "hv4_b.jp2", T0 + timedelta(minutes=12)),
    ]
    from src.backend.solar.sunpy_archive import SunPySearchRow

    rows = [SunPySearchRow(f.when, f.when, "SOHO", "LASCO", "Helioviewer", f.filename, "") for f in files]
    result = da._result(spec, files, rows)
    manager = _FakeManager([_jp2_bytes(), b'{"error": "No image found"}'])
    seen = []

    out = da.fetch_direct(result, tmp_path, [0, 1], byte_progress_cb=seen.append, download_manager=manager)

    assert [Path(p).name for p in out.paths] == ["hv4_a.jp2"]
    assert out.failed_count == 1 and "JPEG2000" in out.errors[0]
    assert out.failed_rows == [1]
    assert not (tmp_path / "hv4_b.jp2").exists()
    assert seen and seen[-1].files_done == 2
    assert all(Path(item.dest).parent == tmp_path.resolve() for item in manager.items)


def test_jp2_frames_load_as_marked_quicklook_maps(tmp_path):
    pytest.importorskip("sunpy.map")
    good = tmp_path / "hv4_a.jp2"
    good.write_bytes(_jp2_bytes())
    bad = tmp_path / "hv4_b.jp2"
    bad.write_bytes(b"not a jp2")
    progress = []

    result = da.load_jp2_frames([good, bad], max_px=8, progress_cb=lambda d, n: progress.append((d, n)))

    assert progress == [(1, 2), (2, 2)]
    assert len(result.maps_or_timeseries) == 1
    frame = result.maps_or_timeseries[0]
    assert hv.is_quicklook_frame(frame)
    assert frame.data.shape == (8, 8)
    assert result.metadata["product"] == dp.PRODUCT_JP2
    assert result.metadata["load_errors"] and "hv4_b.jp2" in result.metadata["load_errors"][0]
