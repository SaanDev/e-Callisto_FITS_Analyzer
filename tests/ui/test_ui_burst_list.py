"""Offline regressions for catalog browsing and the shared FITS workflow."""

from datetime import date, datetime, timezone
from threading import Event
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("requests")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QCoreApplication, QDate, QEvent, QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from src.backend.radio.burst_list import BurstEvent, BurstListCancelled, BurstListResult
from src.backend.radio.callisto_archive import day_url
from src.ui.radio import burst_fits
from src.ui.radio import burst_list_tab as burst_ui
from src.ui.downloads import callisto_downloader as downloader


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def offline_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader.callisto_cache, "_app_data_root", lambda: tmp_path)


@pytest.fixture
def tab(app):
    widget = burst_ui.BurstListTab(["BIR", "GREENLAND", "AUSTRALIA-ASSA"])
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.fixture
def dialog(app):
    widget = downloader.CallistoDownloaderApp()
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def _event(hour=12, burst_type="III", stations=("BIR", "GREENLAND"), remarks=""):
    return BurstEvent(
        start_utc=datetime(2024, 1, 2, hour, 2),
        end_utc=datetime(2024, 1, 2, hour, 4),
        burst_type=burst_type,
        frequency_min_mhz=None,
        frequency_max_mhz=None,
        stations=stations,
        remarks=remarks,
        source_url=burst_ui.CATALOG_URL + "2024/e-CALLISTO_2024_01.txt",
        raw_line=f"20240102 {hour:02}:02-{hour:02}:04 {burst_type} {','.join(stations)}",
    )


def _candidate(station="BIR", receiver="01"):
    name = f"{station}_20240102_120000_{receiver}.fit.gz"
    return downloader.CallistoEventCandidate(
        station=station,
        observed_at_utc=datetime(2024, 1, 2, 12),
        filename=name,
        url="https://example.test/" + name,
        receiver_id=receiver,
    )


def _select_event(tab, event=None):
    event = event or _event()
    tab.display_catalog({"events": [event]})
    tab.catalog_table.selectRow(0)
    assert tab.selected_event() == event
    return event


def _visible_events(tab):
    return [tab.catalog_table.item(row, 0).data(Qt.UserRole)
            for row in range(tab.catalog_table.rowCount())]


def test_burst_tab_follows_overview_and_dates_survive_reopen(dialog):
    labels = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
    assert labels == ["Single Station", "Multi-Station Event", "Spectral Overview", "Burst List"]
    tab = dialog.burst_list_tab
    tab.start_date_edit.setDate(QDate(2024, 1, 1))
    tab.end_date_edit.setDate(QDate(2024, 2, 3))
    dialog.tabs.setCurrentWidget(tab)
    state = dialog.date_time_state()
    assert state["burst_start_date"] == QDate(2024, 1, 1)
    assert state["burst_end_date"] == QDate(2024, 2, 3)

    tab.start_date_edit.setDate(QDate(2025, 1, 1))
    tab.end_date_edit.setDate(QDate(2025, 2, 3))
    dialog.tabs.setCurrentIndex(0)
    dialog.restore_date_time_state(state)

    assert tab.date_time_state() == {
        "burst_start_date": QDate(2024, 1, 1), "burst_end_date": QDate(2024, 2, 3),
    }
    assert dialog.tabs.currentWidget() is tab
    assert tab.start_date_edit.displayFormat() == "yyyy-MM-dd"
    assert tab.start_date_edit.minimumDate() == QDate(2010, 1, 1)


def test_overview_disables_burst_tab_until_work_finishes(dialog):
    tab_index = dialog.tabs.indexOf(dialog.burst_list_tab)
    dialog._set_overview_running(True)
    assert not dialog.tabs.isTabEnabled(tab_index)
    assert not dialog.burst_list_tab.load_button.isEnabled()
    dialog._set_overview_running(False)
    assert dialog.tabs.isTabEnabled(tab_index)
    assert dialog.burst_list_tab.load_button.isEnabled()


def test_burst_request_prevents_overview_until_worker_cleanup(dialog, monkeypatch):
    tab = dialog.burst_list_tab
    thread, worker = Mock(), Mock()
    monkeypatch.setattr(burst_ui, "QThread", lambda parent: thread)
    overview_worker = Mock()
    monkeypatch.setattr(downloader, "SpectralOverviewWorker", overview_worker)
    dialog.tabs.setCurrentWidget(tab)

    tab._start_worker(worker, tab.display_catalog)
    try:
        assert tab.is_busy()
        assert dialog.tabs.isTabEnabled(dialog.tabs.indexOf(tab))
        assert all(not dialog.tabs.isTabEnabled(index)
                   for index in range(dialog.tabs.count()) if dialog.tabs.widget(index) is not tab)
        dialog.generate_spectral_overview()
        overview_worker.assert_not_called()
        assert dialog._overview_thread is None
    finally:
        tab._worker_finished()

    assert all(dialog.tabs.isTabEnabled(index) for index in range(dialog.tabs.count()))
    assert not tab.is_busy()
    thread.deleteLater.assert_called_once()


def test_initial_range_uses_utc_calendar_day(app, monkeypatch):
    class FixedDateTime:
        @staticmethod
        def now(tz):
            assert tz is timezone.utc
            return datetime(2024, 3, 1, 0, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(burst_ui, "datetime", FixedDateTime)
    widget = burst_ui.BurstListTab([])
    try:
        assert widget.end_date_edit.date() == QDate(2024, 3, 1)
        assert widget.start_date_edit.date() == QDate(2024, 1, 31)
    finally:
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_filters_combine_type_family_station_count_and_text(tab):
    chosen = _event(10, "II", ("BIR", "GREENLAND"), "Harmonic structure")
    events = [chosen, _event(11, "III"), _event(12, "II", ("BIR",)),
              _event(13, "II", ("GREENLAND", "AUSTRALIA-ASSA")),
              _event(14, "II", ("BIR", "GREENLAND"), "Weak burst")]
    tab.display_catalog({"events": events})
    tab.type_filter.setCurrentIndex(tab.type_filter.findData("II"))
    assert _visible_events(tab) == [events[index] for index in (0, 2, 3, 4)]
    tab.station_filter.setText("bir")
    assert _visible_events(tab) == [events[index] for index in (0, 2, 4)]
    tab.min_stations.setValue(2)
    assert _visible_events(tab) == [chosen, events[4]]
    tab.text_filter.setText("HARMONIC")
    assert _visible_events(tab) == [chosen]
    assert "1 of 5" in tab.catalog_status.text()
    tab.text_filter.setText("no match")
    assert tab.catalog_table.rowCount() == 0
    assert not tab.find_button.isEnabled()


def test_reset_filters_restores_loaded_events_and_keeps_the_date_range(tab):
    tab.restore_date_time_state({"burst_start_date": QDate(2024, 1, 1), "burst_end_date": QDate(2024, 1, 31)})
    dates = tab.date_time_state()
    chosen = _event(10, "II", ("BIR", "GREENLAND"), "Harmonic")
    events = [chosen, _event(12, "III", ("GREENLAND",))]
    tab.display_catalog({"events": events})
    tab.type_filter.setCurrentIndex(tab.type_filter.findData("II"))
    tab.station_filter.setText("BIR")
    tab.min_stations.setValue(2)
    tab.text_filter.setText("Harmonic")
    tab.catalog_table.selectRow(0)
    tab.display_fits({"candidates": [_candidate()]})
    assert _visible_events(tab) == [chosen]

    tab.reset_filters_button.click()

    assert _visible_events(tab) == events
    assert tab.date_time_state() == dates
    assert tab.type_filter.currentData() == ""
    assert tab.station_filter.text() == tab.text_filter.text() == ""
    assert tab.min_stations.value() == 0
    assert tab.selected_files() == []
    assert not tab.preview_button.isEnabled()


def test_unusual_catalog_type_is_available_as_filter(tab):
    event = _event(burst_type="DCIM")
    tab.display_catalog({"events": [event, _event(13)]})
    index = tab.type_filter.findData("DCIM")
    assert index >= 0
    tab.type_filter.setCurrentIndex(index)
    assert _visible_events(tab) == [event]


def test_sorting_keeps_event_identity_and_station_count_is_numeric(tab):
    early = _event(10, stations=("BIR", "GREENLAND"))
    late = _event(15, stations=tuple(f"SITE-{index}" for index in range(10)))
    tab.display_catalog({"events": [late, early]})
    tab.catalog_table.selectRow(0)
    assert tab.selected_event() == early

    tab.catalog_table.sortItems(0, Qt.DescendingOrder)
    assert _visible_events(tab) == [late, early]
    assert tab.selected_event() == early
    tab.catalog_table.sortItems(3, Qt.AscendingOrder)
    assert _visible_events(tab) == [early, late]
    assert tab.selected_event() == early


@pytest.mark.parametrize("station_choice,expected", [
    (None, ["BIR", "GREENLAND"]),
    ("GREENLAND", ["GREENLAND"]),
    ("  CUSTOM-OBSERVATORY  ", ["CUSTOM-OBSERVATORY"]),
])
def test_find_fits_receives_selected_event_station_and_padding(tab, monkeypatch, station_choice, expected):
    event = _select_event(tab)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda worker, receiver: started.append((worker, receiver)))
    if station_choice is not None:
        tab.fits_station_combo.setEditText(station_choice)
    tab.padding_spin.setValue(7)
    tab.find_fits()

    worker, receiver = started[0]
    assert isinstance(worker, burst_ui.BurstFitsWorker)
    assert worker.burst_event == event
    assert worker.stations == expected
    assert worker.padding_minutes == 7
    assert receiver == tab.display_fits


def test_find_fits_runs_real_qthread_and_keeps_search_progress_visible(tab, app, monkeypatch):
    event = _select_event(tab, _event(stations=("BIR",)))
    candidate = _candidate()
    entered, release = Event(), Event()
    calls, ran_on_gui_thread, idle, busy_changes = [], [], [], []

    class Response:
        status_code = 200

        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **_kwargs):
            calls.append(url)
            ran_on_gui_thread.append(QThread.currentThread() is app.thread())
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test did not release FITS search")
            html = f'<a href="{candidate.filename}">{candidate.filename}</a>' if len(calls) == 2 else ""
            return Response(html)

    monkeypatch.setattr(burst_fits, "build_archive_session", Session)
    tab.idle.connect(lambda: idle.append(True))
    tab.busy_changed.connect(busy_changes.append)
    tab.find_button.click()
    try:
        assert entered.wait(2)
        QTest.qWait(10)
        assert isinstance(tab._worker, burst_ui.BurstFitsWorker)
        assert tab._worker.burst_event is event
        assert tab._worker.thread() is tab._thread
        assert tab.is_busy()
        assert not tab.progress_bar.isHidden()
        assert tab.progress_bar.maximum() == 2
        assert tab.progress_detail.text() == "0%"
        assert "Reading FITS archive" in tab.activity_label.text()
        assert tab.cancel_button.isEnabled()
        assert not tab.find_button.isEnabled()
    finally:
        release.set()
        for _ in range(300):
            QTest.qWait(10)
            if not tab.is_busy():
                break

    assert not tab.is_busy()
    assert calls == [day_url(date(2024, 1, 1)), day_url(date(2024, 1, 2))]
    assert ran_on_gui_thread == [False, False]
    assert [item.filename for item in tab.selected_files()] == [candidate.filename]
    assert idle == [True]
    assert busy_changes == [True, False]
    assert not tab.progress_bar.isHidden()
    assert tab.progress_bar.value() == tab.progress_bar.maximum() == 100
    assert tab.progress_detail.text() == "100%"
    assert tab.activity_label.text() == "FITS search complete."
    assert tab.preview_button.isEnabled()


@pytest.mark.parametrize("target", ["catalog", "fits", "preview"])
def test_worker_thread_start_failure_recovers_actions_and_reports_error(tab, monkeypatch, target):
    _select_event(tab)
    thread, worker = Mock(), Mock()
    worker.moveToThread.side_effect = TypeError("Qt rejected the worker")
    monkeypatch.setattr(burst_ui, "QThread", lambda parent: thread)
    busy_changes = []
    tab.busy_changed.connect(busy_changes.append)

    tab._start_worker(worker, getattr(tab, f"display_{target}"))

    assert not tab.is_busy()
    assert tab._worker is tab._thread is None
    assert tab.load_button.isEnabled()
    assert tab.find_button.isEnabled()
    assert not tab.cancel_button.isEnabled()
    assert "Qt rejected the worker" in tab.activity_label.text()
    assert tab.progress_bar.format() == "Failed"
    assert tab.progress_detail.text() == "Failed"
    assert not tab.progress_bar.isHidden()
    assert busy_changes == []
    thread.start.assert_not_called()
    thread.deleteLater.assert_called_once()
    worker.deleteLater.assert_called_once()


def test_unreported_event_requires_station_but_allows_manual_archive_station(tab, monkeypatch):
    _select_event(tab, _event(stations=()))
    warnings, started = [], []
    monkeypatch.setattr(burst_ui.QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(tab, "_start_worker", lambda worker, receiver: started.append(worker))
    tab.find_fits()
    assert started == []
    assert warnings[0][1] == "No Station"
    tab.fits_station_combo.setEditText("BIR")
    tab.find_fits()
    assert started[0].stations == ["BIR"]


@pytest.mark.parametrize("change", ["station", "padding", "event", "filter", "date"])
def test_parameter_changes_clear_stale_fits_and_disable_file_actions(tab, change):
    first, second = _event(), _event(13)
    tab.display_catalog({"events": [first, second]})
    tab.catalog_table.selectRow(0)
    tab.display_fits({"candidates": [_candidate()]})
    assert tab.import_button.isEnabled()

    if change == "station":
        tab.fits_station_combo.setEditText("GREENLAND")
    elif change == "padding":
        tab.padding_spin.setValue(9)
    elif change == "event":
        tab.catalog_table.selectRow(1)
    elif change == "filter":
        tab.text_filter.setText("no matching event")
    else:
        tab.start_date_edit.setDate(QDate(2024, 1, 1))
        assert tab.catalog_table.rowCount() == 0
        assert "Load Events" in tab.catalog_status.text()

    assert tab.selected_files() == []
    assert tab.fits_table.rowCount() == 0
    assert all(not button.isEnabled() for button in
               (tab.preview_button, tab.download_button, tab.import_button, tab.compare_button))


def test_load_events_uses_inclusive_date_range_and_clears_previous_results(tab, monkeypatch):
    tab.restore_date_time_state({"burst_start_date": QDate(2024, 1, 2), "burst_end_date": QDate(2024, 1, 5)})
    _select_event(tab)
    tab.display_fits({"candidates": [_candidate()]})
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda worker, receiver: started.append((worker, receiver)))

    tab.load_events()

    worker, receiver = started[0]
    assert isinstance(worker, burst_ui.BurstCatalogWorker)
    assert (worker.start_date, worker.end_date) == (date(2024, 1, 2), date(2024, 1, 5))
    assert receiver == tab.display_catalog
    assert tab.catalog_table.rowCount() == tab.fits_table.rowCount() == 0


def test_invalid_date_range_does_not_launch_request(tab, monkeypatch):
    tab.start_date_edit.setDate(QDate(2024, 2, 1))
    tab.end_date_edit.setDate(QDate(2024, 1, 1))
    warnings, started = [], []
    monkeypatch.setattr(burst_ui.QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(tab, "_start_worker", lambda *args: started.append(args))
    tab.load_events()
    assert started == []
    assert warnings[0][1] == "Invalid Date Range"


def test_checked_files_follow_existing_import_and_compare_signals(dialog):
    tab = dialog.burst_list_tab
    first, second, third = _candidate(), _candidate(receiver="02"), _candidate("GREENLAND")
    tab.display_fits({"candidates": [first, second, third]})
    tab.fits_table.item(1, 0).setCheckState(Qt.Unchecked)
    imported, compared = [], []
    dialog.import_request.connect(imported.append)
    dialog.comparison_request.connect(compared.append)

    tab.import_selected()

    assert imported == [[first.url, third.url]]
    assert compared == []
    assert dialog.result() != QDialog.Accepted
    tab.compare_selected()
    assert compared == [[first.url, third.url]]
    assert dialog.result() == QDialog.Accepted


def test_select_all_and_clear_toggle_transfer_actions(tab):
    candidates = [_candidate(), _candidate(receiver="02")]
    tab.display_fits({"candidates": candidates})
    tab.clear_button.click()
    assert tab.selected_files() == []
    assert not tab.download_button.isEnabled()
    assert not tab.preview_button.isEnabled()
    assert tab.selection_count.text() == "0 of 2 files selected"
    tab.select_all_button.click()
    assert tab.selected_files() == candidates
    assert tab.download_button.isEnabled()
    assert tab.preview_button.isEnabled()
    assert tab.selection_count.text() == "2 of 2 files selected"


def test_preview_button_prepares_only_checked_candidates_without_importing(tab, monkeypatch):
    candidates = [_candidate(), _candidate(receiver="02"), _candidate("GREENLAND")]
    tab.display_fits({"candidates": candidates})
    tab.fits_table.item(1, 0).setCheckState(Qt.Unchecked)
    started, imported = [], []
    monkeypatch.setattr(tab, "_start_worker", lambda worker, receiver: started.append((worker, receiver)))
    tab.import_request.connect(imported.append)
    tab.comparison_request.connect(imported.append)

    tab.preview_button.click()

    worker, receiver = started[0]
    assert isinstance(worker, burst_ui.BurstPreviewWorker)
    assert worker.candidates == [candidates[0], candidates[2]]
    assert receiver == tab.display_preview
    assert imported == []
    assert "2 selected FITS" in tab.fits_status.text()


def test_preview_uses_existing_window_and_reports_cache_and_partial_failures(tab, monkeypatch):
    panels = [{"title": "BIR preview"}]
    calls, cache_changes = [], []

    def create_preview(received_panels, title, *, parent):
        calls.append((received_panels, title, parent))
        preview = QDialog(parent)
        preview.setAttribute(Qt.WA_DeleteOnClose)
        return preview

    monkeypatch.setattr(downloader, "PreviewWindow", create_preview)
    tab.cache_changed.connect(lambda: cache_changes.append(True))

    tab.display_preview({"panels": panels, "cached_count": 2, "errors": ["One station unavailable"]})

    assert calls == [(panels, "BIR preview", tab.window())]
    assert cache_changes == [True]
    assert len(tab._preview_windows) == 1
    preview = tab._preview_windows[0]
    assert preview.isVisible()
    assert "2 file(s) reused from cache" in tab.fits_status.text()
    assert "1 warning(s)" in tab.fits_status.text()
    assert tab.fits_status.toolTip() == "One station unavailable"
    preview.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert tab._preview_windows == []


@pytest.mark.parametrize("cancellation", ["payload", "late_request"])
def test_cancelled_preview_does_not_open_a_window(tab, monkeypatch, cancellation):
    preview_window = Mock()
    monkeypatch.setattr(downloader, "PreviewWindow", preview_window)
    cache_changes = []
    tab.cache_changed.connect(lambda: cache_changes.append(True))
    if cancellation == "late_request":
        worker = Mock()
        tab._worker = worker
        tab.request_cancel()
        worker.request_cancel.assert_called_once()
        tab._worker = None
    payload = {"panels": [{"title": "Ready just before cancellation"}], "cached_count": 1}
    if cancellation == "payload":
        payload = {"cancelled": True}

    tab.display_preview(payload)

    preview_window.assert_not_called()
    assert tab._preview_windows == []
    assert tab.fits_status.text() == "Preview preparation cancelled."
    assert tab._job_outcome == "cancelled"
    assert cache_changes == [True]


@pytest.mark.parametrize("failure", [False, True])
def test_download_uses_shared_tasks_cache_and_locks_actions_until_finished(tab, monkeypatch, tmp_path, failure):
    candidates = [_candidate(), _candidate(receiver="02")]
    _select_event(tab)
    tab.display_fits({"candidates": candidates})
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    cached = tmp_path / "cached.fit.gz"
    cached.write_bytes(b"offline cached FITS content")
    calls, queued, messages, cache_changes, idle, busy_changes = [], [], [], [], [], []
    transfer_progress = []

    class ManualPool:
        def start(self, task):
            queued.append(task)

    def fetch_cached(url, filename, *, progress_cb):
        calls.append((url, filename))
        for fraction in (0.25, 0.5, 1.0):
            progress_cb(fraction, f"Downloading {filename}: {fraction:.0%}")
            transfer_progress.append((filename, tab.progress_bar.value(), tab._download_done))
            assert not (output_dir / filename).exists()
            assert tab.is_busy()
        if failure and filename == candidates[1].filename:
            raise OSError("offline transfer failure")
        return cached, True

    monkeypatch.setattr(burst_ui.QFileDialog, "getExistingDirectory", lambda *args: str(output_dir))
    monkeypatch.setattr(burst_ui, "QThreadPool", type("Pool", (), {"globalInstance": staticmethod(ManualPool)}))
    monkeypatch.setattr(downloader.callisto_cache, "fetch_cached", fetch_cached)
    for method in ("information", "warning"):
        monkeypatch.setattr(burst_ui.QMessageBox, method, lambda *args: messages.append(args))
    tab.cache_changed.connect(lambda: cache_changes.append(True))
    tab.idle.connect(lambda: idle.append(True))
    tab.busy_changed.connect(busy_changes.append)

    tab.download_selected()

    assert len(queued) == 2
    assert all(isinstance(task, downloader.EventDownloadTask) for task in queued)
    assert tab.is_busy()
    assert busy_changes == [True]
    assert all(not widget.isEnabled() for widget in (
        tab.load_button, tab.start_date_edit, tab.type_filter, tab.catalog_table,
        tab.fits_station_combo, tab.padding_spin, tab.fits_table, tab.find_button,
        tab.preview_button, tab.download_button, tab.import_button, tab.compare_button,
    ))
    emitted = []
    tab.import_request.connect(emitted.append)
    tab.comparison_request.connect(emitted.append)
    tab.import_selected()
    tab.compare_selected()
    tab.preview_selected()
    tab.download_selected()
    assert len(queued) == 2
    assert emitted == []

    queued[0].run()
    assert tab.is_busy()
    assert not tab.progress_bar.isHidden()
    assert tab.progress_bar.value() == 1000
    assert tab.progress_detail.text() == "50%"
    assert transfer_progress[:3] == [
        (candidates[0].filename, 250, 0),
        (candidates[0].filename, 500, 0),
        (candidates[0].filename, 990, 0),
    ]
    assert not tab.import_button.isEnabled()
    queued[1].run()

    assert calls == [(candidate.url, candidate.filename) for candidate in candidates]
    assert (output_dir / candidates[0].filename).read_bytes() == cached.read_bytes()
    assert (output_dir / candidates[1].filename).exists() is (not failure)
    assert tab.is_busy() is False
    assert tab.import_button.isEnabled()
    assert not tab.progress_bar.isHidden()
    assert tab.progress_bar.value() == tab.progress_bar.maximum() == 100
    assert tab.progress_detail.text() == "100%"
    assert cache_changes == idle == [True]
    assert busy_changes == [True, False]
    assert emitted == []
    assert len(messages) == int(failure)
    status = f"Downloaded {1 if failure else 2} of 2 selected FITS file(s)."
    if failure:
        status += " 1 failed."
    assert tab.fits_status.text() == status
    assert tab.activity_label.text() == status
    if failure:
        assert "offline transfer failure" in tab.fits_status.toolTip()


@pytest.mark.parametrize("target,payload,message", [
    ("catalog", {"cancelled": True}, "Catalog loading cancelled."),
    ("catalog", {"error": "HTTP 503"}, "Could not load the burst list: HTTP 503"),
    ("fits", {"cancelled": True}, "FITS search cancelled."),
    ("fits", {"error": "network unavailable"}, "Could not find FITS files: network unavailable"),
])
def test_error_and_cancel_status_is_visible(tab, target, payload, message):
    getattr(tab, f"display_{target}")(payload)
    assert getattr(tab, f"{target}_status").text() == message


def test_partial_catalog_and_fits_warnings_remain_visible(tab):
    warnings = ["January unavailable", "February incomplete", "March malformed"]
    tab.display_catalog({"events": [_event()], "warnings": warnings})
    assert "1 of 1" in tab.catalog_status.text()
    assert warnings[0] in tab.catalog_status.text()
    assert warnings[2] in tab.catalog_status.toolTip()
    tab.display_fits({"candidates": [_candidate()], "warnings": ["Second day unavailable"]})
    assert "Found 1" in tab.fits_status.text()
    assert "Second day unavailable" in tab.fits_status.text()
    assert tab.import_button.isEnabled()


@pytest.mark.parametrize("mode", ["success", "cancelled", "error"])
def test_catalog_worker_reports_result_progress_and_failure(app, monkeypatch, mode):
    event = _event()
    calls, results, progress = [], [], []

    def fetch(start, stop, *, progress, cancelled):
        calls.append((start, stop))
        if mode == "cancelled":
            assert cancelled()
            raise BurstListCancelled()
        if mode == "error":
            raise OSError("catalog offline")
        progress(1, 1, "Loaded January")
        return BurstListResult(events=[event], warnings=["One line skipped"])

    monkeypatch.setattr(burst_ui, "fetch_burst_events", fetch)
    worker = burst_ui.BurstCatalogWorker(date(2024, 1, 1), date(2024, 1, 31))
    worker.finished.connect(results.append)
    worker.progress.connect(lambda *args: progress.append(args))
    if mode == "cancelled":
        worker.request_cancel()
    worker.run()

    assert calls == [(date(2024, 1, 1), date(2024, 1, 31))]
    if mode == "success":
        assert results == [{"events": [event], "warnings": ["One line skipped"]}]
        assert progress == [(1, 1, "Loaded January")]
    elif mode == "cancelled":
        assert results == [{"cancelled": True}]
    else:
        assert results == [{"error": "catalog offline"}]


@pytest.mark.parametrize("close_method", ["close", "reject", "accept"])
def test_close_waits_for_catalog_worker_cleanup(dialog, monkeypatch, close_method):
    entered, release = Event(), Event()
    cancellation_seen = []

    def fetch(start, stop, *, progress, cancelled):
        entered.set()
        if not release.wait(3):
            raise RuntimeError("test did not release catalog worker")
        cancellation_seen.append(cancelled())
        if cancelled():
            raise BurstListCancelled()
        return BurstListResult()

    monkeypatch.setattr(burst_ui, "fetch_burst_events", fetch)
    tab = dialog.burst_list_tab
    dialog.show()
    tab.load_events()
    try:
        assert entered.wait(2)
        assert dialog._cache_is_busy()
        assert not tab.load_button.isEnabled()

        getattr(dialog, close_method)()

        expected = QDialog.Accepted if close_method == "accept" else QDialog.Rejected
        assert dialog._burst_pending_result == expected
        assert dialog.isVisible()
        assert tab.is_busy()
        assert not tab.cancel_button.isEnabled()
    finally:
        release.set()
        for _ in range(300):
            QTest.qWait(10)
            if not tab.is_busy():
                break
    assert cancellation_seen == [True]
    assert not tab.is_busy()
    assert not dialog.isVisible()
    assert dialog._burst_pending_result is None
    assert dialog.result() == expected
    assert tab.catalog_status.text() == "Catalog loading cancelled."
