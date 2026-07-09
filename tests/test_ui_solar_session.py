"""
e-CALLISTO FITS Analyzer
Offscreen tests for self-contained solar analysis sessions
(save/open wiring in src/UI/solar_data_analysis_window.py).

Frames are lightweight WcsMap fakes and are loaded through _apply_loaded_frames
directly, matching tests/test_ui_solar_measure_tools.py. The async file reload
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

from src.Backend.solar_session import SOLAR_SESSION_MAGIC, read_solar_session
from src.UI.solar_data_analysis_window import SolarDataAnalysisWindow


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
