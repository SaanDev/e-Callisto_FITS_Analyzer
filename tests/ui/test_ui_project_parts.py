"""
e-CALLISTO FITS Analyzer
Offscreen tests for the one project file (src/ui/app/project_parts.py and the
main window's save/open): solar-only and GCS-only projects, link and embed
storage, the legacy .ecsolar import and the one shared GCS window.

Solar frames are light WcsMap fakes, loaded synchronously by replacing the
window's threaded FITS loader; GCS frames are synthetic sunpy maps handed
straight to the panels instead of downloading JP2s.
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.backend.session.project_session import read_project, read_project_meta
from src.ui.app.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _flush(times: int = 6):
    app = _app()
    for _ in range(times):
        app.processEvents()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No modal dialogs, and every cache under tmp_path."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    for name in ("exec", "exec_"):
        monkeypatch.setattr(QMessageBox, name, lambda self, *a, **k: 0, raising=False)
    monkeypatch.setenv("ECALLISTO_PROJECTS_CACHE", str(tmp_path / "projects_cache"))
    import src.ui.solar.sunpy_solar_viewer as viewer

    cache = tmp_path / "sunpy_cache"
    cache.mkdir()
    monkeypatch.setattr(viewer, "_default_cache_dir", lambda: cache)
    yield


# --------------------------------------------------------------------- solar


class CorWcsMap:
    """Minimal COR2 frame with a full arcsec WCS (2"/px, centre pixel 5,5)."""

    observatory = "STEREO_A"
    instrument = "SECCHI"
    detector = "COR2"
    wavelength = ""
    nickname = ""
    source = ""

    def __init__(self, data, *, date):
        self.data = np.asarray(data, dtype=float)
        self.date = date
        self.meta = {
            "instrume": "SECCHI", "detector": "COR2", "polar": 1001.0,
            "cdelt1": 2.0, "cdelt2": 2.0, "crpix1": 6.0, "crpix2": 6.0,
            "crval1": 0.0, "crval2": 0.0, "rsun_obs": 8.0,
        }


def _frames_for(paths):
    """One fake frame per file, timed by the file's position (16:00, 16:10, …)."""
    out = []
    for path in paths:
        index = int(open(path, "rb").read().decode("ascii").split("-")[1])
        out.append(CorWcsMap(np.ones((11, 11)), date=f"2012-07-12T16:{10 * index:02d}:00"))
    return out


def _sync_loader(monkeypatch):
    """Replace the threaded FITS loader with a synchronous one reading the fakes."""
    from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow

    calls = []

    def load_local_paths(self, paths):
        calls.append(list(paths))
        self._apply_loaded_frames(_frames_for(paths), paths=list(paths), metadata={})

    monkeypatch.setattr(SolarDataAnalysisWindow, "load_local_paths", load_local_paths)
    return calls


def _frame_files(tmp_path, count, folder="frames"):
    directory = tmp_path / folder
    directory.mkdir(exist_ok=True)
    paths = []
    for index in range(count):
        path = directory / f"cor2_{index}.fits"
        path.write_bytes(f"FITS-{index}".encode("ascii"))
        paths.append(str(path))
    return paths


def _solar_with_picks(window, paths):
    solar = window._ensure_solar_window()
    solar.load_local_paths(paths)
    _flush()
    solar.measurements_check.setChecked(True)
    solar.height_time_btn.setChecked(True)
    solar.tracking_panel.auto_advance_check.setChecked(False)
    for index, x in ((0, 4.0), (1, 8.0)):
        solar.frame_slider.setValue(index)
        _flush(2)
        solar._on_plot_canvas_click(x, 0.0, "left")
    return solar


def test_a_solar_only_project_can_be_saved_and_reopened_linked(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    paths = _frame_files(tmp_path, 3)
    window = MainWindow(theme=None)
    solar = _solar_with_picks(window, paths)
    assert window.raw_data is None
    assert window._project_dirty is True  # solar work alone makes the project dirty
    assert window.save_project_action.isEnabled()

    project = tmp_path / "solar_only.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    monkeypatch.setattr(window, "_ask_project_storage", lambda current: "link")
    assert window.save_project_as()
    assert window._project_dirty is False

    meta = read_project_meta(str(project))
    assert meta["schema_version"] == 2
    assert meta["parts"]["solar"] is True and meta["parts"]["radio"] is False
    assert meta["storage"] == "link"
    assert meta["solar"]["frame_paths"] == paths
    with zipfile.ZipFile(project) as zf:
        assert not [name for name in zf.namelist() if name.startswith("solar/")]
    solar.close()
    window.close()

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    reopened = other._solar_data_analysis_window
    assert reopened is not None and len(reopened._map_frames) == 3
    assert sorted(reopened._measure.picks) == [0, 1]
    assert other._project_dirty is False  # an open is not an edit
    reopened.close()
    other.close()


def test_an_embedded_solar_project_reopens_after_the_originals_are_gone(tmp_path, monkeypatch):
    _app()
    loads = _sync_loader(monkeypatch)
    paths = _frame_files(tmp_path, 3)
    window = MainWindow(theme=None)
    solar = _solar_with_picks(window, paths)

    project = tmp_path / "solar_embed.efaproj"
    assert window._write_project_to(str(project), "embed")
    _flush()
    with zipfile.ZipFile(project) as zf:
        embedded = sorted(name for name in zf.namelist() if name.startswith("solar/frames/"))
        assert len(embedded) == 3
        assert zf.getinfo(embedded[0]).compress_type == zipfile.ZIP_STORED
    solar.close()
    window.close()
    for path in paths:
        os.remove(path)

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    reopened = other._solar_data_analysis_window
    assert len(reopened._map_frames) == 3
    assert sorted(reopened._measure.picks) == [0, 1]
    unpacked = loads[-1]
    assert all(str(tmp_path / "projects_cache") in path for path in unpacked)
    assert other._project_storage == "embed"
    reopened.close()
    other.close()


def test_the_open_embedded_project_can_be_opened_again(tmp_path, monkeypatch):
    """Reopening (to discard changes) while its frames are on screen and memory-mapped."""
    _app()
    loads = _sync_loader(monkeypatch)
    window = MainWindow(theme=None)
    _solar_with_picks(window, _frame_files(tmp_path, 3))
    project = tmp_path / "reopen.efaproj"
    assert window._write_project_to(str(project), "embed")
    window._solar_data_analysis_window.close()
    window.close()

    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush()
    first = list(loads[-1])
    # Real FITS frames keep their files mapped; on Windows a mapped file can be
    # neither deleted nor rewritten.
    maps = [np.memmap(path, dtype=np.uint8, mode="r") for path in first]

    assert other.open_project_path(str(project))
    _flush()
    second = list(loads[-1])
    assert len(other._solar_data_analysis_window._map_frames) == 3
    assert sorted(other._solar_data_analysis_window._measure.picks) == [0, 1]
    assert {os.path.dirname(path) for path in second}.isdisjoint({os.path.dirname(path) for path in first})
    del maps
    other._solar_data_analysis_window.close()
    other.close()


def test_a_legacy_ecsolar_imports_as_an_untitled_dirty_project(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    from src.backend.session.solar_session import SOLAR_SESSION_MAGIC, serialize_picks

    picks = {1: (datetime(2012, 7, 12, 16, 10), 1.0, 8.0, 0.0, 270.0)}
    meta = {
        "magic": SOLAR_SESSION_MAGIC,
        "schema_version": 1,
        "source": {"frame_count": 3, "frame_times": [
            "2012-07-12T16:00:00", "2012-07-12T16:10:00", "2012-07-12T16:20:00"]},
        "view": {"current_frame_index": 1},
        "measurements": {"height_time_picks": serialize_picks(picks)},
        "frame_files": [],
    }
    legacy = tmp_path / "old_event.ecsolar"
    with zipfile.ZipFile(legacy, "w") as zf:
        for index in range(3):
            arcname = f"frames/{index:04d}_cor2_{index}.fits"
            zf.writestr(arcname, f"FITS-{index}".encode("ascii"))
            meta["frame_files"].append({"arcname": arcname, "name": f"cor2_{index}.fits"})
        zf.writestr("meta.json", json.dumps(meta))

    window = MainWindow(theme=None)
    assert window.open_dropped_paths([str(legacy)])
    _flush()
    solar = window._solar_data_analysis_window
    assert len(solar._map_frames) == 3
    assert sorted(solar._measure.picks) == [1]
    assert window._project_path is None
    assert window._project_dirty is True
    # Saving it writes a project, never an .ecsolar.
    project = tmp_path / "converted.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    monkeypatch.setattr(window, "_ask_project_storage", lambda current: "link")
    assert window.save_project()
    assert read_project_meta(str(project))["parts"]["solar"] is True
    solar.close()
    window.close()


def test_ctrl_s_in_the_solar_window_saves_the_one_project(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    window = MainWindow(theme=None)
    solar = _solar_with_picks(window, _frame_files(tmp_path, 2))
    project = tmp_path / "via_solar.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    monkeypatch.setattr(window, "_ask_project_storage", lambda current: "link")
    assert solar.save_project_action.isEnabled()
    solar.save_project_action.trigger()
    assert project.is_file()
    assert window._project_path == str(project)
    solar.close()
    window.close()


def test_autosave_snapshots_use_link_mode(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    window = MainWindow(theme=None)
    solar = _solar_with_picks(window, _frame_files(tmp_path, 2))
    saved = {}

    def fake_snapshot(**kwargs):
        saved.update(kwargs)
        return "x"

    monkeypatch.setattr("src.ui.app.main_window.save_recovery_snapshot", fake_snapshot)
    window._perform_autosave()
    assert saved["meta"]["storage"] == "link"
    assert saved["meta"]["parts"]["solar"] is True
    solar.close()
    window.close()


# ----------------------------------------------------------------------- GCS


def _sequence(observer_lon_deg: float, *, n=3, cadence_min=12):
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import frames
    from sunpy.map.header_helper import make_fitswcs_header

    out = []
    base = datetime(2012, 7, 12, 16, 0, 0)
    for index in range(n):
        stamp = (base + timedelta(minutes=index * cadence_min)).isoformat()
        observer = SkyCoord(observer_lon_deg * u.deg, 0 * u.deg, 1.0 * u.AU,
                            frame=frames.HeliographicStonyhurst, obstime=stamp)
        data = 10.0 + index + np.random.default_rng(index).random((48, 48))
        ref = SkyCoord(0 * u.arcsec, 0 * u.arcsec, obstime=stamp, observer=observer, frame="helioprojective")
        header = make_fitswcs_header(data, ref, scale=[112, 112] * u.arcsec / u.pix,
                                     instrument="LASCO", detector="C2", exposure=1.0 * u.s)
        out.append(sunpy.map.Map(data, header))
    return out


def _fake_fetch(monkeypatch, longitudes):
    """Panels 'load' synthetic frames instantly instead of downloading JP2s."""
    pytest.importorskip("sunpy.map")
    from src.ui.gcs.gcs_viewpoint_panel import GCSViewpointPanel

    fetched = []

    def start_fetch(self):
        fetched.append(self.label)
        self.set_frames(_sequence(longitudes[self.label]))
        return True

    monkeypatch.setattr(GCSViewpointPanel, "start_fetch", start_fetch)
    return fetched


def test_a_gcs_only_project_restores_fits_and_points_by_frame_time(tmp_path, monkeypatch):
    _app()
    longitudes = {"A": 0.0, "B": 62.0, "C": -70.0}
    fetched = _fake_fetch(monkeypatch, longitudes)
    window = MainWindow(theme=None)
    gcs = window.open_gcs_fitting_window(datetime(2012, 7, 12, 16, 12))
    for panel in gcs.panels:
        panel.start_fetch()
    _flush()
    gcs.time_slider.setValue(1)
    _flush()
    shared = gcs._shared_time
    gcs._on_canvas_click("B", 300.0, 400.0, "left")
    gcs._on_canvas_click("B", 320.0, 380.0, "left")
    gcs._on_commit()
    assert len(gcs._fits) == 1
    gcs.panels[1].colormap_combo.setCurrentText("viridis")
    gcs._on_difference_mode("base")
    assert window._project_dirty is True

    project = tmp_path / "gcs_only.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    monkeypatch.setattr(window, "_ask_project_storage", lambda current: "link")
    assert window.save_project_as()
    meta = read_project_meta(str(project))
    assert meta["parts"]["gcs"] is True and meta["parts"]["radio"] is False
    rows = meta["gcs"]["tracks"]["gcs"]["frame_points"]
    assert rows and all("frame_time" in row for row in rows)  # never an id
    gcs.close()
    window.close()

    fetched.clear()
    other = MainWindow(theme=None)
    assert other.open_project_path(str(project))
    _flush(10)
    restored = other._gcs_window
    assert sorted(fetched) == ["A", "B", "C"]
    assert restored._shared_time == shared
    assert len(restored._fits) == 1
    assert restored._clicks["B"] == [(300.0, 400.0), (320.0, 380.0)]
    assert restored._difference_mode() == "base"
    assert restored.panels[1].colormap_combo.currentText() == "viridis"
    assert other._project_dirty is False
    restored.close()
    other.close()


def test_embedded_jp2s_go_into_the_cache_and_seed_the_listing(tmp_path, monkeypatch):
    _app()
    from src.backend.solar import helioviewer_jp2 as hvjp2

    longitudes = {"A": 0.0, "B": 62.0, "C": -70.0}
    _fake_fetch(monkeypatch, longitudes)
    window = MainWindow(theme=None)
    gcs = window.open_gcs_fitting_window(datetime(2012, 7, 12, 16, 12))
    panel = gcs.panels[1]
    panel.start_fetch()
    _flush()
    # Pretend these frames came from the archive: listing times and cached files.
    source = panel.source()
    when = datetime(2012, 7, 12, 16, 0, 0)
    panel.picked_times = (when,)
    cached = hvjp2.cache_path(panel.jp2_cache_directory(), source, when)
    cached.write_bytes(b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"j" * 64)
    meta, files = gcs.project_part_payload("embed")
    arcname = f"gcs/jp2/B/{cached.name}"
    assert files == {arcname: str(cached)}
    assert meta["panels"][1]["jp2_members"] == [arcname]
    gcs.close()
    window.close()

    cached.unlink()
    unpacked = tmp_path / "unpacked.jp2"
    unpacked.write_bytes(b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"j" * 64)
    other = MainWindow(theme=None)
    restored = other._ensure_gcs_window()
    restored.restore_project_state(meta, {arcname: str(unpacked)})
    _flush(8)
    assert cached.is_file()
    start, end = restored.panels[1].date_range()
    key = hvjp2.listing_cache_key(source, start, end)
    assert hvjp2._SEEDED_LISTINGS[key] == [when]
    restored.close()
    other.close()


def test_the_menu_action_opens_the_gcs_window():
    # QAction.triggered passes checked=False, which once landed in target_time
    # ("'bool' object has no attribute 'tzinfo'").
    _app()
    window = MainWindow(theme=None)
    try:
        window.open_gcs_fitting_action.trigger()
        _flush()
        gcs = window._gcs_window
        assert gcs is not None and gcs.isVisible()
        assert not isinstance(gcs._requested_time, bool)
        window.open_gcs_fitting_action.trigger()  # again: the same window comes forward
        _flush()
        assert window._gcs_window is gcs
    finally:
        window.close()


def test_the_solar_and_main_windows_share_one_gcs_window(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    window = MainWindow(theme=None)
    solar = window._ensure_solar_window()
    solar.load_local_paths(_frame_files(tmp_path, 2))
    _flush()
    solar.open_gcs_fitting_window()
    gcs = solar._gcs_window
    assert gcs is window._gcs_window
    window.open_gcs_fitting_window()
    assert window._gcs_window is gcs
    # Handing over the solar frame's moment retargets the shared window.
    assert gcs._requested_time == datetime(2012, 7, 12, 16, 0)
    solar.close()
    gcs.close()
    window.close()


def test_closing_the_main_window_closes_the_gcs_window(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    gcs = window.open_gcs_fitting_window()
    closed = []
    gcs.destroyed.connect(lambda *_: closed.append(True))
    window.close()
    _flush()
    assert window._gcs_window is None


def test_opening_a_radio_project_clears_a_stale_solar_analysis(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    from tests.helpers.learmonth import write_test_callisto_fit

    fits_path = write_test_callisto_fit(
        tmp_path / "STAT_20240101_120000_01.fit",
        data=np.arange(12, dtype=np.uint8).reshape(3, 4),
        freqs=np.array([60.0, 50.0, 40.0]),
        time=np.array([0.0, 225.0, 450.0, 675.0]),
        date_obs="2024/01/01",
        time_obs="12:00:00",
    )
    radio = MainWindow(theme=None)
    radio.open_fits_paths([str(fits_path)])
    _flush()
    project = tmp_path / "radio.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    assert radio.save_project_as()
    radio.close()

    window = MainWindow(theme=None)
    solar = _solar_with_picks(window, _frame_files(tmp_path, 2))
    assert window.open_project_path(str(project))
    _flush()
    assert window.raw_data is not None
    assert solar._map_frames == []
    assert solar._measure.picks == {}
    payload = read_project(str(project))
    assert payload.meta["parts"]["radio"] is True
    solar.close()
    window.close()


def test_opening_a_solar_only_project_clears_the_radio_spectrum(tmp_path, monkeypatch):
    _app()
    _sync_loader(monkeypatch)
    from tests.helpers.learmonth import write_test_callisto_fit

    window = MainWindow(theme=None)
    solar = _solar_with_picks(window, _frame_files(tmp_path, 2))
    project = tmp_path / "solar.efaproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(project), ""))
    monkeypatch.setattr(window, "_ask_project_storage", lambda current: "link")
    assert window.save_project_as()
    solar.close()
    window.close()

    fits_path = write_test_callisto_fit(
        tmp_path / "STAT_20240101_120000_01.fit",
        data=np.arange(12, dtype=np.uint8).reshape(3, 4),
        freqs=np.array([60.0, 50.0, 40.0]),
        time=np.array([0.0, 225.0, 450.0, 675.0]),
        date_obs="2024/01/01",
        time_obs="12:00:00",
    )
    other = MainWindow(theme=None)
    other.open_fits_paths([str(fits_path)])
    _flush()
    assert other.raw_data is not None
    assert other.open_project_path(str(project))
    _flush()
    assert other.raw_data is None
    assert other.filename == ""
    assert len(other._solar_data_analysis_window._map_frames) == 2
    assert other.save_project_action.isEnabled()
    other._solar_data_analysis_window.close()
    other.close()


# ------------------------------------------------------- damaged sections


def _radio_fits(tmp_path):
    from tests.helpers.learmonth import write_test_callisto_fit

    return write_test_callisto_fit(
        tmp_path / "BIR_20240510_063000_01.fit",
        data=(np.arange(40 * 60) % 200).reshape(40, 60).astype(np.uint8),
        freqs=np.linspace(80.0, 20.0, 40),
        time=np.linspace(0.0, 1800.0, 60),
        date_obs="2024/05/10",
        time_obs="06:30:00",
    )


def _snapshot_with_event_context(tmp_path, events):
    """A recovery snapshot holding radio data and an event-context section."""
    from src.backend.session.project_session import STORAGE_LINK, write_project
    from src.backend.space_weather import event_catalogs as ec

    window = MainWindow(theme=None)
    try:
        window.open_fits_paths([str(_radio_fits(tmp_path))])
        _flush()
        controller = window._event_context
        controller.result = ec.EventContextResult(datetime(2024, 5, 10), datetime(2024, 5, 11))
        controller._result_key = controller.request_key()
        meta, arrays, _files = window._project_coordinator.capture(STORAGE_LINK)
    finally:
        window.close()
    assert meta["parts"]["event_context"] is True
    meta["event_context"]["result"]["events"] = events
    path = tmp_path / "autosave.efaproj"
    write_project(str(path), meta=meta, arrays=arrays)
    return str(path)


def test_a_snapshot_with_events_in_an_unexpected_shape_still_recovers(tmp_path, monkeypatch):
    # The macOS report: "events" stored as a list crashed Recover Last Session.
    _app()
    path = _snapshot_with_event_context(tmp_path, ["2024-05-10", "2024-05-11"])
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    other = MainWindow(theme=None)
    try:
        assert other._load_snapshot_path(path) is True
        assert other.raw_data is not None
        assert other._event_context.result is not None and not any(other._event_context.result.events.values())
        assert warned == []
    finally:
        other.close()


def test_a_part_that_cannot_be_restored_is_skipped_and_named(tmp_path, monkeypatch):
    _app()
    from src.ui.app.event_context_strip import EventContextController

    path = _snapshot_with_event_context(tmp_path, {})

    def broken(self, meta, arrays):
        raise ValueError("dictionary update sequence element #0 has length 10; 2 is required")

    monkeypatch.setattr(EventContextController, "project_restore", broken)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    other = MainWindow(theme=None)
    try:
        assert other._load_snapshot_path(path) is True
        assert other.raw_data is not None  # the radio part still came back
        assert other._event_context.result is None
        assert other._project_coordinator.failed_parts[0][0] == "event_context"
        assert len(warned) == 1 and "Event context strip" in warned[0] and "length 10" in warned[0]
    finally:
        other.close()
