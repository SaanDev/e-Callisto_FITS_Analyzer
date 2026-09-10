from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QThread, QTimer, Qt, Slot
from PySide6.QtWidgets import QApplication

from src.Backend.burst_list import BurstEvent
from src.Backend.callisto_archive import REQUEST_TIMEOUT, day_url
from src.UI import burst_fits
from src.UI.burst_fits import (
    BurstFitsWorker,
    burst_archive_dates,
    filter_burst_candidates,
    station_matches_burst,
)


def _event(start=None, stop=None):
    start = start or datetime(2024, 1, 2, 12)
    return SimpleNamespace(start_utc=start, end_utc=stop or start)


def _filter(names, *, start=None, stop=None, stations=None):
    event = _event(start, stop)
    return filter_burst_candidates(
        names,
        day_url=day_url(event.start_utc.date()),
        selected_stations=stations or ["GLASGOW"],
        start_dt=event.start_utc,
        stop_dt=event.end_utc,
    )


def test_filter_includes_files_starting_before_burst_and_entire_final_minute():
    names = [
        "GLASGOW_20240102_114500_01.fit.gz",  # Ends exactly at burst start.
        "GLASGOW_20240102_115500_01.fit.gz",
        "GLASGOW_20240102_120059_01.fit.gz",  # Within the catalog end minute.
        "GLASGOW_20240102_120100_01.fit.gz",
    ]
    result = _filter(names + [names[1], "invalid.fit.gz"])
    assert [item.filename for item in result] == names[1:3]
    assert result[0].station == "GLASGOW"
    assert result[0].receiver_id == "01"
    assert result[0].url == day_url(date(2024, 1, 2)) + names[1]


def test_filter_uses_explicit_stop_and_handles_stop_after_midnight():
    names = [
        "GLASGOW_20240102_115500_115900_01.fit.gz",  # Explicitly already ended.
        "GLASGOW_20240102_114000_120500_02.fits",  # Longer than nominal 15 min.
        "GLASGOW_20240102_115500_250000_03.fit",  # Invalid end clock.
    ]
    assert [item.filename for item in _filter(names)] == [names[1]]
    name = "GLASGOW_20240101_235000_000500_01.fit.gz"
    assert [item.filename for item in _filter([name], start=datetime(2024, 1, 2, 0, 2))] == [name]


def test_archive_dates_include_previous_day_and_final_catalog_minute():
    assert burst_archive_dates(datetime(2024, 1, 2, 0, 2), datetime(2024, 1, 2, 0, 3)) == [
        date(2024, 1, 1), date(2024, 1, 2)
    ]
    assert burst_archive_dates(datetime(2024, 1, 2, 23, 58), datetime(2024, 1, 2, 23, 59)) == [date(2024, 1, 1), date(2024, 1, 2)]
    assert burst_archive_dates(datetime(2024, 1, 2, 23, 59), datetime(2024, 1, 3, 0, 0)) == [
        date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)
    ]


def test_filter_normalizes_aware_datetimes_to_utc():
    cairo = timezone(timedelta(hours=2))
    names = ["GLASGOW_20240102_115500_01.fit.gz"]
    assert len(_filter(names, start=datetime(2024, 1, 2, 14, tzinfo=cairo))) == 1


@pytest.mark.parametrize(("archive", "selected", "matches"), [
    ("MEXICO-LANCE-A", "MEXICO-LANCE", True),
    ("MEXICO-LANCE-B", "MEXICO-LANCE", True),
    ("MEXICO-LANCE-B", "MEXICO-LANCE-A", False),
    ("MEXICO-LANCE-OTHER", "MEXICO-LANCE", False),
    ("GLASGOW", "UK-GLASGOW", True),
    ("UK-GLASGOW", "glasgow", True),
    ("GLASGOW", "GLSAGOW", True),
    ("ALASKA-HAARP", "ALASKA-HAAR", True),
    ("Malaysia-Banting", "Malaysia_Banting", True),
    ("SWISS-Landschlacht", " swiss-landsCHLacht ", True),
    ("SWISS-HB9SCT", "SWISS", False),
    ("SWISS-Landschlacht", "BLEN", False),
    ("GLASGOW", "", False),
])
def test_station_matching_is_exact_or_an_explicit_alias(archive, selected, matches):
    assert station_matches_burst(archive, selected) is matches


def test_filter_applies_catalog_station_aliases_and_preserves_archive_names():
    names = [
        "MEXICO-LANCE-A_20240102_115500_63.fit.gz",
        "MEXICO-LANCE-B_20240102_115500_62.fit.gz",
        "MEXICO-LANCE-OTHER_20240102_115500_01.fit.gz",
        "GLASGOW_20240102_115500_01.fit.gz",
    ]
    result = _filter(names, stations=["MEXICO-LANCE", "UK-GLASGOW"])
    assert [item.station for item in result] == ["GLASGOW", "MEXICO-LANCE-A", "MEXICO-LANCE-B"]


class _Response:
    def __init__(self, names=(), status=200):
        self.status_code = status
        self.text = "\n".join(f'<a href="{name}">{name}</a>' for name in names)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class _Session:
    def __init__(self, responses, on_get=None):
        self.responses = responses
        self.on_get = on_get
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def get(self, url, *, timeout):
        assert timeout == REQUEST_TIMEOUT
        self.calls.append(url)
        if self.on_get:
            self.on_get()
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def _run_worker(monkeypatch, worker, session):
    emitted = []
    monkeypatch.setattr(burst_fits, "build_archive_session", lambda: session)
    worker.finished.connect(emitted.append)
    worker.run()
    assert len(emitted) == 1
    return emitted[0]


def test_worker_handles_qt_events_and_runs_after_moving_to_a_thread(monkeypatch):
    app = QApplication.instance() or QApplication([])
    event = BurstEvent(
        start_utc=datetime(2024, 1, 2, 12),
        end_utc=datetime(2024, 1, 2, 12, 1),
        burst_type="III",
        frequency_min_mhz=None,
        frequency_max_mhz=None,
        stations=("GLASGOW",),
        remarks="",
        source_url="https://example.test/bursts.txt",
        raw_line="",
    )
    name = "GLASGOW_20240102_115500_01.fit.gz"
    execution_threads = []
    session = _Session(
        {day_url(date(2024, 1, 1)): _Response(), day_url(date(2024, 1, 2)): _Response([name])},
        on_get=lambda: execution_threads.append(QThread.currentThread()),
    )
    monkeypatch.setattr(burst_fits, "build_archive_session", lambda: session)
    emitted = []
    loop = QEventLoop()

    class Receiver(QObject):
        @Slot(object)
        def receive(self, payload):
            emitted.append(payload)
            loop.quit()

    receiver = Receiver()
    worker = BurstFitsWorker(event, ["GLASGOW"])
    thread = QThread()
    # Exercise Qt's real virtual-event dispatch, including the ThreadChange
    # event that exposed the QObject.event/catalog-event attribute collision.
    QCoreApplication.sendEvent(worker, QEvent(QEvent.Type.User))
    worker.moveToThread(thread)
    assert worker.burst_event is event
    assert worker.thread() is thread
    thread.started.connect(worker.run)
    worker.finished.connect(receiver.receive)
    worker.finished.connect(worker.deleteLater)
    worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    try:
        timeout.start(5000)
        thread.start()
        loop.exec()
    finally:
        timeout.stop()
        thread.quit()
        assert thread.wait(5000), "Burst FITS worker thread did not stop."
        thread.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    assert len(emitted) == 1
    assert [candidate.filename for candidate in emitted[0]["candidates"]] == [name]
    assert "error" not in emitted[0]
    assert execution_threads == [thread, thread]
    assert session.closed


def test_worker_resolves_previous_day_files_with_padding(monkeypatch):
    event = _event(datetime(2024, 1, 2, 0, 16))
    name = "GLASGOW_20240101_235800_01.fit.gz"
    response = _Response([name])
    session = _Session({day_url(date(2024, 1, 1)): response, day_url(date(2024, 1, 2)): _Response()})
    worker = BurstFitsWorker(event, ["UK-GLASGOW"], padding_minutes=5)
    progress = []
    worker.progress.connect(lambda *args: progress.append(args))
    result = _run_worker(monkeypatch, worker, session)
    assert [item.filename for item in result["candidates"]] == [name]
    assert result["warnings"] == []
    assert progress[-1][:2] == (2, 2)
    assert session.closed and response.closed


def test_worker_retains_results_and_reports_partial_archive_failure(monkeypatch):
    event = _event(datetime(2024, 1, 2, 0, 2))
    name = "GLASGOW_20240102_000000_01.fit.gz"
    session = _Session({
        day_url(date(2024, 1, 1)): _Response(status=503),
        day_url(date(2024, 1, 2)): _Response([name]),
    })
    result = _run_worker(monkeypatch, BurstFitsWorker(event, ["GLASGOW"]), session)
    assert [item.filename for item in result["candidates"]] == [name]
    assert "error" not in result
    assert len(result["warnings"]) == 1
    assert "2024-01-01" in result["warnings"][0]
    assert "HTTP 503" in result["warnings"][0]


def test_worker_reports_error_when_every_day_failed(monkeypatch):
    session = _Session({
        day_url(date(2024, 1, 1)): ConnectionError("offline"),
        day_url(date(2024, 1, 2)): ConnectionError("offline"),
    })
    result = _run_worker(monkeypatch, BurstFitsWorker(_event(), ["GLASGOW"]), session)
    assert result["candidates"] == []
    assert result["error"]
    assert "offline" in result["warnings"][0]


def test_worker_reports_unresolved_stations_without_substituting_other_sites(monkeypatch):
    session = _Session({
        day_url(date(2024, 1, 1)): _Response(),
        day_url(date(2024, 1, 2)): _Response(["SWISS-HB9SCT_20240102_115500_01.fit.gz"]),
    })
    result = _run_worker(monkeypatch, BurstFitsWorker(_event(), ["BLEN"]), session)
    assert result["candidates"] == []
    assert "BLEN" in result["warnings"][0]
    assert "error" not in result


def test_worker_discovers_long_explicit_interval_from_previous_day(monkeypatch):
    name = "GLASGOW_20240101_235000_013000_01.fit.gz"
    session = _Session({
        day_url(date(2024, 1, 1)): _Response([name]),
        day_url(date(2024, 1, 2)): _Response(),
    })
    worker = BurstFitsWorker(_event(datetime(2024, 1, 2, 1)), ["GLASGOW"])
    result = _run_worker(monkeypatch, worker, session)
    assert [item.filename for item in result["candidates"]] == [name]
    assert result["warnings"] == []


def test_worker_cancellation_before_start_does_not_access_archive(monkeypatch):
    worker = BurstFitsWorker(_event(), ["GLASGOW"])
    worker.request_cancel()
    session = _Session({})
    result = _run_worker(monkeypatch, worker, session)
    assert result == {"candidates": [], "warnings": [], "cancelled": True}
    assert session.calls == []


def test_worker_cancellation_after_request_stops_before_next_day(monkeypatch):
    worker = BurstFitsWorker(_event(datetime(2024, 1, 2, 0, 2)), ["GLASGOW"])
    response = _Response(["GLASGOW_20240101_235800_01.fit.gz"])
    session = _Session({day_url(date(2024, 1, 1)): response}, on_get=worker.request_cancel)
    result = _run_worker(monkeypatch, worker, session)
    assert result == {"candidates": [], "warnings": [], "cancelled": True}
    assert session.calls == [day_url(date(2024, 1, 1))]
    assert session.closed and response.closed


@pytest.mark.parametrize(("event", "stations", "padding", "error"), [
    (_event(), [], 0, "station"),
    (_event(), ["GLASGOW"], -1, "padding"),
    (_event(datetime(2024, 1, 2, 12), datetime(2024, 1, 2, 11)), ["GLASGOW"], 0, "end time"),
    (_event(datetime(2024, 1, 2, 12), datetime(2024, 1, 2, 11)), ["GLASGOW"], 120, "end time"),
])
def test_worker_validates_input_before_network(monkeypatch, event, stations, padding, error):
    session = _Session({})
    result = _run_worker(monkeypatch, BurstFitsWorker(event, stations, padding), session)
    assert error in result["error"]
    assert session.calls == []
