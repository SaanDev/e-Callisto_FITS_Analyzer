"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Selectable FITS servers: listings, cache namespaces, download validation and
timeline extension, all from fixtures. The live checks run only when
E_CALLISTO_LIVE_ARCHIVE_TESTS is set, so ordinary runs stay off the network.
"""

from __future__ import annotations

from datetime import date, datetime
import gzip
from io import BytesIO
import json
import os
import threading
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("astropy")

from astropy.io import fits

from src.backend.radio import callisto_archive as archive
from src.backend.radio import callisto_cache, callisto_timeline as timeline
from src.backend.radio.callisto_archive import (
    ASTRODONCEL_SOURCE_ID,
    ARCHIVE_SOURCES,
    BASE_URL,
    DEFAULT_SOURCE_ID,
    ArchiveListingError,
    day_url,
    fetch_day_stations,
    list_day,
    normalize_source_id,
    source_id_for_url,
)

DAY = date(2026, 10, 1)
ANCHORAGE = "ALASKA-ANCHORAGE_20261001_000000_01.fit.gz"
ASTRODONCEL_FILES = "https://astrodoncel.uah.es/ecallistodata/"
LISTING_URL = "https://astrodoncel.uah.es/dashboard/datos/getitems.php"


def _record(name, *, directory=False, size=1234):
    return {"name": name, "isDirectory": directory, "dateModified": "2026-10-01T00:00:00", "size": size}


class _Response:
    def __init__(self, status=200, text="", content=b"", headers=None):
        self.status_code = status
        self.text = text
        self.content = content
        self.headers = dict(headers if headers is not None else {"Content-Length": str(len(content))})

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=None):
        step = chunk_size or 1024
        for start in range(0, len(self.content), step):
            yield self.content[start:start + step]


class _Session:
    """Answers by URL, or for the Astrodoncel listing by its ``path`` parameter."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get(self, url, params=None, **_kwargs):
        self.calls.append((url, params))
        key = (url, params["path"]) if params else url
        result = self.responses.get(key, _Response(404))
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def _listing(day, records):
    return {(LISTING_URL, day.strftime("%Y/%m/%d")): _Response(text=json.dumps(records))}


def _fits_payload():
    handle = BytesIO()
    fits.PrimaryHDU(data=np.zeros((2, 3), dtype=np.float32)).writeto(handle)
    return handle.getvalue()


def _gz_fits():
    return gzip.compress(_fits_payload())


@pytest.fixture(autouse=True)
def _fresh_day_cache():
    timeline.clear_day_cache()
    yield
    timeline.clear_day_cache()


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    root = tmp_path / "appdata"
    monkeypatch.setattr(callisto_cache, "_app_data_root", lambda: root)
    monkeypatch.setattr(callisto_cache, "max_cache_bytes", lambda: callisto_cache.DEFAULT_MAX_CACHE_BYTES)
    return root / callisto_cache.CACHE_DIR_NAME


# -----------------------------
# Source definitions
# -----------------------------
def test_the_two_servers_and_their_stable_ids():
    assert [(source.id, source.label) for source in ARCHIVE_SOURCES] == [
        ("ecallisto", "e-CALLISTO"),
        ("astrodoncel", "Astrodoncel"),
    ]
    assert archive.BASE_URL == "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/"


@pytest.mark.parametrize("value,expected", [
    ("astrodoncel", "astrodoncel"),
    ("ecallisto", "ecallisto"),
    ("Astrodoncel", "ecallisto"),
    ("", "ecallisto"),
    (None, "ecallisto"),
    (3, "ecallisto"),
])
def test_saved_values_fall_back_to_the_default_server(value, expected):
    assert normalize_source_id(value) == expected


def test_day_url_keeps_its_default_and_follows_the_source():
    assert day_url(DAY) == f"{BASE_URL}2026/10/01/"
    assert day_url(DAY, ASTRODONCEL_SOURCE_ID) == f"{ASTRODONCEL_FILES}2026/10/01/"


def test_a_url_says_which_server_a_file_came_from():
    assert source_id_for_url(f"{ASTRODONCEL_FILES}2026/10/01/{ANCHORAGE}") == ASTRODONCEL_SOURCE_ID
    assert source_id_for_url(f"{BASE_URL}2026/10/01/{ANCHORAGE}") == DEFAULT_SOURCE_ID
    assert source_id_for_url(f"https://astrodoncel.uah.es/other/{ANCHORAGE}") == DEFAULT_SOURCE_ID
    assert source_id_for_url("https://archive.test/x.fit.gz") == DEFAULT_SOURCE_ID


def test_an_unknown_source_is_a_programming_error():
    with pytest.raises(ValueError):
        list_day(DAY, "elsewhere", _Session())


# -----------------------------
# e-CALLISTO listing
# -----------------------------
def _html(*names):
    return "<html>" + "".join(f'<a href="{name}">{name}</a>' for name in names) + "</html>"


def test_ecallisto_records_are_normalized_from_the_html_listing():
    session = _Session({f"{BASE_URL}2026/10/01/": _Response(text=_html(ANCHORAGE))})

    [entry] = list_day(DAY, session=session)

    assert entry.source_id == DEFAULT_SOURCE_ID
    assert entry.filename == ANCHORAGE
    assert entry.station == "ALASKA-ANCHORAGE"
    assert entry.observed_at_utc == datetime(2026, 10, 1)
    assert entry.focus_code == "01"
    assert entry.download_url == f"{BASE_URL}2026/10/01/{ANCHORAGE}"
    assert entry.size_bytes is None


def test_ecallisto_can_keep_names_it_cannot_read_for_the_single_station_list():
    name = "notes_about_the_day.fit"
    session = _Session({f"{BASE_URL}2026/10/01/": _Response(text=_html(ANCHORAGE, name))})

    assert [entry.filename for entry in list_day(DAY, session=session)] == [ANCHORAGE]
    entries = list_day(DAY, session=session, include_unparsed=True)
    assert [entry.filename for entry in entries] == [ANCHORAGE, name]
    assert entries[1].observed_at_utc is None and entries[1].station == ""


def test_ecallisto_missing_directory_is_an_empty_day_but_other_errors_are_not():
    assert list_day(DAY, session=_Session()) == []
    session = _Session({f"{BASE_URL}2026/10/01/": _Response(503)})
    with pytest.raises(ArchiveListingError, match="HTTP 503") as caught:
        list_day(DAY, session=session)
    assert caught.value.status == 503


# -----------------------------
# Astrodoncel listing
# -----------------------------
def test_astrodoncel_lists_a_day_through_its_json_endpoint():
    session = _Session(_listing(DAY, [_record(ANCHORAGE, size=218952)]))

    [entry] = list_day(DAY, ASTRODONCEL_SOURCE_ID, session)

    assert session.calls == [(LISTING_URL, {"path": "2026/10/01"})]
    assert entry.source_id == ASTRODONCEL_SOURCE_ID
    assert entry.station == "ALASKA-ANCHORAGE"
    assert entry.observed_at_utc == datetime(2026, 10, 1)
    assert entry.focus_code == "01"
    assert entry.size_bytes == 218952
    assert entry.download_url == f"{ASTRODONCEL_FILES}2026/10/01/{ANCHORAGE}"


def test_astrodoncel_keeps_station_spelling_nonzero_seconds_and_focus_codes():
    names = ["SRI-Lanka_20261001_000229_059.fit.gz", "Malaysia_Banting_20261001_235959_01.fit.gz"]
    session = _Session(_listing(DAY, [_record(name) for name in names]))

    entries = list_day(DAY, ASTRODONCEL_SOURCE_ID, session)

    assert [entry.station for entry in entries] == ["SRI-Lanka", "Malaysia_Banting"]
    assert entries[0].observed_at_utc == datetime(2026, 10, 1, 0, 2, 29)
    assert [entry.focus_code for entry in entries] == ["059", "01"]


def test_astrodoncel_ignores_what_is_not_an_observation_of_that_day():
    records = [
        _record("subfolder", directory=True),
        _record("BIR_20261001_000000_01.fit.gz", directory=True),
        _record("readme.txt"),
        _record("notes.fit.gz"),
        _record("BIR_20260930_234500_01.fit.gz"),
        _record(ANCHORAGE),
    ]
    session = _Session(_listing(DAY, records))

    assert [entry.filename for entry in list_day(DAY, ASTRODONCEL_SOURCE_ID, session)] == [ANCHORAGE]


@pytest.mark.parametrize("name", [
    "../BIR_20261001_000000_01.fit.gz",
    "..\\BIR_20261001_000000_01.fit.gz",
    "a/BIR_20261001_000000_01.fit.gz",
    "C:BIR_20261001_000000_01.fit.gz",
    "BIR..x_20261001_000000_01.fit.gz",
    ".BIR_20261001_000000_01.fit.gz",
    "BIR\n_20261001_000000_01.fit.gz",
])
def test_astrodoncel_rejects_unsafe_filenames(name):
    session = _Session(_listing(DAY, [_record(name)]))

    assert list_day(DAY, ASTRODONCEL_SOURCE_ID, session) == []


def test_astrodoncel_encodes_the_filename_in_its_url():
    name = "NEW STATION+1_20261001_000000_01.fit.gz"
    session = _Session(_listing(DAY, [_record(name)]))

    [entry] = list_day(DAY, ASTRODONCEL_SOURCE_ID, session)

    assert entry.filename == name
    assert entry.download_url == (
        f"{ASTRODONCEL_FILES}2026/10/01/NEW%20STATION%2B1_20261001_000000_01.fit.gz"
    )


def test_astrodoncel_empty_array_is_an_empty_day():
    session = _Session(_listing(DAY, []))

    assert list_day(DAY, ASTRODONCEL_SOURCE_ID, session) == []
    assert fetch_day_stations(DAY, session, ASTRODONCEL_SOURCE_ID) == []


@pytest.mark.parametrize("body", [
    "<html>Service unavailable</html>",
    "",
    '{"error": "nope"}',
    "null",
    '"text"',
    "[1, 2]",
    '[{"isDirectory": false}]',
    '[{"name": "x.fit.gz"}]',
    '[{"name": 5, "isDirectory": false}]',
])
def test_astrodoncel_unexpected_response_is_an_error_not_an_empty_day(body):
    session = _Session({(LISTING_URL, "2026/10/01"): _Response(text=body)})

    with pytest.raises(ArchiveListingError, match="Astrodoncel listing for 2026-10-01"):
        list_day(DAY, ASTRODONCEL_SOURCE_ID, session)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_astrodoncel_http_errors_are_errors_not_empty_days(status):
    session = _Session({(LISTING_URL, "2026/10/01"): _Response(status)})

    with pytest.raises(ArchiveListingError, match=f"HTTP {status}") as caught:
        list_day(DAY, ASTRODONCEL_SOURCE_ID, session)
    assert caught.value.status == status


def test_astrodoncel_timeouts_propagate_to_the_caller():
    session = _Session({(LISTING_URL, "2026/10/01"): TimeoutError("timed out")})

    with pytest.raises(TimeoutError):
        list_day(DAY, ASTRODONCEL_SOURCE_ID, session)


def test_astrodoncel_tolerates_a_missing_or_odd_size():
    records = [_record(ANCHORAGE, size="big"), _record("BIR_20261001_000000_01.fit.gz", size=None)]
    session = _Session(_listing(DAY, records))

    assert [entry.size_bytes for entry in list_day(DAY, ASTRODONCEL_SOURCE_ID, session)] == [None, None]


def test_stations_come_from_the_same_listing_on_every_server():
    names = ["BIR_20261001_000000_01.fit.gz", "bir_20261001_001500_01.fit.gz", ANCHORAGE]
    session = _Session(_listing(DAY, [_record(name) for name in names]))

    assert fetch_day_stations(DAY, session, ASTRODONCEL_SOURCE_ID) == ["ALASKA-ANCHORAGE", "BIR"]


# -----------------------------
# Cache namespaces
# -----------------------------
def _astrodoncel_url(name=ANCHORAGE):
    return f"{ASTRODONCEL_FILES}2026/10/01/{name}"


def _ecallisto_url(name=ANCHORAGE):
    return f"{BASE_URL}2026/10/01/{name}"


def _serving(url, content):
    return _Session({url: _Response(content=content)})


def test_each_server_has_its_own_cache_location(cache_dir):
    assert callisto_cache.cache_path_for(_ecallisto_url()) == (
        cache_dir / "ALASKA-ANCHORAGE" / "2026-10-01" / ANCHORAGE
    )
    assert callisto_cache.cache_path_for(_astrodoncel_url()) == (
        cache_dir / "sources" / "astrodoncel" / "ALASKA-ANCHORAGE" / "2026-10-01" / ANCHORAGE
    )


def test_a_legacy_entry_is_not_a_hit_for_another_server(cache_dir):
    payload = _gz_fits()
    legacy, _ = callisto_cache.fetch_cached(_ecallisto_url(), session=_serving(_ecallisto_url(), payload))

    session = _serving(_astrodoncel_url(), payload)
    path, was_cached = callisto_cache.fetch_cached(_astrodoncel_url(), session=session)

    assert was_cached is False and session.calls
    assert path != legacy
    assert legacy.exists() and path.exists()
    assert callisto_cache.cached_file(_ecallisto_url()) == legacy
    assert callisto_cache.cached_file(_astrodoncel_url()) == path


def test_size_eviction_and_clearing_cover_both_namespaces(cache_dir):
    payload = _gz_fits()
    legacy, _ = callisto_cache.fetch_cached(_ecallisto_url(), session=_serving(_ecallisto_url(), payload))
    other, _ = callisto_cache.fetch_cached(_astrodoncel_url(), session=_serving(_astrodoncel_url(), payload))
    assert callisto_cache.cache_size_bytes() == 2 * len(payload)

    os.utime(other, (time.time() - 100, time.time() - 100))
    callisto_cache.enforce_cache_limit(len(payload))
    assert not other.exists() and legacy.exists()
    assert not (cache_dir / "sources").exists(), "an empty namespace is pruned"

    callisto_cache.fetch_cached(_astrodoncel_url(), session=_serving(_astrodoncel_url(), payload))
    assert callisto_cache.clear_cache() == 0
    assert callisto_cache.cache_size_bytes() == 0


def test_a_cached_path_says_which_server_it_came_from(cache_dir):
    astrodoncel = callisto_cache.cache_path_for(_astrodoncel_url())
    ecallisto = callisto_cache.cache_path_for(_ecallisto_url())

    assert callisto_cache.source_id_for_path(astrodoncel) == ASTRODONCEL_SOURCE_ID
    assert callisto_cache.source_id_for_path(ecallisto) == DEFAULT_SOURCE_ID
    assert callisto_cache.source_id_for_path("C:/Users/me/fits/" + ANCHORAGE) == DEFAULT_SOURCE_ID
    assert callisto_cache.source_id_for_path("") == DEFAULT_SOURCE_ID
    assert callisto_cache.source_id_for_path("/x/callisto_cache/sources/unknown/" + ANCHORAGE) == DEFAULT_SOURCE_ID


# -----------------------------
# Download validation
# -----------------------------
def test_a_valid_transfer_stays_compressed_in_the_cache(cache_dir):
    payload = _gz_fits()

    path, was_cached = callisto_cache.fetch_cached(_astrodoncel_url(), session=_serving(_astrodoncel_url(), payload))

    assert was_cached is False
    assert path.read_bytes() == payload
    assert not list(cache_dir.rglob("*.part"))
    with fits.open(path, memmap=False) as hdul:
        assert hdul[0].data.shape == (2, 3)


def test_an_uncompressed_fits_transfer_is_accepted(cache_dir):
    name = "BIR_20261001_000000_01.fit"
    path, _ = callisto_cache.fetch_cached(
        _astrodoncel_url(name), session=_serving(_astrodoncel_url(name), _fits_payload())
    )
    assert path.exists()


@pytest.mark.parametrize("label,content", [
    ("html error page", b"<html><body>Not found</body></html>"),
    ("truncated gzip", _gz_fits()[:-20]),
    ("gzip of something else", gzip.compress(b"not a fits file at all")),
    ("corrupt gzip", b"\x1f\x8b\x08\x00" + b"\x00" * 40),
    ("truncated header", b"SIMPLE  =                    T" + b" " * 50),
])
def test_an_unusable_transfer_never_becomes_a_cache_entry(cache_dir, label, content):
    session = _serving(_astrodoncel_url(), content)

    with pytest.raises(callisto_cache.InvalidArchiveFile, match="Astrodoncel sent an unusable copy"):
        callisto_cache.fetch_cached(_astrodoncel_url(), session=session)

    assert callisto_cache.cached_file(_astrodoncel_url()) is None
    assert not list(cache_dir.rglob("*.part")), label


def test_a_short_transfer_is_rejected_by_its_content_length(cache_dir):
    payload = _gz_fits()
    headers = {"Content-Length": str(len(payload) + 10)}
    session = _Session({_astrodoncel_url(): _Response(content=payload, headers=headers)})

    with pytest.raises(callisto_cache.InvalidArchiveFile, match="received"):
        callisto_cache.fetch_cached(_astrodoncel_url(), session=session)
    assert callisto_cache.cached_file(_astrodoncel_url()) is None


def test_a_transfer_encoding_does_not_fail_the_length_check(cache_dir):
    payload = _gz_fits()
    headers = {"Content-Length": "5", "Content-Encoding": "gzip"}
    session = _Session({_astrodoncel_url(): _Response(content=payload, headers=headers)})

    path, _ = callisto_cache.fetch_cached(_astrodoncel_url(), session=session)
    assert path.exists()


def test_an_existing_entry_is_served_without_being_revalidated(cache_dir):
    target = callisto_cache.cache_path_for(_ecallisto_url())
    target.parent.mkdir(parents=True)
    target.write_bytes(b"entry from an older version")

    path, was_cached = callisto_cache.fetch_cached(_ecallisto_url(), session=_Session())

    assert was_cached is True and path == target


def test_a_cancelled_transfer_leaves_no_partial_file(cache_dir):
    class Cancelled(Exception):
        pass

    def cancel(_fraction, _message):
        raise Cancelled()

    session = _serving(_astrodoncel_url(), _gz_fits())
    with pytest.raises(Cancelled):
        callisto_cache.fetch_cached(_astrodoncel_url(), session=session, progress_cb=cancel)
    assert not list(cache_dir.rglob("*.part"))


def test_concurrent_requests_for_one_file_do_not_share_a_part_file(cache_dir):
    payload = _gz_fits()
    barrier = threading.Barrier(2, timeout=10)
    part_names = []

    class Racing(_Response):
        def iter_content(self, chunk_size=None):
            yield self.content[:10]
            # Both transfers are now writing; each must see only its own bytes.
            part_names.append(sorted(path.name for path in cache_dir.rglob("*.part")))
            barrier.wait()
            yield self.content[10:]

    class RacingSession(_Session):
        def get(self, url, params=None, **_kwargs):
            return Racing(content=payload)

    results, errors = [], []

    def run():
        try:
            results.append(callisto_cache.fetch_cached(_astrodoncel_url(), session=RacingSession()))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert not errors
    assert len(results) == 2
    target = callisto_cache.cache_path_for(_astrodoncel_url())
    assert target.read_bytes() == payload
    assert not list(cache_dir.rglob("*.part"))


def test_only_abandoned_part_files_are_removed(cache_dir):
    folder = cache_dir / "BIR" / "2026-10-01"
    folder.mkdir(parents=True)
    running = folder / "a.fit.gz.1.part"
    abandoned = folder / "b.fit.gz.2.part"
    running.write_bytes(b"x")
    abandoned.write_bytes(b"x")
    old = time.time() - callisto_cache._STALE_PART_SECONDS - 60
    os.utime(abandoned, (old, old))

    callisto_cache.enforce_cache_limit()

    assert running.exists() and not abandoned.exists()


# -----------------------------
# Timeline extension
# -----------------------------
def _segment_names(*clocks, station="ALASKA-ANCHORAGE", focus="01"):
    return [f"{station}_20261001_{clock}_{focus}.fit.gz" for clock in clocks]


def test_a_timeline_remembers_the_server_of_its_cached_files(cache_dir):
    name = _segment_names("000000")[0]

    astrodoncel = timeline.describe_timeline([callisto_cache.cache_path_for(_astrodoncel_url(name))])
    ecallisto = timeline.describe_timeline([callisto_cache.cache_path_for(_ecallisto_url(name))])
    local = timeline.describe_timeline([name])

    assert astrodoncel.source_id == ASTRODONCEL_SOURCE_ID
    assert ecallisto.source_id == DEFAULT_SOURCE_ID
    assert local.source_id == DEFAULT_SOURCE_ID


def test_extending_an_astrodoncel_observation_stays_on_astrodoncel(cache_dir):
    names = _segment_names("000000", "001503")
    payload = _gz_fits()
    responses = {**_listing(DAY, [_record(name) for name in names]), **_listing(date(2026, 9, 30), [])}
    responses.update(_listing(date(2026, 10, 2), []))
    responses[_astrodoncel_url(names[1])] = _Response(content=payload)
    session = _Session(responses)

    first_path = callisto_cache.cache_path_for(_astrodoncel_url(names[0]))
    first_path.parent.mkdir(parents=True)
    first_path.write_bytes(payload)
    state = timeline.describe_timeline([str(first_path)])

    available, reason = timeline.probe_segment(state, "next", session=session)
    assert available, reason
    paths = timeline.resolve_segment(state, "next", session=session)

    assert paths == [str(callisto_cache.cache_path_for(_astrodoncel_url(names[1])))]
    assert all(BASE_URL not in str(url) for url, _params in session.calls)

    extended = timeline.describe_timeline(timeline.extended_paths(state, paths, "next"))
    assert extended.source_id == ASTRODONCEL_SOURCE_ID
    assert len(extended.segments) == 2


def test_a_timeline_crosses_midnight_on_its_server(cache_dir):
    name = "BIR_20261001_234500_01.fit.gz"
    next_day = date(2026, 10, 2)
    following = "BIR_20261002_000002_01.fit.gz"
    responses = {
        **_listing(DAY, [_record(name)]),
        **_listing(next_day, [_record(following)]),
    }
    session = _Session(responses)
    path = callisto_cache.cache_path_for(_astrodoncel_url(name))
    state = timeline.describe_timeline([str(path)])

    [entry] = timeline.find_archive_segment(state, "next", session=session)

    assert entry.filename == following
    assert entry.url == f"{ASTRODONCEL_FILES}2026/10/02/{following}"


def test_day_listings_are_cached_per_server():
    session = _Session({
        f"{BASE_URL}2026/10/01/": _Response(text=_html("ONE_20261001_000000_01.fit.gz")),
        **_listing(DAY, [_record("TWO_20261001_000000_01.fit.gz")]),
    })

    ecallisto = timeline.list_archive_day(DAY, session=session)
    astrodoncel = timeline.list_archive_day(DAY, session=session, source_id=ASTRODONCEL_SOURCE_ID)
    timeline.list_archive_day(DAY, session=session, source_id=ASTRODONCEL_SOURCE_ID)

    assert [entry.station for entry in ecallisto] == ["ONE"]
    assert [entry.station for entry in astrodoncel] == ["TWO"]
    assert len(session.calls) == 2


def test_a_failing_server_makes_the_timeline_unavailable_with_its_name():
    session = _Session({(LISTING_URL, "2026/10/01"): _Response(text="<html>")})
    with pytest.raises(timeline.TimelineUnavailable, match="Astrodoncel"):
        timeline.list_archive_day(DAY, session=session, source_id=ASTRODONCEL_SOURCE_ID)

    broken = _Session({(LISTING_URL, "2026/10/01"): OSError("connection reset")})
    with pytest.raises(timeline.TimelineUnavailable, match="Could not reach the archive .Astrodoncel."):
        timeline.list_archive_day(DAY, session=broken, source_id=ASTRODONCEL_SOURCE_ID)


# -----------------------------
# Opt-in live checks
# -----------------------------
live = pytest.mark.skipif(
    not os.environ.get("E_CALLISTO_LIVE_ARCHIVE_TESTS"),
    reason="set E_CALLISTO_LIVE_ARCHIVE_TESTS=1 to read the real servers",
)


@live
@pytest.mark.parametrize("source_id", [DEFAULT_SOURCE_ID, ASTRODONCEL_SOURCE_ID])
def test_live_listing_finds_the_anchorage_file(source_id):
    entries = list_day(DAY, source_id)

    assert ANCHORAGE in [entry.filename for entry in entries]
    assert "ALASKA-ANCHORAGE" in {entry.station for entry in entries}


@live
def test_live_astrodoncel_file_downloads_validates_and_reads(cache_dir):
    from src.backend.radio.fits_io import load_callisto_fits

    entry = next(item for item in list_day(DAY, ASTRODONCEL_SOURCE_ID) if item.filename == ANCHORAGE)

    path, was_cached = callisto_cache.fetch_cached(entry.download_url, entry.filename)

    assert was_cached is False
    assert path.stat().st_size == entry.size_bytes
    assert callisto_cache.source_id_for_path(path) == ASTRODONCEL_SOURCE_ID
    assert load_callisto_fits(str(path), memmap=False).data.size


@live
def test_live_future_day_is_empty_on_both_servers():
    future = date(2099, 1, 1)

    assert list_day(future) == []
    assert list_day(future, ASTRODONCEL_SOURCE_ID) == []
