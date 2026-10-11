"""
e-CALLISTO FITS Analyzer
Offscreen tests for the project report's event story (src/ui/app/report_story.py):
the story told from what the main window holds, missing catalogs and the
geomagnetic response fetched off the GUI thread before the PDF is written,
cancelling while fetching, and a story with fetching switched off. Every
loader is stubbed; nothing touches the network.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QMessageBox

import src.ui.app.gui_workers as gui_workers
import src.ui.app.report_story as report_story
from src.backend.session.project_report import ProjectReportResult
from src.backend.space_weather import event_catalogs as ec
from src.backend.space_weather.geomagnetic_context import GeoSeries
from tests.backend.test_backend_event_story import START, _catalog, _geomagnetic, _markers

AUTOFETCH_ENVS = ("E_CALLISTO_DISABLE_GEOMAGNETIC_AUTOFETCH", "E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH")


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 10):
    for _ in range(times):
        _app().processEvents()


def _wait_for(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _flush(2)
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _storm():
    shock = START + timedelta(hours=34)
    geo = _geomagnetic(dst_min=-180.0, dst_at=shock + timedelta(hours=10), kp=7.7, kp_at=shock + timedelta(hours=6),
                       g="G3", klass="intense")
    geo.markers = _markers(shock)
    hours = np.arange(np.datetime64(geo.start, "h"), np.datetime64(geo.end, "h"), np.timedelta64(1, "h"))
    dip = np.exp(-(((np.arange(hours.size) - 68) / 12.0) ** 2))
    geo.series["dst"] = GeoSeries("dst", hours.astype("datetime64[s]"), -10.0 - 170.0 * dip)
    return geo


def _window(tmp_path):
    from src.ui.app.main_window import MainWindow
    from tests.helpers.learmonth import write_test_callisto_fit

    path = write_test_callisto_fit(
        tmp_path / "BIR_20240510_063000_01.fit",
        data=(np.arange(40 * 60) % 200).reshape(40, 60).astype(np.uint8),
        freqs=np.linspace(80.0, 20.0, 40),
        time=np.linspace(0.0, 1800.0, 60),
        date_obs="2024/05/10",
        time_obs="06:30:00",
    )
    window = MainWindow(theme=None)
    window.open_fits_paths([str(path)])
    _flush(20)
    return window


def test_the_report_tells_the_story_from_what_the_window_holds(tmp_path):
    _app()
    window = _window(tmp_path)
    try:
        window._event_context.result = _catalog()
        window._geomagnetic.context = _storm()
        request = report_story.story_request(window, window._project_report_analysis_session())
        assert request.radio.start == START and request.radio.station
        assert request.missing_sources == () and request.fetch_geomagnetic is False and not request.needs_fetch()

        report = window._build_project_report_input()
        assert "GOES X3.9 flare" in report.event_story.headline
        assert "G3 geomagnetic storm followed" in report.event_story.headline
        assert [f.title for f in report.story_figures] == ["Event Chain", "Event Overview", "Geomagnetic Response"]
        assert all(f.png_bytes for f in report.story_figures)
        # The geomagnetic response moved into the story; it is not drawn twice.
        assert "Geomagnetic Response" not in [f.title for f in report.figures]
    finally:
        window.close()


def test_missing_parts_are_fetched_off_the_gui_thread_before_the_pdf(tmp_path, monkeypatch):
    _app()
    window = _window(tmp_path)
    calls, written = [], []
    gui_thread = threading.get_ident()

    def catalogs(start, end, *, sources, cache_dir, cancel_cb):
        calls.append(("catalogs", start, end, tuple(sources), threading.get_ident()))
        return _catalog()

    def geomagnetic(event, *, cache_dir, cancel_cb, event_label):
        calls.append(("geomagnetic", event, threading.get_ident()))
        return _storm()

    def pdf(path, report, progress_cb=None):
        written.append(report)
        return ProjectReportResult(path=str(path), file_size=1, figures_written=0)

    monkeypatch.setattr(report_story, "load_event_context", catalogs)
    monkeypatch.setattr(report_story, "load_geomagnetic_context", geomagnetic)
    monkeypatch.setattr(gui_workers, "generate_project_report_pdf", pdf)
    monkeypatch.setattr(window, "_pick_project_report_path", lambda: str(tmp_path / "report.pdf"))
    for name in AUTOFETCH_ENVS:
        monkeypatch.delenv(name, raising=False)
    try:
        window.generate_project_report()
        assert _wait_for(lambda: written and window._project_report_worker is None)
        kinds = [call[0] for call in calls]
        assert kinds == ["catalogs", "geomagnetic"]
        assert calls[0][1:4] == (START, START + timedelta(minutes=30), ec.SOURCES)
        assert calls[1][1] == START
        assert all(call[-1] != gui_thread for call in calls)  # never on the GUI thread
        story = written[0].event_story
        assert "GOES X3.9 flare" in story.headline and "G3 geomagnetic storm followed" in story.headline
        assert window._project_report_progress_dialog is None
    finally:
        window.close()


def test_cancelling_while_fetching_stops_the_report(tmp_path, monkeypatch):
    _app()
    window = _window(tmp_path)
    written, started = [], threading.Event()

    def slow_catalogs(start, end, *, sources, cache_dir, cancel_cb):
        started.set()
        while not cancel_cb():
            time.sleep(0.01)
        raise ec.CatalogCancelled("cancelled")

    monkeypatch.setattr(report_story, "load_event_context", slow_catalogs)
    monkeypatch.setattr(gui_workers, "generate_project_report_pdf", lambda *a, **k: written.append(a))
    monkeypatch.setattr(window, "_pick_project_report_path", lambda: str(tmp_path / "report.pdf"))
    monkeypatch.delenv("E_CALLISTO_DISABLE_EVENT_CONTEXT_AUTOFETCH", raising=False)
    try:
        window.generate_project_report()
        assert started.wait(5)
        window._cancel_project_report_generation()
        assert _wait_for(lambda: window._project_report_worker is None)
        assert written == []
        assert window._project_report_thread is None
        assert window._project_report_progress_dialog is None
    finally:
        window.close()


def test_with_fetching_off_the_story_says_what_is_missing(tmp_path):
    _app()
    window = _window(tmp_path)
    try:
        request = report_story.story_request(window, None)
        assert not request.needs_fetch()
        assert any("Event catalogs were not loaded" in note for note in request.notes)
        assert any("geomagnetic response was not loaded" in note for note in request.notes)
        report = window._build_project_report_input(request)
        assert report.event_story.headline.startswith("On 10 May 2024,")
        assert "recorded the radio event from 06:30 UT to 07:00 UT" in report.event_story.headline
        assert any("automatic fetching is off" in note for note in report.story_notes)
        assert report.story_figures[0].png_bytes  # the chain is drawn even for a radio-only story
    finally:
        window.close()
