"""
Batch processing dialog for FIT/FITS files.
"""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from src.UI.gui_workers import BatchProcessWorker


class BatchProcessingDialog(QDialog):
    _COLORMAP_OPTIONS = (
        "Custom",
        "viridis",
        "plasma",
        "inferno",
        "magma",
        "cividis",
        "turbo",
        "RdYlBu",
        "jet",
        "cubehelix",
    )

    def __init__(
        self,
        *,
        cmap_name_provider: Callable[[], str] | None = None,
        cold_digits_provider: Callable[[], float] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Batch FIT Processing")
        self.resize(760, 360)

        self._cmap_name_provider = cmap_name_provider
        self._cold_digits_provider = cold_digits_provider
        self._thread = None
        self._worker = None
        self._close_after_finish = False
        self._last_payload = None

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(10)

        in_label = QLabel("Input Folder")
        self.input_dir_edit = QLineEdit()
        self.input_dir_edit.setPlaceholderText("Select folder containing FIT files")
        self.input_browse_btn = QPushButton("Browse...")
        self.input_browse_btn.clicked.connect(self._browse_input_dir)

        out_label = QLabel("Output Folder")
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("Select folder for output PNG files")
        self.output_browse_btn = QPushButton("Browse...")
        self.output_browse_btn.clicked.connect(self._browse_output_dir)

        form.addWidget(in_label, 0, 0)
        form.addWidget(self.input_dir_edit, 0, 1)
        form.addWidget(self.input_browse_btn, 0, 2)
        form.addWidget(out_label, 1, 0)
        form.addWidget(self.output_dir_edit, 1, 1)
        form.addWidget(self.output_browse_btn, 1, 2)

        root.addLayout(form)

        output_group = QGroupBox("Output Type")
        output_layout = QHBoxLayout(output_group)
        self.output_mode_group = QButtonGroup(self)
        self.raw_output_radio = QRadioButton("Raw")
        self.background_output_radio = QRadioButton("Background Subtracted")
        self.background_output_radio.setChecked(True)
        self.output_mode_group.addButton(self.raw_output_radio)
        self.output_mode_group.addButton(self.background_output_radio)
        output_layout.addWidget(self.raw_output_radio)
        output_layout.addWidget(self.background_output_radio)
        output_layout.addStretch(1)
        root.addWidget(output_group)

        process_group = QGroupBox("Processing Options")
        process_layout = QGridLayout(process_group)
        process_layout.setHorizontalSpacing(8)
        process_layout.setVerticalSpacing(8)

        self.background_method_label = QLabel("Background Method")
        self.background_method_combo = QComboBox()
        self.background_method_combo.addItems(["Mean", "Median"])

        self.colormap_label = QLabel("Colormap")
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(list(self._COLORMAP_OPTIONS))
        default_cmap = self._current_cmap_name()
        if default_cmap in self._COLORMAP_OPTIONS:
            self.colormap_combo.setCurrentText(default_cmap)
        else:
            self.colormap_combo.setCurrentText("Custom")

        process_layout.addWidget(self.background_method_label, 0, 0)
        process_layout.addWidget(self.background_method_combo, 0, 1)
        process_layout.addWidget(self.colormap_label, 1, 0)
        process_layout.addWidget(self.colormap_combo, 1, 1)
        root.addWidget(process_group)

        self.raw_output_radio.toggled.connect(self._sync_background_method_enabled)
        self.background_output_radio.toggled.connect(self._sync_background_method_enabled)
        self._sync_background_method_enabled()

        self.status_label = QLabel("Select input/output folders, then click Start.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        root.addWidget(self.progress_bar)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start_batch)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.close_btn)
        root.addLayout(button_row)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def force_shutdown(self, timeout_ms: int = 2000) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            if self._worker is not None:
                self._worker.request_cancel()
        except Exception:
            pass
        if thread.isRunning():
            thread.quit()
            thread.wait(max(1, int(timeout_ms)))

    def _set_controls_enabled(self, enabled: bool):
        flag = bool(enabled)
        self.input_dir_edit.setEnabled(flag)
        self.output_dir_edit.setEnabled(flag)
        self.input_browse_btn.setEnabled(flag)
        self.output_browse_btn.setEnabled(flag)
        self.start_btn.setEnabled(flag)
        self.close_btn.setEnabled(flag)
        self.raw_output_radio.setEnabled(flag)
        self.background_output_radio.setEnabled(flag)
        self.colormap_combo.setEnabled(flag)
        self._sync_background_method_enabled()

        if self.is_running():
            self.cancel_btn.setText("Cancel Run")
            self.cancel_btn.setEnabled(True)
        else:
            self.cancel_btn.setText("Cancel")
            self.cancel_btn.setEnabled(True)

    def _current_cmap_name(self) -> str:
        provider = self._cmap_name_provider
        if callable(provider):
            try:
                value = str(provider() or "").strip()
                if value:
                    return value
            except Exception:
                pass
        return "Custom"

    def _current_cold_digits(self) -> float:
        provider = self._cold_digits_provider
        if callable(provider):
            try:
                return float(provider())
            except Exception:
                pass
        return 0.0

    def _sync_background_method_enabled(self):
        enabled = bool(self.background_output_radio.isChecked()) and bool(self.background_output_radio.isEnabled())
        self.background_method_label.setEnabled(enabled)
        self.background_method_combo.setEnabled(enabled)

    def _browse_input_dir(self):
        start = self.input_dir_edit.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select Input Folder", start)
        if path:
            self.input_dir_edit.setText(path)

    def _browse_output_dir(self):
        start = self.output_dir_edit.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder", start)
        if path:
            self.output_dir_edit.setText(path)

    def _start_batch(self):
        if self.is_running():
            QMessageBox.information(self, "Batch Processing", "A batch run is already in progress.")
            return

        input_dir = self.input_dir_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()

        if not input_dir:
            QMessageBox.warning(self, "Missing Input Folder", "Please select an input folder.")
            return
        if not os.path.isdir(input_dir):
            QMessageBox.warning(self, "Invalid Input Folder", "Selected input folder does not exist.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Missing Output Folder", "Please select an output folder.")
            return

        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Output Folder Error", f"Could not create output folder:\n{e}")
            return

        if not os.path.isdir(output_dir):
            QMessageBox.warning(self, "Invalid Output Folder", "Selected output folder does not exist.")
            return

        self._last_payload = None
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label.setText("Preparing batch processing...")
        self._set_controls_enabled(False)
        output_mode = "raw" if self.raw_output_radio.isChecked() else "background_subtracted"
        background_method = self.background_method_combo.currentText().strip().lower() or "mean"
        cmap_name = self.colormap_combo.currentText().strip() or "Custom"
        cold_digits = self._current_cold_digits()

        self._thread = QThread(self)
        self._worker = BatchProcessWorker(
            input_dir=input_dir,
            output_dir=output_dir,
            cmap_name=cmap_name,
            output_mode=output_mode,
            background_method=background_method,
            cold_digits=cold_digits,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress_text.connect(self._on_progress_text)
        self._worker.progress_range.connect(self._on_progress_range)
        self._worker.progress_value.connect(self._on_progress_value)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._cleanup_thread_state)

        self._thread.start()

    def _on_cancel_clicked(self):
        if self.is_running():
            try:
                if self._worker is not None:
                    self._worker.request_cancel()
                self.status_label.setText("Cancelling current batch run...")
            except Exception:
                pass
            return
        self.close()

    @Slot(str)
    def _on_progress_text(self, text: str):
        self.status_label.setText(str(text or "Processing..."))

    @Slot(int, int)
    def _on_progress_range(self, minimum: int, maximum: int):
        mn = int(minimum)
        mx = max(int(maximum), mn)
        self.progress_bar.setRange(mn, mx)
        if self.progress_bar.value() < mn:
            self.progress_bar.setValue(mn)

    @Slot(int)
    def _on_progress_value(self, value: int):
        v = int(value)
        if v > self.progress_bar.maximum():
            self.progress_bar.setMaximum(v)
        self.progress_bar.setValue(v)

    @Slot(object)
    def _on_finished(self, payload):
        self._last_payload = payload if isinstance(payload, dict) else {}
        data = dict(self._last_payload or {})

        total = int(data.get("total", 0) or 0)
        processed = int(data.get("processed", 0) or 0)
        succeeded = int(data.get("succeeded", 0) or 0)
        failed = int(data.get("failed", 0) or 0)
        cancelled = bool(data.get("cancelled", False))
        errors = list(data.get("errors") or [])
        output_mode = str(data.get("output_mode", "background_subtracted") or "background_subtracted")
        background_method = str(data.get("background_method", "mean") or "mean")
        cmap_name = str(data.get("cmap_name", "Custom") or "Custom")

        if self.progress_bar.maximum() < max(total, processed):
            self.progress_bar.setMaximum(max(total, processed))
        self.progress_bar.setValue(processed)

        self._set_controls_enabled(True)

        mode_text = "Raw" if output_mode == "raw" else "Background Subtracted"
        method_text = "N/A" if output_mode == "raw" else background_method.capitalize()
        summary_lines = [
            f"Total files found: {total}",
            f"Processed: {processed}",
            f"Succeeded: {succeeded}",
            f"Failed: {failed}",
            f"Cancelled: {'Yes' if cancelled else 'No'}",
            f"Output mode: {mode_text}",
            f"Background method: {method_text}",
            f"Colormap: {cmap_name}",
        ]

        if errors:
            preview = errors[:8]
            summary_lines.append("")
            summary_lines.append("Errors:")
            for err in preview:
                path = os.path.basename(str(err.get("input_path", "") or ""))
                msg = str(err.get("error", "") or "Unknown error")
                summary_lines.append(f"- {path}: {msg}")
            if len(errors) > len(preview):
                summary_lines.append(f"- ... and {len(errors) - len(preview)} more")

        summary = "\n".join(summary_lines)
        self.status_label.setText("Batch processing finished.")

        if total == 0:
            QMessageBox.information(self, "Batch Processing", summary)
        elif failed > 0:
            QMessageBox.warning(self, "Batch Processing Finished With Errors", summary)
        else:
            QMessageBox.information(self, "Batch Processing Complete", summary)

        if self._close_after_finish:
            self._close_after_finish = False
            self.close()

    @Slot(str)
    def _on_failed(self, message: str):
        self.status_label.setText("Batch processing failed.")
        self._set_controls_enabled(True)
        QMessageBox.critical(self, "Batch Processing Failed", str(message or "Batch processing failed."))
        if self._close_after_finish:
            self._close_after_finish = False
            self.close()

    def _cleanup_thread_state(self):
        self._thread = None
        self._worker = None
        self._set_controls_enabled(True)

    def closeEvent(self, event):
        if self.is_running():
            self._close_after_finish = True
            try:
                if self._worker is not None:
                    self._worker.request_cancel()
            except Exception:
                pass
            event.ignore()
            return
        super().closeEvent(event)
