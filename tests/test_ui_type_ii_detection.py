from __future__ import annotations

from dataclasses import replace
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QDialog

from src.Backend.type_ii_detection import detect_type_ii_bursts
from src.Backend.type_ii_learning import TypeIILearningLibrary
from src.UI.dialogs.type_ii_detection_dialog import (
    TypeIIDetectionOptionsDialog,
    TypeIIDetectionPreviewDialog,
    TypeIILearningManagerDialog,
)
from src.UI.gui_workers import TypeIIDetectionWorker
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _spectrum():
    rng = np.random.default_rng(44)
    freqs = np.linspace(110.0, 20.0, 140)
    times = np.linspace(0.0, 160.0, 641)
    data = rng.normal(0.0, 1.0, (freqs.size, times.size)).astype(np.float32)
    for column, time_s in enumerate(times):
        center = 92.0 - 0.25 * time_s
        data[:, column] += 9.0 * np.exp(-0.5 * np.square((freqs - center) / 1.1))
    return data, freqs, times


def test_detection_worker_finishes_without_mutating_source():
    data, freqs, times = _spectrum()
    before = data.copy()
    worker = TypeIIDetectionWorker(data, freqs, times, station="TEST")
    finished = []
    failed = []
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert failed == []
    assert len(finished) == 1
    assert finished[0].candidates
    assert np.array_equal(data, before)


def test_detection_options_expose_automatic_and_deterministic_engines():
    _app()
    dialog = TypeIIDetectionOptionsDialog(
        default_engine="automatic",
        active_model_id="validated-model",
    )

    assert dialog.engine == "automatic"
    assert "validated-model" in dialog.engine_combo.currentText()
    dialog.engine_combo.setCurrentIndex(
        dialog.engine_combo.findData("deterministic")
    )
    assert dialog.engine == "deterministic"
    dialog.close()


def test_learning_manager_runs_one_click_automatic_detection(tmp_path):
    _app()
    library = TypeIILearningLibrary(tmp_path / "learning")
    modes = []
    detections = []
    dialog = TypeIILearningManagerDialog(
        library,
        detection_mode="deterministic",
        mode_changed_callback=modes.append,
        detect_callback=lambda: detections.append(True),
    )

    dialog.detect_button.click()

    assert dialog.mode_combo.currentData() == "automatic"
    assert modes[-1] == "automatic"
    assert detections == [True]
    assert "deterministic fallback" in dialog.readiness.text()
    dialog.close()


def test_preview_requires_explicit_band_selection(monkeypatch):
    _app()
    data, freqs, times = _spectrum()
    result = detect_type_ii_bursts(data, freqs, times)
    dialog = TypeIIDetectionPreviewDialog(result, data, freqs, times)
    messages = []
    monkeypatch.setattr(
        "src.UI.dialogs.type_ii_detection_dialog.QMessageBox.information",
        lambda *args: messages.append(args),
    )

    dialog._accept_selected()
    assert dialog.selected_band is None
    assert messages

    dialog.harmonic_radio.setChecked(True)
    dialog._accept_selected()
    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_band == "harmonic"
    assert dialog.selected_candidate is not None
    dialog.close()


def test_preview_maps_paired_band_choice_to_separate_candidate_mask():
    _app()
    data, freqs, times = _spectrum()
    detected = detect_type_ii_bursts(data, freqs, times)
    base = detected.candidates[0]
    fundamental = replace(
        base,
        candidate_id="fundamental-lane",
        suggested_band="fundamental",
        paired_candidate_id="harmonic-lane",
    )
    harmonic = replace(
        base,
        candidate_id="harmonic-lane",
        mask=np.roll(base.mask, 5, axis=0),
        suggested_band="harmonic",
        paired_candidate_id="fundamental-lane",
    )
    result = replace(
        detected,
        candidates=(fundamental,),
        generated_candidates=(fundamental, harmonic),
    )
    dialog = TypeIIDetectionPreviewDialog(result, data, freqs, times)

    dialog.harmonic_radio.setChecked(True)
    dialog._accept_selected()

    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_band == "harmonic"
    assert dialog.selected_candidate is harmonic
    assert np.array_equal(dialog.selected_candidate.mask, harmonic.mask)
    assert not np.array_equal(dialog.selected_candidate.mask, fundamental.mask)
    dialog.close()


def test_preview_allows_both_only_for_a_detected_fundamental_harmonic_pair():
    _app()
    data, freqs, times = _spectrum()
    detected = detect_type_ii_bursts(data, freqs, times)
    base = detected.candidates[0]
    fundamental = replace(
        base,
        candidate_id="fundamental-lane",
        suggested_band="fundamental",
        paired_candidate_id="harmonic-lane",
    )
    harmonic = replace(
        base,
        candidate_id="harmonic-lane",
        mask=np.roll(base.mask, 5, axis=0),
        suggested_band="harmonic",
        paired_candidate_id="fundamental-lane",
    )
    paired_result = replace(
        detected,
        candidates=(fundamental,),
        generated_candidates=(fundamental, harmonic),
    )
    dialog = TypeIIDetectionPreviewDialog(paired_result, data, freqs, times)

    assert dialog.both_radio.isVisibleTo(dialog) is True
    dialog.both_radio.setChecked(True)
    dialog._accept_selected()

    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_band == "both"
    assert {item.suggested_band for item in dialog.selected_candidates} == {
        "fundamental",
        "harmonic",
    }
    assert dialog.selected_candidate is fundamental
    dialog.close()

    unpaired_dialog = TypeIIDetectionPreviewDialog(detected, data, freqs, times)
    assert unpaired_dialog.both_radio.isHidden() is True
    unpaired_dialog.close()


def test_main_window_actions_follow_data_and_isolation_state():
    _app()
    window = MainWindow(theme=None)
    data, freqs, times = _spectrum()
    window.raw_data = data
    window.freqs = freqs
    window.time = times
    window.filename = "TEST_20240101.fit"
    window._sync_toolbar_enabled_states()

    assert window.detect_type_ii_action.isEnabled() is True
    assert window.auto_detect_type_ii_action.isEnabled() is True
    assert window.teach_type_ii_action.isEnabled() is False

    window.noise_reduced_data = data.copy()
    window._sync_toolbar_enabled_states()
    assert window.teach_type_ii_action.isEnabled() is True

    window.current_plot_type = "Isolated Burst"
    window._sync_toolbar_enabled_states()
    assert window.detect_type_ii_action.isEnabled() is False
    assert window.auto_detect_type_ii_action.isEnabled() is False
    assert window.teach_type_ii_action.isEnabled() is False
    window.close()


def test_one_click_automatic_action_skips_options_and_persists_engine(monkeypatch):
    _app()
    window = MainWindow(theme=None)
    calls = []
    monkeypatch.setattr(
        window,
        "_start_type_ii_detection",
        lambda **kwargs: calls.append(kwargs),
    )

    window.start_automatic_type_ii_detection()

    assert calls == [{"show_options": False, "forced_engine": "automatic"}]
    assert window._ui_settings.value("type_ii_detection/engine") == "automatic"
    window.close()


def test_accepted_candidate_reuses_isolation_and_records_metadata(tmp_path, monkeypatch):
    _app()
    window = MainWindow(theme=None)
    data, freqs, times = _spectrum()
    window.raw_data = data.copy()
    window.noise_reduced_data = data.copy()
    window.freqs = freqs
    window.time = times
    window.filename = "TEST_20240101_000000.fit"
    window.current_plot_type = "Background Subtracted"
    window._type_ii_learning_library = TypeIILearningLibrary(tmp_path / "learning")
    result = detect_type_ii_bursts(data, freqs, times)
    candidate = result.candidates[0]
    window._type_ii_detection_display_data = data

    class FakePreview:
        def __init__(self, *args, **kwargs):
            self.selected_candidate = candidate
            self.selected_band = "fundamental"
            self.rejected_candidate_ids = []
            self.clean_range_requested = False

        def exec(self):
            return QDialog.Accepted

    isolated = []
    monkeypatch.setattr("src.UI.main_window.TypeIIDetectionPreviewDialog", FakePreview)
    monkeypatch.setattr(window, "_plot_isolated_burst", lambda mask: isolated.append(np.asarray(mask)))
    monkeypatch.setattr(window, "queue_type_ii_training", lambda **kwargs: None)

    window._on_type_ii_detection_finished(result)

    assert len(isolated) == 1
    assert isolated[0].shape == data.shape
    assert window._type_ii_detection_state["band"] == "fundamental"
    assert window._type_ii_detection_state["candidate"]["confidence"] >= 0.75
    assert window._type_ii_learning_library.counts()["positive"] == 1
    assert np.array_equal(window.lasso_mask, isolated[0])
    window.close()


def test_accepted_paired_bands_combine_masks_and_store_separate_labels(tmp_path, monkeypatch):
    _app()
    window = MainWindow(theme=None)
    data, freqs, times = _spectrum()
    detected = detect_type_ii_bursts(data, freqs, times)
    base = detected.candidates[0]
    fundamental = replace(
        base,
        candidate_id="fundamental-lane",
        suggested_band="fundamental",
        paired_candidate_id="harmonic-lane",
    )
    harmonic = replace(
        base,
        candidate_id="harmonic-lane",
        mask=np.roll(base.mask, 5, axis=0),
        suggested_band="harmonic",
        paired_candidate_id="fundamental-lane",
    )
    result = replace(
        detected,
        candidates=(fundamental,),
        generated_candidates=(fundamental, harmonic),
    )
    window.raw_data = data.copy()
    window.noise_reduced_data = data.copy()
    window.freqs = freqs
    window.time = times
    window.filename = "TEST_PAIRED.fit"
    window.current_plot_type = "Background Subtracted"
    window._type_ii_learning_library = TypeIILearningLibrary(tmp_path / "learning")
    window._type_ii_detection_display_data = data

    class FakePreview:
        def __init__(self, *args, **kwargs):
            self.selected_candidate = fundamental
            self.selected_candidates = (fundamental, harmonic)
            self.selected_band = "both"
            self.rejected_candidate_ids = []
            self.clean_range_requested = False

        def exec(self):
            return QDialog.Accepted

    isolated = []
    monkeypatch.setattr("src.UI.main_window.TypeIIDetectionPreviewDialog", FakePreview)
    monkeypatch.setattr(window, "_plot_isolated_burst", lambda mask: isolated.append(np.asarray(mask)))
    monkeypatch.setattr(window, "queue_type_ii_training", lambda **kwargs: None)

    window._on_type_ii_detection_finished(result)

    expected = np.logical_or(fundamental.mask, harmonic.mask)
    examples = window._type_ii_learning_library.examples()
    assert len(isolated) == 1
    assert np.array_equal(isolated[0], expected)
    assert window._type_ii_detection_state["band"] == "both"
    assert len(window._type_ii_detection_state["candidates"]) == 2
    assert {example.band for example in examples} == {"fundamental", "harmonic"}
    assert len({example.event_id for example in examples}) == 1
    window.close()


def test_teach_lasso_records_labeled_positive_and_isolates_mask(tmp_path, monkeypatch):
    _app()
    window = MainWindow(theme=None)
    data, freqs, times = _spectrum()
    result = detect_type_ii_bursts(data, freqs, times)
    mask = result.candidates[0].mask
    window.raw_data = data.copy()
    window.noise_reduced_data = data.copy()
    window.freqs = freqs
    window.time = times
    window.filename = "TEST_20240101.fit"
    window.current_plot_type = "Background Subtracted"
    window._type_ii_learning_library = TypeIILearningLibrary(tmp_path / "learning")
    window._type_ii_teach_context = {"event_id": "event-a", "items": []}

    class PairChoice:
        def isChecked(self):
            return False

    class FakeLabelDialog:
        def __init__(self, *args, **kwargs):
            self.band = "harmonic"
            self.add_pair = PairChoice()

        def exec(self):
            return QDialog.Accepted

    isolated = []
    monkeypatch.setattr("src.UI.main_window.TypeIITeachLabelDialog", FakeLabelDialog)
    monkeypatch.setattr(window, "_plot_isolated_burst", lambda selected: isolated.append(np.asarray(selected)))
    monkeypatch.setattr(window, "queue_type_ii_training", lambda **kwargs: None)

    window._handle_type_ii_teaching_mask(mask)

    examples = window._type_ii_learning_library.examples()
    assert len(examples) == 1
    assert examples[0].label == 1
    assert examples[0].band == "harmonic"
    assert examples[0].event_id == "event-a"
    assert len(isolated) == 1
    assert window._type_ii_detection_state["algorithm_version"] == "manual-teach-1"
    assert window._type_ii_detection_state["band"] == "harmonic"
    window.close()


def test_raw_assisted_acceptance_preserves_raw_state_for_undo(tmp_path, monkeypatch):
    _app()
    window = MainWindow(theme=None)
    data, freqs, times = _spectrum()
    result = detect_type_ii_bursts(data, freqs, times, source_kind="raw_snr")
    candidate = result.candidates[0]
    window.raw_data = data.copy()
    window.noise_reduced_data = None
    window.freqs = freqs
    window.time = times
    window.filename = "TEST_RAW.fit"
    window.current_plot_type = "Raw"
    window._type_ii_detection_display_data = data
    window._type_ii_learning_library = TypeIILearningLibrary(tmp_path / "learning")

    class FakePreview:
        def __init__(self, *args, **kwargs):
            self.selected_candidate = candidate
            self.selected_band = "fundamental"
            self.rejected_candidate_ids = []
            self.clean_range_requested = False

        def exec(self):
            return QDialog.Accepted

    calls = []
    monkeypatch.setattr("src.UI.main_window.TypeIIDetectionPreviewDialog", FakePreview)
    monkeypatch.setattr(
        window,
        "_plot_isolated_burst",
        lambda mask, **kwargs: calls.append((np.asarray(mask), kwargs.get("undo_state"))),
    )
    monkeypatch.setattr(window, "queue_type_ii_training", lambda **kwargs: None)

    window._on_type_ii_detection_finished(result)

    assert window.noise_reduced_data is not None
    assert len(calls) == 1
    assert calls[0][1] is not None
    assert calls[0][1]["noise_reduced_data"] is None
    assert calls[0][1]["current_plot_type"] == "Raw"
    window.close()


def test_type_ii_detection_metadata_round_trips_through_project_payload(tmp_path):
    _app()
    window = MainWindow(theme=None)
    data, freqs, times = _spectrum()
    window.raw_data = data
    window.noise_reduced_data = data.copy()
    window.freqs = freqs
    window.time = times
    window.filename = "TEST.fit"
    window._type_ii_detection_state = {
        "algorithm_version": "1",
        "model_id": "model-a",
        "band": "harmonic",
        "candidate": {"confidence": 0.93},
        "experimental": True,
    }

    meta, arrays = window._capture_project_payload()
    assert meta["type_ii_detection"]["band"] == "harmonic"

    restored = MainWindow(theme=None)
    restored._apply_project_payload(meta, arrays)
    assert restored._type_ii_detection_state["model_id"] == "model-a"
    assert restored._type_ii_detection_state["band"] == "harmonic"
    window._set_project_clean(None)
    restored._set_project_clean(None)
    window.close()
    restored.close()


def test_type_ii_training_progress_is_delivered_on_the_gui_thread(tmp_path):
    app = _app()
    window = MainWindow(theme=None)
    window._type_ii_learning_library = TypeIILearningLibrary(tmp_path / "learning")
    progress_on_gui_thread = []
    status_bar = window.statusBar()
    original_show_message = status_bar.showMessage

    def checked_show_message(*args, **kwargs):
        progress_on_gui_thread.append(QThread.currentThread() is window.thread())
        return original_show_message(*args, **kwargs)

    status_bar.showMessage = checked_show_message
    window.queue_type_ii_training(force=True)
    deadline = time.monotonic() + 5.0
    while window._type_ii_training_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()

    assert window._type_ii_training_worker is None
    assert progress_on_gui_thread
    assert all(progress_on_gui_thread)
    window.close()
