"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from datetime import date

import pytest

pytest.importorskip("requests")

from src.backend.radio.callisto_archive import BASE_URL, fetch_day_stations, stations_in_listing


def test_stations_in_listing_lists_each_station_once_in_case_insensitive_order():
    hrefs = [
        "UK-GLASGOW_20261001_000000_01.fit.gz",
        "BIR_20261001_000000_01.fit.gz",
        "BIR_20261001_001500_02.fit.gz",
        "Finland-Kempele_20261001_000000_01.fit.gz",
        "FINLAND-KEMPELE_20261001_001500_01.fit.gz",
        "Australia-ASSA_20261001_000000_57.fit.gz",
        "Malaysia_Banting_20261001_000000_01.fit",
        "BIR_20261001_000000.fit.gz",
    ]

    assert stations_in_listing(hrefs) == [
        "Australia-ASSA",
        "BIR",
        "Finland-Kempele",
        "Malaysia_Banting",
        "UK-GLASGOW",
    ]


class _Page:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Session:
    def __init__(self, page):
        self.page = page
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.page


def test_fetch_day_stations_reads_the_day_listing():
    session = _Session(_Page(200, '<a href="GREENLAND_20261001_000000_62.fit.gz">x</a>'))

    assert fetch_day_stations(date(2026, 10, 1), session) == ["GREENLAND"]
    assert session.urls == [f"{BASE_URL}2026/10/01/"]


def test_fetch_day_stations_treats_a_missing_day_as_empty():
    assert fetch_day_stations(date(2026, 10, 5), _Session(_Page(404))) == []


def test_fetch_day_stations_reports_server_errors():
    with pytest.raises(RuntimeError, match="HTTP 503"):
        fetch_day_stations(date(2026, 10, 1), _Session(_Page(503)))
