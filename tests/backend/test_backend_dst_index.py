"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.backend.space_weather import dst_index as dst


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


def test_fetch_month_data_falls_back_when_kyoto_forbids_a_moved_realtime_month():
    # A real-time month promoted to the provisional archive answers 403.
    class FakeResponse:
        def __init__(self, url: str, status_code: int, text: str):
            self.url = url
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                error = dst.requests.HTTPError(f"{self.status_code} Client Error: Forbidden for url: {self.url}")
                error.response = self
                raise error

    class FakeSession:
        def get(self, url, timeout):
            if "dst_realtime" in url:
                return FakeResponse(url, 403, "Forbidden")
            return FakeResponse(url, 200, REALTIME_HTML)

    month = dst.fetch_month_data(2026, 4, session=FakeSession(), use_cache=False)

    assert month.source_label == "Provisional"


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


def _month(year, month, label, values):
    return dst.DstMonthData(
        year=year,
        month=month,
        source_label=label,
        updated_at_utc=None,
        timestamps=tuple(datetime(year, month, 1, hour) for hour in range(len(values))),
        values_nt=tuple(values),
    )


def test_load_dst_range_accepts_timezone_aware_datetimes(monkeypatch):
    from datetime import timedelta, timezone

    monkeypatch.setattr(dst, "fetch_month_data", lambda year, month, **_k: _month(year, month, dst.ARCHIVE_FINAL, [-5, -6, -7]))
    aware_start = datetime(2020, 1, 1, 1, 0, tzinfo=timezone.utc)
    # 02:00 in UTC+1 is 01:00 UTC; it must be converted, not compared naively.
    aware_end = datetime(2020, 1, 1, 3, 0, tzinfo=timezone(timedelta(hours=1)))
    times, values, sources = dst.load_dst_range(aware_start, aware_end)
    assert list(times) == [datetime(2020, 1, 1, 1, 0), datetime(2020, 1, 1, 2, 0)]
    assert list(values) == [-6.0, -7.0]


def test_fill_hours_become_nan(monkeypatch):
    import math

    monkeypatch.setattr(dst, "fetch_month_data", lambda year, month, **_k: _month(year, month, dst.ARCHIVE_REALTIME, [-20, 9999, -25]))
    times, values, sources = dst.load_dst_samples(datetime(2026, 2, 1, 0), datetime(2026, 2, 1, 2))
    assert values[0] == -20.0 and math.isnan(values[1]) and values[2] == -25.0
    assert sources == ("Real-time", "Real-time", "Real-time")


def test_live_months_expire_from_the_cache_but_final_ones_do_not(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(dst.time, "monotonic", lambda: clock["t"])
    dst.clear_month_cache()
    fetched = []

    class Response:
        status_code = 200
        text = REALTIME_HTML

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, timeout):
            fetched.append(url)
            return Response()

    dst.fetch_month_data(2026, 2, session=Session())
    dst.fetch_month_data(2026, 2, session=Session())
    assert len(fetched) == 1  # cached within the TTL
    clock["t"] += dst.LIVE_MONTH_TTL_S + 1
    dst.fetch_month_data(2026, 2, session=Session())
    assert len(fetched) == 2  # a real-time month is re-read after the TTL

    dst.clear_month_cache()
    fetched.clear()
    dst.fetch_month_data(2019, 2, session=Session())
    clock["t"] += 10 * dst.LIVE_MONTH_TTL_S
    dst.fetch_month_data(2019, 2, session=Session())
    assert len(fetched) == 1  # final data never change
    dst.clear_month_cache()
