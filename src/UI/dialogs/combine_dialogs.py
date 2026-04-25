"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import io
import os
import tempfile

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from src.Backend.burst_processor import combine_frequency, describe_frequency_combination
from src.Backend.frequency_axis import (
    finite_data_limits,
    frequency_gap_spans,
    masked_display_data,
    matplotlib_extent,
    transparent_bad_cmap,
)
from src.Backend.fits_io import build_combined_header, extract_ut_start_sec, load_callisto_fits, preview_callisto_fits
from src.UI.mpl_style import style_axes


class FrequencyCombineOptionsDialog(QDialog):
    def __init__(self, file_paths, parent=None):
        super().__init__(parent)
        self.file_paths = list(file_paths or [])
        self.relation = describe_frequency_combination(self.file_paths)
        self.setWindowTitle("Frequency Combine Options")
        self.setMinimumWidth(520)

        self.gap_fill_combo = QComboBox()
        self.gap_fill_combo.addItem("Interpolated background fill", "background")
        self.gap_fill_combo.addItem("Gray-hatched blank gap", "hatched")
        self.gap_fill_combo.addItem("Average edge background fill", "average")
        self.gap_fill_combo.addItem("Zero fill", "zero")

        self.overlap_policy_combo = QComboBox()
        self.overlap_policy_combo.addItem("Split at connection frequency", "split")
        self.overlap_policy_combo.addItem(self._low_overlap_label(), "low")
        self.overlap_policy_combo.addItem(self._high_overlap_label(), "high")
        self.overlap_policy_combo.addItem("Reject overlap", "reject")
        self.overlap_policy_combo.currentIndexChanged.connect(self._sync_connection_controls)

        self.connection_spin = QDoubleSpinBox()
        self.connection_spin.setDecimals(3)
        self.connection_spin.setRange(0.0, 10000.0)
        self.connection_spin.setSuffix(" MHz")
        self.connection_spin.setEnabled(False)
        self._configure_connection_range()

        layout = QVBoxLayout()
        layout.addWidget(QLabel(self._summary_text()))

        if self.relation.get("has_gap", False):
            gap_group = QGroupBox("Frequency Gap")
            gap_layout = QFormLayout()
            gap_layout.addRow("Gap handling", self.gap_fill_combo)
            gap_group.setLayout(gap_layout)
            layout.addWidget(gap_group)

        if self.relation.get("has_overlap", False):
            overlap_group = QGroupBox("Frequency Overlap")
            overlap_layout = QFormLayout()
            overlap_layout.addRow("Overlap handling", self.overlap_policy_combo)
            overlap_layout.addRow("Connection frequency", self.connection_spin)
            overlap_group.setLayout(overlap_layout)
            layout.addWidget(overlap_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._sync_connection_controls()

    @classmethod
    def choose(cls, parent, file_paths):
        relation = describe_frequency_combination(file_paths)
        if not relation.get("has_gap", False) and not relation.get("has_overlap", False):
            return {
                "gap_fill": "background",
                "overlap_policy": "split",
                "overlap_connection_mhz": None,
            }

        dialog = cls(file_paths, parent=parent)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.selected_options()

    def selected_options(self) -> dict:
        return {
            "gap_fill": str(self.gap_fill_combo.currentData() or "background"),
            "overlap_policy": str(self.overlap_policy_combo.currentData() or "split"),
            "overlap_connection_mhz": self._selected_connection_mhz(),
        }

    def _selected_connection_mhz(self):
        if not self.connection_spin.isEnabled():
            return None
        return float(self.connection_spin.value())

    def _sync_connection_controls(self):
        has_overlap = bool(self.relation.get("has_overlap", False))
        is_split = str(self.overlap_policy_combo.currentData() or "split") == "split"
        self.connection_spin.setEnabled(bool(has_overlap and is_split))

    def _configure_connection_range(self):
        overlaps = list(self.relation.get("overlaps") or [])
        if not overlaps:
            return
        lo = min(float(item["low"]) for item in overlaps)
        hi = max(float(item["high"]) for item in overlaps)
        if hi < lo:
            lo, hi = hi, lo
        self.connection_spin.setRange(lo, hi)
        self.connection_spin.setValue(0.5 * (lo + hi))

    def _summary_text(self) -> str:
        lines = ["The selected files can be frequency-combined, but require a merge choice."]
        gaps = list(self.relation.get("gaps") or [])
        overlaps = list(self.relation.get("overlaps") or [])
        if gaps:
            gap_text = ", ".join(f"{float(g['low']):.3f}-{float(g['high']):.3f} MHz" for g in gaps)
            lines.append(f"Gap detected: {gap_text}.")
        if overlaps:
            overlap_text = ", ".join(f"{float(o['low']):.3f}-{float(o['high']):.3f} MHz" for o in overlaps)
            lines.append(f"Overlap detected: {overlap_text}.")
        return "\n".join(lines)

    def _low_overlap_label(self) -> str:
        overlaps = list(self.relation.get("overlaps") or [])
        if len(overlaps) == 1:
            return f"Use lower-frequency file ({os.path.basename(str(overlaps[0]['lower_file']))})"
        return "Use lower-frequency file(s)"

    def _high_overlap_label(self) -> str:
        overlaps = list(self.relation.get("overlaps") or [])
        if len(overlaps) == 1:
            return f"Use higher-frequency file ({os.path.basename(str(overlaps[0]['higher_file']))})"
        return "Use higher-frequency file(s)"


class CombineFrequencyDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Combine Frequency Ranges")
        self.setMinimumWidth(600)

        self.file_paths = []

        self.load_button = QPushButton("Import FITS Files")
        self.load_button.clicked.connect(self.load_files)

        self.combine_button = QPushButton("Combine")
        self.combine_button.clicked.connect(self.combine_files)
        self.combine_button.setEnabled(False)

        self.import_button = QPushButton("Import to Analyzer")
        self.import_button.clicked.connect(self.import_to_main)
        self.import_button.setEnabled(False)

        self.gap_fill_combo = QComboBox()
        self.gap_fill_combo.addItem("Interpolated background", "background")
        self.gap_fill_combo.addItem("Gray hatched gap", "hatched")
        self.gap_fill_combo.addItem("Average edge background", "average")
        self.gap_fill_combo.addItem("Zero fill", "zero")

        self.overlap_policy_combo = QComboBox()
        self.overlap_policy_combo.addItem("Split at connection frequency", "split")
        self.overlap_policy_combo.addItem("Keep low band in overlap", "low")
        self.overlap_policy_combo.addItem("Keep high band in overlap", "high")
        self.overlap_policy_combo.addItem("Reject overlap", "reject")
        self.overlap_policy_combo.currentIndexChanged.connect(self._sync_connection_controls)

        self.connection_spin = QDoubleSpinBox()
        self.connection_spin.setDecimals(3)
        self.connection_spin.setRange(0.0, 10000.0)
        self.connection_spin.setSuffix(" MHz")
        self.connection_spin.setEnabled(False)

        options_group = QGroupBox("Frequency Combine Options")
        options_layout = QFormLayout()
        options_layout.addRow("Gap handling", self.gap_fill_combo)
        options_layout.addRow("Overlap handling", self.overlap_policy_combo)
        options_layout.addRow("Connection frequency", self.connection_spin)
        options_group.setLayout(options_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.image_label = QLabel("Combined output will appear here.")
        self.image_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout()
        layout.addWidget(self.load_button)
        layout.addWidget(options_group)
        layout.addWidget(self.combine_button)
        layout.addWidget(self.import_button)
        layout.addWidget(self.image_label)
        layout.addWidget(self.progress_bar)

        self.setLayout(layout)

        self.combined_data = None
        self.combined_freqs = None
        self.combined_time = None
        self.combined_filename = "Combined_Frequency"
        self.combined_header0 = None
        self.combined_gap_row_mask = None
        self.combined_frequency_step_mhz = None
        self._overlap_range = None

    def load_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select FITS Files to Combine",
            "",
            "FITS files (*.fit *.fits *.fit.gz *.fits.gz)"
        )
        if len(files) != 2:
            QMessageBox.warning(self, "Error", "Please select exactly TWO files.")
            return

        station1 = files[0].split("/")[-1].split("_")[0]
        station2 = files[1].split("/")[-1].split("_")[0]

        if station1 != station2:
            QMessageBox.critical(self, "Error",
                                 "You must select frequency data files from the same station!")
            return

        self.file_paths = files
        self._refresh_overlap_controls()
        options = FrequencyCombineOptionsDialog.choose(self, self.file_paths)
        if options is None:
            self.file_paths = []
            self.combine_button.setEnabled(False)
            return
        self._apply_selected_options(options)
        self.combine_button.setEnabled(True)

    def _apply_selected_options(self, options: dict):
        gap_idx = self.gap_fill_combo.findData(str(options.get("gap_fill", "background")))
        if gap_idx >= 0:
            self.gap_fill_combo.setCurrentIndex(gap_idx)

        overlap_idx = self.overlap_policy_combo.findData(str(options.get("overlap_policy", "split")))
        if overlap_idx >= 0:
            self.overlap_policy_combo.setCurrentIndex(overlap_idx)

        connection = options.get("overlap_connection_mhz", None)
        if connection is not None:
            try:
                self.connection_spin.setValue(float(connection))
            except Exception:
                pass
        self._sync_connection_controls()

    def _selected_gap_fill(self):
        return str(self.gap_fill_combo.currentData() or "background")

    def _selected_overlap_policy(self):
        return str(self.overlap_policy_combo.currentData() or "split")

    def _selected_connection_mhz(self):
        if not self.connection_spin.isEnabled():
            return None
        return float(self.connection_spin.value())

    def _sync_connection_controls(self):
        has_overlap = self._overlap_range is not None
        is_split = self._selected_overlap_policy() == "split"
        self.connection_spin.setEnabled(bool(has_overlap and is_split))

    def _refresh_overlap_controls(self):
        self._overlap_range = None
        self.connection_spin.setEnabled(False)
        if len(self.file_paths) != 2:
            return

        try:
            ranges = []
            for path in self.file_paths:
                preview = preview_callisto_fits(path, memmap=False)
                freqs = np.asarray(preview.freqs, dtype=float).ravel()
                ranges.append((float(np.nanmin(freqs)), float(np.nanmax(freqs))))
            overlap_min = max(r[0] for r in ranges)
            overlap_max = min(r[1] for r in ranges)
            if overlap_min <= overlap_max:
                self._overlap_range = (overlap_min, overlap_max)
                self.connection_spin.setRange(overlap_min, overlap_max)
                self.connection_spin.setValue(0.5 * (overlap_min + overlap_max))
        except Exception:
            self._overlap_range = None

        self._sync_connection_controls()

    def combine_files(self):
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)
        QApplication.processEvents()

        try:
            combined = combine_frequency(
                self.file_paths,
                gap_fill=self._selected_gap_fill(),
                overlap_policy=self._selected_overlap_policy(),
                overlap_connection_mhz=self._selected_connection_mhz(),
            )
            self.combined_data = np.asarray(combined["data"])
            self.combined_freqs = np.asarray(combined["freqs"], dtype=float)
            self.combined_time = np.asarray(combined["time"], dtype=float)
            self.combined_header0 = combined.get("header0", None)
            self.combined_gap_row_mask = combined.get("gap_row_mask", None)
            self.combined_frequency_step_mhz = combined.get("frequency_step_mhz", None)
            self.progress_bar.setValue(80)
            QApplication.processEvents()

            # Plot image
            fig, ax = plt.subplots(figsize=(6, 4))
            style_axes(ax)
            cmap = transparent_bad_cmap(
                mcolors.LinearSegmentedColormap.from_list("custom", [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')])
            )
            im = ax.imshow(
                masked_display_data(self.combined_data),
                aspect="auto",
                extent=matplotlib_extent(
                    self.combined_freqs,
                    self.combined_time,
                    default_step=self.combined_frequency_step_mhz,
                ),
                cmap=cmap,
            )
            vmin, vmax = finite_data_limits(self.combined_data)
            if vmin is not None and vmax is not None:
                im.set_clim(vmin, vmax)
            for lo, hi in frequency_gap_spans(
                self.combined_freqs,
                self.combined_gap_row_mask,
                default_step=self.combined_frequency_step_mhz or 1.0,
            ):
                ax.axhspan(
                    lo,
                    hi,
                    facecolor="#b8b8b8",
                    edgecolor="#555555",
                    alpha=0.35,
                    hatch="///",
                    linewidth=0.0,
                    zorder=3,
                )
            ax.set_xlabel("Time [s]")
            ax.set_ylabel("Frequency [MHz]")
            # Extract base filenames (e.g., 'BIR_20240720_123000_123000_00.fit.gz')
            fname1 = os.path.basename(self.file_paths[0])
            fname2 = os.path.basename(self.file_paths[1])

            # Extract focus codes (last 2 digits before .fit.gz, assuming filename ends with _00.fit.gz or _01.fit.gz etc.)
            focus1 = fname1.split("_")[-1].split(".")[0]
            focus2 = fname2.split("_")[-1].split(".")[0]

            # Extract common base (e.g., remove focus code and extension)
            base_name = "_".join(fname1.split("_")[:-1])

            # Set title with base + both focus codes
            ax.set_title(f"{base_name}_{focus1}+{focus2} (Combined Frequency)")

            self.combined_title = f"{base_name}_{focus1}+{focus2} (Combined Frequency)"
            ax.set_title(self.combined_title)

            buf = io.BytesIO()
            fig.savefig(buf, format='png')
            buf.seek(0)
            img = QImage()
            img.loadFromData(buf.read())
            self.image_label.setPixmap(QPixmap.fromImage(img).scaledToWidth(550))
            buf.close()
            plt.close(fig)

            self.progress_bar.setValue(100)
            QApplication.processEvents()
            self.import_button.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.progress_bar.setVisible(False)

    def import_to_main(self):
        if self.combined_data is None or self.combined_freqs is None or self.combined_time is None:
            QMessageBox.warning(self, "No Data", "Please combine the files first.")
            return
        payload = {
            "data": self.combined_data,
            "freqs": self.combined_freqs,
            "time": self.combined_time,
            "filename": getattr(self, "combined_title", self.combined_filename),
            "ut_start_sec": extract_ut_start_sec(self.combined_header0),
            "combine_type": "frequency",
            "sources": list(self.file_paths),
            "header0": self.combined_header0,
            "gap_row_mask": self.combined_gap_row_mask,
            "frequency_step_mhz": self.combined_frequency_step_mhz,
        }
        self.main_window.load_combined_into_main(payload)
        self.close()


class CombineTimeDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Combine Time Ranges")
        self.setMinimumWidth(600)

        self.file_paths = []
        self.combined_data = None
        self.combined_time = None
        self.combined_freqs = None
        self.combined_filename = "Combined_Time"
        self.combined_header0 = None

        # Buttons
        self.load_button = QPushButton("Import FITS Files")
        self.load_button.clicked.connect(self.load_files)

        self.combine_button = QPushButton("Combine")
        self.combine_button.clicked.connect(self.combine_files)
        self.combine_button.setEnabled(False)

        self.import_button = QPushButton("Import to Analyzer")
        self.import_button.clicked.connect(self.import_to_main)
        self.import_button.setEnabled(False)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        # Output Image Preview
        self.image_label = QLabel("Combined output will appear here.")
        self.image_label.setAlignment(Qt.AlignCenter)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.load_button)
        layout.addWidget(self.combine_button)
        layout.addWidget(self.import_button)
        layout.addWidget(self.image_label)
        layout.addWidget(self.progress_bar)
        self.setLayout(layout)

    def load_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select FITS Files to Combine",
            "",
            "FITS files (*.fit *.fits *.fit.gz *.fits.gz)"
        )

        if len(files) < 2:
            QMessageBox.warning(self, "Error", "Please select at least 2 FITS files.")
            return

        try:
            from datetime import datetime

            # Sort files by timestamp
            self.file_paths = sorted(files, key=lambda f: os.path.basename(f).split("_")[2])

            # Check station/date and time continuity
            parts_ref = os.path.basename(self.file_paths[0]).split("_")
            t_ref = datetime.strptime(parts_ref[2], "%H%M%S")

            for f in self.file_paths[1:]:
                parts = os.path.basename(f).split("_")
                if parts[0] != parts_ref[0] or parts[1] != parts_ref[1]:
                    raise ValueError("Different station or date")

                t_next = datetime.strptime(parts[2], "%H%M%S")
                diff = abs((t_next - t_ref).total_seconds())
                if not (800 <= diff <= 1000):  # ~15min ±1.5min
                    raise ValueError(f"File {f} is not consecutive")
                t_ref = t_next

            self.combine_button.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Invalid Selection", f"Error while validating files:\n{str(e)}")

    def combine_files(self):
        if len(self.file_paths) < 2:
            QMessageBox.warning(self, "Error", "Please load at least 2 valid FITS files to combine.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)

        try:
            combined_data = None
            combined_time = None
            reference_freqs = None
            header0 = None

            for idx, file_path in enumerate(self.file_paths):
                res = load_callisto_fits(file_path, memmap=False)
                data, freqs, time = res.data, res.freqs, res.time
                if header0 is None:
                    header0 = res.header0

                if reference_freqs is None:
                    reference_freqs = freqs
                elif not np.allclose(freqs, reference_freqs):
                    raise ValueError("Frequency mismatch in file: " + os.path.basename(file_path))

                # Compute dt and shift time
                if idx == 0:
                    dt = time[1] - time[0]
                    adjusted_time = time
                    combined_data = data
                    combined_time = adjusted_time
                else:
                    dt = time[1] - time[0]
                    shift = combined_time[-1] + dt
                    adjusted_time = time + shift
                    combined_data = np.concatenate((combined_data, data), axis=1)
                    combined_time = np.concatenate((combined_time, adjusted_time))

            self.combined_data = combined_data
            self.combined_time = combined_time
            self.combined_freqs = reference_freqs
            self.combined_header0 = build_combined_header(
                header0,
                mode="time",
                sources=self.file_paths,
                data_shape=combined_data.shape,
                freqs=reference_freqs,
                time=combined_time,
            )

            self.progress_bar.setValue(80)

            # Plot preview
            fig, ax = plt.subplots(figsize=(6, 4))
            style_axes(ax)
            extent = [combined_time[0], combined_time[-1], reference_freqs[-1], reference_freqs[0]]
            cmap = LinearSegmentedColormap.from_list('custom_cmap', [(0, 'darkblue'), (1, 'orange')])
            im = ax.imshow(combined_data, aspect='auto', extent=extent, cmap=cmap)
            ax.set_xlabel("Time [s]")
            ax.set_ylabel("Frequency [MHz]")
            ax.set_title("Combined Time Plot")
            fig.tight_layout()

            temp_dir = tempfile.gettempdir()
            preview_path = os.path.join(temp_dir, "preview_combined_time.png")
            fig.savefig(preview_path, dpi=100)
            plt.close(fig)

            self.image_label.setPixmap(QPixmap(preview_path).scaled(550, 350, Qt.KeepAspectRatio))
            self.progress_bar.setValue(100)
            self.import_button.setEnabled(True)

            # Set filename
            base1 = os.path.basename(self.file_paths[0]).split(".")[0]
            self.combined_filename = base1 + "_combined_time"

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to combine:\n{str(e)}")

    def import_to_main(self):
        if self.combined_data is None or self.combined_time is None or self.combined_freqs is None:
            QMessageBox.warning(self, "No Data", "Please combine the files first.")
            return

        payload = {
            "data": self.combined_data,
            "freqs": self.combined_freqs,
            "time": self.combined_time,
            "filename": self.combined_filename,
            "ut_start_sec": extract_ut_start_sec(self.combined_header0),
            "combine_type": "time",
            "sources": list(self.file_paths),
            "header0": self.combined_header0,
        }
        self.main_window.load_combined_into_main(payload)
        self.close()
