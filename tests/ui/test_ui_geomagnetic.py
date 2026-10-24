"""
e-CALLISTO FITS Analyzer
Offscreen tests for the geomagnetic response: the controller's triggers and
request keys, the sidebar card, the full window and the project part.
The network is never touched: loaders are stubbed.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.backend.space_weather.geomagnetic_context import GeomagneticContext, GeoSeries, summarise
from src.backend.space_weather.geomagnetic_figures import total_rows
from src.ui.space_weather.geomagnetic_card import GeomagneticCard
from src.ui.space_weather.geomagnetic_controller import AUTOFETCH_ENV, GeomagneticController


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 5):
    app = _app()
    for _ in range(times):
        app.processEvents()


def _wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _flush(2)
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def _context(event: datetime, label: str = "stub") -> GeomagneticContext:
    context = GeomagneticContext(event_start=event, start=event - timedelta(days=1), end=event + timedelta(days=7),
                                 event_label=label)
    hourly = np.datetime64(event - timedelta(days=1), "s") + np.arange(8 * 24) * np.timedelta64(3600, "s")
    dst = -400.0 * np.exp(-((np.arange(hourly.size) - 33.0) / 10.0) ** 2)
    context.series["dst"] = GeoSeries("dst", hourly, dst)
    context.series["symh"] = GeoSeries("symh", hourly, dst - 5.0)
    kp_times = hourly[::3]
    context.series["kp"] = GeoSeries("kp", kp_times, np.clip(np.abs(dst[::3]) / 45.0, 0, 9))
    for name, values in (("bt", 10 + 0 * dst), ("bz", dst / 10.0), ("by", 0 * dst + 2), ("v", 500 - dst),
                         ("np", 5 + 0 * dst), ("t", 1e5 + 0 * dst), ("pdyn", 2 + 0 * dst), ("ey", -dst / 50),
                         ("beta", 0.5 + 0 * dst), ("mach", 8 + 0 * dst), ("ae", -dst), ("al", dst), ("au", 0 * dst + 50)):
        context.series[name] = GeoSeries(name, hourly, np.asarray(values, dtype=float))
    context.summary = summarise(context)
    return context


class StubLoader:
    def __init__(self):
        self.calls = []

    def __call__(self, when, **kwargs):
        self.calls.append((when, kwargs.get("event_label")))
        return _context(when, kwargs.get("event_label") or "")


# ----------------------------------------------------------------- controller


def test_a_loaded_event_without_autofetch_waits_for_refresh(tmp_path):
    _app()
    loader = StubLoader()
    controller = GeomagneticController(loader=loader, cache_dir=tmp_path)
    statuses = []
    controller.statusChanged.connect(statuses.append)
    controller.set_loaded_event(datetime(2024, 5, 10, 17), "FITS: x.fit")
    _flush()
    assert loader.calls == []
    assert any("Refresh" in text for text in statuses)
    controller.refresh()
    assert _wait_for(lambda: controller.context is not None)
    assert loader.calls == [(datetime(2024, 5, 10, 17), "FITS: x.fit")]
    controller.shutdown()


def test_requests_are_debounced_and_deduplicated_on_the_event_hour(tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv(AUTOFETCH_ENV, "0")
    loader = StubLoader()
    controller = GeomagneticController(loader=loader, cache_dir=tmp_path)
    for minute in (0, 10, 20):
        controller.set_loaded_event(datetime(2024, 5, 10, 17, minute), "FITS")
    assert _wait_for(lambda: controller.context is not None)
    assert len(loader.calls) == 1  # three triggers inside the debounce window
    controller.set_loaded_event(datetime(2024, 5, 10, 17, 45), "FITS: same hour")
    _flush()
    time.sleep(0.8)
    _flush()
    assert len(loader.calls) == 1  # same event hour: nothing refetched
    assert controller.context.event_label == "FITS: same hour"
    controller.shutdown()


def test_a_stale_reply_is_ignored(tmp_path):
    _app()
    controller = GeomagneticController(loader=StubLoader(), cache_dir=tmp_path)
    controller._requested_key = "20240511T00|86400|604800"
    controller._on_finished("20240510T17|86400|604800", _context(datetime(2024, 5, 10, 17)))
    assert controller.context is None
    fresh = _context(datetime(2024, 5, 11))
    controller._on_finished("20240511T00|86400|604800", fresh)
    assert controller.context is fresh


def test_a_selection_overrides_the_loaded_event_until_use_loaded_data(tmp_path):
    _app()
    controller = GeomagneticController(loader=StubLoader(), cache_dir=tmp_path)
    card = GeomagneticCard(controller)
    controller.set_loaded_event(datetime(2024, 5, 10, 17), "FITS: a.fit")
    assert not controller.is_overridden() and card.use_loaded_btn.isHidden()
    controller.select_event(datetime(2024, 5, 8, 5, 36), "Burst list: type II", "burst_list")
    assert controller.is_overridden()
    assert "Burst list" in card.event_label.text()
    assert not card.use_loaded_btn.isHidden()
    card.use_loaded_btn.click()
    assert not controller.is_overridden()
    assert "FITS: a.fit" in card.event_label.text()


def test_the_card_shows_the_summary(tmp_path):
    _app()
    controller = GeomagneticController(loader=StubLoader(), cache_dir=tmp_path)
    card = GeomagneticCard(controller)
    controller.context = _context(datetime(2024, 5, 10, 17))
    controller.contextChanged.emit(controller.context)
    assert card.value_labels["dst"].text().startswith("-400 nT")
    assert "+9 h" in card.value_labels["dst"].text()
    assert "G4" in card.value_labels["kp"].text() or "G5" in card.value_labels["kp"].text()
    assert card.value_labels["storm"].text() == "Super storm"


def test_the_project_round_trip_restores_without_fetching(tmp_path):
    _app()
    controller = GeomagneticController(loader=StubLoader(), cache_dir=tmp_path)
    controller.set_loaded_event(datetime(2024, 5, 10, 17), "FITS: a.fit",
                                radio_interval=(datetime(2024, 5, 10, 17), datetime(2024, 5, 10, 17, 15)))
    controller.set_span(timedelta(days=2), timedelta(days=5))
    controller.context = _context(datetime(2024, 5, 10, 17), "FITS: a.fit")
    meta, arrays = controller.project_capture()
    assert all(key.startswith("geo_") for key in arrays)

    loader = StubLoader()
    other = GeomagneticController(loader=loader, cache_dir=tmp_path)
    other.project_restore(meta, arrays)
    _flush()
    time.sleep(0.7)
    _flush()
    assert loader.calls == []
    assert other.before == timedelta(days=2) and other.after == timedelta(days=5)
    assert other.context.summary.dst_min_nt == pytest.approx(controller.context.summary.dst_min_nt)
    assert other.radio_interval == (datetime(2024, 5, 10, 17), datetime(2024, 5, 10, 17, 15))
    assert other.event_label().startswith("FITS: a.fit")
    other.project_clear()
    assert other.context is None
    controller.shutdown()
    other.shutdown()


# --------------------------------------------------------------------- window


def test_the_window_renders_the_selected_panels_and_remembers_them(tmp_path):
    _app()
    from src.ui.space_weather import geomagnetic_response_window as module

    controller = GeomagneticController(loader=StubLoader(), cache_dir=tmp_path)
    controller.context = _context(datetime(2024, 5, 10, 17))
    window = module.GeomagneticResponseWindow(controller)
    _flush()
    base_axes = [ax for ax in window.drawn_axes() if ax.get_subplotspec() is not None]
    assert len({ax.get_subplotspec().rowspan.start for ax in base_axes}) == total_rows(window.panels)

    window.panel_checks["indices"].setChecked(True)
    window.panel_checks["plasma"].setChecked(False)
    _flush()
    assert "indices" in window.panels and "plasma" not in window.panels
    assert module.load_panel_choice() == window.panels
    rows = {ax.get_subplotspec().rowspan.start for ax in window.drawn_axes() if ax.get_subplotspec() is not None}
    assert len(rows) == total_rows(window.panels)
    window.close()


def test_the_window_exports_a_figure_and_a_csv_with_provenance(tmp_path, monkeypatch):
    _app()
    from src.ui.space_weather.geomagnetic_response_window import GeomagneticResponseWindow

    controller = GeomagneticController(loader=StubLoader(), cache_dir=tmp_path)
    controller.context = _context(datetime(2024, 5, 10, 17))
    window = GeomagneticResponseWindow(controller)
    for suffix in ("png", "pdf", "svg", "eps"):
        target = tmp_path / f"geo.{suffix}"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, t=target, **k: (str(t), ""))
        window.export_figure()
        assert target.is_file() and target.stat().st_size > 1000
    csv_path = tmp_path / "geo.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(csv_path), ""))
    window.export_csv()
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "time_utc,series,value,units,provenance"
    window.close()


# ----------------------------------------------------------------- main window


def _fits(tmp_path):
    from tests.helpers.learmonth import write_test_callisto_fit

    return write_test_callisto_fit(
        tmp_path / "STAT_20240510_170000_01.fit",
        data=np.arange(12, dtype=np.uint8).reshape(3, 4),
        freqs=np.array([60.0, 50.0, 40.0]),
        time=np.array([0.0, 225.0, 450.0, 675.0]),
        date_obs="2024/05/10",
        time_obs="17:00:00",
    )


def test_loading_fits_points_the_card_at_the_observation_start(tmp_path):
    _app()
    from src.ui.app.main_window import MainWindow

    window = MainWindow(theme=None)
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush()
    choice = window._geomagnetic.current_choice()
    assert choice is not None and choice.when == datetime(2024, 5, 10, 17, 0)
    assert choice.label.startswith("FITS: STAT_20240510")
    assert "2024-05-10 17:00" in window.geomagnetic_card.event_label.text()
    assert window._geomagnetic.radio_interval[1] > window._geomagnetic.radio_interval[0]
    window.close()


def test_a_burst_list_or_downloader_selection_reaches_the_card(tmp_path):
    _app()
    from src.ui.app.main_window import MainWindow
    from src.ui.downloads.callisto_downloader import CallistoDownloaderApp

    window = MainWindow(theme=None)
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush()
    dialog = CallistoDownloaderApp()
    dialog.event_time_selected.connect(window._on_external_event_selected)

    dialog.date_edit.setDate(QDate(2024, 5, 8))
    _flush()
    assert window._geomagnetic.current_choice().when == datetime(2024, 5, 8)
    assert window._geomagnetic.is_overridden()
    assert not window.geomagnetic_card.use_loaded_btn.isHidden()

    from src.backend.radio.burst_list import BurstEvent

    event = BurstEvent(datetime(2024, 5, 8, 5, 36), datetime(2024, 5, 8, 5, 50), "II", 20.0, 80.0,
                       ("BIR",), "", "https://example.invalid/list.txt", "raw")
    dialog.burst_list_tab.selected_event = lambda: event
    dialog.burst_list_tab._event_selected()
    _flush()
    choice = window._geomagnetic.current_choice()
    assert choice.when == datetime(2024, 5, 8, 5, 36)
    assert choice.label.startswith("Burst list: type II")
    window.geomagnetic_card.use_loaded_btn.click()
    assert window._geomagnetic.current_choice().when == datetime(2024, 5, 10, 17)
    dialog.close()
    window.close()


def test_the_geomagnetic_part_is_saved_in_the_project(tmp_path, monkeypatch):
    _app()
    from src.backend.session.project_session import read_project_meta
    from src.ui.app.main_window import MainWindow

    window = MainWindow(theme=None)
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush()
    window._geomagnetic.context = _context(datetime(2024, 5, 10, 17), "FITS: x")
    window._geomagnetic.modified.emit()
    project = tmp_path / "with_geo.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert window.save_project_as()
    meta = read_project_meta(str(project))
    assert meta["parts"]["geomagnetic"] is True and meta["parts"]["radio"] is True
    assert meta["geomagnetic"]["context"]["summary"]["storm_class"] == "super"
    window.close()

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    assert other._geomagnetic.context is not None
    assert other._geomagnetic.context.summary.storm_class == "super"
    assert other.geomagnetic_card.value_labels["storm"].text() == "Super storm"
    figure = other._capture_geomagnetic_report_figure()
    assert figure.png_bytes
    other.close()


def test_opening_a_radio_project_points_the_card_and_strip_at_its_event(tmp_path, monkeypatch):
    """A project saved before any response was loaded (every v3.1 project) still targets its spectrum."""
    _app()
    from src.backend.session.project_session import read_project_meta
    from src.ui.app.main_window import MainWindow

    window = MainWindow(theme=None)
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush()
    project = tmp_path / "radio_only.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert window.save_project_as()
    meta = read_project_meta(str(project))
    assert meta["parts"]["geomagnetic"] is False and meta["parts"]["event_context"] is False
    window.close()

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    choice = other._geomagnetic.current_choice()
    assert choice is not None and choice.when == datetime(2024, 5, 10, 17, 0)
    assert choice.label.startswith("FITS: STAT_20240510")
    assert other._event_context.range is not None
    assert other._event_context.range[0] == datetime(2024, 5, 10, 17, 0)
    other.close()


def test_a_restored_selection_is_kept_when_the_project_opens(tmp_path, monkeypatch):
    _app()
    from src.ui.app.main_window import MainWindow

    window = MainWindow(theme=None)
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush()
    window._geomagnetic.select_event(datetime(2024, 5, 8, 5, 36), "Burst list: type II", "burst_list")
    window._geomagnetic.context = _context(datetime(2024, 5, 8, 5, 36), "Burst list: type II")
    window._geomagnetic.modified.emit()
    project = tmp_path / "with_selection.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert window.save_project_as()
    window.close()

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    choice = other._geomagnetic.current_choice()
    assert choice.when == datetime(2024, 5, 8, 5, 36)
    assert choice.label.startswith("Burst list: type II")
    assert other._geomagnetic.context is not None
    other.close()
