"""
e-CALLISTO FITS Analyzer
Offscreen tests for the event-figure composer (src/ui/app/event_figure_composer.py):
loaded frames win over Helioviewer, the spec round-trips through the project,
the preview follows the controls, missing data is fetched in a worker, and
the figure exports. Nothing touches the network.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.backend.common.event_figure import EventFigureSpec, PanelSpec, SepSeries, ThumbnailImage, ThumbnailSpec
from src.ui.app import event_figure_composer as composer_module
from src.ui.app.event_figure_composer import collect_event_figure_data, missing_thumbnails, thumb_key


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 8):
    app = _app()
    for _ in range(times):
        app.processEvents()


def _wait_for(predicate, timeout_s: float = 8.0) -> bool:
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
    for name in ("exec", "exec_"):
        monkeypatch.setattr(QMessageBox, name, lambda self, *a, **k: 0, raising=False)


class CorWcsMap:
    observatory = "STEREO_A"
    instrument = "SECCHI"
    detector = "COR2"
    wavelength = ""
    nickname = ""
    source = ""

    def __init__(self, date):
        self.data = np.random.default_rng(0).random((11, 11)) + 1.0
        self.date = date
        self.meta = {"instrume": "SECCHI", "detector": "COR2", "polar": 1001.0, "cdelt1": 2.0, "cdelt2": 2.0,
                     "crpix1": 6.0, "crpix2": 6.0, "crval1": 0.0, "crval2": 0.0, "rsun_obs": 8.0}


def _main_with_data(tmp_path):
    from tests.helpers.learmonth import write_test_callisto_fit
    from src.ui.app.main_window import MainWindow

    path = write_test_callisto_fit(
        tmp_path / "BIR_20240510_063000_01.fit",
        data=(np.arange(30 * 40) % 200).reshape(30, 40).astype(np.uint8),
        freqs=np.linspace(80.0, 20.0, 30), time=np.linspace(0.0, 1800.0, 40),
        date_obs="2024/05/10", time_obs="06:30:00",
    )
    window = MainWindow(theme=None)
    window.open_fits_paths([str(path)])
    _flush()
    return window


def _load_solar_frames(window, tmp_path):
    solar = window._ensure_solar_window()
    paths = []
    for index in range(3):
        p = tmp_path / f"cor2_{index}.fits"
        p.write_bytes(b"x")
        paths.append(str(p))
    frames = [CorWcsMap(f"2024-05-10T06:{40 + 5 * i:02d}:00") for i in range(3)]
    solar._apply_loaded_frames(frames, paths=paths, metadata={})
    _flush()
    return solar


def test_a_loaded_frame_is_preferred_over_helioviewer(tmp_path):
    _app()
    window = _main_with_data(tmp_path)
    solar = _load_solar_frames(window, tmp_path)
    state = window._composer_state
    near = datetime(2024, 5, 10, 6, 46)
    far = datetime(2024, 5, 10, 9, 0)
    spec = EventFigureSpec(thumbnails=[ThumbnailSpec(near, "AIA 193"), ThumbnailSpec(far, "AIA 193")])
    # A Helioviewer image cached for the near time must lose to the loaded frame.
    state.thumbs[thumb_key("AIA 193", near)] = ThumbnailImage(near, "AIA 193", np.zeros((4, 4, 3)), "helioviewer")
    data = collect_event_figure_data(window, spec, state)
    assert len(data.thumbnails) == 1
    assert data.thumbnails[0].source == "loaded"
    assert data.thumbnails[0].time == datetime(2024, 5, 10, 6, 45)  # nearest loaded frame
    assert [t.time for t in missing_thumbnails(window, spec, state)] == [far]
    state.thumbs[thumb_key("AIA 193", far)] = ThumbnailImage(far, "AIA 193", np.zeros((4, 4, 3)), "helioviewer")
    data = collect_event_figure_data(window, spec, state)
    assert [t.source for t in data.thumbnails] == ["loaded", "helioviewer"]
    solar.close()
    window.close()


def test_the_data_carries_spectrum_goes_events_and_the_geomagnetic_block(tmp_path):
    _app()
    window = _main_with_data(tmp_path)
    data = collect_event_figure_data(window, EventFigureSpec(), window._composer_state)
    assert data.base_utc == datetime(2024, 5, 10, 6, 30)
    assert data.spectrum is not None and data.spectrum.title == ""
    assert data.x_range == pytest.approx((0.0, 1800.0))
    assert data.geomagnetic is None and data.events is None
    window.close()


def test_the_composer_previews_reorders_and_exports(tmp_path, monkeypatch):
    _app()
    window = _main_with_data(tmp_path)
    composer = window.open_event_figure_composer()
    _flush()
    assert composer.figure is not None
    keys = [composer.panel_table.item(row, 0).data(0x0100) for row in range(composer.panel_table.rowCount())]
    assert keys[0] == "spectrum"
    assert composer.panel_table.item(0, 0).checkState() == composer.panel_table.item(0, 0).checkState().Checked
    assert composer.thumb_table.rowCount() == 3  # three across the data's range by default

    composer.panel_table.setCurrentCell(0, 0)
    composer._move_panel(1)
    assert composer.current_spec().panels[1].key == "spectrum"
    assert window._composer_state.touched and window._project_dirty

    composer.size_combo.setCurrentText("8.8 cm (one column)")
    # The preview canvas rounds the page to whole pixels; exports build it exactly.
    assert composer.build_figure().get_size_inches()[0] == pytest.approx(8.8 / 2.54)
    assert _wait_for(lambda: abs(composer.figure.get_size_inches()[0] - 8.8 / 2.54) < 0.02, 3.0)

    for suffix in ("pdf", "eps"):
        target = tmp_path / f"event.{suffix}"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, t=target, **k: (str(t), ""))
        composer.export_figure()
        assert target.is_file() and target.stat().st_size > 2000
    composer.close()
    window.close()


def test_missing_data_is_fetched_in_a_worker(tmp_path, monkeypatch):
    _app()
    window = _main_with_data(tmp_path)
    composer = window.open_event_figure_composer()
    _flush()
    spec = composer.current_spec()
    for panel in spec.panels:
        panel.enabled = panel.key in ("spectrum", "sep")
    composer._load_spec(spec)

    import src.backend.space_weather.sep_proton as sep_module
    import src.backend.solar.helioviewer as hv

    class Sep:
        times = tuple(datetime(2024, 5, 10, 6, 30) + timedelta(minutes=5 * i) for i in range(7))
        low_flux = tuple(float(i + 1) for i in range(7))
        high_flux = tuple(0.1 * (i + 1) for i in range(7))
        low_channel_label, high_channel_label, units, spacecraft = "P≥10 MeV", "P≥100 MeV", "pfu", "GOES-18"

    monkeypatch.setattr(sep_module, "load_sep_proton_range", lambda start, end, **k: Sep())
    png = _tiny_png()
    monkeypatch.setattr(hv, "fetch_preview_cached", lambda inst, chan, when, **k: hv.CachedPreview(png, when, "x", False))
    composer.fetch_missing()
    assert _wait_for(lambda: not composer.is_fetching(), 8.0)
    state = window._composer_state
    assert isinstance(state.sep, SepSeries) and state.sep.spacecraft == "GOES-18"
    assert len(state.thumbs) == 3
    assert all(item.source == "helioviewer" for item in state.thumbs.values())
    assert "Helioviewer" in composer.thumb_table.item(0, 2).text()
    assert collect_event_figure_data(window, composer.current_spec(), state).sep is state.sep
    composer.close()
    window.close()


def test_series_fetched_for_another_event_are_not_drawn(tmp_path):
    _app()
    from tests.helpers.learmonth import write_test_callisto_fit

    window = _main_with_data(tmp_path)
    state = window._composer_state
    spec = EventFigureSpec()
    first = collect_event_figure_data(window, spec, state)
    start = first.base_utc + timedelta(seconds=first.x_range[0])
    end = first.base_utc + timedelta(seconds=first.x_range[1])
    # What a fetch for this file leaves behind.
    state.sep = SepSeries((start, end), (1.0, 2.0), (0.1, 0.2), "P≥10 MeV", "P≥100 MeV", "pfu", "GOES-18")
    state.sep_for = (start, end)
    state.goes = [("GOES-18 XRS-B", np.array([0.0, 1800.0]), np.array([1e-6, 2e-6]))]
    state.goes_for = (first.base_utc, start, end)
    again = collect_event_figure_data(window, spec, state)
    assert again.sep is state.sep and again.goes == state.goes

    # Another day's file: the old series would be drawn at the wrong times.
    other = write_test_callisto_fit(
        tmp_path / "BIR_20240511_120000_01.fit",
        data=(np.arange(30 * 40) % 200).reshape(30, 40).astype(np.uint8),
        freqs=np.linspace(80.0, 20.0, 30), time=np.linspace(0.0, 1800.0, 40),
        date_obs="2024/05/11", time_obs="12:00:00",
    )
    window.open_fits_paths([str(other)])
    _flush()
    after = collect_event_figure_data(window, spec, state)
    assert after.base_utc == datetime(2024, 5, 11, 12, 0)
    assert after.sep is None and after.goes == []
    window.close()


def _tiny_png() -> bytes:
    import io

    from matplotlib import image as mpimg

    buffer = io.BytesIO()
    mpimg.imsave(buffer, np.zeros((8, 8, 3)), format="png")
    return buffer.getvalue()


def test_the_composer_spec_round_trips_through_the_project(tmp_path, monkeypatch):
    _app()
    from src.backend.session.project_session import read_project_meta
    from src.ui.app.main_window import MainWindow

    window = _main_with_data(tmp_path)
    spec = EventFigureSpec(panels=[PanelSpec("goes", True, 2.5), PanelSpec("spectrum", True, 3.0)],
                           thumbnails=[ThumbnailSpec(datetime(2024, 5, 10, 6, 40), "LASCO C2")],
                           size_preset="12 cm", title="My event")
    window._composer_state.set_spec(spec)
    project = tmp_path / "composer.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert window.save_project_as()
    assert read_project_meta(str(project))["parts"]["composer"] is True
    window.close()

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    restored = other._composer_state.spec
    assert [p.key for p in restored.panels][:2] == ["goes", "spectrum"]
    assert restored.panels[0].height_ratio == 2.5
    assert restored.thumbnails[0].source == "LASCO C2" and restored.size_preset == "12 cm"
    composer = other.open_event_figure_composer()
    _flush()
    assert composer.title_edit.text() == "My event"
    assert composer.size_combo.currentText() == "12 cm"
    composer.close()
    other.close()
