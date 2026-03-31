"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.Backend import dst_index as dst


REALTIME_HTML = """
<html>
<body>
<span style="position:absolute; right:0em;">[Updated at 2026-03-30 15:05UT]</span>
<pre class="data">
                                      WDC for Geomagnetism, Kyoto
                                Hourly Equatorial Dst Values (REAL-TIME)
                                           FEBRUARY   2026
DAY
 1  -20 -16 -12 -10  -8 -10 -12 -11   -7  -6  -6  -6  -8 -14 -20 -24  -26 -24 -25 -23 -18 -13 -16 -20
 2  -17 -19 -19 -15 -11 -11 -12 -11   -5  -5  -9  -9  -7 -10 -13 -16  -14  -9  -6  -3   0   2   0  -1
</pre>
</body>
</html>
"""


def test_preferred_archives_follow_current_kyoto_split():
    assert [item.label for item in dst.preferred_archives_for_month(2020, 12)] == ["Final"]
    assert [item.label for item in dst.preferred_archives_for_month(2025, 6)] == ["Provisional", "Real-time"]
    assert [item.label for item in dst.preferred_archives_for_month(2025, 7)] == ["Real-time", "Provisional"]


def test_parse_dst_html_page_reads_hourly_rows_and_update_time():
    month = dst.parse_dst_html_page(2026, 2, REALTIME_HTML, dst.ARCHIVE_REALTIME)

    assert month.source_label == "Real-time"
    assert month.updated_at_utc == "2026-03-30 15:05UT"
    assert len(month.timestamps) == 48
    assert month.timestamps[0] == datetime(2026, 2, 1, 0, 0, 0)
    assert month.timestamps[23] == datetime(2026, 2, 1, 23, 0, 0)
    assert month.values_nt[:5] == (-20, -16, -12, -10, -8)
    assert month.values_nt[-1] == -1


def test_fetch_month_data_falls_back_when_primary_archive_is_missing():
    urls = []

    class FakeResponse:
        def __init__(self, url: str, status_code: int, text: str):
            self.url = url
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                error = dst.requests.HTTPError(f"{self.status_code} for {self.url}")
                error.response = self
                raise error

    class FakeSession:
        def get(self, url, timeout):
            urls.append((url, timeout))
            if "dst_realtime" in url:
                return FakeResponse(url, 404, "missing")
            return FakeResponse(url, 200, REALTIME_HTML)

    month = dst.fetch_month_data(2025, 7, session=FakeSession(), use_cache=False)

    assert month.source_label == "Provisional"
    assert len(urls) == 2
    assert "dst_realtime" in urls[0][0]
    assert "dst_provisional" in urls[1][0]


def test_load_dst_range_filters_samples_across_month_boundaries(monkeypatch):
    feb = dst.DstMonthData(
        year=2026,
        month=2,
        source_label=dst.ARCHIVE_REALTIME,
        updated_at_utc=None,
        timestamps=(
            datetime(2026, 2, 28, 22, 0, 0),
            datetime(2026, 2, 28, 23, 0, 0),
        ),
        values_nt=(-10, -20),
    )
    mar = dst.DstMonthData(
        year=2026,
        month=3,
        source_label=dst.ARCHIVE_REALTIME,
        updated_at_utc=None,
        timestamps=(
            datetime(2026, 3, 1, 0, 0, 0),
            datetime(2026, 3, 1, 1, 0, 0),
        ),
        values_nt=(-30, -40),
    )

    def fake_fetch(year, month, **_kwargs):
        if (year, month) == (2026, 2):
            return feb
        if (year, month) == (2026, 3):
            return mar
        raise AssertionError(f"unexpected month {(year, month)}")

    monkeypatch.setattr(dst, "fetch_month_data", fake_fetch)

    times, values, sources = dst.load_dst_range(
        datetime(2026, 2, 28, 23, 0, 0),
        datetime(2026, 3, 1, 0, 0, 0),
    )

    assert list(times) == [datetime(2026, 2, 28, 23, 0, 0), datetime(2026, 3, 1, 0, 0, 0)]
    assert list(values) == [-20.0, -30.0]
    assert sources == (dst.ARCHIVE_REALTIME,)
