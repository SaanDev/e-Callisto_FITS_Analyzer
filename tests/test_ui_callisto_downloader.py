"""
e-CALLISTO FITS Analyzer
Version 2.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


from datetime import date

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("bs4")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from src.UI.callisto_downloader import FetchWorker, BASE_URL


def test_fetch_worker_day_url():
    worker = FetchWorker(date(2024, 1, 2), "test")
    assert worker._day_url() == f"{BASE_URL}2024/01/02/"


def test_fetch_worker_check_server_handles_status(monkeypatch):
    worker = FetchWorker(date(2024, 1, 2), "test")

    class FakeResponse:
        status_code = 500

    def fake_head(*_args, **_kwargs):
        return FakeResponse()

    monkeypatch.setattr("src.UI.callisto_downloader.requests.head", fake_head)

    ok, msg = worker._check_server()
    assert ok is False
    assert "HTTP" in msg
