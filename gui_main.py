from PySide6.QtWidgets import (
    QMainWindow, QLineEdit, QDialog, QMenuBar, QMessageBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QStatusBar, QProgressBar, QApplication, QMenu, QCheckBox, QRadioButton, QButtonGroup
)
from PySide6.QtGui import QAction, QPixmap, QImage, QGuiApplication
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from callisto_downloader import CallistoDownloaderApp
from goes_xrs_gui import MainWindow as GoesXrsWindow
from soho_lasco_viewer import CMEViewer as CMEViewerWindow
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.widgets import LassoSelector
from matplotlib.path import Path
from astropy.io import fits
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.colors as mcolors
from mpl_toolkits.axes_grid1 import make_axes_locatable
from matplotlib.ticker import FuncFormatter, ScalarFormatter
import csv
import matplotlib.pyplot as plt
from openpyxl import load_workbook, Workbook
import io
import os
import re
import gc

def start_combine(self):
    QTimer.singleShot(100, self.combine_files)  # delays execution and avoids UI freeze

class MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=10, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("e-CALLISTO FITS Analyzer 1.6.0")
        self.resize(1000, 700)
        self.setMinimumSize(1000, 700)

        self.use_utc = False
        self.ut_start_sec = None

        # Canvas
        self.canvas = MplCanvas(self, width=10, height=6)
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        # Colorbar
        self.current_colorbar = None
        self.current_cax = None

        #Statusbar
        self.setStatusBar(QStatusBar())

        # Threshold input fields
        self.lower_thresh_input = QLineEdit("-5")
        self.lower_thresh_input.setMaximumWidth(80)
        self.upper_thresh_input = QLineEdit("20")
        self.upper_thresh_input.setMaximumWidth(80)

        # Labels
        lower_label = QLabel("Lower Threshold:")
        upper_label = QLabel("Upper Threshold:")

        # Buttons
        button_defs = [
            ("Load FITS File", "load_button"),
            ("Apply Noise Reduction", "noise_button"),
            ("Estimate Drift Rate", "drift_button"),
            ("Isolate Burst", "lasso_button"),
            ("Plot Maximum Intensities", "max_plot_button"),
            ("Reset Selection", "reset_selection_button"),
            ("Reset All", "reset_all_button"),
            ("Close Application", "close_button")
        ]

        for label, attr in button_defs:
            btn = QPushButton(label)
            btn.setMinimumWidth(180)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            setattr(self, attr, btn)

        # Threshold input fields using QDoubleSpinBox
        self.lower_thresh_input = QDoubleSpinBox()
        self.lower_thresh_input.setRange(-1000, 1000)
        self.lower_thresh_input.setValue(0)
        self.lower_thresh_input.setDecimals(0)
        self.lower_thresh_input.setSingleStep(1)
        self.lower_thresh_input.setFixedWidth(100)

        self.upper_thresh_input = QDoubleSpinBox()
        self.upper_thresh_input.setRange(-1000, 1000)
        self.upper_thresh_input.setValue(0)
        self.upper_thresh_input.setDecimals(0)
        self.upper_thresh_input.setSingleStep(1)
        self.upper_thresh_input.setFixedWidth(100)

        # Labels
        lower_label = QLabel("Lower Threshold:")
        upper_label = QLabel("Upper Threshold:")

        # Group thresholds into a small form layout
        thresh_form_layout = QFormLayout()
        thresh_form_layout.addRow(lower_label, self.lower_thresh_input)
        thresh_form_layout.addRow(upper_label, self.upper_thresh_input)

        thresh_group = QGroupBox("Noise Clipping Thresholds")
        thresh_group.setLayout(thresh_form_layout)
        thresh_group.setMaximumWidth(250)

        self.lower_thresh_input.setToolTip("Lower clipping threshold for pixel intensity.\nRecommended: -5")
        self.upper_thresh_input.setToolTip("Upper clipping threshold for pixel intensity.\nRecommended: 20")

        # Buttons
        self.load_button = QPushButton("Load FITS File")
        self.noise_button = QPushButton("Apply Noise Reduction")
        self.lasso_button = QPushButton("Isolate Burst")
        self.max_plot_button = QPushButton("Plot Maximum Intensities")

        self.reset_selection_button = QPushButton("Reset Selection")
        self.reset_all_button = QPushButton("Reset All")
        self.close_button = QPushButton("Close Application")

        for btn in [self.reset_selection_button, self.reset_all_button, self.close_button]:
            btn.setMinimumWidth(180)

        for btn in [self.load_button, self.noise_button, self.lasso_button, self.max_plot_button]:
            btn.setMinimumWidth(180)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # Layouts
        button_layout = QVBoxLayout()
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.noise_button)
        button_layout.addWidget(self.drift_button)
        button_layout.addWidget(self.lasso_button)
        button_layout.addWidget(self.max_plot_button)
        button_layout.addWidget(self.reset_selection_button)
        button_layout.addWidget(self.reset_all_button)
        button_layout.addWidget(self.close_button)

        self.noise_button.setEnabled(False)
        self.drift_button.setEnabled(False)
        self.lasso_button.setEnabled(False)
        self.max_plot_button.setEnabled(False)
        self.reset_selection_button.setEnabled(False)
        self.reset_all_button.setEnabled(False)


        # Sidebar layout with thresholds and buttons
        side_panel = QVBoxLayout()
        side_panel.addWidget(thresh_group)
        side_panel.addSpacing(10)
        side_panel.addLayout(button_layout)

        # Main layout with side panel and canvas
        main_layout = QHBoxLayout()
        main_layout.addLayout(side_panel)
        main_layout.addWidget(self.canvas, stretch=1)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # ----- Menu Bar -----
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("File")

        # --- Open ---
        self.open_action = QAction("Open", self)
        file_menu.addAction(self.open_action)

        # --- Save As (disabled for main window) ---
        self.save_action = QAction("Save As", self)
        self.save_action.setEnabled(False)
        file_menu.addAction(self.save_action)

        # --- Export As ---
        self.export_action = QAction("Export As", self)
        file_menu.addAction(self.export_action)
        self.export_action.triggered.connect(self.export_figure)

        # Edit Menu
        edit_menu = menubar.addMenu("Edit")
        reset_action = QAction("Reset All", self)
        edit_menu.addAction(reset_action)
        reset_action.triggered.connect(self.reset_all)

        # Download Menu
        download_menu = menubar.addMenu("Download")

        # --- Launch Downloader ---
        launch_downloader_action = QAction("Launch FITS Downloader", self)
        download_menu.addAction(launch_downloader_action)
        launch_downloader_action.triggered.connect(self.launch_downloader)

        # Combine Menu
        combine_menu = menubar.addMenu("Combine FITS")

        combine_freq_action = QAction("Combine Frequency", self)
        combine_freq_action.triggered.connect(self.open_combine_freq_window)
        combine_menu.addAction(combine_freq_action)

        combine_time_action = QAction("Combine Time", self)
        combine_time_action.triggered.connect(self.open_combine_time_window)
        combine_menu.addAction(combine_time_action)

        # Graph Menu
        graph_menu = menubar.addMenu("Graph")

        xaxis_unit_menu = QMenu("x-axis units", self)
        self.xaxis_sec_action = QAction("Seconds (s)", self, checkable=True)
        self.xaxis_ut_action = QAction("Universal Time (UT)", self, checkable=True)

        # Make "Seconds" default
        self.xaxis_sec_action.setChecked(True)

        xaxis_unit_menu.addAction(self.xaxis_sec_action)
        xaxis_unit_menu.addAction(self.xaxis_ut_action)
        graph_menu.addMenu(xaxis_unit_menu)

        # Toggle logic
        self.xaxis_sec_action.triggered.connect(self.set_axis_to_seconds)
        self.xaxis_ut_action.triggered.connect(self.set_axis_to_utc)

        #CMEs
        cmes_menu = self.menuBar().addMenu("CME")
        soho_lasco_action = QAction("SOHO/LASCO CME Catalog", self)
        soho_lasco_action.triggered.connect(self.open_soho_lasco_window)
        cmes_menu.addAction(soho_lasco_action)

        #Flares
        flares_menu = self.menuBar().addMenu("Flares")
        goes_flux_action = QAction("GOES X-Ray Flux", self)
        goes_flux_action.triggered.connect(self.open_goes_xrs_window)
        flares_menu.addAction(goes_flux_action)

        # About Menu
        about_menu = menubar.addMenu("About")
        about_action = QAction("About", self)
        about_action.setMenuRole(QAction.NoRole)
        about_menu.addAction(about_action)
        about_action.triggered.connect(self.show_about_dialog)

        # (OPTIONAL) Connect them later like:
        # open_action.triggered.connect(self.open_file)

        self.setCentralWidget(container)

        # Signals
        self.load_button.clicked.connect(self.load_file)
        self.noise_button.clicked.connect(self.apply_noise)
        self.drift_button.clicked.connect(self.activate_drift_tool)
        self.lasso_button.clicked.connect(self.activate_lasso)
        self.max_plot_button.clicked.connect(self.plot_max_intensities)
        self.open_action.triggered.connect(self.load_file)
        self.reset_selection_button.clicked.connect(self.reset_selection)
        self.reset_all_button.clicked.connect(self.reset_all)
        self.close_button.clicked.connect(self.close)

        # Data placeholders
        self.raw_data = None
        self.freqs = None
        self.time = None
        self.filename = ""
        self.current_plot_type = "Raw"  # or "NoiseReduced" or "Isolated"

        self.lasso = None
        self.lasso_mask = None
        self.noise_reduced_data = None

        self.setStyleSheet("""
            QLabel {
                font-size: 13px;
            }
            QPushButton {
                font-size: 13px;
                padding: 6px 10px;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
            }
        """)

        self.noise_reduced_original = None  # backup before lasso

    def load_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open FITS File", "", "FITS files (*.fit.gz)")
        if file_path:
            self.filename = file_path.split("/")[-1]
            hdul = fits.open(file_path)
            self.raw_data = hdul[0].data
            self.freqs = hdul[1].data['frequency'][0]
            self.time = hdul[1].data['time'][0]
            hdr = hdul[0].header
            hh, mm, ss = hdr['TIME-OBS'].split(":")
            hh = int(hh)
            mm = int(mm)
            ss = float(ss)  # allow fractional seconds
            self.ut_start_sec = hh * 3600 + mm * 60 + ss

            self.ut_start_sec = hh * 3600 + mm * 60 + ss

            hdul.close()
            self.plot_data(self.raw_data, title="Raw Data")

    def apply_noise(self):
        if self.raw_data is not None:
            try:
                clip_low = float(self.lower_thresh_input.text())
                clip_high = float(self.upper_thresh_input.text())
            except ValueError:
                print("Invalid threshold values")
                return

            data = self.raw_data.copy()
            data = data - data.mean(axis=1, keepdims=True)
            data = np.clip(data, clip_low, clip_high)
            data = data * 2500.0 / 255.0 / 25.4
            self.noise_reduced_data = data
            self.noise_reduced_original = data.copy()  # backup
            self.plot_data(data, title="Noise Reduced")
            self.drift_button.setEnabled(True)
            self.lasso_button.setEnabled(True)
            self.max_plot_button.setEnabled(True)
            self.reset_selection_button.setEnabled(True)
            self.statusBar().showMessage("Noise reduction applied", 5000)


    def plot_data(self, data, title="Dynamic Spectrum"):
        QTimer.singleShot(0, lambda: self._plot_data_internal(data, title))

    def _plot_data_internal(self, data, title="Dynamic Spectrum"):
        if self.time is None or self.freqs is None:
            print("Time or frequency data not loaded. Skipping plot.")
            return

        if not hasattr(self.canvas, 'ax') or self.canvas.ax is None:
            print("Canvas not ready yet")
            return

        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        # Remove old colorbar axis safely
        try:
            if self.current_cax:
                self.current_cax.remove()
                self.current_cax = None
            if self.current_colorbar:
                self.current_colorbar.remove()
                self.current_colorbar = None
        except Exception as e:
            print("Error removing previous colorbar:", e)

        # Define colormap
        colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
        custom_cmap = mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)

        # x-axis always in seconds, UT formatting handled separately
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]

        # Prepare colorbar axis
        divider = make_axes_locatable(self.canvas.ax)
        cax = divider.append_axes("right", size="5%", pad=0.1)
        self.current_cax = cax

        # Show image
        im = self.canvas.ax.imshow(data, aspect='auto', extent=extent, cmap=custom_cmap)
        self.current_colorbar = self.canvas.figure.colorbar(im, cax=cax)
        self.current_colorbar.set_label("Intensity", fontsize=11)

        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.ax.set_title(f"{self.filename} - {title}", fontsize=14)

        self.format_axes()  # Format x-axis based on user selection (seconds/UT)
        self.canvas.draw()

        self.current_plot_type = title
        self.noise_button.setEnabled(True)
        self.reset_all_button.setEnabled(True)
        self.statusBar().showMessage(f"Loaded: {self.filename}", 5000)

    def activate_drift_tool(self):
        self.statusBar().showMessage("Click multiple points along the burst. Right-click or double-click to finish.",
                                     8000)
        self.drift_points = []
        self.drift_click_cid = self.canvas.mpl_connect("button_press_event", self.on_drift_point_click)

    def on_drift_point_click(self, event):
        if not event.inaxes:
            return

        # Right-click or double-click to finish
        if event.button == 3 or event.dblclick:
            self.finish_drift_estimation()
            return

        self.drift_points.append((event.xdata, event.ydata))
        self.canvas.ax.plot(event.xdata, event.ydata, 'w*')
        self.canvas.draw()

    def finish_drift_estimation(self):
        self.canvas.mpl_disconnect(self.drift_click_cid)

        if len(self.drift_points) < 2:
            self.statusBar().showMessage("Need at least two points to estimate drift.", 4000)
            return

        drift_rates = []
        for i in range(len(self.drift_points) - 1):
            x1, y1 = self.drift_points[i]
            x2, y2 = self.drift_points[i + 1]
            drift = (y2 - y1) / (x2 - x1)
            drift_rates.append(drift)
            # Draw line between points
            self.canvas.ax.plot([x1, x2], [y1, y2], linestyle='--', color='lime')

        avg_drift = np.mean(drift_rates)
        self.canvas.ax.legend(["Drift Segments"])
        self.canvas.draw()

        self.statusBar().showMessage(f"Average Drift Rate: {avg_drift:.4f} MHz/s, Start Frequency: {y1: .3f}, End Frequency: {y2: .3f}, Duration: {x2-x1: .3f} s",  0)

    def activate_lasso(self):
        if self.noise_reduced_data is None:
            QMessageBox.warning(self, "Error", "Please apply noise reduction before isolating a burst.")
            return

        # Disconnect old lasso
        if self.lasso:
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None

        self.canvas.ax.set_title("Draw around the burst")
        self.canvas.draw()

        self.lasso = LassoSelector(self.canvas.ax, onselect=self.on_lasso_select)

    def on_lasso_select(self, verts):

        if self.noise_reduced_data is None:
            print("Lasso used before data was prepared. Ignoring.")
            return

        path = Path(verts)

        ny, nx = self.noise_reduced_data.shape
        y = np.linspace(self.freqs[0], self.freqs[-1], ny)
        x = np.linspace(0, self.time[-1], nx)
        X, Y = np.meshgrid(x, y)

        coords = np.column_stack((X.flatten(), Y.flatten()))
        mask = path.contains_points(coords).reshape(ny, nx)

        self.lasso_mask = mask  # store for use later

        # Safely disconnect the lasso tool
        if self.lasso:
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None

        # Defer drawing to avoid crash during event handling
        QTimer.singleShot(0, lambda: self._plot_isolated_burst(mask))

    def _plot_isolated_burst(self, mask):
        burst_isolated = np.zeros_like(self.noise_reduced_data)
        burst_isolated[mask] = self.noise_reduced_data[mask]

        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        # Safely remove previous colorbar and colorbar axis
        if self.current_colorbar:
            try:
                self.current_colorbar.remove()
            except Exception:
                pass
            self.current_colorbar = None

        if self.current_cax:
            try:
                self.current_cax.remove()
            except Exception:
                pass
            self.current_cax = None

        # Use your consistent colormap
        colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
        custom_cmap = mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)

        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]

        # Add new colorbar
        divider = make_axes_locatable(self.canvas.ax)
        cax = divider.append_axes("right", size="5%", pad=0.1)
        self.current_cax = cax

        im = self.canvas.ax.imshow(burst_isolated, aspect='auto', extent=extent, cmap=custom_cmap)
        self.current_colorbar = self.canvas.figure.colorbar(im, cax=cax)
        self.current_colorbar.set_label("Intensity", fontsize=11)

        self.canvas.ax.set_title("Isolated Burst")
        self.canvas.ax.set_xlabel("Time [s]")
        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.draw()

        self.noise_reduced_data = burst_isolated

        self.statusBar().showMessage("Burst isolated using lasso", 4000)

    def plot_max_intensities(self):
        # Ensure any active lasso from the main plot is fully disconnected
        if getattr(self, "lasso", None):
            try:
                self.lasso.disconnect_events()
            except Exception:
                pass
            self.lasso = None

        if self.noise_reduced_data is None:
            print("No burst-isolated data available.")
            return

        # Diagnostics
        print("🖥️ Screens:", QGuiApplication.screens())
        print("🎯 Creating MaxIntensityPlotDialog...")

        try:
            data = self.noise_reduced_data
            ny, nx = data.shape
            time_channel_number = np.linspace(0, nx, nx)
            max_intensity_freqs = self.freqs[np.argmax(data, axis=0)]

            # Safely create the dialog
            dialog = MaxIntensityPlotDialog(time_channel_number, max_intensity_freqs, self.filename, self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

            # Connect to GC after close
            dialog.finished.connect(lambda: gc.collect())

            dialog.exec()
            gc.collect()

        except Exception as e:
            print(f"❌ Error showing MaxIntensityPlotDialog: {e}")



    def export_figure(self):
        from PySide6.QtWidgets import QFileDialog

        if not self.filename:
            print("No file loaded.")
            return

        base_name = self.filename.split(".")[0]
        suffix = self.current_plot_type.replace(" ", "")  # e.g. "NoiseReduced"
        full_title = f"{base_name}_{suffix}"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export PNG",
            f"{full_title}.png",
            "PNG files (*.png)"
        )
        if not file_path:
            return

        self.canvas.figure.savefig(file_path, dpi=300, bbox_inches="tight")
        print(f"Saved image: {file_path}")

    def reset_all(self):
        # Safely remove colorbar
        try:
            if self.current_colorbar and self.current_colorbar.ax:
                self.current_colorbar.remove()
        except Exception:
            pass
        self.current_colorbar = None

        try:
            if self.current_cax:
                self.current_cax.remove()
        except Exception:
            pass
        self.current_cax = None

        # Clear canvas
        self.canvas.ax.clear()
        self.canvas.draw()

        # Clear data
        self.raw_data = None
        self.freqs = None
        self.time = None
        self.filename = ""
        self.noise_reduced_data = None
        self.noise_reduced_original = None
        self.lasso_mask = None
        self.current_plot_type = "Raw"

        # Reset GUI
        self.lower_thresh_input.setValue(0.0)
        self.upper_thresh_input.setValue(0.0)
        self.statusBar().showMessage("All reset", 4000)

        # Disable buttons
        self.noise_button.setEnabled(False)
        self.drift_button.setEnabled(False)
        self.lasso_button.setEnabled(False)
        self.max_plot_button.setEnabled(False)
        self.reset_selection_button.setEnabled(False)
        self.reset_all_button.setEnabled(False)

        print("Application reset to initial state.")

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            "About e-Callisto FITS Analyzer",
            "e-CALLISTO FITS Analyzer version 1.6.0.\n\n"
            "Developed by Sahan S Liyanage\n\n"
            "Astronomical and Space Science Unit\n"
            "University of Colombo, Sri Lanka\n\n"
            "2025©Copyright, All Rights Reserved."
        )

    def reset_selection(self):
        if self.noise_reduced_original is not None:
            self.noise_reduced_data = self.noise_reduced_original.copy()
            if self.time is not None and self.freqs is not None:
                self.plot_data(self.noise_reduced_data, title="Noise Reduced")
            self.lasso_mask = None
            self.lasso = None
            self.statusBar().showMessage("Selection Reset", 4000)
            print("Lasso selection reset. Original noise-reduced data restored.")
        else:
            print("No noise-reduced backup found. Reset skipped.")

    def open_combine_freq_window(self):
        dialog = CombineFrequencyDialog(self)
        dialog.exec()

    def open_combine_time_window(self):
        dialog = CombineTimeDialog(self)
        dialog.exec()

    def set_axis_to_seconds(self):
        self.use_utc = False
        self.xaxis_sec_action.setChecked(True)
        self.xaxis_ut_action.setChecked(False)
        if self.raw_data is not None:
            self.plot_data(self.raw_data, title=self.current_plot_type)

    def set_axis_to_utc(self):
        self.use_utc = True
        self.xaxis_sec_action.setChecked(False)
        self.xaxis_ut_action.setChecked(True)
        if self.raw_data is not None:
            self.plot_data(self.raw_data, title=self.current_plot_type)

    def format_axes(self):
        if self.use_utc and self.ut_start_sec is not None:
            def format_func(x, pos):
                total_seconds = self.ut_start_sec + x
                hours = int(total_seconds // 3600) % 24
                minutes = int((total_seconds % 3600) // 60)
                seconds = int(total_seconds % 60)
                return f"{hours:02d}:{minutes:02d}"

            self.canvas.ax.xaxis.set_major_formatter(FuncFormatter(format_func))
            self.canvas.ax.set_xlabel("Time [UT]")
        else:
            self.canvas.ax.xaxis.set_major_formatter(ScalarFormatter())
            self.canvas.ax.set_xlabel("Time [s]")

        self.canvas.ax.figure.canvas.draw()

    def launch_downloader(self):
        self.downloader_dialog = CallistoDownloaderApp()
        self.downloader_dialog.setModal(True)
        self.downloader_dialog.exec()

    def open_goes_xrs_window(self):
        self.goes_window = GoesXrsWindow()
        self.goes_window.show()

    def open_soho_lasco_window(self):
        self.soho_window = CMEViewerWindow()
        self.soho_window.show()


class MaxIntensityPlotDialog(QDialog):
    def __init__(self, time_channels, max_freqs, filename, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Maximum Intensities for Each Time Channel")
        self.resize(1000, 700)
        self.filename = filename

        # Data
        self.time_channels = np.array(time_channels)
        self.freqs = np.array(max_freqs)
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self.lasso = None



        # Canvas
        self.canvas = MplCanvas(self, width=10, height=6)
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)
        self.canvas.ax.scatter(self.time_channels, self.freqs, marker="o", s=5, color='red')
        self.canvas.ax.set_xlabel("Time (s)")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.set_title("Maximum Intensity for Each Time Channel")
        self.canvas.draw()

        # Buttons
        self.select_button = QPushButton("Select Outliers")
        self.remove_button = QPushButton("Remove Outliers")

        self.fundamental_radio = QRadioButton("Fundamental")
        self.harmonic_radio = QRadioButton("Harmonic")
        self.fundamental_radio.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.fundamental_radio)
        self.mode_group.addButton(self.harmonic_radio)

        self.analyze_button = QPushButton("Analyze Burst")
        self.select_button.setToolTip("Use Lasso tool to select points to remove")
        self.remove_button.setToolTip("Remove previously selected outliers")
        self.select_button.setMinimumWidth(150)
        self.remove_button.setMinimumWidth(150)
        self.analyze_button.setMinimumWidth(150)
        self.select_button.clicked.connect(self.activate_lasso)
        self.remove_button.clicked.connect(self.remove_selected_outliers)

        self.analyze_button.clicked.connect(lambda: self.open_analyze_window(
            fundamental=self.fundamental_radio.isChecked(),
            harmonic=self.harmonic_radio.isChecked()
        ))

        # Layouts
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.remove_button)
        button_layout.addWidget(self.fundamental_radio)
        button_layout.addWidget(self.harmonic_radio)
        button_layout.addWidget(self.analyze_button)
        button_layout.addStretch()

        # Status bar
        self.status = QStatusBar()
        self.status.showMessage("Ready")

        # Menubar
        menubar = QMenuBar(self)
        file_menu = menubar.addMenu("File")
        self.save_action = QAction("Save As", self)
        self.export_action = QAction("Export As", self)
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.export_action)
        self.save_action.triggered.connect(self.save_as_csv)
        self.export_action.triggered.connect(self.export_figure)

        edit_menu = menubar.addMenu("Edit")
        reset_action = QAction("Reset All", self)
        edit_menu.addAction(reset_action)
        reset_action.triggered.connect(self.reset_all)

        analyze_menu = menubar.addMenu("Analyze")
        analyze_action = QAction("Open Analyzer", self)
        analyze_menu.addAction(analyze_action)
        analyze_action.triggered.connect(self.open_analyze_window)

        about_menu = menubar.addMenu("About")
        about_action = QAction("About", self)
        about_action.setMenuRole(QAction.NoRole)
        about_menu.addAction(about_action)
        about_action.triggered.connect(self.show_about_dialog)

        # Main Layout
        layout = QVBoxLayout()
        layout.setMenuBar(menubar)
        layout.addLayout(button_layout)
        layout.addWidget(self.canvas)
        layout.addWidget(self.status)
        self.setLayout(layout)

        # Styling
        self.setStyleSheet("""
            QPushButton { font-size: 13px; padding: 6px 12px; }
            QLabel { font-size: 13px; }
        """)

    def activate_lasso(self):
        self.canvas.ax.set_title("Draw around outliers to remove")
        self.canvas.draw()

        if self.lasso:
            self.lasso.disconnect_events()

        self.lasso = LassoSelector(self.canvas.ax, onselect=self.on_lasso_select)

    def on_lasso_select(self, verts):
        path = Path(verts)
        points = np.column_stack((self.time_channels, self.freqs))
        self.selected_mask = path.contains_points(points)
        if self.lasso:
            self.lasso.disconnect_events()
            self.lasso = None
        self.status.showMessage(f"{np.sum(self.selected_mask)} points selected", 3000)

    def remove_selected_outliers(self):
        if not np.any(self.selected_mask):
            self.status.showMessage("No points selected for removal", 3000)
            return

        self.time_channels = self.time_channels[~self.selected_mask]
        self.freqs = self.freqs[~self.selected_mask]
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)

        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        self.canvas.ax.scatter(self.time_channels, self.freqs, marker="o", s=5, color='red')
        self.canvas.ax.set_xlabel("Time Channel Number")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.set_title("Filtered Max Intensities")
        self.canvas.draw()

        self.status.showMessage("Selected outliers removed", 3000)

    def reset_all(self):
        # Clear canvas
        self.canvas.ax.clear()
        self.canvas.draw()

        # Clear internal variables
        self.raw_data = None
        self.freqs = None
        self.time = None
        self.filename = ""
        self.noise_reduced_data = None
        self.lasso_mask = None
        self.current_plot_type = "Raw"

        # Reset text boxes
        self.lower_thresh_input.setValue(0.0)
        self.upper_thresh_input.setValue(0.0)

        self.statusBar().showMessage("All reset", 4000)

        print("Application reset to initial state.")

    def save_as_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV File", "", "CSV files (*.csv)")
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Time Channel", "Frequency (MHz)"])
                for t, fval in zip(self.time_channels * 0.25, self.freqs):
                    writer.writerow([t, fval])
            self.status.showMessage(f"Saved to {file_path}", 3000)
        except Exception as e:
            self.status.showMessage(f"Error: {e}", 3000)

    def export_figure(self):
        if not self.filename:
            self.status.showMessage("No base filename available", 3000)
            return

        base_name = self.filename.split(".")[0]
        full_title = f"{base_name}_MaxIntensities"
        file_path, _ = QFileDialog.getSaveFileName(self, "Export PNG", f"{full_title}.png", "PNG files (*.png)")
        if not file_path:
            return

        self.canvas.fig.savefig(file_path, dpi=300, bbox_inches="tight")
        self.status.showMessage(f"Image saved: {file_path}", 3000)

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            "About e-Callisto FITS Analyzer",
            "e-CALLISTO FITS Analyzer version 1.6.0.\n\n"
            "Developed by Sahan S Liyanage\n\n"
            "Astronomical and Space Science Unit\n"
            "University of Colombo, Sri Lanka\n\n"
            "2025©Copyright, All Rights Reserved."
        )

    def open_analyze_window(self, fundamental=True, harmonic=False):
        dialog = AnalyzeDialog(self.time_channels, self.freqs, self.filename, fundamental=fundamental, harmonic=harmonic, parent=self)
        dialog.exec()

    def closeEvent(self, event):
        try:
            if hasattr(self.canvas, 'ax'):
                self.canvas.ax.clear()
            self.canvas.figure.clf()
            self.canvas.deleteLater()

            if self.lasso:
                self.lasso.disconnect_events()
                self.lasso = None
        except Exception as e:
            print(f"Cleanup error: {e}")
        event.accept()


from PySide6.QtWidgets import (
    QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QComboBox, QScrollArea, QWidget, QSizePolicy
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score, mean_squared_error

class MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=10, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

class AnalyzeDialog(QDialog):
    def __init__(self, time_channels, freqs, filename, fundamental=True, harmonic=False, parent=None):
        super().__init__(parent)
        self.fundamental = fundamental
        self.harmonic = harmonic

        self.setWindowTitle("Analyzer")
        self.resize(1100, 700)

        self.time = np.array(time_channels) * 0.25
        self.freq = np.array(freqs)
        self.filename = filename.split(".")[0]
        self.current_plot_title = f"{self.filename}_Best_Fit"

        # Canvas
        self.canvas = MplCanvas(self, width=8, height=5)
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        # Buttons
        self.max_button = QPushButton("Maximum Intensities")
        self.fit_button = QPushButton("Best Fit")
        self.save_plot_button = QPushButton("Save Graph")
        self.save_data_button = QPushButton("Save Data")
        self.existing_excel_checkbox = QCheckBox("Existing Excel File")

        self.extra_plot_label = QLabel("Extra Plots:")
        self.extra_plot_combo = QComboBox()
        self.extra_plot_combo.addItems([
            "Shock Speed vs Shock Height",
            "Shock Speed vs Frequency",
            "Shock Height vs Frequency"
        ])
        self.extra_plot_button = QPushButton("Plot")

        self.max_button.clicked.connect(self.plot_max)
        self.fit_button.clicked.connect(self.plot_fit)
        self.save_plot_button.clicked.connect(self.save_graph)
        self.save_data_button.clicked.connect(self.save_data)
        self.extra_plot_button.clicked.connect(self.plot_extra)

        # Plot control layout
        plot_button_layout = QHBoxLayout()
        plot_button_layout.addWidget(self.max_button)
        plot_button_layout.addWidget(self.fit_button)

        left_layout = QVBoxLayout()
        left_layout.addLayout(plot_button_layout)
        left_layout.addWidget(self.canvas)

        # === Info Panel ===
        self.equation_label = QLabel("Best Fit Equation:")
        self.equation_display = QLabel("")
        self.equation_display.setTextFormat(Qt.RichText)
        self.equation_display.setStyleSheet("font-size: 16px; padding: 4px;")

        self.stats_header = QLabel("<b>Fit Metrics:</b>")
        self.r2_display = QLabel("R² = ")
        self.rmse_display = QLabel("RMSE = ")

        self.shock_header = QLabel("<b>Shock Parameters:</b>")
        self.avg_freq_display = QLabel("")
        self.drift_display = QLabel("")
        self.start_freq_display = QLabel("")
        self.initial_shock_speed_display = QLabel("")
        self.initial_shock_height_display = QLabel("")
        self.avg_shock_speed_display = QLabel("")
        self.avg_shock_height_display = QLabel("")

        self.labels = [
            self.equation_label, self.equation_display,
            self.stats_header, self.r2_display, self.rmse_display,
            self.shock_header,
            self.avg_freq_display, self.drift_display, self.start_freq_display,
            self.initial_shock_speed_display, self.initial_shock_height_display,
            self.avg_shock_speed_display, self.avg_shock_height_display,
            self.save_plot_button, self.save_data_button,self.existing_excel_checkbox,
            self.extra_plot_label, self.extra_plot_combo, self.extra_plot_button
        ]

        right_inner = QVBoxLayout()
        for widget in self.labels:
            right_inner.addWidget(widget)
        right_inner.addStretch()

        right_widget = QWidget()
        right_widget.setLayout(right_inner)
        right_scroll = QScrollArea()
        right_scroll.setWidget(right_widget)
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(300)

        self.status = QStatusBar()
        self.status.showMessage("Ready")

        # Main layout
        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout, stretch=3)
        main_layout.addWidget(right_scroll, stretch=1)
        main_with_status = QVBoxLayout()
        main_with_status.addLayout(main_layout)
        main_with_status.addWidget(self.status)
        self.setLayout(main_with_status)

        # Styling
        self.setStyleSheet("""
            QLabel {
                font-size: 13px;
                padding: 2px;
            }
            QLabel#value {
                font-weight: bold;
            }
            QPushButton {
                padding: 6px;
            }
        """)

    def plot_max(self):
        self.canvas.ax.clear()
        self.canvas.ax.scatter(self.time, self.freq, s=10, color='blue')
        self.canvas.ax.set_title(f"{self.filename}_Maximum_Intensity")
        self.canvas.ax.set_xlabel("Time (s)")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.grid(True)
        self.canvas.draw()
        self.equation_display.setText("")
        self.status.showMessage("Max intensities plotted successfully!", 3000)

    def plot_fit(self):
        def model_func(t, a, b): return a * t ** (b)
        def drift_rate(t, a_, b_): return a_ * b_ * t ** (b_ - 1)

        params, cov = curve_fit(model_func, self.time, self.freq, maxfev=10000)
        a, b = params
        std_errs = np.sqrt(np.diag(cov))

        time_fit = np.linspace(self.time.min(), self.time.max(), 400)
        freq_fit = model_func(time_fit, a, b)

        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)

        self.canvas.ax.scatter(self.time, self.freq, s=10, color='blue', label="Original Data")
        self.canvas.ax.plot(time_fit, freq_fit, color='red', label=fr"Best Fit: $f = {a:.2f} \cdot t^{{{b:.2f}}}$")
        self.canvas.ax.set_title(f"{self.filename}_Best_Fit")
        self.canvas.ax.set_xlabel("Time (s)")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.legend()
        self.canvas.ax.grid(True)
        self.canvas.draw()
        self.current_plot_title = f"{self.filename}_Best_Fit"

        predicted = model_func(self.time, a, b)
        r2 = r2_score(self.freq, predicted)
        rmse = np.sqrt(mean_squared_error(self.freq, predicted))

        self.equation_display.setText(f"<b>f(t) = {a:.2f} · t<sup>{b:.2f}</sup></b>")
        self.r2_display.setText(f"R² = {r2:.4f}")
        self.rmse_display.setText(f"RMSE = {rmse:.4f}")

        drift_vals = drift_rate(self.time, a, b)
        residuals = self.freq - predicted
        freq_err = np.std(residuals)
        drift_errs = np.abs(drift_vals) * np.sqrt((std_errs[0]/a)**2 + (std_errs[1]/b)**2)

        shock_speed = (13853221.38 * np.abs(drift_vals)) / (self.freq * (np.log(self.freq ** 2 / 3.385)) ** 2)
        R_p = 4.32 * np.log(10) / np.log(self.freq ** 2 / 3.385)

        percentile = 90
        start_freq = np.percentile(self.freq, percentile)

        if self.harmonic:
            start_freq = start_freq / 2

        idx = np.abs(self.freq - start_freq).argmin()
        f0 = self.freq[idx]
        start_shock_speed = shock_speed[idx]
        start_height = R_p[idx]
        drift0 = drift_vals[idx]
        drift_err0 = drift_errs[idx]

        shock_speed_err = (13853221.38 * drift_err0) / (f0 * (np.log(f0 ** 2 / 3.385)) ** 2)
        dRp_df = (8.64 / f0) / np.log(10) / np.log(f0 ** 2 / 3.385)
        Rp_err = np.abs(dRp_df * freq_err)

        avg_freq = np.mean(self.freq)
        avg_freq_err = np.std(self.freq)/np.sqrt(len(self.freq))
        avg_drift = np.mean(drift_vals)
        avg_drift_err = np.std(drift_vals) / np.sqrt(len(drift_vals))
        avg_speed = np.mean(shock_speed)
        avg_speed_err = np.std(shock_speed) / np.sqrt(len(shock_speed))
        avg_height = np.mean(R_p)
        avg_height_err = np.std(R_p) / np.sqrt(len(R_p))

        self.shock_speed = shock_speed
        self.R_p = R_p
        self.freq_err = freq_err
        self.start_freq = start_freq
        self.start_height = start_height

        self.status.showMessage("Best fit plotted successfully!", 3000)

        # Display values
        self.avg_freq_display.setText(f"Average Frequency: <b>{avg_freq:.2f} ± {avg_freq_err:.2f}</b> MHz")
        self.drift_display.setText(f"Average Drift Rate: <b>{avg_drift:.4f} ± {avg_drift_err:.4f}</b> MHz/s")
        self.start_freq_display.setText(f"Starting Frequency: <b>{start_freq:.2f} ± {freq_err:.2f}</b> MHz")
        self.initial_shock_speed_display.setText(f"Initial Shock Speed: <b>{start_shock_speed:.2f} ± {shock_speed_err:.2f}</b> km/s")
        self.initial_shock_height_display.setText(f"Initial Shock Height: <b>{start_height:.3f} ± {Rp_err:.3f}</b> Rₛ")
        self.avg_shock_speed_display.setText(f"Average Shock Speed: <b>{avg_speed:.2f} ± {avg_speed_err:.2f}</b> km/s")
        self.avg_shock_height_display.setText(f"Average Shock Height: <b>{avg_height:.3f} ± {avg_height_err:.3f}</b> Rₛ")

    def save_graph(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Plot", f"{self.current_plot_title}.png", "PNG Files (*.png)")
        if path:
            self.canvas.figure.savefig(path, dpi=300, bbox_inches='tight')
        self.status.showMessage("Graph saved successfully!.", 3000)

    def save_data(self):

        # All known e-Callisto station names
        station_list = [
            'ALASKA-ANCHORAGE', 'ALASKA-COHOE', 'ALASKA-HAARP', 'ALGERIA-CRAAG', 'ALMATY',
            'Arecibo-observatory', 'AUSTRIA-Krumbach', 'AUSTRIA-MICHELBACH', 'AUSTRIA-OE3FLB',
            'AUSTRIA-UNIGRAZ', 'Australia-ASSA', 'BRAZIL', 'BIR', 'Croatia-Visnjan', 'DENMARK',
            'EGYPT-Alexandria', 'EGYPT-SpaceAgency', 'ETHIOPIA', 'FINLAND-Siuntio', 'FINLAND-Kempele',
            'GERMANY-ESSEN', 'GERMANY-DLR', 'GLASGOW', 'GREENLAND', 'HUMAIN', 'HURBANOVO',
            'INDIA-GAURI', 'INDIA-Nashik', 'INDIA-OOTY', 'INDIA-UDAIPUR', 'INDONESIA',
            'ITALY-Strassolt', 'JAPAN-IBARAKI', 'KASI', 'KRIM', 'MEXART',
            'MEXICO-ENSENADA-UNAM', 'MEXICO-FCFM-UANL', 'MEXICO-FCFM-UNACH', 'MEXICO-LANCE-A',
            'MEXICO-LANCE-B', 'MEXICO-UANL-INFIERNILLO', 'MONGOLIA-UB', 'MRO', 'MRT1', 'MRT3',
            'Malaysia_Banting', 'NASA-GSFC', 'NORWAY-EGERSUND', 'NORWAY-NY-AALESUND', 'NORWAY-RANDABERG',
            'PARAGUAY', 'POLAND-BALDY', 'POLAND-Grotniki', 'ROMANIA', 'ROSWELL-NM', 'RWANDA',
            'SOUTHAFRICA-SANSA', 'SPAIN-ALCALA', 'SPAIN-PERALEJOS', 'SPAIN-SIGUENZA', 'SRI-Lanka',
            'SSRT', 'SWISS-CalU', 'SWISS-FM', 'SWISS-HB9SCT', 'SWISS-HEITERSWIL', 'SWISS-IRSOL',
            'SWISS-Landschlacht', 'SWISS-MUHEN', 'TAIWAN-NCU', 'THAILAND-Pathumthani', 'TRIEST',
            'TURKEY', 'UNAM', 'URUGUAY', 'USA-ARIZONA-ERAU', 'USA-BOSTON', 'UZBEKISTAN'
        ]

        # ✅ Extract Station
        station = "UNKNOWN"
        filename_lower = self.filename.lower()
        for s in station_list:
            if filename_lower.startswith(s.lower()):
                station = s
                break

        # ✅ Extract Date
        date_match = re.search(r'_(\d{4})(\d{2})(\d{2})_', self.filename)
        if date_match:
            date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
        else:
            date = "UNKNOWN"

        # ✅ Excel File Handling
        if self.existing_excel_checkbox.isChecked():
            path, _ = QFileDialog.getOpenFileName(self, "Select Existing Excel File", "", "Excel Files (*.xlsx)")
            if not path:
                return
            try:
                wb = load_workbook(path)
                ws = wb.active
            except Exception as e:
                QMessageBox.critical(self, "Load Error", f"Could not open Excel file:\n{str(e)}")
                return
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Save as Excel", f"{self.filename}_data.xlsx",
                                                  "Excel Files (*.xlsx)")
            if not path:
                return
            try:
                wb = Workbook()
                ws = wb.active
                headers = [
                    "Date", "Station", "Best_fit", "R_sq", "RMSE",
                    "avg_freq", "avg_freq_err", "Avg_drift", "avg_drift_err",
                    "start_freq", "start_freq_err", "initial_shock_speed", "initial_shock_speed_err",
                    "initial_shock_height", "initial_shock_height_err", "avg_shock_speed", "avg_shock_speed_err",
                    "avg_shock_height", "avg_shock_height_err", "avg_drift_abs"
                ]
                ws.append(headers)
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Could not create Excel file:\n{str(e)}")
                return

        # ✅ Extract and clean text
        def extract_val_err(label):
            # Remove HTML tags
            clean_text = re.sub(r'<[^>]+>', '', label.text())
            # Remove units and stray characters
            clean_text = re.sub(r'(MHz|km/s|Rₛ|s|/)', '', clean_text)
            # Clean spaces
            clean_text = clean_text.strip()
            # Extract value ± error
            value_text = clean_text.split(":")[-1].strip()
            if "±" in value_text:
                value, err = value_text.split("±")
                return value.strip(), err.strip()
            else:
                return value_text.strip(), ""

        # ✅ Read values
        try:
            best_fit = re.sub(r'<[^>]+>', '', self.equation_display.text()).replace("<sup>", "^").replace("</sup>", "")
            r2 = self.r2_display.text().split("=")[-1].strip()
            rmse = self.rmse_display.text().split("=")[-1].strip()

            avg_freq, avg_freq_err = extract_val_err(self.avg_freq_display)
            avg_drift, avg_drift_err = extract_val_err(self.drift_display)

            try:
                avg_drift_abs = abs(float(avg_drift))
            except ValueError:
                avg_drift_abs = ""

            start_freq, start_freq_err = extract_val_err(self.start_freq_display)
            init_speed, init_speed_err = extract_val_err(self.initial_shock_speed_display)
            init_height, init_height_err = extract_val_err(self.initial_shock_height_display)
            avg_speed, avg_speed_err = extract_val_err(self.avg_shock_speed_display)
            avg_height, avg_height_err = extract_val_err(self.avg_shock_height_display)

            row = [
                date, station, best_fit, r2, rmse,
                avg_freq, avg_freq_err, avg_drift, avg_drift_err,
                start_freq, start_freq_err, init_speed, init_speed_err,
                init_height, init_height_err, avg_speed, avg_speed_err,
                avg_height, avg_height_err,avg_drift_abs
            ]

            ws.append(row)
            wb.save(path)
            self.status.showMessage("✅ Data saved to Excel successfully!", 3000)

        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Could not write to Excel file:\n{str(e)}")
            self.status.showMessage("❌ Failed to save data to Excel.", 3000)

    def plot_extra(self):
        choice = self.extra_plot_combo.currentText()
        self.canvas.ax.clear()
        if choice == "Shock Speed vs Shock Height":
            self.canvas.ax.scatter(self.R_p, self.shock_speed, color='green', s=10)
            self.canvas.ax.set_xlabel("Shock Height (Rₛ)")
            self.canvas.ax.set_ylabel("Shock Speed (km/s)")
            self.canvas.ax.set_title(f"{self.filename}_Shock_Speed_vs_Shock_Height")
            self.current_plot_title = f"{self.filename}_Shock_Speed_vs_Shock_Height"
            self.status.showMessage("Shock Speed vs Shock Height plotted successfully!", 3000)

        elif choice == "Shock Speed vs Frequency":
            self.canvas.ax.scatter(self.freq, self.shock_speed, color='purple', s=10)
            self.canvas.ax.set_xlabel("Frequency (MHz)")
            self.canvas.ax.set_ylabel("Shock Speed (km/s)")
            self.canvas.ax.set_title(f"{self.filename}_Shock_Speed_vs_Frequency")
            self.current_plot_title = f"{self.filename}_Shock_Speed_vs_Frequency"
            self.status.showMessage("Shock Speed vs Frequency plotted successfully!", 3000)

        elif choice == "Shock Height vs Frequency":
            self.canvas.ax.scatter(self.R_p, self.freq, color='red', s=10)
            self.canvas.ax.set_xlabel("Shock Height (Rₛ)")
            self.canvas.ax.set_ylabel("Frequency (MHz)")
            self.canvas.ax.set_title(f"{self.filename}_Rs_vs_Freq")
            self.current_plot_title = f"{self.filename}_Rs_vs_Freq"
            self.status.showMessage("Shock Height vs Frequency plotted successfully!", 3000)
        self.canvas.ax.grid(True)
        self.canvas.draw()

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

    def load_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Two FITS Files", "", "FITS files (*.fit.gz)")
        if len(files) != 2:
            QMessageBox.warning(self, "Error", "Please select exactly TWO files.")
            return

        station1 = files[0].split("/")[-1].split("_")[0]
        station2 = files[1].split("/")[-1].split("_")[0]

        if station1 != station2:
            QMessageBox.critical(self, "Error", "You must select consecutive frequency data files from the same station!")
            return

        self.file_paths = files
        self.combine_button.setEnabled(True)

    def combine_files(self):
        from astropy.io import fits
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)
        QApplication.processEvents()

        try:
            hdul1 = fits.open(self.file_paths[0])
            data1 = hdul1[0].data
            freqs1 = hdul1[1].data['frequency'][0]
            time1 = hdul1[1].data['time'][0]
            hdul1.close()
            self.progress_bar.setValue(30)
            QApplication.processEvents()

            hdul2 = fits.open(self.file_paths[1])
            data2 = hdul2[0].data
            freqs2 = hdul2[1].data['frequency'][0]
            time2 = hdul2[1].data['time'][0]
            hdul2.close()
            self.progress_bar.setValue(60)
            QApplication.processEvents()

            if not np.allclose(time1, time2, rtol=1e-2):
                QMessageBox.critical(self, "Error", "Time arrays must match to combine frequencies.")
                self.progress_bar.setVisible(False)
                return

            self.combined_data = np.vstack([data1, data2])
            self.combined_freqs = np.concatenate([freqs1, freqs2])
            self.combined_time = time1
            self.progress_bar.setValue(80)
            QApplication.processEvents()

            # Plot image
            fig, ax = plt.subplots(figsize=(6, 4))
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
        self.main_window.raw_data = self.combined_data
        self.main_window.freqs = self.combined_freqs
        self.main_window.time = self.combined_time
        self.main_window.filename = self.combined_title  # ✅ update filename as the title
        self.main_window.plot_data(self.combined_data, title="Raw Data (Combined Frequency)")
        self.close()

class CombineTimeDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Combine Time Ranges")
        self.setMinimumWidth(600)

        self.file_paths = []
        self.combined_data = None

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
        files, _ = QFileDialog.getOpenFileNames(self, "Select FITS Files to Combine", "", "FITS files (*.fit.gz)")
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

            for idx, file_path in enumerate(self.file_paths):
                hdul = fits.open(file_path)
                data = hdul[0].data
                freqs = hdul[1].data["frequency"][0]
                time = hdul[1].data["time"][0]

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

                hdul.close()

            self.combined_data = combined_data
            self.combined_time = combined_time
            self.main_window.freqs = reference_freqs
            self.main_window.time = combined_time

            self.progress_bar.setValue(80)

            # Plot preview
            fig, ax = plt.subplots(figsize=(6, 4))
            extent = [combined_time[0], combined_time[-1], reference_freqs[-1], reference_freqs[0]]
            cmap = LinearSegmentedColormap.from_list('custom_cmap', [(0, 'darkblue'), (1, 'orange')])
            im = ax.imshow(combined_data, aspect='auto', extent=extent, cmap=cmap)
            ax.set_xlabel("Time [s]")
            ax.set_ylabel("Frequency [MHz]")
            ax.set_title("Combined Time Plot")
            fig.tight_layout()

            preview_path = "preview_combined_time.png"
            fig.savefig(preview_path, dpi=100)
            plt.close(fig)

            self.image_label.setPixmap(QPixmap(preview_path).scaled(550, 350, Qt.KeepAspectRatio))
            self.progress_bar.setValue(100)
            self.import_button.setEnabled(True)

            # Set filename
            base1 = os.path.basename(self.file_paths[0]).split(".")[0]
            self.main_window.filename = base1 + "_combined_time"

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to combine:\n{str(e)}")

    def import_to_main(self):
        if self.combined_data is not None and self.combined_time is not None:
            self.main_window.raw_data = self.combined_data
            self.main_window.freqs = self.main_window.freqs  # already set earlier
            self.main_window.time = self.combined_time
            self.main_window.filename = self.main_window.filename  # already set earlier

            # Calculate UT start from FITS header of first file
            try:
                hdul = fits.open(self.file_paths[0])
                hdr = hdul[0].header
                hh, mm, ss = hdr['TIME-OBS'].split(":")
                hh = int(hh)
                mm = int(mm)
                ss = float(ss)
                self.main_window.ut_start_sec = hh * 3600 + mm * 60 + ss
                hdul.close()
            except Exception as e:
                print("⚠️ Could not extract UT time from first file:", e)
                self.main_window.ut_start_sec = None

            self.main_window.plot_data(self.combined_data, title="Combined Time")
            self.close()
