"""
e-CALLISTO FITS Analyzer
Version 3.2.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

The downloader's Server selector: what it remembers, what a change resets, and
that every tab and worker keeps to the server it was started with.
"""

from datetime import date, datetime
import json
import threading
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QCoreApplication, QDate, QDateTime, QEvent, QSettings, QTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.backend.radio.burst_list import BurstEvent
from src.backend.radio.callisto_archive import ASTRODONCEL_SOURCE_ID, BASE_URL, DEFAULT_SOURCE_ID
from src.ui.downloads import archive_stations
from src.ui.downloads import callisto_downloader as downloader
from src.ui.radio.burst_fits import BurstFitsWorker

LISTING_URL = "https://astrodoncel.uah.es/dashboard/datos/getitems.php"
FILES_URL = "https://astrodoncel.uah.es/ecallistodata/"
DAY = date(2024, 1, 2)
OTHER_DAY = date(2024, 3, 4)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = str(tmp_path / "selector.ini")
    monkeypatch.setattr(downloader, "_make_settings", lambda: QSettings(path, QSettings.IniFormat))
    return path


@pytest.fixture
def archive(monkeypatch, settings_file):
    """Serve station lists per (source id, UTC day) and record what was read."""
    listings = {}
    calls = []
    lock = threading.Lock()

    def fetch(day, source_id=DEFAULT_SOURCE_ID):
        with lock:
            calls.append((source_id, day))
        result = listings.get((source_id, day), [])
        if isinstance(result, Exception):
            raise result
        return list(result)

    monkeypatch.setattr(archive_stations, "fetch_stations_for_day", fetch)
    monkeypatch.setattr(archive_stations, "LOOKUP_DELAY_MS", 0)
    archive_stations.clear_station_cache()
    return listings, calls


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        QTest.qWait(10)


def _make_dialog(app):
    return downloader.CallistoDownloaderApp()


def _dispose(app, widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.fixture
def dialog(app, archive):
    widget = _make_dialog(app)
    yield widget
    _dispose(app, widget)


def _texts(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def _list_texts(widget):
    return [widget.item(i).text() for i in range(widget.count())]


def _select(dialog, source_id):
    dialog.server_combo.setCurrentIndex(dialog.server_combo.findData(source_id))


def _candidate(source=FILES_URL, name="BIR_20240102_000000_01.fit.gz"):
    return downloader.CallistoEventCandidate(
        "BIR", datetime(2024, 1, 2), name, f"{source}2024/01/02/{name}", "01"
    )


def _burst_event():
    start = datetime(2024, 1, 2, 12, 0)
    return BurstEvent(
        start_utc=start, end_utc=start, burst_type="III", stations=("BIR",), remarks="",
        frequency_min_mhz=None, frequency_max_mhz=None, raw_line="line", source_url="url",
    )


# -----------------------------
# Choices and persistence
# -----------------------------
def test_the_selector_offers_exactly_the_two_servers_and_defaults_to_e_callisto(dialog):
    assert _texts(dialog.server_combo) == ["e-CALLISTO", "Astrodoncel"]
    assert dialog.server_combo.currentData() == DEFAULT_SOURCE_ID
    assert dialog.archive_source_id() == DEFAULT_SOURCE_ID
    assert dialog.server_retry_btn.isHidden()


def test_the_choice_survives_a_reopening(app, dialog, archive):
    _select(dialog, ASTRODONCEL_SOURCE_ID)

    reopened = _make_dialog(app)
    try:
        assert reopened.server_combo.currentData() == ASTRODONCEL_SOURCE_ID
        assert reopened.archive_source_id() == ASTRODONCEL_SOURCE_ID
    finally:
        _dispose(app, reopened)


def test_a_saved_server_applies_before_the_first_station_lookups(app, archive, settings_file):
    _listings, calls = archive
    settings = QSettings(settings_file, QSettings.IniFormat)
    settings.setValue(downloader.ARCHIVE_SOURCE_SETTING_KEY, ASTRODONCEL_SOURCE_ID)
    settings.sync()

    widget = _make_dialog(app)
    try:
        assert widget.single_station_loader.source_id() == ASTRODONCEL_SOURCE_ID
        _wait_until(lambda: widget.single_station_loader.state() == "loaded")
        assert calls and all(source_id == ASTRODONCEL_SOURCE_ID for source_id, _day in calls)
    finally:
        _dispose(app, widget)


def test_an_invalid_saved_value_falls_back_to_e_callisto(app, archive, settings_file):
    settings = QSettings(settings_file, QSettings.IniFormat)
    settings.setValue(downloader.ARCHIVE_SOURCE_SETTING_KEY, "some-old-server")
    settings.sync()

    widget = _make_dialog(app)
    try:
        assert widget.server_combo.currentData() == DEFAULT_SOURCE_ID
    finally:
        _dispose(app, widget)


def test_the_reopened_state_carries_the_server(dialog):
    _select(dialog, ASTRODONCEL_SOURCE_ID)
    state = dialog.date_time_state()
    assert state["archive_source"] == ASTRODONCEL_SOURCE_ID

    _select(dialog, DEFAULT_SOURCE_ID)
    dialog.restore_date_time_state(state)
    assert dialog.archive_source_id() == ASTRODONCEL_SOURCE_ID

    dialog.restore_date_time_state({"archive_source": "bogus"})
    assert dialog.archive_source_id() == DEFAULT_SOURCE_ID


# -----------------------------
# Switching
# -----------------------------
def test_switching_clears_results_keeps_dates_and_starts_no_search(dialog):
    dialog.date_edit.setDate(QDate(2024, 1, 2))
    dialog.overview_date_edit.setDate(QDate(2024, 3, 4))
    start = QDateTime(QDate(2024, 1, 2), QTime(1, 0))
    dialog.event_start_dt_edit.setDateTime(start)
    dialog.event_stop_dt_edit.setDateTime(start.addSecs(600))

    candidate = _candidate(BASE_URL)
    dialog.display_fetched_files([(candidate.filename, candidate.url)])
    dialog.selected_files.append(candidate.filename)
    dialog._event_candidates = [candidate]
    dialog._append_event_candidate_row(candidate)
    dialog._sync_event_actions()
    dialog.overview_focus_combo.addItem("01", "01")
    assert dialog.file_url_map and dialog.file_tree.topLevelItemCount() == 1

    _select(dialog, ASTRODONCEL_SOURCE_ID)

    assert dialog.file_tree.topLevelItemCount() == 0
    assert dialog.file_url_map == {}
    assert dialog.selected_files == []
    assert dialog._event_candidates == []
    assert dialog.event_results_table.rowCount() == 0
    assert not dialog.event_download_btn.isEnabled()
    assert dialog.overview_focus_combo.count() == 1
    assert dialog.overview_preview_tabs.count() == 1
    assert dialog._overview_result is None
    assert dialog.date_edit.date() == QDate(2024, 1, 2)
    assert dialog.overview_date_edit.date() == QDate(2024, 3, 4)
    assert dialog.event_start_dt_edit.dateTime() == start
    assert not dialog._archive_work_active()


def test_switching_reloads_stations_from_the_new_server_only(dialog, archive):
    listings, calls = archive
    listings[(DEFAULT_SOURCE_ID, DAY)] = ["ONE"]
    listings[(ASTRODONCEL_SOURCE_ID, DAY)] = ["TWO", "THREE"]
    dialog.date_edit.setDate(QDate(2024, 1, 2))
    _wait_until(lambda: _texts(dialog.station_dropdown) == ["ONE"])

    _select(dialog, ASTRODONCEL_SOURCE_ID)
    assert dialog.station_dropdown.count() == 0, "old stations must not linger while loading"
    _wait_until(lambda: _texts(dialog.station_dropdown) == ["THREE", "TWO"])

    assert (ASTRODONCEL_SOURCE_ID, DAY) in calls
    assert dialog.single_station_status.summary().startswith("Astrodoncel: 2 stations")


def test_a_selected_station_stays_only_where_the_new_server_has_it(dialog, archive):
    listings, _calls = archive
    listings[(DEFAULT_SOURCE_ID, DAY)] = ["ALPHA", "SHARED"]
    listings[(ASTRODONCEL_SOURCE_ID, DAY)] = ["BETA", "SHARED"]
    listings[(DEFAULT_SOURCE_ID, OTHER_DAY)] = ["ALPHA", "ONLY-HERE"]
    listings[(ASTRODONCEL_SOURCE_ID, OTHER_DAY)] = ["BETA", "SHARED"]
    dialog.date_edit.setDate(QDate(2024, 1, 2))
    _wait_until(lambda: dialog.station_dropdown.count() == 2)
    dialog.station_dropdown.setCurrentIndex(1)
    assert dialog.station_dropdown.currentText() == "SHARED"

    _select(dialog, ASTRODONCEL_SOURCE_ID)
    _wait_until(lambda: dialog.station_dropdown.count() == 2)
    assert dialog.station_dropdown.currentText() == "SHARED"

    _select(dialog, DEFAULT_SOURCE_ID)
    dialog.date_edit.setDate(QDate(2024, 3, 4))
    _wait_until(lambda: _texts(dialog.station_dropdown) == ["ALPHA", "ONLY-HERE"])
    dialog.station_dropdown.setCurrentIndex(1)

    _select(dialog, ASTRODONCEL_SOURCE_ID)
    _wait_until(lambda: _texts(dialog.station_dropdown) == ["BETA", "SHARED"])
    assert dialog.station_dropdown.currentText() == "BETA"


def test_only_checked_event_stations_the_new_server_has_stay_checked(dialog, archive):
    listings, _calls = archive
    start = QDateTime(QDate(2024, 1, 2), QTime(1, 0))
    listings[(DEFAULT_SOURCE_ID, DAY)] = ["KEPT", "LOST"]
    listings[(ASTRODONCEL_SOURCE_ID, DAY)] = ["KEPT", "NEW"]
    dialog.event_start_dt_edit.setDateTime(start)
    dialog.event_stop_dt_edit.setDateTime(start.addSecs(600))
    _wait_until(lambda: _list_texts(dialog.event_station_list) == ["KEPT", "LOST"])
    dialog.select_all_event_stations()
    assert dialog._checked_event_stations() == ["KEPT", "LOST"]

    _select(dialog, ASTRODONCEL_SOURCE_ID)
    _wait_until(lambda: _list_texts(dialog.event_station_list) == ["KEPT", "NEW"])

    assert dialog._checked_event_stations() == ["KEPT"]
    assert dialog._event_checked_station_keys == {"kept"}


def test_a_late_result_of_a_superseded_request_is_ignored(app, archive, monkeypatch):
    """Away and back while the first lookup hangs: only the newest request counts."""
    release_first = threading.Event()
    calls = []

    def fetch(day, source_id=DEFAULT_SOURCE_ID):
        calls.append((source_id, day))
        if source_id == DEFAULT_SOURCE_ID and calls.count((source_id, day)) == 1:
            release_first.wait(5)
            return ["STALE"]
        return ["FRESH-" + source_id]

    monkeypatch.setattr(archive_stations, "fetch_stations_for_day", fetch)
    archive_stations.clear_station_cache()
    loader = archive_stations.ArchiveStationLoader(delay_ms=0)
    reports = []
    loader.loaded.connect(lambda stations: reports.append((loader.source_id(), list(stations))))
    try:
        loader.request([DAY])
        _wait_until(lambda: calls == [(DEFAULT_SOURCE_ID, DAY)])

        loader.set_source(ASTRODONCEL_SOURCE_ID)
        _wait_until(lambda: reports == [(ASTRODONCEL_SOURCE_ID, ["FRESH-astrodoncel"])])

        loader.set_source(DEFAULT_SOURCE_ID)
        _wait_until(lambda: calls.count((DEFAULT_SOURCE_ID, DAY)) == 2)
        _wait_until(lambda: reports[-1] == (DEFAULT_SOURCE_ID, ["FRESH-ecallisto"]))

        release_first.set()
        QTest.qWait(150)
        assert reports[-1] == (DEFAULT_SOURCE_ID, ["FRESH-ecallisto"])
        assert ["STALE"] not in [stations for _source, stations in reports]
        assert loader.state() == "loaded"
    finally:
        release_first.set()
        loader.deleteLater()


def test_a_late_result_of_the_previous_server_never_reaches_a_loader(app, archive, monkeypatch):
    release = threading.Event()

    def fetch(day, source_id=DEFAULT_SOURCE_ID):
        if source_id == DEFAULT_SOURCE_ID:
            release.wait(5)
            return ["OLD"]
        return []

    monkeypatch.setattr(archive_stations, "fetch_stations_for_day", fetch)
    archive_stations.clear_station_cache()
    loader = archive_stations.ArchiveStationLoader(delay_ms=0)
    reports = []
    loader.loaded.connect(lambda stations: reports.append(list(stations)))
    try:
        loader.request([DAY])
        QTest.qWait(30)
        loader.set_source(ASTRODONCEL_SOURCE_ID)
        _wait_until(lambda: reports == [[]])

        release.set()
        QTest.qWait(150)
        assert reports == [[]]
    finally:
        release.set()
        loader.deleteLater()


def test_a_failed_lookup_offers_retry_and_names_the_server(dialog, archive):
    listings, calls = archive
    listings[(ASTRODONCEL_SOURCE_ID, DAY)] = RuntimeError("HTTP 503")
    dialog.date_edit.setDate(QDate(2024, 1, 2))
    _wait_until(lambda: dialog.single_station_loader.state() == "loaded")

    _select(dialog, ASTRODONCEL_SOURCE_ID)
    _wait_until(lambda: dialog.single_station_loader.state() == "failed")

    assert dialog.single_station_status.summary().startswith("Astrodoncel: could not load")
    assert "Retry" in dialog.single_station_status.text()
    assert dialog.server_combo.currentData() == ASTRODONCEL_SOURCE_ID, "never switches by itself"

    listings[(ASTRODONCEL_SOURCE_ID, DAY)] = ["BACK"]
    before = calls.count((ASTRODONCEL_SOURCE_ID, DAY))
    dialog.single_station_loader.reload()
    _wait_until(lambda: _texts(dialog.station_dropdown) == ["BACK"])
    assert calls.count((ASTRODONCEL_SOURCE_ID, DAY)) > before


# -----------------------------
# Busy state
# -----------------------------
class _Running:
    def isRunning(self):
        return True


@pytest.mark.parametrize("attribute", [
    "_fetch_thread", "_preview_thread", "_event_fetch_thread", "_overview_thread",
])
def test_the_selector_locks_while_a_worker_runs(dialog, attribute):
    setattr(dialog, attribute, _Running())
    try:
        dialog._sync_server_selector()
        assert not dialog.server_combo.isEnabled()

        _select(dialog, ASTRODONCEL_SOURCE_ID)
        assert dialog.archive_source_id() == DEFAULT_SOURCE_ID
        assert dialog.server_combo.currentData() == DEFAULT_SOURCE_ID
    finally:
        setattr(dialog, attribute, None)
    dialog._sync_server_selector()
    assert dialog.server_combo.isEnabled()


def test_the_selector_locks_during_downloads_until_they_finish(dialog, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)

    dialog._download_total, dialog._download_done = 2, 1
    dialog._sync_server_selector()
    assert not dialog.server_combo.isEnabled()
    dialog._download_done = 2
    dialog.show_download_complete_message()
    assert dialog.server_combo.isEnabled()

    dialog._event_download_total, dialog._event_download_done = 1, 0
    dialog._sync_server_selector()
    assert not dialog.server_combo.isEnabled()
    dialog._event_download_done = 1
    dialog.finish_event_download()
    assert dialog.server_combo.isEnabled()


def test_the_selector_follows_burst_list_work(dialog, monkeypatch):
    monkeypatch.setattr(dialog.burst_list_tab, "is_busy", lambda: True)
    dialog.burst_list_tab.busy_changed.emit(True)
    assert not dialog.server_combo.isEnabled()

    monkeypatch.setattr(dialog.burst_list_tab, "is_busy", lambda: False)
    dialog.burst_list_tab.busy_changed.emit(False)
    assert dialog.server_combo.isEnabled()


def test_a_real_search_locks_the_selector_and_unlocks_on_failure(dialog, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, text: messages.append(text))

    class _Fake(_FakeSession):
        def get(self, url, params=None, **_kwargs):
            return _FakeResponse(status=503)

    monkeypatch.setattr(downloader, "build_archive_session", lambda: _Fake())
    _select(dialog, ASTRODONCEL_SOURCE_ID)
    _wait_until(lambda: dialog.single_station_loader.state() == "loaded")
    dialog.station_dropdown.addItem("BIR")
    dialog.station_dropdown.setCurrentIndex(0)

    dialog.show_available_fits()
    assert not dialog.server_combo.isEnabled()
    _wait_until(lambda: dialog._fetch_thread is None)

    assert dialog.server_combo.isEnabled()
    assert messages and "Astrodoncel" in messages[0]
    assert not dialog.server_retry_btn.isHidden()
    assert dialog.server_combo.currentData() == ASTRODONCEL_SOURCE_ID


# -----------------------------
# Failure and retry
# -----------------------------
def test_a_failure_offers_retry_and_changing_server_withdraws_it(dialog):
    retried = []
    dialog._report_server_failure("Astrodoncel failed.\nDetails here", lambda: retried.append(True))

    assert dialog.server_failure_label.text() == "Astrodoncel failed."
    assert "Details here" in dialog.server_failure_label.toolTip()
    assert not dialog.server_retry_btn.isHidden()

    dialog.server_retry_btn.click()
    assert retried == [True]
    assert dialog.server_retry_btn.isHidden()

    dialog._report_server_failure("Astrodoncel failed.", lambda: retried.append(True))
    _select(dialog, ASTRODONCEL_SOURCE_ID)
    assert dialog.server_retry_btn.isHidden()
    dialog.server_retry_btn.click()
    assert retried == [True]


def test_retry_waits_for_running_work(dialog):
    retried = []
    dialog._report_server_failure("failed", lambda: retried.append(True))
    dialog._fetch_thread = _Running()
    try:
        dialog._retry_after_server_failure()
        assert retried == []
        assert not dialog.server_retry_btn.isHidden()
    finally:
        dialog._fetch_thread = None


def test_burst_list_failures_reach_the_retry_row(dialog):
    dialog.burst_list_tab.display_fits(
        {"candidates": [], "warnings": ["2024-01-02: HTTP 503"], "server_error": True,
         "error": "Unable to read the Astrodoncel FITS archive for the selected burst."}
    )

    assert "Astrodoncel" in dialog.server_failure_label.text()
    assert not dialog.server_retry_btn.isHidden()


def test_a_burst_error_that_is_not_the_servers_offers_no_retry(dialog):
    dialog.burst_list_tab.display_fits({"candidates": [], "warnings": [], "error": "Bad padding"})

    assert dialog.server_retry_btn.isHidden()


# -----------------------------
# Workers against an Astrodoncel fixture
# -----------------------------
class _FakeResponse:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        pass


class _FakeSession:
    def __init__(self, listings=None):
        self.listings = listings or {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        pass

    def head(self, url, **_kwargs):
        raise AssertionError(f"this server has no root to probe: {url}")

    def get(self, url, params=None, **_kwargs):
        self.calls.append((url, params))
        assert url == LISTING_URL, f"unexpected request: {url}"
        result = self.listings.get(params["path"], _FakeResponse(text="[]"))
        if isinstance(result, Exception):
            raise result
        return result


def _listing(*names):
    records = [{"name": name, "isDirectory": False, "size": 10} for name in names]
    return _FakeResponse(text=json.dumps(records))


@pytest.fixture
def fake_server(monkeypatch):
    def install(listings):
        session = _FakeSession(listings)
        monkeypatch.setattr(downloader, "build_archive_session", lambda: session)
        monkeypatch.setattr("src.ui.radio.burst_fits.build_archive_session", lambda: session)
        return session

    return install


def test_single_station_worker_lists_the_captured_server(fake_server):
    session = fake_server({"2024/01/02": _listing("BIR_20240102_000000_01.fit.gz", "OTHER_20240102_000000_01.fit.gz")})
    worker = downloader.FetchWorker(DAY, "BIR", source_id=ASTRODONCEL_SOURCE_ID)
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert worker._day_url() == f"{FILES_URL}2024/01/02/"
    assert session.calls == [(LISTING_URL, {"path": "2024/01/02"})]
    assert emitted == [[("BIR_20240102_000000_01.fit.gz", f"{FILES_URL}2024/01/02/BIR_20240102_000000_01.fit.gz")]]


@pytest.mark.parametrize("response,marker", [
    (_FakeResponse(text="<html>"), "__FETCH_ERROR__"),
    (_FakeResponse(status=403), "__FETCH_ERROR__"),
])
def test_single_station_worker_reports_a_bad_listing_as_an_error(fake_server, response, marker):
    fake_server({"2024/01/02": response})
    worker = downloader.FetchWorker(DAY, "BIR", source_id=ASTRODONCEL_SOURCE_ID)
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert emitted[0][0][0] == marker


def test_single_station_worker_reports_an_empty_day_as_empty(fake_server):
    fake_server({"2024/01/02": _FakeResponse(text="[]")})
    worker = downloader.FetchWorker(DAY, "BIR", source_id=ASTRODONCEL_SOURCE_ID)
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert emitted == [[]]


def test_single_station_worker_reports_a_timeout_as_unreachable(fake_server):
    import requests

    fake_server({"2024/01/02": requests.Timeout("timed out")})
    worker = downloader.FetchWorker(DAY, "BIR", source_id=ASTRODONCEL_SOURCE_ID)
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert emitted[0][0][0] == "__SERVER_UNREACHABLE__"


def test_event_worker_crosses_midnight_and_keeps_nonzero_seconds(fake_server):
    fake_server({
        "2024/01/01": _listing("BIR_20240101_234503_01.fit.gz", "BIR_20240101_120000_01.fit.gz"),
        "2024/01/02": _listing("BIR_20240102_000002_059.fit.gz", "BIR_20240102_120000_01.fit.gz"),
    })
    worker = downloader.EventFetchWorker(
        datetime(2024, 1, 1, 23, 30), datetime(2024, 1, 2, 0, 30), ["BIR"], source_id=ASTRODONCEL_SOURCE_ID,
    )
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    candidates = emitted[0]["candidates"]
    assert [candidate.filename for candidate in candidates] == [
        "BIR_20240101_234503_01.fit.gz", "BIR_20240102_000002_059.fit.gz",
    ]
    assert [candidate.receiver_id for candidate in candidates] == ["01", "059"]
    assert {candidate.source_id for candidate in candidates} == {ASTRODONCEL_SOURCE_ID}
    assert candidates[1].url == f"{FILES_URL}2024/01/02/BIR_20240102_000002_059.fit.gz"


def test_event_worker_makes_all_days_failing_an_error_naming_the_server(fake_server):
    fake_server({"2024/01/02": _FakeResponse(text="not json")})
    worker = downloader.EventFetchWorker(
        datetime(2024, 1, 2), datetime(2024, 1, 2, 1), ["BIR"], source_id=ASTRODONCEL_SOURCE_ID,
    )
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert "Could not read Astrodoncel" in emitted[0]["error"]


def test_event_worker_finds_nothing_on_an_empty_day_without_an_error(fake_server):
    fake_server({})
    worker = downloader.EventFetchWorker(
        datetime(2024, 1, 2), datetime(2024, 1, 2, 1), ["BIR"], source_id=ASTRODONCEL_SOURCE_ID,
    )
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert not emitted[0].get("error")
    assert emitted[0]["candidates"] == []
    assert emitted[0]["missing_stations"] == ["BIR"]


def test_overview_worker_reports_a_failing_server_for_retry(fake_server):
    fake_server({"2024/01/02": _FakeResponse(status=500)})
    worker = downloader.SpectralOverviewWorker(DAY, "BIR", source_id=ASTRODONCEL_SOURCE_ID)
    server_failures, failures = [], []
    worker.serverFailed.connect(server_failures.append)
    worker.failed.connect(failures.append)

    worker.run()

    assert len(server_failures) == 1 and "Astrodoncel" in server_failures[0]
    assert failures == server_failures


def test_overview_worker_treats_an_empty_day_as_no_data_not_a_server_failure(fake_server):
    fake_server({})
    worker = downloader.SpectralOverviewWorker(DAY, "BIR", source_id=ASTRODONCEL_SOURCE_ID)
    server_failures, failures = [], []
    worker.serverFailed.connect(server_failures.append)
    worker.failed.connect(failures.append)

    worker.run()

    assert server_failures == []
    assert "No FITS files were found" in failures[0]


def test_burst_worker_searches_the_captured_server(fake_server):
    session = fake_server({"2024/01/02": _listing("BIR_20240102_115500_01.fit.gz")})
    worker = BurstFitsWorker(_burst_event(), ["BIR"], source_id=ASTRODONCEL_SOURCE_ID)
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert {params["path"] for _url, params in session.calls} == {"2024/01/01", "2024/01/02"}
    [candidate] = emitted[0]["candidates"]
    assert candidate.source_id == ASTRODONCEL_SOURCE_ID
    assert candidate.url == f"{FILES_URL}2024/01/02/BIR_20240102_115500_01.fit.gz"


def test_burst_worker_reports_an_unreadable_server(fake_server):
    fake_server({
        "2024/01/01": _FakeResponse(status=503),
        "2024/01/02": _FakeResponse(text="oops"),
    })
    worker = BurstFitsWorker(_burst_event(), ["BIR"], source_id=ASTRODONCEL_SOURCE_ID)
    emitted = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert emitted[0]["server_error"] is True
    assert "Astrodoncel" in emitted[0]["error"]


# -----------------------------
# Provenance and the Burst List tab
# -----------------------------
def test_results_keep_the_server_and_url_they_came_from(dialog):
    _select(dialog, ASTRODONCEL_SOURCE_ID)
    candidate = _candidate()

    dialog.display_fetched_files([(candidate.filename, candidate.url)])

    [listed] = [
        dialog.file_tree.topLevelItem(0).child(row).data(0, Qt.UserRole)
        for row in range(dialog.file_tree.topLevelItem(0).childCount())
    ]
    assert listed.source_id == ASTRODONCEL_SOURCE_ID
    assert listed.url == candidate.url
    assert "Astrodoncel" in dialog.status_label.text()


def test_import_carries_the_astrodoncel_url(dialog):
    candidate = _candidate()
    dialog._append_event_candidate_row(candidate)
    requests_seen = []
    dialog.import_request.connect(requests_seen.append)

    dialog.import_selected_event_files()

    assert requests_seen == [[candidate.url]]


def test_an_open_preview_keeps_naming_its_server(dialog, app):
    panel = {
        "title": "Focus 01",
        "subtitle": "x",
        "data": [[0.0, 1.0], [2.0, 3.0]],
        "freqs": [10.0, 20.0],
        "time": [0.0, 1.0],
    }
    import numpy as np

    panel["data"] = np.array(panel["data"])
    window = downloader.PreviewWindow([panel], "Focus 01", parent=dialog, source_label="Astrodoncel")
    try:
        assert "Astrodoncel" in window.windowTitle()
        _select(dialog, ASTRODONCEL_SOURCE_ID)
        _select(dialog, DEFAULT_SOURCE_ID)
        assert "Astrodoncel" in window.windowTitle()
    finally:
        window.close()


def test_burst_catalog_stays_and_find_uses_the_selected_server(dialog, monkeypatch):
    tab = dialog.burst_list_tab
    tab.display_catalog({"events": [_burst_event()], "warnings": []})
    tab.catalog_table.selectRow(0)
    tab.display_fits({"candidates": [_candidate(BASE_URL)], "warnings": []})
    assert tab.fits_table.rowCount() == 1

    _select(dialog, ASTRODONCEL_SOURCE_ID)

    assert tab.catalog_table.rowCount() == 1, "the catalog has its own fixed source"
    assert tab.selected_event() is not None
    assert tab.fits_table.rowCount() == 0, "FITS results of the old server are gone"
    assert tab.fits_station_combo.currentData() == "__reported__"

    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda worker, receiver: started.append(worker))
    tab.find_fits()
    assert started[0].source_id == ASTRODONCEL_SOURCE_ID
    assert "Astrodoncel" in tab.fits_status.text()


def test_burst_keeps_a_chosen_station_when_the_new_server_lists_it(dialog, archive):
    listings, _calls = archive
    listings[(DEFAULT_SOURCE_ID, DAY)] = ["SHARED"]
    listings[(ASTRODONCEL_SOURCE_ID, DAY)] = ["SHARED", "EXTRA"]
    tab = dialog.burst_list_tab
    tab.display_catalog({"events": [_burst_event()], "warnings": []})
    tab.catalog_table.selectRow(0)
    _wait_until(lambda: tab.fits_station_combo.findText("SHARED") >= 0)
    tab.fits_station_combo.setCurrentIndex(tab.fits_station_combo.findText("SHARED"))

    _select(dialog, ASTRODONCEL_SOURCE_ID)
    _wait_until(lambda: tab.fits_station_combo.findText("EXTRA") >= 0)

    assert tab.fits_station_combo.currentText() == "SHARED"
