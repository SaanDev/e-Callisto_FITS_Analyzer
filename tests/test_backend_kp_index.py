"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.Backend import kp_index as kp


SAMPLE_PAYLOAD = {
    "meta": {"source": "GFZ Potsdam", "license": "CC BY 4.0"},
    "datetime": [
        "2026-03-01T00:00:00Z",
        "2026-03-01T03:00:00Z",
        "2026-03-01T06:00:00Z",
    ],
    "Kp": [2.667, 5.333, 8.333],
    "status": ["pre", "pre", "pre"],
}


def test_kp_decimal_to_code_converts_edge_values():
    assert kp.kp_decimal_to_code(0.0) == "0o"
    assert kp.kp_decimal_to_code(0.333) == "0+"
    assert kp.kp_decimal_to_code(0.667) == "1-"
    assert kp.kp_decimal_to_code(2.667) == "3-"
    assert kp.kp_decimal_to_code(8.333) == "8+"
    assert kp.kp_decimal_to_code(9.0) == "9o"


def test_overlapping_kp_interval_bounds_snaps_to_overlapping_bins():
    start, end = kp.overlapping_kp_interval_bounds(
        datetime(2026, 2, 10, 1, 10, 0),
        datetime(2026, 2, 10, 4, 20, 0),
    )

    assert start == datetime(2026, 2, 10, 0, 0, 0)
    assert end == datetime(2026, 2, 10, 3, 0, 0)


def test_parse_kp_api_payload_reads_intervals_and_codes():
    result = kp.parse_kp_api_payload(SAMPLE_PAYLOAD)

    assert result.source_label == "GFZ Potsdam"
    assert result.interval_starts[0] == datetime(2026, 3, 1, 0, 0, 0)
    assert result.interval_ends[0] == datetime(2026, 3, 1, 3, 0, 0)
    assert result.kp_code == ("3-", "5+", "8+")
    assert result.status == ("pre", "pre", "pre")


def test_parse_kp_api_payload_allows_empty_arrays():
    result = kp.parse_kp_api_payload({"meta": {}, "datetime": [], "Kp": [], "status": []})
    assert result == kp.KpRangeData.empty()


def test_load_kp_range_raises_on_http_error():
    class FakeResponse:
        status_code = 500

        def raise_for_status(self):
            error = kp.requests.HTTPError("500 Server Error")
            error.response = self
            raise error

        def json(self):
            raise AssertionError("json() should not be called after raise_for_status()")

    class FakeSession:
        @staticmethod
        def get(url, params, timeout):
            assert url == kp.GFZ_KP_JSON_URL
            assert params["index"] == "Kp"
            assert timeout == 30
            return FakeResponse()

    with pytest.raises(kp.KpDataError, match="download GFZ Kp data"):
        kp.load_kp_range(datetime(2026, 3, 1, 0, 0, 0), datetime(2026, 3, 1, 3, 0, 0), session=FakeSession())


def test_load_kp_range_filters_to_requested_interval_bounds():
    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return SAMPLE_PAYLOAD

    class FakeSession:
        @staticmethod
        def get(url, params, timeout):
            assert params["start"] == "2026-03-01T03:00:00Z"
            assert params["end"] == "2026-03-01T03:00:00Z"
            return FakeResponse()

    result = kp.load_kp_range(
        datetime(2026, 3, 1, 3, 0, 0),
        datetime(2026, 3, 1, 5, 0, 0),
        session=FakeSession(),
    )

    assert result.interval_starts == (datetime(2026, 3, 1, 3, 0, 0),)
    assert result.kp_decimal == (5.333,)
    assert result.kp_code == ("5+",)
