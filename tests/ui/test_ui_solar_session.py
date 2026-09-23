"""
e-CALLISTO FITS Analyzer
Offscreen tests for self-contained solar analysis sessions
(save/open wiring in src/ui/solar/solar_data_analysis_window.py).

Frames are lightweight WcsMap fakes and are loaded through _apply_loaded_frames
directly, matching tests/ui/test_ui_solar_measure_tools.py. The async file reload
that open_session normally kicks off is simulated by setting
_pending_session_restore and calling _apply_loaded_frames, which fires the same
restore hook.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.backend.session.solar_session import SOLAR_SESSION_MAGIC, read_solar_session
from src.ui.solar.solar_data_analysis_window import SolarDataAnalysisWindow


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Modal message boxes block forever offscreen — stub them out.

    Both the static helpers (QMessageBox.information/…) and the instance .exec()
    used by _on_worker_failed enter a blocking modal loop, so neutralise both.
    """
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    for name in ("exec", "exec_"):
        monkeypatch.setattr(QMessageBox, name, lambda self, *a, **k: 0, raising=False)


def _app():
    return QApplication.instance() or QApplication([])


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
            "instrume": "SECCHI",
            "detector": "COR2",
            "polar": 1001.0,
            "cdelt1": 2.0,
            "cdelt2": 2.0,
            "crpix1": 6.0,
            "crpix2": 6.0,
            "crval1": 0.0,
            "crval2": 0.0,
            "rsun_obs": 8.0,
        }


def _three_frames():
    return [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:20:00"),
    ]


def _write_frame_files(tmp_path, count):
    paths = []
    for i in range(count):
        p = tmp_path / f"frame_{i}.fits"
        p.write_bytes(f"FITS-{i}".encode("ascii"))
        paths.append(str(p))
    return paths


def _load(win, frames, paths):
    win._apply_loaded_frames(frames, paths=paths, metadata={})
    QApplication.processEvents()


def _add_two_picks(win):
    """Two height-time picks on frames 0 and 1 (leading edge marching out)."""
    win.measurements_check.setChecked(True)  # unlock the measurement tools
    win.height_time_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(False)
    win.frame_slider.setValue(0)
    QApplication.processEvents()
    win._measure.on_canvas_click(4.0, 0.0, "left")  # 0.5 R☉
    win.frame_slider.setValue(1)
    QApplication.processEvents()
    win._measure.on_canvas_click(8.0, 0.0, "left")  # 1.0 R☉


def test_collect_session_meta_captures_display_and_picks(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))

    win.colormap_combo.setCurrentText("soholasco2")
    win.movie_content_combo.setCurrentText("Running Difference")
    _add_two_picks(win)
    win._set_frame_index(2)

    meta = win._collect_session_meta()
    assert meta["view"]["colormap"] == "soholasco2"
    assert meta["view"]["difference_mode"] == "Running Difference"
    assert meta["view"]["current_frame_index"] == 2
    assert meta["source"]["frame_count"] == 3
    picks = meta["measurements"]["height_time_picks"]
    assert len(picks) == 2
    assert picks[0]["frame_index"] == 0
    assert picks[0]["height_rsun"] == pytest.approx(0.5, abs=1e-6)
    win.close()


def test_save_session_embeds_frame_bytes(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    paths = _write_frame_files(tmp_path, 3)
    _load(win, _three_frames(), paths)

    out = tmp_path / "event.ecsolar"
    assert win._write_session_to(str(out)) is True
    assert win._session_path == str(out)

    result = read_solar_session(str(out), extract_dir=str(tmp_path / "restore"))
    assert result.meta["magic"] == SOLAR_SESSION_MAGIC
    assert len(result.frame_paths) == 3
    for i, path in enumerate(result.frame_paths):
        with open(path, "rb") as fh:
            assert fh.read() == f"FITS-{i}".encode("ascii")
    win.close()


def test_save_session_requires_loaded_frames(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    # Nothing loaded: save must not write a file (guard dialog is stubbed).
    assert win.save_session_as() is False
    assert win._session_path is None
    win.close()


def test_restore_hook_replays_view_and_picks(tmp_path):
    _app()
    # 1) Build a session's meta from a fully configured window.
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    win.colormap_combo.setCurrentText("soholasco2")
    win.movie_content_combo.setCurrentText("Running Difference")
    win.colorbar_check.setChecked(False)
    _add_two_picks(win)
    win._set_frame_index(2)
    meta = win._collect_session_meta()
    win.close()

    # 2) A fresh window reloads the same frames with the restore pending — this
    #    is exactly what open_session sets up before the async load lands.
    reload_dir = tmp_path / "b"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    _load(win2, _three_frames(), _write_frame_files(reload_dir, 3))

    assert win2._pending_session_restore is None
    assert win2.colormap_combo.currentText() == "soholasco2"
    assert win2.movie_content_combo.currentText() == "Running Difference"
    assert win2.colorbar_check.isChecked() is False
    assert win2._current_frame_index == 2
    assert len(win2._measure.picks) == 2
    # The panel is visible and the fit was recomputed from the restored picks.
    assert not win2.tracking_panel.isHidden()
    assert "km/s" in win2.tracking_panel.speed_label.text()
    win2.close()


def test_restore_clamps_out_of_range_picks(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_picks(win)
    meta = win._collect_session_meta()
    # Forge a pick on a frame that won't exist after a 2-frame reload.
    meta["measurements"]["height_time_picks"].append(
        {"frame_index": 9, "time": "2012-07-12T16:40:00", "height_rsun": 2.0,
         "x_arc": 16.0, "y_arc": 0.0, "pa_deg": 270.0}
    )
    win.close()

    reload_dir = tmp_path / "c"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    two = [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
    ]
    _load(win2, two, _write_frame_files(reload_dir, 2))
    # Picks 0 and 1 survive; the frame-9 pick is dropped.
    assert set(win2._measure.picks.keys()) == {0, 1}
    win2.close()


def test_failed_load_clears_pending_restore():
    _app()
    win = SolarDataAnalysisWindow()
    win._pending_session_restore = {"view": {}}
    win._on_worker_failed("boom\nValueError: bad frame")
    assert win._pending_session_restore is None
    win.close()


# --------------------------------------------------------------------------- #
# Circle fits
# --------------------------------------------------------------------------- #
def _add_two_circles(win):
    """Circle fits on frames 0 and 1 (a front expanding 0.5 -> 1.0 R☉)."""
    win.measurements_check.setChecked(True)
    win.circle_tool_btn.setChecked(True)
    win.tracking_panel.auto_advance_check.setChecked(True)
    win.frame_slider.setValue(0)
    QApplication.processEvents()
    for radius in (4.0, 8.0):
        for dx, dy in ((radius, 0.0), (0.0, radius), (-radius, 0.0)):
            win._measure.on_canvas_click(dx, dy, "left")
        win._measure.commit_circle()


def test_collect_session_meta_captures_circle_fits(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_circles(win)

    meta = win._collect_session_meta()
    circles = meta["measurements"]["circle_fits"]
    assert [row["frame_index"] for row in circles] == [0, 1]
    assert circles[0]["radius_rsun"] == pytest.approx(0.5, abs=1e-6)
    assert circles[1]["radius_rsun"] == pytest.approx(1.0, abs=1e-6)
    assert circles[0]["height_rsun"] == pytest.approx(2.0, abs=1e-6)  # 1 + 2 * 0.5
    assert circles[1]["height_rsun"] == pytest.approx(3.0, abs=1e-6)
    assert circles[0]["n_points"] == 3
    # The clicked arcs ride along so a reopened session can be edited, not redone.
    assert [row["frame_index"] for row in meta["measurements"]["circle_points"]] == [0, 1]
    assert meta["measurements"]["circle_lock_center"] is None
    win.close()


def test_restore_hook_replays_circle_fits(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_circles(win)
    meta = win._collect_session_meta()
    win.close()

    reload_dir = tmp_path / "b"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    _load(win2, _three_frames(), _write_frame_files(reload_dir, 3))

    assert len(win2._measure.circles) == 2
    assert win2._measure.circles[1].radius_rsun == pytest.approx(1.0, abs=1e-6)
    assert win2._measure.circles[1].height_rsun == pytest.approx(3.0, abs=1e-6)
    assert win2._measure._circle_points[0]  # the clicked arc came back too
    # The circle tool owns the panel again and the kinematics were recomputed.
    assert win2.circle_tool_btn.isChecked() is True
    assert win2.tracking_panel.table.columnCount() == 6
    assert "km/s" in win2.tracking_panel.speed_label.text()
    win2.close()


def test_restore_of_a_legacy_circle_session_fits_the_bubble_height(tmp_path):
    """Sessions saved before the height was stored carry only the radius; the
    reopened analysis must fit 1 R☉ + 2r, not the radius those builds fitted."""
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_circles(win)
    meta = win._collect_session_meta()
    for row in meta["measurements"]["circle_fits"]:
        row.pop("height_rsun")
    win.close()

    reload_dir = tmp_path / "legacy"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    _load(win2, _three_frames(), _write_frame_files(reload_dir, 3))

    assert win2._measure.circles[0].height_rsun == pytest.approx(2.0, abs=1e-6)
    assert win2._measure.circles[1].height_rsun == pytest.approx(3.0, abs=1e-6)
    _, plotted = win2.tracking_panel._scatter.getData()
    assert list(plotted) == pytest.approx([2.0, 3.0], abs=1e-6)
    win2.close()


def test_restore_clamps_out_of_range_circles(tmp_path):
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_circles(win)
    meta = win._collect_session_meta()
    meta["measurements"]["circle_fits"].append(
        {"frame_index": 9, "time": "2012-07-12T16:40:00", "radius_rsun": 2.0,
         "center_x_arc": 0.0, "center_y_arc": 0.0, "radius_arcsec": 16.0,
         "leading_edge_rsun": 2.0, "rms_arcsec": 0.0, "n_points": 3, "center_pa_deg": 0.0}
    )
    win.close()

    reload_dir = tmp_path / "c"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    two = [
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:00:00"),
        CorWcsMap(np.ones((11, 11)), date="2012-07-12T16:10:00"),
    ]
    _load(win2, two, _write_frame_files(reload_dir, 2))
    assert set(win2._measure.circles.keys()) == {0, 1}
    win2.close()


def test_restore_of_a_session_saved_before_circle_fitting(tmp_path):
    """Old sessions carry no circle keys — they must still restore cleanly."""
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_picks(win)
    meta = win._collect_session_meta()
    for key in ("circle_fits", "circle_points", "circle_lock_center"):
        meta["measurements"].pop(key)
    win.close()

    reload_dir = tmp_path / "d"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    _load(win2, _three_frames(), _write_frame_files(reload_dir, 3))

    assert win2._measure.circles == {}
    assert len(win2._measure.picks) == 2
    assert win2.circle_tool_btn.isChecked() is False
    assert "km/s" in win2.tracking_panel.speed_label.text()
    win2.close()


def test_session_round_trips_the_fit_order(tmp_path):
    """A cubic analysis must not reopen as a straight line — the speeds differ."""
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _add_two_picks(win)
    win.tracking_panel.fit_order_combo.setCurrentIndex(1)  # quadratic
    QApplication.processEvents()

    meta = win._collect_session_meta()
    assert meta["view"]["fit_order"] == 2
    win.close()

    reload_dir = tmp_path / "e"
    reload_dir.mkdir()
    win2 = SolarDataAnalysisWindow()
    win2._pending_session_restore = meta
    _load(win2, _three_frames(), _write_frame_files(reload_dir, 3))

    assert win2.tracking_panel.fit_order() == 2
    win2.close()


def _select(win, label: str) -> int:
    index = win.wavelength_combo.findText(label)
    assert index >= 0, f"observable {label!r} not found"
    win.wavelength_combo.setCurrentIndex(index)
    return index


def test_session_round_trips_the_observable_by_userdata(tmp_path):
    """The saved observable must survive a change to the combo's ordering.

    Sessions used to store only the combo position, so adding a mission to the
    selector silently shifted every later observable in an existing session.
    """
    _app()
    win = SolarDataAnalysisWindow()
    _load(win, _three_frames(), _write_frame_files(tmp_path, 3))
    _select(win, "SOHO/EIT 284 A")

    meta = win._collect_session_meta()
    assert meta["source"]["observable_data"] == ["EIT", 284.0]
    win.close()

    win2 = SolarDataAnalysisWindow()
    # Restore from a position that points somewhere else entirely: the userData
    # has to win, not the stale index.
    restored = dict(meta)
    restored["source"] = {**meta["source"], "observable_index": 0}
    win2._restore_source_widgets(restored["source"])
    assert win2._current_observable() == ("EIT", 284.0)
    win2.close()


def test_session_restore_falls_back_to_index_for_legacy_sessions(tmp_path):
    """Sessions written before observable_data existed still restore."""
    _app()
    win = SolarDataAnalysisWindow()
    index = _select(win, "SOHO/LASCO C3")
    expected = win._current_observable()

    _select(win, "AIA 193 A")
    win._restore_source_widgets({"observable_index": index})
    assert win._current_observable() == expected
    win.close()


def test_session_restore_ignores_an_observable_that_no_longer_exists():
    _app()
    win = SolarDataAnalysisWindow()
    _select(win, "AIA 193 A")
    before = win._current_observable()
    # A mission that was dropped from the selector leaves the combo alone
    # rather than restoring an arbitrary neighbour.
    win._restore_source_widgets({"observable_data": ["MDI", "magnetogram"]})
    assert win._current_observable() == before
    win.close()
