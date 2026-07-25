"""Dialogs for assisted Type II detection, teaching, and model management."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.Backend.frequency_axis import matplotlib_extent, masked_display_data
from src.Backend.type_ii_detection import TypeIIBand, TypeIICandidate, TypeIIDetectionResult
from src.Backend.type_ii_learning import TypeIILearningLibrary
from src.UI.gui_shared import MplCanvas
from src.UI.mpl_style import style_axes


class TypeIIDetectionOptionsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        default_scope: str = "full",
        default_sensitivity: str = "conservative",
        default_engine: str = "automatic",
        active_model_id: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Assisted Type II Detection — Experimental")
        self.setModal(True)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Full loaded spectrum", "full")
        self.scope_combo.addItem("Visible zoom range", "visible")
        index = self.scope_combo.findData(str(default_scope or "full"))
        self.scope_combo.setCurrentIndex(max(0, index))

        self.sensitivity_combo = QComboBox()
        self.sensitivity_combo.addItem("Conservative — fewer false positives", "conservative")
        self.sensitivity_combo.addItem("Balanced", "balanced")
        self.sensitivity_combo.addItem("Sensitive — more candidates", "sensitive")
        index = self.sensitivity_combo.findData(str(default_sensitivity or "conservative"))
        self.sensitivity_combo.setCurrentIndex(max(0, index))

        self.engine_combo = QComboBox()
        if active_model_id:
            automatic_label = f"Automatic — validated local model {active_model_id}"
        else:
            automatic_label = "Automatic — deterministic fallback (no validated local model)"
        self.engine_combo.addItem(automatic_label, "automatic")
        self.engine_combo.addItem("Deterministic detector only", "deterministic")
        index = self.engine_combo.findData(str(default_engine or "automatic"))
        self.engine_combo.setCurrentIndex(max(0, index))

        notice = QLabel(
            "Experimental: candidates are suggestions. Review the mask and select "
            "Fundamental, Harmonic, or both when a paired detection is available."
        )
        notice.setWordWrap(True)
        notice.setObjectName("TypeIIExperimentalNotice")
        form = QFormLayout()
        form.addRow("Scan:", self.scope_combo)
        form.addRow("Sensitivity:", self.sensitivity_combo)
        form.addRow("Detection engine:", self.engine_combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Scan for Type II Bursts")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(notice)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @property
    def scope(self) -> str:
        return str(self.scope_combo.currentData() or "full")

    @property
    def sensitivity(self) -> str:
        return str(self.sensitivity_combo.currentData() or "conservative")

    @property
    def engine(self) -> str:
        return str(self.engine_combo.currentData() or "automatic")


class TypeIIDetectionPreviewDialog(QDialog):
    def __init__(
        self,
        result: TypeIIDetectionResult,
        display_data: np.ndarray,
        freqs_mhz: np.ndarray,
        times_s: np.ndarray,
        parent=None,
        *,
        cmap="viridis",
    ):
        super().__init__(parent)
        self.setWindowTitle("Type II Candidates — Experimental")
        self.resize(980, 720)
        self.detection_result = result
        self.display_data = np.asarray(display_data)
        self.freqs = np.asarray(freqs_mhz, dtype=float)
        self.times = np.asarray(times_s, dtype=float)
        self.candidates = list(result.candidates)
        self.rejected_candidate_ids: list[str] = []
        self.clean_range_requested = False
        self.selected_candidate: TypeIICandidate | None = None
        self.selected_candidates: tuple[TypeIICandidate, ...] = ()
        self.selected_band: TypeIIBand | Literal["both"] | None = None

        self.candidate_combo = QComboBox()
        for index, candidate in enumerate(self.candidates, start=1):
            suggested = f" · likely {candidate.suggested_band}" if candidate.suggested_band else ""
            self.candidate_combo.addItem(
                f"{index}. confidence {candidate.confidence:.3f}{suggested}",
                candidate.candidate_id,
            )
        self.candidate_combo.currentIndexChanged.connect(self._refresh_candidate)

        self.metrics = QLabel()
        self.metrics.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.metrics.setWordWrap(True)

        band_box = QGroupBox("Band to isolate (required)")
        band_layout = QHBoxLayout(band_box)
        self.fundamental_radio = QRadioButton("Fundamental")
        self.harmonic_radio = QRadioButton("Harmonic")
        self.both_radio = QRadioButton("Both detected bands")
        self.band_group = QButtonGroup(self)
        self.band_group.addButton(self.fundamental_radio)
        self.band_group.addButton(self.harmonic_radio)
        self.band_group.addButton(self.both_radio)
        band_layout.addWidget(self.fundamental_radio)
        band_layout.addWidget(self.harmonic_radio)
        band_layout.addWidget(self.both_radio)
        band_layout.addStretch()

        self.canvas = MplCanvas(self, width=9, height=5)
        self.isolate_button = QPushButton("Isolate Selected Band")
        self.reject_button = QPushButton("Not Type II")
        self.clean_button = QPushButton("No Type II in Reviewed Range")
        self.cancel_button = QPushButton("Cancel")
        self.isolate_button.clicked.connect(self._accept_selected)
        self.reject_button.clicked.connect(self._reject_current)
        self.clean_button.clicked.connect(self._mark_clean)
        self.cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self.isolate_button)
        button_row.addWidget(self.reject_button)
        button_row.addWidget(self.clean_button)
        button_row.addStretch()
        button_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.candidate_combo)
        layout.addWidget(self.metrics)
        layout.addWidget(band_box)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(button_row)
        self._cmap = cmap
        self._refresh_candidate()

    def _current_candidate(self) -> TypeIICandidate | None:
        candidate_id = str(self.candidate_combo.currentData() or "")
        return next((item for item in self.candidates if item.candidate_id == candidate_id), None)

    def _paired_candidate(self, candidate: TypeIICandidate | None) -> TypeIICandidate | None:
        if candidate is None or not candidate.paired_candidate_id:
            return None
        partner = next(
            (
                item
                for item in self.detection_result.generated_candidates
                if item.candidate_id == candidate.paired_candidate_id
            ),
            None,
        )
        if partner is None:
            return None
        if {
            candidate.suggested_band,
            partner.suggested_band,
        } != {"fundamental", "harmonic"}:
            return None
        return partner

    def _refresh_candidate(self):
        candidate = self._current_candidate()
        self.band_group.setExclusive(False)
        self.fundamental_radio.setChecked(False)
        self.harmonic_radio.setChecked(False)
        self.both_radio.setChecked(False)
        self.band_group.setExclusive(True)
        partner = self._paired_candidate(candidate)
        self.both_radio.setVisible(partner is not None)
        self.both_radio.setEnabled(partner is not None)
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)
        style_axes(self.canvas.ax)
        self.canvas.ax.imshow(
            masked_display_data(self.display_data),
            aspect="auto",
            extent=matplotlib_extent(self.freqs, self.times),
            cmap=self._cmap,
        )
        if candidate is None:
            self.metrics.setText("No remaining candidates.")
            self.isolate_button.setEnabled(False)
            self.reject_button.setEnabled(False)
            self.canvas.draw_idle()
            return
        learned = "not active" if candidate.learned_probability is None else f"{candidate.learned_probability:.3f}"
        calibration = "station-calibrated" if candidate.station_calibrated else "global/deterministic"
        self.metrics.setText(
            f"Confidence {candidate.confidence:.3f} · deterministic {candidate.deterministic_score:.3f} "
            f"· learned {learned} · {calibration}\n"
            f"Time {candidate.start_time_s:.1f}–{candidate.end_time_s:.1f} s · "
            f"frequency {candidate.high_frequency_mhz:.2f}–{candidate.low_frequency_mhz:.2f} MHz\n"
            f"Drift {candidate.drift_mhz_s:.4f} MHz/s · duration {candidate.duration_s:.1f} s "
            f"· span {candidate.frequency_span_mhz:.2f} MHz · continuity {candidate.continuity:.2f} "
            f"· median SNR {candidate.median_snr:.2f} · fit {candidate.fit_quality:.3f}"
        )
        overlay = np.ma.masked_where(~candidate.mask, candidate.mask.astype(float))
        self.canvas.ax.imshow(
            overlay,
            aspect="auto",
            extent=matplotlib_extent(self.freqs, self.times),
            cmap="autumn",
            alpha=0.35,
            vmin=0.0,
            vmax=1.0,
        )
        self.canvas.ax.plot(candidate.ridge_times_s, candidate.ridge_freqs_mhz, color="#00ffff", linewidth=1.5)
        if partner is not None:
            partner_overlay = np.ma.masked_where(~partner.mask, partner.mask.astype(float))
            self.canvas.ax.imshow(
                partner_overlay,
                aspect="auto",
                extent=matplotlib_extent(self.freqs, self.times),
                cmap="winter",
                alpha=0.20,
                vmin=0.0,
                vmax=1.0,
            )
            self.canvas.ax.plot(
                partner.ridge_times_s,
                partner.ridge_freqs_mhz,
                color="#66ff99",
                linewidth=1.1,
                linestyle="--",
            )
        self.canvas.ax.set_title(f"Type II candidate {candidate.candidate_id}")
        self.canvas.ax.set_xlabel("Time [s]")
        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.figure.tight_layout()
        self.canvas.draw_idle()
        self.isolate_button.setEnabled(True)
        self.reject_button.setEnabled(True)

    def _accept_selected(self):
        candidate = self._current_candidate()
        if candidate is None:
            return
        if self.fundamental_radio.isChecked():
            band: TypeIIBand | Literal["both"] = "fundamental"
        elif self.harmonic_radio.isChecked():
            band = "harmonic"
        elif self.both_radio.isChecked():
            band = "both"
        else:
            QMessageBox.information(
                self,
                "Select Emission Band",
                "Choose Fundamental, Harmonic, or Both detected bands before isolation.",
            )
            return
        partner = self._paired_candidate(candidate)
        if band == "both":
            if partner is None:
                QMessageBox.information(
                    self,
                    "Paired Bands Unavailable",
                    "Both can be selected only when a validated fundamental/harmonic pair was detected.",
                )
                return
            selected = (candidate, partner)
            selected_candidate = next(
                (item for item in selected if item.suggested_band == "fundamental"),
                candidate,
            )
            self.selected_candidates = tuple(
                sorted(selected, key=lambda item: item.suggested_band != "fundamental")
            )
            self.selected_candidate = selected_candidate
            self.selected_band = band
            self.accept()
            return
        selected_candidate = candidate
        if (
            partner is not None
            and candidate.suggested_band in ("fundamental", "harmonic")
            and candidate.suggested_band != band
        ):
            if partner.suggested_band == band:
                selected_candidate = partner
        self.selected_candidate = selected_candidate
        self.selected_candidates = (selected_candidate,)
        self.selected_band = band
        self.accept()

    def _reject_current(self):
        candidate = self._current_candidate()
        if candidate is None:
            return
        self.rejected_candidate_ids.append(candidate.candidate_id)
        self.candidates = [item for item in self.candidates if item.candidate_id != candidate.candidate_id]
        self.candidate_combo.blockSignals(True)
        self.candidate_combo.clear()
        for index, item in enumerate(self.candidates, start=1):
            self.candidate_combo.addItem(f"{index}. confidence {item.confidence:.3f}", item.candidate_id)
        self.candidate_combo.blockSignals(False)
        if not self.candidates:
            self.reject()
        else:
            self._refresh_candidate()

    def _mark_clean(self):
        answer = QMessageBox.question(
            self,
            "Confirm Reviewed Range",
            "Mark every generated candidate in this scanned range as Not Type II?\n\n"
            "Unreviewed pixels outside the range will not be labeled.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.clean_range_requested = True
        self.reject()


class TypeIITeachLabelDialog(QDialog):
    def __init__(self, parent=None, *, allow_pair: bool = True, required_band: TypeIIBand | None = None):
        super().__init__(parent)
        self.setWindowTitle("Teach Type II Detector")
        text = QLabel("Confirm the Type II emission band enclosed by this lasso.")
        text.setWordWrap(True)
        self.fundamental_radio = QRadioButton("Fundamental")
        self.harmonic_radio = QRadioButton("Harmonic")
        group = QButtonGroup(self)
        group.addButton(self.fundamental_radio)
        group.addButton(self.harmonic_radio)
        if required_band == "fundamental":
            self.fundamental_radio.setChecked(True)
            self.harmonic_radio.setEnabled(False)
        elif required_band == "harmonic":
            self.harmonic_radio.setChecked(True)
            self.fundamental_radio.setEnabled(False)
        self.add_pair = QCheckBox("Add the paired band with a second lasso")
        self.add_pair.setVisible(bool(allow_pair))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Confirm Teaching Mask")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.fundamental_radio)
        row.addWidget(self.harmonic_radio)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(row)
        layout.addWidget(self.add_pair)
        layout.addWidget(buttons)

    @property
    def band(self) -> TypeIIBand | None:
        if self.fundamental_radio.isChecked():
            return "fundamental"
        if self.harmonic_radio.isChecked():
            return "harmonic"
        return None

    def _validate(self):
        if self.band is None:
            QMessageBox.information(self, "Select Emission Band", "Choose Fundamental or Harmonic.")
            return
        self.accept()


class TypeIILearningManagerDialog(QDialog):
    def __init__(
        self,
        library: TypeIILearningLibrary,
        parent=None,
        *,
        train_callback: Callable[[], None] | None = None,
        detect_callback: Callable[[], None] | None = None,
        detection_mode: str = "automatic",
        mode_changed_callback: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Type II Learning Manager")
        self.resize(760, 500)
        self.library = library
        self.train_callback = train_callback
        self.detect_callback = detect_callback
        self.mode_changed_callback = mode_changed_callback
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        self.path_label = QLabel(str(library.root))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.history = QTableWidget(0, 7)
        self.history.setHorizontalHeaderLabels(
            [
                "Created",
                "Model",
                "Status",
                "Precision",
                "Recall",
                "False/15 min",
                "Mask IoU",
            ]
        )

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(
            "Automatic — validated local model with deterministic fallback",
            "automatic",
        )
        self.mode_combo.addItem("Deterministic detector only", "deterministic")
        mode_index = self.mode_combo.findData(str(detection_mode or "automatic"))
        self.mode_combo.setCurrentIndex(max(0, mode_index))
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)

        self.detect_button = QPushButton("Detect Current Spectrum Automatically…")
        self.detect_button.clicked.connect(self._detect_automatically)
        train_button = QPushButton("Train Now")
        rollback_button = QPushButton("Rollback")
        export_button = QPushButton("Export Library…")
        import_button = QPushButton("Import Library…")
        delete_button = QPushButton("Delete Learned Data…")
        close_button = QPushButton("Close")
        train_button.clicked.connect(self._train)
        rollback_button.clicked.connect(self._rollback)
        export_button.clicked.connect(self._export)
        import_button.clicked.connect(self._import)
        delete_button.clicked.connect(self._delete)
        close_button.clicked.connect(self.accept)
        row = QHBoxLayout()
        for button in (train_button, rollback_button, export_button, import_button, delete_button):
            row.addWidget(button)
        row.addStretch()
        row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Persistent local learning library:"))
        layout.addWidget(self.path_label)
        layout.addWidget(self.summary)
        mode_form = QFormLayout()
        mode_form.addRow("Detection mode:", self.mode_combo)
        layout.addLayout(mode_form)
        layout.addWidget(self.detect_button)
        layout.addWidget(self.readiness)
        layout.addWidget(self.history, 1)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        counts = self.library.counts()
        examples = self.library.examples()
        positive_sources = {
            item.source_hash for item in examples if int(item.label) == 1
        }
        negative_sources = {
            item.source_hash for item in examples if int(item.label) == 0
        }
        active = self.library.active_manifest()
        active_text = (
            "deterministic cold start"
            if active is None
            else f"{active.model_id} (Locally validated)"
        )
        self.summary.setText(
            f"{counts['positive']} positive · {counts['negative']} negative · "
            f"{counts['sources']} observations · active: {active_text}"
        )
        history = self.library.model_history()
        if active is None:
            engine_text = (
                "Automatic detection is available now using the deterministic fallback. "
                "A learned model will be used automatically only after it passes validation."
            )
        else:
            engine_text = (
                f"Automatic detection will use validated model {active.model_id}, blended "
                "with the deterministic Type II score."
            )
        coverage_text = (
            f"Training coverage: {len(positive_sources)} positive-source and "
            f"{len(negative_sources)} negative-source observations. Validation reserves "
            "at least 5 of each while keeping both classes for training."
        )
        latest_text = ""
        if history:
            latest = history[0]
            if latest.metrics:
                latest_text = (
                    " Latest validation: "
                    f"precision {float(latest.metrics.get('precision', 0.0)):.3f}, "
                    f"recall {float(latest.metrics.get('recall', 0.0)):.3f}, "
                    "false candidates/15 min "
                    f"{float(latest.metrics.get('false_candidates_per_15m', 0.0)):.3f}."
                )
            elif latest.status_reason:
                latest_text = f" Latest training result: {latest.status_reason}"
        self.readiness.setText(f"{engine_text}\n{coverage_text}{latest_text}")
        self.history.setRowCount(len(history))
        for row, item in enumerate(history):
            values = (
                item.created_at,
                item.model_id,
                "Promoted" if item.promoted else item.status_reason,
                item.metrics.get("precision", ""),
                item.metrics.get("recall", ""),
                item.metrics.get("false_candidates_per_15m", ""),
                item.metrics.get("mask_iou", ""),
            )
            for column, value in enumerate(values):
                if isinstance(value, float):
                    text = f"{value:.3f}"
                else:
                    text = str(value)
                self.history.setItem(row, column, QTableWidgetItem(text))
        self.history.resizeColumnsToContents()

    def _mode_changed(self):
        if self.mode_changed_callback is not None:
            self.mode_changed_callback(str(self.mode_combo.currentData() or "automatic"))

    def _detect_automatically(self):
        automatic_index = self.mode_combo.findData("automatic")
        self.mode_combo.setCurrentIndex(max(0, automatic_index))
        if self.detect_callback is None:
            QMessageBox.information(
                self,
                "Automatic Type II Detection",
                "Automatic detection is unavailable from this window.",
            )
            return
        self.detect_callback()

    def _train(self):
        if self.train_callback is not None:
            self.train_callback()
        self.refresh()

    def _rollback(self):
        selected = self.library.rollback()
        if selected is None:
            QMessageBox.information(self, "Rollback", "No earlier promoted model is available.")
        else:
            QMessageBox.information(self, "Rollback", f"Active model changed to {selected.model_id}.")
        self.refresh()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Type II Learning Library",
            "type_ii_learning.ecallisto-typeii",
            "Type II learning packages (*.ecallisto-typeii)",
        )
        if not path:
            return
        if not path.lower().endswith(".ecallisto-typeii"):
            path += ".ecallisto-typeii"
        try:
            self.library.export_package(path)
            QMessageBox.information(self, "Export Complete", f"Learning library saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Type II Learning Library",
            "",
            "Type II learning packages (*.ecallisto-typeii)",
        )
        if not path:
            return
        try:
            count = self.library.import_package(path)
            QMessageBox.information(self, "Import Complete", f"Imported {count} new reviewed examples.")
            if count and self.train_callback is not None:
                self.train_callback()
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", str(exc))
        self.refresh()

    def _delete(self):
        answer = QMessageBox.warning(
            self,
            "Delete Learned Type II Data",
            "Permanently delete all local Type II examples and model versions?\n\n"
            "Export a backup first if this data may be needed on another installation.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        import shutil

        shutil.rmtree(self.library.root, ignore_errors=False)
        self.library.ensure()
        self.refresh()
