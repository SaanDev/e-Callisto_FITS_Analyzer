"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Station pickers list the stations the archive holds for the chosen dates.
"""

from datetime import date, datetime
import threading
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QCoreApplication, QDate, QDateTime, QEvent, QTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.backend.radio.burst_list import BurstEvent
from src.ui.downloads import archive_stations
from src.ui.downloads import callisto_downloader as downloader
from src.ui.radio import burst_list_tab as burst_ui


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def archive(monkeypatch):
    """Serve station lists per UTC day and record which days were read."""
    listings = {}
    calls = []
    lock = threading.Lock()

    def fetch(day):
        with lock:
            calls.append(day)
        result = listings.get(day, [])
        if isinstance(result, Exception):
            raise result
        return list(result)

    monkeypatch.setattr(archive_stations, "fetch_stations_for_day", fetch)
    monkeypatch.setattr(archive_stations, "LOOKUP_DELAY_MS", 0)
    archive_stations.clear_station_cache()
    return listings, calls


@pytest.fixture
def dialog(app, archive):
    widget = downloader.CallistoDownloaderApp()
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the station lookup")
        QTest.qWait(10)


def _texts(widget):
    return [widget.itemText(i) for i in range(widget.count())]


def _list_texts(widget):
    return [widget.item(i).text() for i in range(widget.count())]


def _typed(year, month, day, hour, minute):
    # A date-time edit keeps what the user typed as local wall-clock fields,
    # which the event workflow reads as UTC.
    return QDateTime(QDate(year, month, day), QTime(hour, minute))


def test_loader_reports_the_union_of_its_days_and_reuses_the_cache(app, archive):
    listings, calls = archive
    listings[date(2024, 1, 1)] = ["BIR", "GREENLAND"]
    listings[date(2024, 1, 2)] = ["bir", "Australia-ASSA"]
    loaded = []
    loader = archive_stations.ArchiveStationLoader()
    loader.loaded.connect(loaded.append)

    loader.request([date(2024, 1, 1), date(2024, 1, 2)])
    _wait_until(lambda: loaded)

    assert loaded == [["Australia-ASSA", "BIR", "GREENLAND"]]
    assert sorted(calls) == [date(2024, 1, 1), date(2024, 1, 2)]

    other = archive_stations.ArchiveStationLoader()
    other.loaded.connect(loaded.append)
    other.request([date(2024, 1, 2)])

    assert loaded[-1] == ["Australia-ASSA", "bir"]
    assert len(calls) == 2


def test_loader_reads_only_the_last_of_quickly_changed_days(app, archive):
    listings, calls = archive
    listings[date(2024, 1, 3)] = ["MRO"]
    loaded = []
    loader = archive_stations.ArchiveStationLoader(delay_ms=50)
    loader.loaded.connect(loaded.append)

    for day in (1, 2, 3):
        loader.request([date(2024, 1, day)])
    _wait_until(lambda: loaded)

    assert calls == [date(2024, 1, 3)]
    assert loaded == [["MRO"]]


def test_failed_lookup_offers_a_retry_that_reads_the_archive_again(app, archive):
    listings, calls = archive
    day = date(2024, 1, 2)
    listings[day] = ConnectionError("archive unreachable")
    loader = archive_stations.ArchiveStationLoader()
    label = archive_stations.StationAvailabilityLabel(loader)
    failures, loaded = [], []
    loader.failed.connect(failures.append)
    loader.loaded.connect(loaded.append)

    loader.request([day])
    _wait_until(lambda: failures)

    assert failures == ["archive unreachable"]
    assert loader.state() == "failed"
    assert 'href="retry"' in label.text()
    assert label.toolTip() == "archive unreachable"

    listings[day] = ["BIR"]
    label.linkActivated.emit("retry")
    _wait_until(lambda: loaded)

    assert loaded == [["BIR"]]
    assert calls == [day, day]
    assert label.summary() == "1 station has data on 2024-01-02 (UTC)."


def test_single_station_list_follows_the_date_and_keeps_the_users_station(dialog, archive):
    listings, _calls = archive
    listings[date(2024, 1, 1)] = ["BIR", "GREENLAND", "MRO"]
    listings[date(2024, 1, 3)] = ["UK-GLASGOW", "MRO", "BIR"]
    listings[date(2024, 1, 4)] = ["GREENLAND", "BIR"]
    combo = dialog.station_dropdown

    dialog.date_edit.setDate(QDate(2024, 1, 1))
    _wait_until(lambda: _texts(combo) == ["BIR", "GREENLAND", "MRO"])
    combo.setCurrentIndex(combo.findText("MRO"))
    combo.textActivated.emit("MRO")

    dialog.date_edit.setDate(QDate(2024, 1, 4))
    _wait_until(lambda: _texts(combo) == ["BIR", "GREENLAND"])
    assert combo.currentText() == "BIR"

    dialog.date_edit.setDate(QDate(2024, 1, 3))
    _wait_until(lambda: _texts(combo) == ["BIR", "MRO", "UK-GLASGOW"])
    assert combo.currentText() == "MRO"
    assert dialog.single_station_status.summary() == "3 stations have data on 2024-01-03 (UTC)."


def test_show_available_fits_needs_a_station_from_the_archive(dialog, archive, monkeypatch):
    messages, started = [], []
    monkeypatch.setattr(
        "src.ui.downloads.callisto_downloader.QMessageBox.information",
        lambda _parent, _title, text, *_args: messages.append(text),
    )
    monkeypatch.setattr(downloader, "FetchWorker", lambda *args: started.append(args))

    dialog.date_edit.setDate(QDate(2024, 1, 5))
    _wait_until(lambda: dialog.single_station_loader.state() == "loaded")
    dialog.show_available_fits()

    assert dialog.station_dropdown.count() == 0
    assert dialog.station_dropdown.placeholderText() == "No stations on this date"
    assert started == []
    assert messages == ["No station is selected. No station has published data for 2024-01-05 (UTC)."]


def test_spectral_overview_station_list_follows_its_date(dialog, archive):
    listings, _calls = archive
    listings[date(2024, 1, 2)] = ["SSRT", "BIR"]

    dialog.overview_date_edit.setDate(QDate(2024, 1, 2))
    _wait_until(lambda: _texts(dialog.overview_station_dropdown) == ["BIR", "SSRT"])

    assert dialog.overview_station_dropdown.currentText() == "BIR"
    assert dialog.overview_station_status.summary() == "2 stations have data on 2024-01-02 (UTC)."


def test_event_station_list_spans_the_window_and_keeps_checked_stations(dialog, archive):
    listings, _calls = archive
    listings[date(2024, 1, 1)] = ["BIR", "GREENLAND"]
    listings[date(2024, 1, 2)] = ["GREENLAND", "MRO"]
    listings[date(2024, 1, 3)] = ["MRO"]
    station_list = dialog.event_station_list

    dialog.event_start_dt_edit.setDateTime(_typed(2024, 1, 1, 23, 30))
    dialog.event_stop_dt_edit.setDateTime(_typed(2024, 1, 2, 0, 30))
    _wait_until(lambda: _list_texts(station_list) == ["BIR", "GREENLAND", "MRO"])
    assert dialog.event_station_status.summary() == "3 stations have data on 2024-01-01 to 2024-01-02 (UTC)."
    station_list.item(1).setCheckState(Qt.Checked)
    station_list.item(2).setCheckState(Qt.Checked)

    dialog.event_stop_dt_edit.setDateTime(_typed(2024, 1, 3, 1, 0))
    dialog.event_start_dt_edit.setDateTime(_typed(2024, 1, 3, 0, 0))
    _wait_until(lambda: _list_texts(station_list) == ["MRO"])
    assert dialog._checked_event_stations() == ["MRO"]

    dialog.event_start_dt_edit.setDateTime(_typed(2023, 12, 1, 0, 0))
    assert station_list.count() == 0
    assert "Narrow it to 7 days or fewer" in dialog.event_station_status.summary()

    dialog.event_start_dt_edit.setDateTime(_typed(2024, 1, 1, 23, 30))
    dialog.event_stop_dt_edit.setDateTime(_typed(2024, 1, 2, 0, 30))
    _wait_until(lambda: _list_texts(station_list) == ["BIR", "GREENLAND", "MRO"])
    assert dialog._checked_event_stations() == ["GREENLAND", "MRO"]


def test_burst_station_choices_add_the_archive_stations_of_the_burst_day(app, archive):
    listings, calls = archive
    listings[date(2024, 1, 2)] = ["Australia-ASSA", "bir", "MRO"]
    tab = burst_ui.BurstListTab()
    try:
        event = BurstEvent(
            start_utc=datetime(2024, 1, 2, 12, 2),
            end_utc=datetime(2024, 1, 2, 12, 4),
            burst_type="III",
            frequency_min_mhz=None,
            frequency_max_mhz=None,
            stations=("BIR", "GREENLAND"),
            remarks="",
            source_url="https://example.test/list.txt",
            raw_line="20240102 12:02-12:04 III BIR,GREENLAND",
        )
        tab.display_catalog({"events": [event]})
        tab.catalog_table.selectRow(0)
        tab.fits_station_combo.setEditText("CUSTOM")
        _wait_until(lambda: tab.fits_station_combo.count() == 5)

        assert _texts(tab.fits_station_combo) == [
            "All reported stations", "BIR", "GREENLAND", "Australia-ASSA", "MRO",
        ]
        assert tab.fits_station_combo.currentText() == "CUSTOM"
        assert calls == [date(2024, 1, 2)]
    finally:
        tab.close()
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
