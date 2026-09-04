"""
Extending and trimming a loaded dataset from the main window sidebar.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("astropy")
pytest.importorskip("matplotlib")

from astropy.io import fits
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from src.UI.main_window import MainWindow

NROWS = 20
NCOLS = 40
#: 40 samples at 22.5 s spans exactly one 900 s observation.
SAMPLE_STEP_S = 22.5


def _app():
    return QApplication.instance() or QApplication([])


def _write_fits(folder, stamp: str, clock: str, focus: str = "01", station: str = "BIR"):
    header = fits.Header()
    header["TIME-OBS"] = clock
    header["DATE-OBS"] = "2024-01-02"
    header["FREQMIN"] = 45.0
    header["FREQMAX"] = 79.0
    header["CRVAL1"] = 0.0
    header["CRPIX1"] = 1.0
    header["CDELT1"] = SAMPLE_STEP_S
    name = f"{station}_20240102_{stamp}_{focus}.fit"
    data = np.random.default_rng(abs(hash(name)) % 997).integers(
        0, 200, (NROWS, NCOLS)
    ).astype(np.uint8)
    path = folder / name
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)
    return str(path)


@pytest.fixture
def archive(tmp_path):
    """Four consecutive single-focus observations on disk."""
    folder = tmp_path / "bir"
    folder.mkdir()
    return {
        "10:00": _write_fits(folder, "100000", "10:00:00"),
        "10:15": _write_fits(folder, "101500", "10:15:00"),
        "10:30": _write_fits(folder, "103000", "10:30:00"),
        "10:45": _write_fits(folder, "104500", "10:45:00"),
    }


@pytest.fixture
def offline(monkeypatch):
    """Keep the tests off the network: only the on-disk siblings exist."""
    from src.Backend import callisto_timeline

    def _no_archive(state, direction, **_kwargs):
        raise callisto_timeline.TimelineUnavailable(
            f"No {state.station} data available (offline test)."
        )

    monkeypatch.setattr(callisto_timeline, "find_archive_segment", _no_archive)
    return monkeypatch


@pytest.fixture
def window(archive, offline):
    _app()
    win = MainWindow(theme=None)
    win.timeline_prefetch_chk.setChecked(False)
    win.load_fits_into_main(archive["10:15"])
    QApplication.processEvents()
    yield win
    win.close()


def _run_extend(win, direction: str, timeout_ticks: int = 800):
    win.extend_timeline(direction)
    for _ in range(timeout_ticks):
        QApplication.processEvents()
        if win._timeline_thread is None:
            return
        QThread.msleep(5)
    raise AssertionError("timeline extension did not finish")


def _segment_labels(win):
    return [win.timeline_list.item(i).text() for i in range(win.timeline_list.count())]


def _basenames(win):
    return [os.path.basename(p) for p in win._combined_sources]


# -----------------------------
# Panel state
# -----------------------------
def test_timeline_panel_describes_a_single_loaded_file(window):
    assert window.timeline_group.isEnabled()
    assert _segment_labels(window) == ["10:15   01"]
    assert "BIR" in window.timeline_status_label.text()
    assert "1 observation" in window.timeline_status_label.text()
    assert window.timeline_trim_start_btn.isEnabled() is False
    assert window.timeline_trim_end_btn.isEnabled() is False


def test_timeline_panel_is_disabled_without_a_dataset(offline):
    _app()
    win = MainWindow(theme=None)
    try:
        assert win.timeline_group.isEnabled() is False
        assert _segment_labels(win) == []
        assert "Load a CALLISTO file" in win.timeline_status_label.text()
    finally:
        win.close()


# -----------------------------
# Extending
# -----------------------------
def test_extend_next_appends_a_time_combined_observation(window):
    _run_extend(window, "next")

    assert window.raw_data.shape == (NROWS, NCOLS * 2)
    assert window._is_combined is True
    assert window._combined_mode == "time"
    assert _basenames(window) == [
        "BIR_20240102_101500_01.fit", "BIR_20240102_103000_01.fit"
    ]
    assert _segment_labels(window) == ["10:15   01", "10:30   01"]


def test_extend_uses_local_files_without_touching_the_archive(window, monkeypatch):
    def _no_network(*_args, **_kwargs):
        raise AssertionError("the archive must not be contacted when siblings exist")

    monkeypatch.setattr("src.Backend.callisto_timeline.find_archive_segment", _no_network)

    _run_extend(window, "next")

    assert window.raw_data.shape == (NROWS, NCOLS * 2)


def test_multi_step_extend_adds_several_observations_at_once(window):
    window.timeline_step_spin.setValue(2)

    _run_extend(window, "next")

    assert window.raw_data.shape == (NROWS, NCOLS * 3)
    assert _segment_labels(window) == ["10:15   01", "10:30   01", "10:45   01"]


def test_extend_stops_with_a_reason_at_the_edge_of_the_archive(window):
    window.timeline_step_spin.setValue(1)
    _run_extend(window, "previous")  # 10:00 exists
    assert _segment_labels(window)[0] == "10:00   01"

    _run_extend(window, "previous")  # 09:45 does not

    assert _segment_labels(window)[0] == "10:00   01", "the dataset must be left alone"
    assert "offline test" in window.timeline_status_label.text()


# -----------------------------
# Coordinate handling
# -----------------------------
def test_appending_leaves_annotation_coordinates_untouched(window):
    window._annotations = [
        {"kind": "line", "points": [[450.0, 60.0], [500.0, 55.0]], "visible": True}
    ]

    _run_extend(window, "next")

    assert window._annotations[0]["points"][0][0] == pytest.approx(450.0)
    assert window._annotations[0]["points"][1][0] == pytest.approx(500.0)


def test_prepending_shifts_every_time_based_marking(window):
    window._annotations = [
        {"kind": "line", "points": [[450.0, 60.0], [500.0, 55.0]], "visible": True}
    ]
    window.drift_points = [(450.0, 60.0)]
    from src.Backend.measurements import calculate_two_point_measurement

    window._measurement_result = calculate_two_point_measurement((100.0, 70.0), (200.0, 50.0))

    _run_extend(window, "previous")

    span = NCOLS * SAMPLE_STEP_S  # one prepended observation
    assert window._annotations[0]["points"][0][0] == pytest.approx(450.0 + span)
    assert window._annotations[0]["points"][1][0] == pytest.approx(500.0 + span)
    assert window.drift_points[0][0] == pytest.approx(450.0 + span)
    assert window._measurement_result.point1.time_s == pytest.approx(100.0 + span)
    assert window._measurement_result.point2.time_s == pytest.approx(200.0 + span)
    # The ruler's derived values describe the same feature, so they must not move.
    assert window._measurement_result.duration_s == pytest.approx(100.0)


def test_prepending_keeps_the_view_on_the_same_real_time(window):
    window.plot_data(window.raw_data, title="Raw")
    QApplication.processEvents()
    window.canvas.ax.set_xlim(400.0, 500.0)
    QApplication.processEvents()

    _run_extend(window, "previous")
    QApplication.processEvents()

    span = NCOLS * SAMPLE_STEP_S
    low, high = window.canvas.ax.get_xlim()
    assert low == pytest.approx(400.0 + span, abs=1.0)
    assert high == pytest.approx(500.0 + span, abs=1.0)


# -----------------------------
# Undo
# -----------------------------
def test_undo_restores_the_arrays_and_the_source_list(window):
    _run_extend(window, "next")
    assert window.raw_data.shape == (NROWS, NCOLS * 2)

    window.undo()
    QApplication.processEvents()

    assert window.raw_data.shape == (NROWS, NCOLS)
    assert [os.path.basename(p) for p in window._timeline_source_paths()] == [
        "BIR_20240102_101500_01.fit"
    ]
    assert window._is_combined is False
    assert _segment_labels(window) == ["10:15   01"]


def test_undo_restores_shifted_annotations(window):
    window._annotations = [
        {"kind": "line", "points": [[450.0, 60.0], [500.0, 55.0]], "visible": True}
    ]

    _run_extend(window, "previous")
    assert window._annotations[0]["points"][0][0] != pytest.approx(450.0)

    window.undo()
    QApplication.processEvents()

    assert window._annotations[0]["points"][0][0] == pytest.approx(450.0)


# -----------------------------
# Trimming
# -----------------------------
def test_trim_end_drops_the_latest_observation(window):
    window.timeline_step_spin.setValue(2)
    _run_extend(window, "next")
    assert len(_segment_labels(window)) == 3

    window.trim_timeline("end")
    QApplication.processEvents()

    assert _segment_labels(window) == ["10:15   01", "10:30   01"]
    assert window.raw_data.shape == (NROWS, NCOLS * 2)


def test_trim_start_drops_the_earliest_and_re_bases_the_axis(window):
    window._annotations = [
        {"kind": "line", "points": [[450.0, 60.0], [500.0, 55.0]], "visible": True}
    ]
    _run_extend(window, "previous")
    span = NCOLS * SAMPLE_STEP_S
    assert window._annotations[0]["points"][0][0] == pytest.approx(450.0 + span)

    window.trim_timeline("start")
    QApplication.processEvents()

    assert _segment_labels(window) == ["10:15   01"]
    assert window._annotations[0]["points"][0][0] == pytest.approx(450.0)


def test_trimming_to_one_observation_becomes_a_plain_single_file(window):
    _run_extend(window, "next")

    window.trim_timeline("end")
    QApplication.processEvents()

    assert window._is_combined is False
    assert window._combined_mode is None
    assert window.filename == "BIR_20240102_101500_01.fit"
    assert window.raw_data.shape == (NROWS, NCOLS)
    assert window.timeline_trim_start_btn.isEnabled() is False
    assert window.timeline_trim_end_btn.isEnabled() is False


def test_trim_is_a_no_op_on_a_single_observation(window):
    window.trim_timeline("end")
    QApplication.processEvents()

    assert _segment_labels(window) == ["10:15   01"]
    assert window.raw_data.shape == (NROWS, NCOLS)
