"""Tests for the unified FITS combination workflow."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication

from src.UI.dialogs import combine_dialogs
from src.UI.dialogs.combine_dialogs import CombineFitsDialog
from src.UI import main_window as main_window_module
from src.UI.main_window import MainWindow
from src.Backend import burst_processor


def _app():
    return QApplication.instance() or QApplication([])


class _FakeMainWindow:
    def __init__(self):
        self.loaded = None

    def _maybe_prompt_save_dirty(self):
        return True

    def load_combined_into_main(self, payload):
        self.loaded = payload


def test_unified_dialog_combines_preflighted_time_frequency_selection(monkeypatch):
    _app()
    main_window = _FakeMainWindow()
    dialog = CombineFitsDialog(main_window)
    dialog.file_paths = ["a.fit", "b.fit", "c.fit", "d.fit"]
    dialog.inspection = {
        "valid": True,
        "combine_type": "time_frequency",
        "frequency_relation": {"has_gap": True, "has_overlap": False, "gaps": [], "overlaps": []},
    }
    dialog.options_group.setVisible(True)
    dialog.gap_fill_combo.setCurrentIndex(dialog.gap_fill_combo.findData("hatched"))

    payload = {
        "data": np.arange(20, dtype=float).reshape(4, 5),
        "freqs": np.array([40.0, 30.0, 20.0, 10.0]),
        "time": np.arange(5, dtype=float),
        "filename": "STAT_20260201_time_frequency_combined",
        "combine_type": "time_frequency",
        "sources": list(dialog.file_paths),
        "header0": None,
        "gap_row_mask": np.array([False, True, False, False]),
        "frequency_step_mhz": 10.0,
    }
    captured = {}

    def fake_combine(paths, **options):
        captured["paths"] = list(paths)
        captured["options"] = dict(options)
        return payload

    monkeypatch.setattr(combine_dialogs, "combine_compatible", fake_combine)

    dialog.combine_files()

    assert captured["paths"] == dialog.file_paths
    assert captured["options"]["gap_fill"] == "hatched"
    assert dialog.combined is payload
    assert dialog.import_button.isEnabled() is True
    assert dialog.image_label.pixmap() is not None

    dialog.import_to_main()
    assert main_window.loaded is payload


def test_main_window_exposes_unified_combine_action():
    _app()
    window = MainWindow(theme=None)

    assert window.combine_fits_action.text() == "Combine FITS Files..."
    assert window.combine_fits_action.isEnabled() is True

    window.close()


def test_normal_open_dispatches_time_frequency_selection_with_shared_options(monkeypatch):
    _app()
    paths = ["a.fit", "b.fit", "c.fit", "d.fit"]

    class _FakeFileDialog:
        ExistingFiles = object()
        DontUseNativeDialog = object()

        def __init__(self, _parent=None):
            pass

        def setFileMode(self, _mode):
            pass

        def setNameFilter(self, _filter):
            pass

        def setOption(self, _option, _enabled):
            pass

        def exec(self):
            return True

        def selectedFiles(self):
            return list(paths)

    relation = {"has_gap": True, "has_overlap": False, "gaps": [], "overlaps": []}
    inspection = {
        "valid": True,
        "combine_type": "time_frequency",
        "frequency_relation": relation,
    }
    payload = {
        "data": np.ones((2, 4)),
        "freqs": np.array([20.0, 10.0]),
        "time": np.arange(4, dtype=float),
        "combine_type": "time_frequency",
        "sources": paths,
    }
    captured = {}

    monkeypatch.setattr(main_window_module, "QFileDialog", _FakeFileDialog)
    monkeypatch.setattr(burst_processor, "inspect_combination", lambda selected: inspection)

    def fake_combine(selected, **options):
        captured["selected"] = list(selected)
        captured["options"] = dict(options)
        return payload

    monkeypatch.setattr(burst_processor, "combine_compatible", fake_combine)
    window = MainWindow(theme=None)
    window._maybe_prompt_save_dirty = lambda: True
    window._choose_frequency_combine_options = lambda selected, relation=None: {
        "gap_fill": "hatched",
        "overlap_policy": "split",
        "overlap_connection_mhz": None,
    }
    window.load_combined_into_main = lambda combined: captured.setdefault("loaded", combined)

    window.load_file()

    assert captured["selected"] == paths
    assert captured["options"]["gap_fill"] == "hatched"
    assert captured["loaded"] is payload
    window.close()
