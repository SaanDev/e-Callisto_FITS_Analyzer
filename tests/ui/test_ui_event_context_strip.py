"""
e-CALLISTO FITS Analyzer
Offscreen tests for the event-context strip: the markers built from catalog
results, the strip on both canvases, the View menu, Export Figure, the
controller's fetch and the project part. Catalog loaders are stubbed.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.backend.space_weather import event_catalogs as ec
from src.ui.app.event_context_strip import AUTOFETCH_ENV, EventContextController, build_event_markers

BASE = datetime(2024, 5, 10, 6, 30)


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 8):
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


def _result() -> ec.EventContextResult:
    return ec.EventContextResult(
        BASE - timedelta(hours=6), BASE + timedelta(hours=7),
        events={
            ec.SOURCE_GOES: [
                ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, datetime(2024, 5, 10, 6, 27),
                                datetime(2024, 5, 10, 6, 54), datetime(2024, 5, 10, 7, 6), "X3.9 (AR 13664)", {"class": "X3.9"}),
                ec.CatalogEvent(ec.KIND_FLARE, ec.SOURCE_GOES, datetime(2024, 5, 10, 3, 15), None,
                                datetime(2024, 5, 10, 3, 31), "C9.8", {"class": "C9.8"}),
            ],
            ec.SOURCE_CDAW: [ec.CatalogEvent(ec.KIND_CME, ec.SOURCE_CDAW, datetime(2024, 5, 10, 6, 48),
                                             label="CME 1353 km/s, halo", details={"speed_kms": 1353.0, "halo": True})],
            ec.SOURCE_DONKI: [ec.CatalogEvent(ec.KIND_IPS, ec.SOURCE_DONKI, datetime(2024, 5, 10, 16, 36),
                                              label="Shock at Earth", details={"location": "Earth"})],
            ec.SOURCE_BURSTS: [ec.CatalogEvent(ec.KIND_BURST, ec.SOURCE_BURSTS, datetime(2024, 5, 10, 6, 40),
                                               end=datetime(2024, 5, 10, 6, 52), label="Type II", details={"type": "II"})],
        },
        errors={},
    )


def test_markers_place_events_on_the_spectrum_time_axis():
    markers = build_event_markers(_result(), ec.SOURCES, BASE, 0.0, 1800.0)
    lanes = {lane.title: lane for lane in markers.lanes}
    assert list(lanes) == ["Flares", "CMEs", "DONKI", "Bursts"]
    flare = lanes["Flares"].marks[0]
    assert flare.x0 == pytest.approx(-180.0) and flare.x1 == pytest.approx(2160.0)
    assert flare.label == "X3.9" and flare.color == ec.flare_class_color("X3.9")
    assert lanes["Flares"].before == 1  # the C9.8 flare, three hours earlier
    cme = lanes["CMEs"].marks[0]
    assert cme.x0 == pytest.approx(1080.0) and cme.x1 is None and cme.label == "1353 km/s, halo"
    assert lanes["DONKI"].marks == () and lanes["DONKI"].after == 0  # 10 h later: beyond the 6 h margin
    assert lanes["Bursts"].marks[0].label == "II"
    assert "2024-05-10 06:27 UTC" in flare.tooltip


def test_disabled_sources_have_no_lane():
    markers = build_event_markers(_result(), (ec.SOURCE_CDAW,), BASE, 0.0, 1800.0)
    assert [lane.title for lane in markers.lanes] == ["CMEs"]


def test_the_hardware_strip_spans_exactly_the_spectrum_width():
    _app()
    from src.ui.widgets.accelerated_plot_widget import AcceleratedPlotWidget

    widget = AcceleratedPlotWidget(use_opengl=False)
    widget.resize(900, 600)
    widget.show()
    widget.update_image(np.random.rand(30, 60), extent=[0.0, 1800.0, 20.0, 80.0], cmap="viridis", title="t")
    markers = build_event_markers(_result(), ec.SOURCES, BASE, 0.0, 1800.0)
    assert widget.set_event_strip(markers)
    _flush()
    strip = widget._event_view.sceneBoundingRect()
    main = widget._viewbox.sceneBoundingRect()
    assert strip.left() == pytest.approx(main.left(), abs=1.0)
    assert strip.width() == pytest.approx(main.width(), abs=1.0)
    assert strip.bottom() <= main.top() + 1.0  # above the spectrum
    tips = [item.toolTip() for item in widget.event_strip_items() if hasattr(item, "toolTip")]
    assert any("X3.9" in tip for tip in tips)
    assert widget.set_event_strip(None) is False
    assert not widget.has_event_strip
    widget.close()


def _fits(tmp_path):
    from tests.helpers.learmonth import write_test_callisto_fit

    return write_test_callisto_fit(
        tmp_path / "BIR_20240510_063000_01.fit",
        data=(np.arange(40 * 60) % 200).reshape(40, 60).astype(np.uint8),
        freqs=np.linspace(80.0, 20.0, 40),
        time=np.linspace(0.0, 1800.0, 60),
        date_obs="2024/05/10",
        time_obs="06:30:00",
    )


def _window_with_result(tmp_path):
    from src.ui.app.main_window import MainWindow

    window = MainWindow(theme=None)
    window.open_fits_paths([str(_fits(tmp_path))])
    _flush()
    controller = window._event_context
    assert controller.range == (BASE, BASE + timedelta(seconds=1800))
    controller.result = _result()
    controller._result_key = controller.request_key()
    return window, controller


def test_the_view_menu_toggles_the_strip_on_the_matplotlib_canvas(tmp_path):
    _app()
    window, controller = _window_with_result(tmp_path)
    assert window._event_strip_ax is None
    window.event_strip_action.setChecked(True)
    _flush(12)
    assert controller.enabled is True
    assert window._event_strip_ax is not None
    labels = [label.get_text() for label in window._event_strip_ax.get_yticklabels()]
    assert labels == ["Flares", "CMEs", "DONKI", "Bursts"]
    assert any("X3.9" in tip for *_box, tip in window._event_strip_hits)
    assert window.accel_canvas.has_event_strip

    window.event_strip_source_actions[ec.SOURCE_DONKI].setChecked(False)
    _flush(12)
    labels = [label.get_text() for label in window._event_strip_ax.get_yticklabels()]
    assert "DONKI" not in labels

    window.event_strip_action.setChecked(False)
    _flush(12)
    assert window._event_strip_ax is None
    assert not window.accel_canvas.has_event_strip
    window.close()


def test_export_figure_includes_the_visible_strip(tmp_path):
    _app()
    window, controller = _window_with_result(tmp_path)
    window.event_strip_action.setChecked(True)
    _flush(12)
    fig = window._build_origin_export_figure()
    strip = [ax for ax in fig.axes if [t.get_text() for t in ax.get_yticklabels()][:1] == ["Flares"]]
    assert strip, "the export carries the strip"
    window.event_strip_action.setChecked(False)
    _flush(12)
    fig = window._build_origin_export_figure()
    assert not [ax for ax in fig.axes if [t.get_text() for t in ax.get_yticklabels()][:1] == ["Flares"]]
    window.close()


def test_the_controller_fetches_with_a_margin_and_ignores_stale_replies(tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv(AUTOFETCH_ENV, "0")
    calls = []

    def loader(start, end, **kwargs):
        calls.append((start, end, kwargs.get("sources")))
        return _result()

    controller = EventContextController(loader=loader, cache_dir=tmp_path)
    controller.set_enabled(True)
    controller.set_range(BASE, BASE + timedelta(minutes=30))
    assert _wait_for(lambda: controller.result is not None)
    assert calls[0][0] == BASE and calls[0][2] == ec.SOURCES
    stale = controller.result
    controller._requested_key = "other"
    controller._on_finished("old-key", ec.EventContextResult(BASE, BASE))
    assert controller.result is stale
    controller.shutdown()


def test_the_strip_is_saved_in_the_project_and_reopens_offline(tmp_path, monkeypatch):
    _app()
    from src.backend.session.project_session import read_project_meta
    from src.ui.app.main_window import MainWindow

    window, controller = _window_with_result(tmp_path)
    window.event_strip_action.setChecked(True)
    _flush(12)
    project = tmp_path / "strip.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert window.save_project_as()
    meta = read_project_meta(str(project))
    assert meta["parts"]["event_context"] is True
    assert meta["event_context"]["enabled"] is True
    window.close()

    other = MainWindow(theme=None)
    other._event_context.set_enabled(False)
    assert other.open_project_path(str(project))
    _flush(15)
    assert other._event_context.enabled is True
    assert len(other._event_context.result.events[ec.SOURCE_GOES]) == 2
    assert other._event_strip_ax is not None
    other.close()
