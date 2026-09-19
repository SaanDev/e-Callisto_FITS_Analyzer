"""Offline regression coverage for each published Monstein catalog format."""

from datetime import date, datetime

import pytest
import requests

from src.backend.radio import burst_list as catalog
from src.backend.radio.burst_list import (
    BURST_LIST_BASE_URL,
    BurstListCancelled,
    BurstListError,
    fetch_burst_events,
    filter_burst_events,
    parse_burst_list,
)


def test_parse_modern_preserves_types_stations_source_and_qualifications():
    line = "20260901\t11:41-11:43\tIII/1\tBIR, (GLASGOW), [GERMANY-DLR], BIR\tweak group"
    events = parse_burst_list("#Date Time Type Stations\n" + line, source_url="source.txt")

    event, = events
    assert event.start_utc == datetime(2026, 9, 1, 11, 41)
    assert event.end_utc == datetime(2026, 9, 1, 11, 43)
    assert event.start_utc.tzinfo is None
    assert event.burst_type == "III/1"
    assert event.stations == ("BIR", "GLASGOW", "GERMANY-DLR")
    assert event.frequency_min_mhz is event.frequency_max_mhz is None
    assert "(GLASGOW)" in event.remarks and "[GERMANY-DLR]" in event.remarks
    assert "weak group" in event.remarks
    assert event.source_url == "source.txt"
    assert event.raw_line == line


def test_parse_modern_omits_no_event_rows_notices_and_invalid_times():
    events = parse_burst_list("""
Product: e-CALLISTO_2026_09.txt
#Date Time Type Stations
20260901  ##:##-##:##
20260902  00:00-23:59 REM Observer reports delayed
20260903  24:03-24:04 III BIR
20260904  12:60-13:00 III BIR
20260931  12:00-13:00 III BIR
20260905  03:00-03:00 --- BIR, GLASGOW
20260905  03:00-03:00 --- BIR, GLASGOW
() barely visible
""", source_url="source.txt")

    event, = events
    assert event.start_utc == event.end_utc == datetime(2026, 9, 5, 3)
    assert event.burst_type == "---"
    assert "No burst classification" in event.remarks


@pytest.mark.parametrize("end", ["00:03", "24:00"])
def test_parse_midnight_and_end_of_day(end):
    event, = parse_burst_list(f"20261231 23:58-{end} II BIR", source_url="test")
    assert event.end_utc.date() == date(2027, 1, 1)
    assert event.end_utc > event.start_utc


def test_parse_2010_2011_legacy_uses_event_time_not_observing_hours():
    events = parse_burst_list("""
100101  0801 1457   BLEN
100102  0801 1458   BLEN   0856.3    0858.2    III      G     1      180     416
100102              BLEN   1412.3    1413.5    DCIM     C     1     1378X   1514X
110101  0801 1457   BLEN   0945.5    1420.0      I           1      214     309
""", source_url="SGD_BLEN_2010_01.txt")

    assert len(events) == 3
    assert events[0].start_utc == datetime(2010, 1, 2, 8, 56, 18)
    assert events[0].end_utc == datetime(2010, 1, 2, 8, 58, 12)
    assert events[0].frequency_min_mhz == 180
    assert events[0].frequency_max_mhz == 416
    assert events[0].stations == ("BLEN",)
    assert "G 1" in events[0].remarks
    assert events[1].burst_type == "DCIM"
    assert events[1].frequency_min_mhz == 1378
    assert "1514X" in events[1].remarks
    assert events[2].start_utc == datetime(2011, 1, 1, 9, 45, 30)


def test_parse_2014_celestina_without_type_column():
    event, = parse_burst_list("""
# Product: 20141101-20141130
# Produced by CELESTINA
#Date \t\tTime \t\tStations
20141101\t10:22-10:25\tBLENSW, DARO-HF, GLASGOW, HUMAIN
""", source_url="20141101-20141130_nhit2.txt")
    assert event.burst_type == "---"
    assert event.stations == ("BLENSW", "DARO-HF", "GLASGOW", "HUMAIN")
    assert event.frequency_min_mhz is None


def test_unknown_new_classification_is_retained():
    event, = parse_burst_list("20260901\t12:00-12:01\tNEW-TYPE\tBIR", source_url="test")
    assert event.burst_type == "NEW-TYPE"
    assert event.stations == ("BIR",)


@pytest.fixture
def typed_events():
    return parse_burst_list("\n".join(
        f"20260901 12:0{index}-12:0{index} {burst_type} BIR, GLASGOW"
        for index, burst_type in enumerate(["I", "II/2", "III/1", "IIIG/2", "IIIGG/3", "IV", "---"])
    ), source_url="source")


@pytest.mark.parametrize("query,expected", [
    ("I", ["I"]), ("II", ["II/2"]), ("III", ["III/1", "IIIG/2", "IIIGG/3"]),
    ("II/2", ["II/2"]), ("IIIG", ["IIIG/2"]), ("Unclassified", ["---"]),
])
def test_type_filter_matches_exact_families(typed_events, query, expected):
    assert [event.burst_type for event in filter_burst_events(typed_events, burst_type=query)] == expected


def test_filter_combines_station_text_and_frequency_overlap(typed_events):
    assert len(filter_burst_events(typed_events, station="glasgow", text="unclassified")) == 0
    assert len(filter_burst_events(typed_events, station="glasgow", text="No burst classification")) == 1
    legacy = parse_burst_list("100102 BLEN 0856.3 0858.2 III G 1 180 416", source_url="legacy")
    events = typed_events + legacy
    assert filter_burst_events(events, frequency_min_mhz=200, frequency_max_mhz=300) == legacy
    assert filter_burst_events(events, frequency_min_mhz=500) == []
    assert filter_burst_events(events, frequency_max_mhz=150) == []
    with pytest.raises(ValueError, match="Minimum frequency"):
        filter_burst_events(events, frequency_min_mhz=300, frequency_max_mhz=200)


class Response:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


class Session:
    def __init__(self, pages):
        self.pages = {BURST_LIST_BASE_URL + path: response for path, response in pages.items()}
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        assert kwargs["timeout"] == catalog.REQUEST_TIMEOUT
        self.calls.append(url)
        response = self.pages.get(url, Response(status=404))
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def listing(*names):
    return Response("\n".join(f'<a href="{name}">{name}</a>' for name in names))


def install_session(monkeypatch, pages):
    session = Session(pages)
    monkeypatch.setattr(catalog, "build_archive_session", lambda: session)
    return session


def test_fetch_only_requested_months_filters_dates_and_retains_notices(monkeypatch):
    session = install_session(monkeypatch, {
        "": listing("2026/"),
        "2026/": listing("e-CALLISTO_2026_08.txt", "e-CALLISTO_2026_09.txt"),
        "2026/e-CALLISTO_2026_09.txt": Response("""
#Date Time Type Stations
# Produced by deARCE_v3
20260901 12:00-12:01 III BIR
20260902 12:00-12:01 II/2 BIR, GLASGOW
20260903 00:00-23:59 REM Reports may be delayed
20260904 12:00-12:01 III BIR
"""),
    })
    progress = []
    result = fetch_burst_events(date(2026, 9, 2), date(2026, 9, 3), progress=lambda *args: progress.append(args))

    assert len(result.events) == 1
    assert result.events[0].burst_type == "II/2"
    assert any("Reports may be delayed" in warning for warning in result.warnings)
    assert "Produced by deARCE" in next(iter(result.source_notes.values()))
    assert len(session.calls) == 3
    assert progress[-1][:2] == (1, 1)
    assert session.closed


def test_fetch_missing_catalog_periods_are_warnings_not_network_errors(monkeypatch):
    install_session(monkeypatch, {"": listing("2010/", "2011/", "2014/", "2020/")})
    result = fetch_burst_events(date(2012, 1, 1), date(2013, 12, 31))
    assert result.events == []
    assert any("2012, 2013" in warning for warning in result.warnings)
    assert any("2012–2019" in warning for warning in result.warnings)


def test_fetch_missing_months_and_published_404_are_reported(monkeypatch):
    session = install_session(monkeypatch, {
        "": listing("2026/"), "2026/": listing("e-CALLISTO_2026_09.txt"),
    })
    result = fetch_burst_events(date(2026, 9, 1), date(2026, 10, 31))
    assert result.events == []
    assert any("month(s): 10" in warning for warning in result.warnings)
    assert any("unavailable" in warning for warning in result.warnings)
    assert session.closed


def test_fetch_prefers_2014_nhit2_and_preserves_detection_provenance(monkeypatch):
    session = install_session(monkeypatch, {
        "": listing("2014/"),
        "2014/": listing("20141101-20141130_nhit3.txt", "20141101-20141130_nhit2.txt"),
        "2014/20141101-20141130_nhit2.txt": Response("20141101\t08:22-08:22\tBLENSW, RWANDA"),
    })
    result = fetch_burst_events(date(2014, 11, 1), date(2014, 11, 30))
    assert len(result.events) == 1
    assert result.events[0].stations == ("BLENSW", "RWANDA")
    assert result.events[0].source_url.endswith("nhit2.txt")
    assert any("2 coincident stations" in warning for warning in result.warnings)
    assert not any("nhit3" in url for url in session.calls)


@pytest.mark.parametrize("failure", [Response(status=503), requests.ConnectionError("offline")])
def test_fetch_network_failure_is_error_and_always_closes_session(monkeypatch, failure):
    session = install_session(monkeypatch, {"": failure})
    with pytest.raises(BurstListError, match="Could not"):
        fetch_burst_events(date(2026, 9, 1), date(2026, 9, 2))
    assert session.closed


def test_fetch_midway_network_failure_does_not_report_partial_data_as_success(monkeypatch):
    session = install_session(monkeypatch, {
        "": listing("2026/"),
        "2026/": listing("e-CALLISTO_2026_08.txt", "e-CALLISTO_2026_09.txt"),
        "2026/e-CALLISTO_2026_08.txt": Response("20260801 12:00-12:01 III BIR"),
        "2026/e-CALLISTO_2026_09.txt": requests.Timeout("timed out"),
    })
    with pytest.raises(BurstListError, match="timed out"):
        fetch_burst_events(date(2026, 8, 1), date(2026, 9, 2))
    assert session.closed


def test_fetch_skips_invalid_records_and_preserves_source_date_typos(monkeypatch):
    install_session(monkeypatch, {
        "": listing("2021/"), "2021/": listing("e-CALLISTO_2021_06.txt"),
        "2021/e-CALLISTO_2021_06.txt": Response("""
#Date Time Type Stations
20220601 00:34-00:35 III Australia-ASSA
20210602 29:00-29:05 III BIR
20210603 12:00-12:01 III BIR
"""),
    })
    result = fetch_burst_events(date(2021, 6, 1), date(2021, 6, 30))
    assert len(result.events) == 1
    assert result.events[0].start_utc.day == 3
    assert any("invalid record" in warning for warning in result.warnings)
    assert any("dates outside" in warning for warning in result.warnings)


def test_fetch_cancellation_stops_before_next_request_and_closes_session(monkeypatch):
    session = install_session(monkeypatch, {
        "": listing("2026/"), "2026/": listing("e-CALLISTO_2026_09.txt"),
    })
    with pytest.raises(BurstListCancelled):
        fetch_burst_events(date(2026, 9, 1), date(2026, 9, 2), cancelled=lambda: len(session.calls) == 1)
    assert session.calls == [BURST_LIST_BASE_URL]
    assert session.closed


def test_fetch_validates_date_range_before_network_access(monkeypatch):
    monkeypatch.setattr(catalog, "build_archive_session", lambda: pytest.fail("Should not access network"))
    with pytest.raises(ValueError, match="Start date"):
        fetch_burst_events(date(2026, 10, 1), date(2026, 9, 1))


def test_fetch_html_response_is_not_mistaken_for_empty_event_list(monkeypatch):
    install_session(monkeypatch, {
        "": listing("2026/"), "2026/": listing("e-CALLISTO_2026_09.txt"),
        "2026/e-CALLISTO_2026_09.txt": Response("<html><title>Login required</title></html>"),
    })
    with pytest.raises(BurstListError, match="Unrecognized"):
        fetch_burst_events(date(2026, 9, 1), date(2026, 9, 2))


def test_fetch_no_bursts_observed_is_valid_empty_catalog(monkeypatch):
    install_session(monkeypatch, {
        "": listing("2020/"), "2020/": listing("e-CALLISTO_2020_01.txt"),
        "2020/e-CALLISTO_2020_01.txt": Response("No bursts observed"),
    })
    result = fetch_burst_events(date(2020, 1, 1), date(2020, 1, 31))
    assert result.events == []
    assert result.warnings == []


def test_fetch_ignores_off_site_or_parent_directory_links(monkeypatch):
    session = install_session(monkeypatch, {
        "": listing("2026/", "../2025/", "https://example.org/2027/"),
        "2026/": listing("https://example.org/e-CALLISTO_2026_09.txt", "../2025/e-CALLISTO_2025_09.txt"),
    })
    result = fetch_burst_events(date(2026, 9, 1), date(2026, 9, 2))
    assert len(session.calls) == 2
    assert result.events == []
    assert any("month(s): 09" in warning for warning in result.warnings)
