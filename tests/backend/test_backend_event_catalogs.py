"""
e-CALLISTO FITS Analyzer
Tests for the event catalogs behind the event-context strip
(src/backend/space_weather/event_catalogs.py). Each parser reads a saved
fixture (tests/backend/fixtures/event_catalogs); nothing touches the network.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.backend.space_weather import event_catalogs as ec

FIXTURES = Path(__file__).parent / "fixtures" / "event_catalogs"


def _json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_hek_flare_rows_become_flare_events():
    events = ec.parse_hek_flare_rows(_json("hek_flares.json"))
    assert [event.label for event in events] == ["M1.3 (AR 13664)", "C9.8", "X3.9 (AR 13664)"]
    x = events[-1]
    assert x.kind == ec.KIND_FLARE and x.source == ec.SOURCE_GOES
    assert x.start == datetime(2024, 5, 10, 6, 27) and x.peak == datetime(2024, 5, 10, 6, 54)
    assert x.end == datetime(2024, 5, 10, 7, 6)
    assert x.details["class"] == "X3.9"
    assert ec.flare_class_color("X3.9") != ec.flare_class_color("M1.3") != ec.flare_class_color("C9.8")


def test_hek_rows_with_astropy_times_are_read():
    pytest.importorskip("astropy")
    from astropy.time import Time

    events = ec.parse_hek_flare_rows([{"event_starttime": Time("2024-05-10 06:27:00"), "fl_goescls": "X3.9"}])
    assert events[0].start == datetime(2024, 5, 10, 6, 27)


def test_cdaw_month_table_parses_every_day_when_no_day_is_given():
    html = (FIXTURES / "cdaw_univ2024_05.html").read_text(encoding="utf-8")
    rows = ec.parse_catalog_rows(html, "2024", "05")
    assert [row.timestamp for row in rows] == [
        datetime(2024, 5, 10, 7, 0, 5), datetime(2024, 5, 10, 19, 36, 5), datetime(2024, 5, 11, 1, 36, 5),
    ]
    one_day = ec.parse_catalog_rows(html, "2024", "05", "11")
    assert len(one_day) == 1
    halo = ec.cdaw_row_to_event(rows[0])
    assert halo.kind == ec.KIND_CME and halo.label == "CME 1353 km/s, halo"
    assert halo.details["halo"] is True and halo.details["speed_kms"] == 1353.0
    assert halo.url.startswith("https://cdaw.gsfc.nasa.gov/CME_list/")
    narrow = ec.cdaw_row_to_event(rows[1])
    assert narrow.label == "CME 512 km/s, 45°"


def test_cdaw_fetch_keeps_only_the_window(monkeypatch):
    html = (FIXTURES / "cdaw_univ2024_05.html").read_text(encoding="utf-8")

    class Response:
        status_code = 200
        text = html

    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: Response())
    events = ec.fetch_cdaw_cmes(datetime(2024, 5, 10, 12), datetime(2024, 5, 11, 0))
    assert [event.start for event in events] == [datetime(2024, 5, 10, 19, 36, 5)]


@pytest.mark.parametrize(
    "kind, expected_kind, first_start, first_label",
    [
        ("CME", ec.KIND_CME, datetime(2024, 5, 8, 5, 36), "CME 870 km/s, 86°"),
        ("FLR", ec.KIND_FLARE, datetime(2024, 5, 8, 1, 33), "X1.0"),
        ("IPS", ec.KIND_IPS, datetime(2024, 5, 10, 14, 0), "Shock at STEREO A"),
        ("GST", ec.KIND_GST, datetime(2024, 5, 10, 15, 0), "Storm Kp 9.0"),
        ("SEP", ec.KIND_SEP, datetime(2024, 5, 9, 13, 59), "SEP"),
    ],
)
def test_donki_records_parse(kind, expected_kind, first_start, first_label):
    events = ec.parse_donki(kind, _json(f"donki_{kind.lower()}.json"))
    assert events and events[0].kind == expected_kind and events[0].source == ec.SOURCE_DONKI
    assert events[0].start == first_start
    assert events[0].label == first_label
    assert events[0].url.startswith("https://ccmc.gsfc.nasa.gov/DONKI/view/")


def test_donki_ips_at_earth_links_back_to_its_cme():
    events = ec.parse_donki("IPS", _json("donki_ips.json"))
    earth = [event for event in events if event.details["location"] == "Earth"]
    assert earth and earth[0].start == datetime(2024, 5, 10, 16, 36)
    assert any("CME" in ident for ident in earth[0].details["linked"])


def test_donki_queries_the_new_ccmc_host(monkeypatch):
    seen = []

    class Response:
        status_code = 200
        text = "[]"

        def json(self):
            return []

    class Session:
        def get(self, url, params=None, **kwargs):
            seen.append((url, params))
            return Response()

    ec.fetch_donki(datetime(2024, 5, 8), datetime(2024, 5, 12, 23), kinds=("IPS",), session=Session())
    assert seen == [("https://ccmc.gsfc.nasa.gov/DONKI/WS/get/IPS", {"startDate": "2024-05-08", "endDate": "2024-05-12"})]


def test_burst_list_events_become_catalog_events():
    from src.backend.radio.burst_list import BurstEvent

    raw = [BurstEvent(datetime(2024, 5, 10, 6, 40), datetime(2024, 5, 10, 6, 52), "II", 25.0, 80.0, ("BIR", "ALASKA"), "",
                      "https://soleil.i4ds.ch/x.txt", "line")]
    events = ec.burst_events_to_catalog(raw)
    assert events[0].label == "Type II" and events[0].end == datetime(2024, 5, 10, 6, 52)
    assert events[0].details["stations"] == ["BIR", "ALASKA"]


def test_window_filtering_and_nearby_counts():
    def event(start, end=None):
        return ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, start, end=end, label="x")

    events = [
        event(datetime(2024, 5, 10, 2)),                                 # 4 h before: nearby
        event(datetime(2024, 5, 10, 5, 50), datetime(2024, 5, 10, 6, 5)),  # overlaps the start
        event(datetime(2024, 5, 10, 6, 30)),                             # inside
        event(datetime(2024, 5, 10, 14)),                                # 7 h after: too far
        event(datetime(2024, 5, 10, 9)),                                 # 2 h after: nearby
    ]
    start, end = datetime(2024, 5, 10, 6), datetime(2024, 5, 10, 7)
    inside = ec.events_in_window(events, start, end)
    assert [e.start.hour for e in inside] == [5, 6]
    assert ec.nearby_counts(events, start, end) == (1, 1)


def test_the_day_cache_is_used_and_recent_days_expire(tmp_path, monkeypatch):
    calls = []

    def fetcher(start, end, **kwargs):
        calls.append((start, end))
        return [ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, start + timedelta(hours=6), label="M1.0")]

    now = datetime(2024, 5, 20)
    window = (datetime(2024, 5, 10, 5), datetime(2024, 5, 11, 7))
    first = ec.load_catalog(ec.SOURCE_GOES, *window, cache_dir=tmp_path, fetcher=fetcher, now=now)
    assert len(calls) == 1  # both missing days in one contiguous request
    assert calls[0] == (datetime(2024, 5, 10), datetime(2024, 5, 11, 23, 59, 59))
    again = ec.load_catalog(ec.SOURCE_GOES, *window, cache_dir=tmp_path, fetcher=fetcher, now=now)
    assert len(calls) == 1 and [e.start for e in again] == [e.start for e in first]

    # Within the last 30 days a cached day is refetched once it is a day old.
    path = tmp_path / "event_catalogs" / ec.SOURCE_GOES / "2024-05-10.json"
    old = path.stat().st_mtime - 2 * 86400
    os.utime(path, (old, old))
    ec.load_catalog(ec.SOURCE_GOES, *window, cache_dir=tmp_path, fetcher=fetcher, now=now)
    assert len(calls) == 2 and calls[-1][0] == datetime(2024, 5, 10)

    # Older than 30 days: never refetched.
    os.utime(path, (old, old))
    ec.load_catalog(ec.SOURCE_GOES, *window, cache_dir=tmp_path, fetcher=fetcher, now=datetime(2025, 1, 1))
    assert len(calls) == 2


def test_one_failing_source_does_not_stop_the_others(tmp_path):
    def ok(start, end, **kwargs):
        return [ec.CatalogEvent(ec.KIND_CME, ec.SOURCE_CDAW, start + timedelta(hours=7), label="CME")]

    def broken(start, end, **kwargs):
        raise RuntimeError("HEK is down")

    result = ec.load_event_context(
        datetime(2024, 5, 10, 6), datetime(2024, 5, 10, 7), sources=(ec.SOURCE_GOES, ec.SOURCE_CDAW),
        cache_dir=tmp_path, fetchers={ec.SOURCE_GOES: broken, ec.SOURCE_CDAW: ok}, now=datetime(2024, 6, 1),
    )
    assert result.errors[ec.SOURCE_GOES] == "HEK is down"
    assert [e.label for e in result.events[ec.SOURCE_CDAW]] == ["CME"]
    assert result.start == datetime(2024, 5, 10, 0)  # queried with the 6 h margin
    back = ec.EventContextResult.from_json(json.loads(json.dumps(result.to_json())))
    assert back.events[ec.SOURCE_CDAW][0].start == result.events[ec.SOURCE_CDAW][0].start
