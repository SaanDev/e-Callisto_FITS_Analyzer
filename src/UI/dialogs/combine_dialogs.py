"""
FITS combine dialogs for e-CALLISTO FITS Analyzer.
"""

from __future__ import annotations

import io
import os
import tempfile

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from src.Backend.fits_io import build_combined_header, extract_ut_start_sec, load_callisto_fits
from src.UI.mpl_style import style_axes

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

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.image_label = QLabel("Combined output will appear here.")
        self.image_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout()
        layout.addWidget(self.load_button)
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
                                 "You must select consecutive frequency data files from the same station!")
            return

        self.file_paths = files
        self.combine_button.setEnabled(True)

    def combine_files(self):
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)
        QApplication.processEvents()

        try:
            res1 = load_callisto_fits(self.file_paths[0], memmap=False)
            data1, freqs1, time1, hdr1 = res1.data, res1.freqs, res1.time, res1.header0
            self.progress_bar.setValue(30)
            QApplication.processEvents()

            res2 = load_callisto_fits(self.file_paths[1], memmap=False)
            data2, freqs2, time2 = res2.data, res2.freqs, res2.time
            self.progress_bar.setValue(60)
            QApplication.processEvents()

            if not np.allclose(time1, time2, rtol=1e-2):
                QMessageBox.critical(self, "Error", "Time arrays must match to combine frequencies.")
                self.progress_bar.setVisible(False)
                return

            self.combined_data = np.vstack([data1, data2])
            self.combined_freqs = np.concatenate([freqs1, freqs2])
            self.combined_time = time1
            self.combined_header0 = build_combined_header(
                hdr1,
                mode="frequency",
                sources=self.file_paths,
                data_shape=self.combined_data.shape,
                freqs=self.combined_freqs,
                time=self.combined_time,
            )
            self.progress_bar.setValue(80)
            QApplication.processEvents()

            # Plot image
            fig, ax = plt.subplots(figsize=(6, 4))
            style_axes(ax)
            extent = [0, self.combined_time[-1], self.combined_freqs[-1], self.combined_freqs[0]]
            cmap = mcolors.LinearSegmentedColormap.from_list("custom", [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')])
            ax.imshow(self.combined_data, aspect='auto', extent=extent, cmap=cmap)
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
