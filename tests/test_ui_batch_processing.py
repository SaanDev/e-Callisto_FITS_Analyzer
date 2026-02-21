from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
pytest.importorskip("astropy")

from astropy.io import fits
from PySide6.QtWidgets import QApplication

from src.Backend.fits_io import FitsLoadResult
from src.UI.gui_workers import BatchProcessWorker
from src.UI.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _build_result() -> FitsLoadResult:
    data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float)
    freqs = np.array([100.0, 90.0], dtype=float)
    time_arr = np.array([0.0, 1.0], dtype=float)
    hdr = fits.Header()
    return FitsLoadResult(data=data, freqs=freqs, time=time_arr, header0=hdr)


def _write_test_fit(path: Path):
    data = np.arange(12, dtype=np.float32).reshape(3, 4)
    fits.PrimaryHDU(data=data).writeto(path, overwrite=True)


def test_batch_worker_continues_on_file_errors(monkeypatch, tmp_path: Path):
    files = ["/tmp/good.fit", "/tmp/bad.fit"]
    saved = []
    progress_values = []
    payloads = []
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    monkeypatch.setattr("src.UI.gui_workers.list_fit_files", lambda *_a, **_k: files)

    def fake_load(path, memmap=False):
        if path.endswith("bad.fit"):
            raise ValueError("broken file")
        return _build_result()

    monkeypatch.setattr("src.UI.gui_workers.load_callisto_fits", fake_load)
    monkeypatch.setattr(
        "src.UI.gui_workers.build_unique_output_png_path",
        lambda out_dir, input_name: str(tmp_path / f"{input_name}.png"),
    )
    monkeypatch.setattr(
        "src.UI.gui_workers.save_background_subtracted_png",
        lambda data, freqs, time_arr, output_path, title, cmap_name: saved.append(output_path),
    )

    worker = BatchProcessWorker(input_dir=str(input_dir), output_dir=str(tmp_path), cmap_name="Custom")
    worker.progress_value.connect(lambda v: progress_values.append(int(v)))
    worker.finished.connect(lambda payload: payloads.append(payload))
    worker.run()

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["total"] == 2
    assert payload["processed"] == 2
    assert payload["succeeded"] == 1
    assert payload["failed"] == 1
    assert payload["cancelled"] is False
    assert len(payload["errors"]) == 1
    assert "bad.fit" in payload["errors"][0]["input_path"]
    assert saved and saved[0].endswith("good.fit.png")
    assert progress_values and progress_values[-1] == 2


def test_batch_worker_cancellation(monkeypatch, tmp_path: Path):
    files = ["/tmp/one.fit", "/tmp/two.fit", "/tmp/three.fit"]
    payloads = []
    calls = []
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    monkeypatch.setattr("src.UI.gui_workers.list_fit_files", lambda *_a, **_k: files)

    worker = BatchProcessWorker(input_dir=str(input_dir), output_dir=str(tmp_path), cmap_name="Custom")

    def fake_load(path, memmap=False):
        calls.append(path)
        if len(calls) == 1:
            worker.request_cancel()
        return _build_result()

    monkeypatch.setattr("src.UI.gui_workers.load_callisto_fits", fake_load)
    monkeypatch.setattr(
        "src.UI.gui_workers.build_unique_output_png_path",
        lambda out_dir, input_name: str(tmp_path / f"{input_name}.png"),
    )
    monkeypatch.setattr(
        "src.UI.gui_workers.save_background_subtracted_png",
        lambda *_args, **_kwargs: None,
    )

    worker.finished.connect(lambda payload: payloads.append(payload))
    worker.run()

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["cancelled"] is True
    assert payload["processed"] == 1
    assert payload["total"] == 3
    assert len(calls) == 1


def test_batch_menu_action_opens_and_reuses_window():
    _app()
    win = MainWindow(theme=None)

    processing_menu = None
    for action in win.menuBar().actions():
        if action.text() == "Processing":
            processing_menu = action.menu()
            break

    assert processing_menu is not None

    batch_submenu = None
    for action in processing_menu.actions():
        if action.text() == "Batch Processing":
            batch_submenu = action.menu()
            break

    assert batch_submenu is not None
    assert win.open_batch_processing_action.text() == "Open Batch Processor"

    win.open_batch_processing_action.trigger()
    QApplication.processEvents()
    first_dialog = win._batch_processing_dialog
    assert first_dialog is not None
    assert first_dialog.isVisible() is True

    win.open_batch_processing_action.trigger()
    QApplication.processEvents()
    assert win._batch_processing_dialog is first_dialog

    first_dialog.close()
    win.close()


def test_batch_run_does_not_mutate_main_window_data(monkeypatch, tmp_path: Path):
    _app()
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir()
    out_dir.mkdir()
    _write_test_fit(in_dir / "demo.fit")

    monkeypatch.setattr("src.UI.dialogs.batch_processing_dialog.QMessageBox.information", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.UI.dialogs.batch_processing_dialog.QMessageBox.warning", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.UI.dialogs.batch_processing_dialog.QMessageBox.critical", lambda *_a, **_k: 0)

    win = MainWindow(theme=None)
    original_data = np.array([[10.0, 20.0], [30.0, 40.0]], dtype=float)
    win.raw_data = original_data.copy()
    win.filename = "original.fit"
    win.current_plot_type = "Raw"

    win.open_batch_processing_window()
    dlg = win._batch_processing_dialog
    assert dlg is not None

    dlg.input_dir_edit.setText(str(in_dir))
    dlg.output_dir_edit.setText(str(out_dir))
    dlg._start_batch()

    deadline = time.time() + 8.0
    while dlg.is_running() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)

    QApplication.processEvents()
    assert dlg.is_running() is False
    assert np.array_equal(win.raw_data, original_data)
    assert win.filename == "original.fit"
    assert win.current_plot_type == "Raw"
    assert len(list(out_dir.glob("*.png"))) == 1

    dlg.close()
    win.close()
