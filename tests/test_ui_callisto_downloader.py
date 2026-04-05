"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


from datetime import date

import pytest

pytest.importorskip("PySide6")
requests = pytest.importorskip("requests")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication, QSpinBox

from src.UI.callisto_downloader import (
    BASE_URL,
    CallistoDownloaderApp,
    FetchWorker,
    extract_fits_links,
)


def _app():
    return QApplication.instance() or QApplication([])


def test_fetch_worker_day_url():
    worker = FetchWorker(date(2024, 1, 2), "test")
    assert worker._day_url() == f"{BASE_URL}2024/01/02/"


def test_fetch_worker_uses_current_archive_host():
    assert BASE_URL == "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/"


def test_fetch_worker_check_server_handles_status():
    worker = FetchWorker(date(2024, 1, 2), "test")

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def close(self):
            pass

    class FakeClient:
        def head(self, *_args, **_kwargs):
            return FakeResponse(500)

        def get(self, *_args, **_kwargs):
            return FakeResponse(500)

    ok, msg = worker._check_server(FakeClient())
    assert ok is False
    assert "HTTP" in msg


def test_fetch_worker_check_server_falls_back_to_get_when_head_fails():
    worker = FetchWorker(date(2024, 1, 2), "test")

    class FakeResponse:
        status_code = 200

        def close(self):
            pass

    class FakeClient:
        def head(self, *_args, **_kwargs):
            raise requests.ConnectionError("reset by peer")

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    ok, msg = worker._check_server(FakeClient())
    assert ok is True
    assert msg == ""


def test_fetch_worker_run_uses_get_listing_when_head_probe_fails(monkeypatch):
    _app()
    worker = FetchWorker(date(2024, 1, 2), "ALASKA-ANCHORAGE")
    emitted = []

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def head(self, url, **_kwargs):
            assert url == BASE_URL
            raise requests.ConnectionError("reset by peer")

        def get(self, url, **_kwargs):
            if url == BASE_URL:
                return FakeResponse(200)
            if url == f"{BASE_URL}2024/01/02/":
                return FakeResponse(
                    200,
                    """
                    <a href="ALASKA-ANCHORAGE_20240102_000000_01.fit.gz">match</a>
                    <a href="OTHER-STATION_20240102_000000_01.fit.gz">ignore</a>
                    """,
                )
            raise AssertionError(url)

    monkeypatch.setattr("src.UI.callisto_downloader.build_archive_session", lambda: FakeSession())
    worker.finished.connect(lambda payload: emitted.append(payload))

    worker.run()

    assert emitted == [[
        (
            "ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
            f"{BASE_URL}2024/01/02/ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
        )
    ]]


def test_extract_fits_links_filters_and_deduplicates():
    html = """
    <html><body>
    <a href="?C=N;O=D">Sort</a>
    <a href="ALASKA-ANCHORAGE_20240102_000000_01.fit.gz">First</a>
    <a href='ALASKA-ANCHORAGE_20240102_001500_01.FIT'>Second</a>
    <a href="ALASKA-ANCHORAGE_20240102_000000_01.fit.gz#fragment">Duplicate</a>
    <a href="/solarradio/data/2002-20yy_Callisto/2024/01/02/readme.txt">Ignore</a>
    </body></html>
    """

    assert extract_fits_links(html) == [
        "ALASKA-ANCHORAGE_20240102_000000_01.fit.gz",
        "ALASKA-ANCHORAGE_20240102_001500_01.FIT",
    ]


def test_downloader_date_edit_shows_full_year():
    _app()
    dlg = CallistoDownloaderApp()
    year_edit = dlg.calendar_popup.findChild(QSpinBox, "qt_calendar_yearedit")

    assert dlg.date_edit.displayFormat() == "yyyy-MM-dd"
    assert dlg.date_edit.minimumWidth() >= 140
    assert year_edit is not None
    assert year_edit.minimumWidth() == 96
    assert year_edit.maximumWidth() == 96
    assert year_edit.minimumHeight() == 34

    dlg.close()
