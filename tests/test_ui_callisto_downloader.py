"""
e-CALLISTO FITS Analyzer
Version 2.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""


from datetime import date

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
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
