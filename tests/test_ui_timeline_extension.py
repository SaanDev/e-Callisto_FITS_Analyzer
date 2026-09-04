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
from src.UI.widgets.collapsible_sections import section_for

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
    win.timeline_prefetch_action.setChecked(False)
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


def test_timeline_panel_shows_only_its_message_without_a_dataset(offline):
    """Controls that cannot act are hidden rather than stacked up greyed out."""
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()
    try:
        assert _segment_labels(win) == []
        assert "Load a CALLISTO file" in win.timeline_status_label.text()
        assert win.timeline_step_row.isHidden()
        assert win.timeline_trim_row.isHidden()
        assert win.timeline_list.isHidden()
    finally:
        win.close()


def test_timeline_controls_appear_once_a_dataset_is_loaded(window):
    window.show()
    QApplication.processEvents()

    assert not window.timeline_step_row.isHidden()
    assert not window.timeline_trim_row.isHidden()
    assert not window.timeline_list.isHidden()


def test_reopening_the_timeline_card_does_not_resurrect_dead_controls(offline):
    """Opening a section re-shows its children wholesale; the gate must re-run."""
    _app()
    win = MainWindow(theme=None)
    win.show()
    QApplication.processEvents()
    try:
        section = section_for(win.timeline_group)
        section.setExpanded(False)
        section.setExpanded(True)
        QApplication.processEvents()

        assert win.timeline_step_row.isHidden()
        assert win.timeline_list.isHidden()
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
    window.timeline_step_combo.setCurrentText("\u00d72")

    _run_extend(window, "next")

    assert window.raw_data.shape == (NROWS, NCOLS * 3)
    assert _segment_labels(window) == ["10:15   01", "10:30   01", "10:45   01"]


def test_extend_stops_with_a_reason_at_the_edge_of_the_archive(window):
    window.timeline_step_combo.setCurrentText("\u00d71")
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


def _flush(win, ticks: int = 30):
    """Let the queued replot land; plot_data defers via a zero-timer."""
    for _ in range(ticks):
        QApplication.processEvents()
        QThread.msleep(5)


def test_prepending_keeps_the_view_on_the_same_real_time(window):
    window.plot_data(window.raw_data, title="Raw")
    _flush(window)

    # Go through the window's own view accessors rather than a specific canvas:
    # the app renders through pyqtgraph when it is available and matplotlib
    # otherwise, and the zoom lives with whichever one is active.
    view = window._capture_view()
    assert view is not None
    window._restore_view({"xlim": (400.0, 500.0), "ylim": view["ylim"]})
    assert window._capture_view()["xlim"] == pytest.approx((400.0, 500.0))

    _run_extend(window, "previous")
    _flush(window)

    span = NCOLS * SAMPLE_STEP_S
    low, high = window._capture_view()["xlim"]
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
    window.timeline_step_combo.setCurrentText("\u00d72")
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


def _click_extend(win, button, timeout_ticks: int = 800):
    """Drive the real button, so a disabled control fails the test."""
    button.click()
    for _ in range(timeout_ticks):
        QApplication.processEvents()
        if win._timeline_thread is None:
            return
        QThread.msleep(5)
    raise AssertionError("timeline extension did not finish")


def test_step_controls_stay_enabled_after_an_extend(window):
    """The completion handler must not leave its own controls disabled.

    It runs as a queued signal *before* the worker thread is asked to quit, so a
    busy check based on QThread.isRunning() disabled Prev/Next and the step
    counter and nothing ever switched them back on.
    """
    assert window.timeline_next_btn.isEnabled()

    _click_extend(window, window.timeline_next_btn)

    assert window.raw_data.shape == (NROWS, NCOLS * 2)
    assert window.timeline_next_btn.isEnabled()
    assert window.timeline_prev_btn.isEnabled()
    assert window.timeline_step_combo.isEnabled()
    assert window.timeline_trim_start_btn.isEnabled()
    assert window.timeline_trim_end_btn.isEnabled()


def test_clicking_next_repeatedly_keeps_extending(window):
    _click_extend(window, window.timeline_next_btn)
    assert window.raw_data.shape == (NROWS, NCOLS * 2)

    _click_extend(window, window.timeline_next_btn)

    assert window.raw_data.shape == (NROWS, NCOLS * 3)
    assert _segment_labels(window) == ["10:15   01", "10:30   01", "10:45   01"]


def test_clicking_previous_after_next_still_extends(window):
    _click_extend(window, window.timeline_next_btn)
    _click_extend(window, window.timeline_prev_btn)

    assert window.raw_data.shape == (NROWS, NCOLS * 3)
    assert _segment_labels(window) == ["10:00   01", "10:15   01", "10:30   01"]


# -----------------------------
# Processing state
# -----------------------------
def _enable_background_subtraction(win):
    win._set_noise_clip_state(-5.0, 25.0, scale=win.NOISE_CLIP_SCALE_LINEAR, sync_widgets=True)
    win.update_noise_live()
    QApplication.processEvents()


def test_background_subtraction_survives_an_extend(window):
    _enable_background_subtraction(window)
    assert window.current_plot_type == "Background Subtracted"

    _run_extend(window, "next")

    assert window.current_plot_type == "Background Subtracted"
    assert window.noise_reduced_data is not None
    assert window.noise_reduced_data.shape == (NROWS, NCOLS * 2)
    # The whole spectrogram is background subtracted, not just the original half.
    assert window.current_display_data.shape == (NROWS, NCOLS * 2)
    assert window.canvas.ax.get_title().endswith("Background Subtracted")


def test_noise_clip_thresholds_are_reapplied_to_the_added_data(window):
    _enable_background_subtraction(window)

    _run_extend(window, "next")

    assert window.noise_clip_low == pytest.approx(-5.0)
    assert window.noise_clip_high == pytest.approx(25.0)
    values = np.asarray(window.noise_reduced_data)
    assert float(np.nanmin(values)) >= -5.0 - 1e-6
    assert float(np.nanmax(values)) <= 25.0 + 1e-6


def test_rfi_cleaning_survives_an_extend(window):
    _enable_background_subtraction(window)
    window.apply_rfi_now()
    QApplication.processEvents()
    assert window.current_plot_type == "RFI Cleaned"
    assert window._rfi_config["applied"] is True

    _run_extend(window, "next")

    assert window.current_plot_type == "RFI Cleaned"
    assert window.noise_reduced_data.shape == (NROWS, NCOLS * 2)
    # The pre-RFI product is kept so Reset to Raw still has a fallback.
    assert window.noise_reduced_original.shape == (NROWS, NCOLS * 2)
    assert window.noise_reduced_original_plot_type == "Background Subtracted"
    assert window.canvas.ax.get_title().endswith("RFI Cleaned")


def test_a_raw_view_stays_raw_after_an_extend(window):
    assert window.current_plot_type == "Raw"

    _run_extend(window, "next")

    assert window.current_plot_type == "Raw"
    assert window.noise_reduced_data is None
    assert window.canvas.ax.get_title().endswith("Raw")


def test_processing_state_survives_a_trim(window):
    _run_extend(window, "next")
    _enable_background_subtraction(window)
    assert window.current_plot_type == "Background Subtracted"

    window.trim_timeline("end")
    QApplication.processEvents()

    assert window.current_plot_type == "Background Subtracted"
    assert window.noise_reduced_data.shape == (NROWS, NCOLS)


def test_a_raw_view_stays_raw_even_with_thresholds_dialled_in(window):
    """Thresholds being set is not the same as the user looking at them.

    A restored project can carry clip thresholds while displaying Raw; the
    extend must follow the view, not the thresholds.
    """
    _enable_background_subtraction(window)
    window.current_plot_type = "Raw"
    window.plot_data(window.raw_data, title="Raw")
    QApplication.processEvents()
    assert window._noise_clip_thresholds_active() is True

    _run_extend(window, "next")

    assert window.current_plot_type == "Raw"
    assert window.canvas.ax.get_title().endswith("Raw")
