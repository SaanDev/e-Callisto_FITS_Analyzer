from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QLabel, QLineEdit, QDialog, QMenuBar, QMenu, QMessageBox
)
from PySide6.QtGui import QAction

from PySide6.QtGui import QFont
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.widgets import LassoSelector
from matplotlib.path import Path
import matplotlib.pyplot as plt
from astropy.io import fits
import numpy as np


class MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=10, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Solar Radio Burst Analyzer")
        self.resize(1000, 700)
        self.setMinimumSize(800, 600)

        # Canvas
        self.canvas = MplCanvas(self, width=10, height=6)

        # Threshold input fields
        self.lower_thresh_input = QLineEdit("")
        self.lower_thresh_input.setMaximumWidth(80)
        self.upper_thresh_input = QLineEdit("")
        self.upper_thresh_input.setMaximumWidth(80)

        # Labels
        lower_label = QLabel("Lower Threshold:")
        upper_label = QLabel("Upper Threshold:")

        # Buttons
        self.load_button = QPushButton("Load FITS File")
        self.load_button.setMaximumWidth(200)
        self.noise_button = QPushButton("Apply Noise Reduction")
        self.noise_button.setMaximumWidth(200)
        self.lasso_button = QPushButton("Isolate Burst")
        self.lasso_button.setMaximumWidth(200)
        self.max_plot_button = QPushButton("Plot Maximum Intensities")
        self.max_plot_button.setMaximumWidth(250)

        # Layouts
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(lower_label)
        threshold_layout.addWidget(self.lower_thresh_input)
        threshold_layout.addSpacing(20)
        threshold_layout.addWidget(upper_label)
        threshold_layout.addWidget(self.upper_thresh_input)
        threshold_layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.noise_button)
        button_layout.addWidget(self.lasso_button)
        button_layout.addWidget(self.max_plot_button)
        button_layout.addStretch()

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addLayout(button_layout)
        layout.addLayout(threshold_layout)
        layout.addWidget(self.canvas)

        container = QWidget()
        container.setLayout(layout)

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

        # --- About (inside File menu) ---
        #file_menu.addSeparator()
        #about_action = QAction("About", self)
        #bout_action.setMenuRole(QAction.NoRole)  # 🛑 Prevent macOS hijacking
        #file_menu.addAction(about_action)


        # Edit Menu
        edit_menu = menubar.addMenu("Edit")
        reset_action = QAction("Reset All", self)
        edit_menu.addAction(reset_action)
        reset_action.triggered.connect(self.reset_all)

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
        self.lasso_button.clicked.connect(self.activate_lasso)
        self.max_plot_button.clicked.connect(self.plot_max_intensities)
        self.open_action.triggered.connect(self.load_file)

        # Data placeholders
        self.raw_data = None
        self.freqs = None
        self.time = None
        self.filename = ""
        self.current_plot_type = "Raw"  # or "NoiseReduced" or "Isolated"

        self.lasso = None
        self.lasso_mask = None
        self.noise_reduced_data = None

    def load_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open FITS File", "", "FITS files (*.fit.gz)")
        if file_path:
            self.filename = file_path.split("/")[-1]
            hdul = fits.open(file_path)
            self.raw_data = hdul[0].data
            self.freqs = hdul[1].data['frequency'][0]
            self.time = hdul[1].data['time'][0]
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
            print("Before clip:", data.min(), data.max())
            data = np.clip(data, clip_low, clip_high)
            data = data * 2500.0 / 255.0 / 25.4
            self.noise_reduced_data = data
            self.plot_data(data, title="Noise Reduced")

    def plot_data(self, data, title="Dynamic Spectrum"):
        self.canvas.ax.clear()
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]
        self.canvas.ax.imshow(data, aspect='auto', extent=extent, cmap='viridis')
        self.canvas.ax.set_xlabel("Time [s]")
        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.ax.set_title(f"{self.filename} - {title}", fontsize=14)
        self.canvas.draw()
        self.current_plot_type = title

    def activate_lasso(self):
        if self.noise_reduced_data is None:
            print("Apply noise reduction first.")
            return

        self.canvas.ax.clear()
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]
        self.canvas.ax.imshow(self.noise_reduced_data, aspect='auto', extent=extent, cmap='viridis')
        self.canvas.ax.set_title("Draw around the burst")
        self.canvas.draw()

        self.lasso = LassoSelector(self.canvas.ax, onselect=self.on_lasso_select)

    def on_lasso_select(self, verts):
        path = Path(verts)

        ny, nx = self.noise_reduced_data.shape
        y = np.linspace(self.freqs[0], self.freqs[-1], ny)
        x = np.linspace(0, self.time[-1], nx)
        X, Y = np.meshgrid(x, y)

        coords = np.column_stack((X.flatten(), Y.flatten()))
        mask = path.contains_points(coords).reshape(ny, nx)

        # Apply mask
        burst_isolated = np.zeros_like(self.noise_reduced_data)
        burst_isolated[mask] = self.noise_reduced_data[mask]

        self.canvas.ax.clear()
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]
        self.canvas.ax.imshow(burst_isolated, aspect='auto', extent=extent, cmap='viridis')
        self.canvas.ax.set_title("Isolated Burst")
        self.canvas.ax.set_xlabel("Time [s]")
        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.draw()

        self.lasso_mask = mask
        self.noise_reduced_data = burst_isolated
        self.lasso.disconnect_events()
        self.lasso = None

    def plot_max_intensities(self):
        if self.noise_reduced_data is None:
            print("No burst-isolated data available.")
            return

        data = self.noise_reduced_data
        ny, nx = data.shape
        time_channel_number = np.linspace(0, nx, nx)
        max_intensity_freqs = self.freqs[np.argmax(data, axis=0)]

        dialog = MaxIntensityPlotDialog(time_channel_number, max_intensity_freqs, self.filename, self)
        dialog.exec()

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

        self.canvas.fig.savefig(file_path, dpi=300, bbox_inches="tight")
        print(f"Saved image: {file_path}")

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
        self.lower_thresh_input.setText("")
        self.upper_thresh_input.setText("")

        print("Application reset to initial state.")

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            "About e-Callisto FITS Analyzer",
            "This application is for analyzing solar radio data from e-Callisto.\n\n"
            "Developed by Sahan Liyanage — 2025\n\n"
            "All Rights Reserved"
        )


class MaxIntensityPlotDialog(QDialog):
    def __init__(self, time_channels, max_freqs,filename, parent=None):
        super().__init__(parent)

        self.filename = filename

        # ----- Menu Bar -----
        menubar = QMenuBar(self)

        # File Menu
        file_menu = menubar.addMenu("File")
        open_action = QAction("Open", self)
        self.save_action = QAction("Save As", self)
        self.export_action = QAction("Export As", self)

        file_menu.addAction(open_action)
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.export_action)
        self.save_action.triggered.connect(self.save_as_csv)
        self.export_action.triggered.connect(self.export_figure)


        # Edit Menu
        edit_menu = menubar.addMenu("Edit")
        reset_action = QAction("Reset All", self)
        edit_menu.addAction(reset_action)
        reset_action.triggered.connect(self.reset_all)

        # About Menu
        about_menu = menubar.addMenu("About")
        about_action = QAction("About", self)
        about_action.setMenuRole(QAction.NoRole)
        about_menu.addAction(about_action)
        about_action.triggered.connect(self.show_about_dialog)

        # Insert into layout (before other widgets)
        layout = QVBoxLayout()
        layout.setMenuBar(menubar)

        self.setWindowTitle("Maximum Intensities")
        self.resize(800, 600)

        # Store the data
        self.time_channels = np.array(time_channels)
        self.freqs = np.array(max_freqs)

        # Create canvas
        self.canvas = MplCanvas(self, width=8, height=6)
        self.ax = self.canvas.ax

        # Initial plot
        self.time_new = self.time_channels*0.25
        self.ax.scatter(self.time_channels, self.freqs, marker="o", s=5, color='red')
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Frequency (MHz)")
        self.ax.set_title("Maximum Intensity for Each Time Channel")
        self.canvas.draw()

        # Create buttons
        self.select_button = QPushButton("Select Outliers")
        self.remove_button = QPushButton("Remove Outliers")
        self.select_button.setMaximumWidth(150)
        self.remove_button.setMaximumWidth(150)

        self.select_button.clicked.connect(self.activate_lasso)
        self.remove_button.clicked.connect(self.remove_selected_outliers)

        # Layout for buttons
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.remove_button)
        button_layout.addStretch()

        # Main layout
        layout = QVBoxLayout()
        layout.addLayout(button_layout)
        layout.addWidget(self.canvas)
        self.setLayout(layout)

        # Internal state
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self.lasso = None

    def activate_lasso(self):
        self.ax.set_title("Draw around outliers to remove")
        self.canvas.draw()

        if self.lasso:
            self.lasso.disconnect_events()

        self.lasso = LassoSelector(self.ax, onselect=self.on_lasso_select)

    def on_lasso_select(self, verts):
        path = Path(verts)
        points = np.column_stack((self.time_channels, self.freqs))
        self.selected_mask = path.contains_points(points)
        self.lasso.disconnect_events()
        self.lasso = None

    def remove_selected_outliers(self):
        if not np.any(self.selected_mask):
            return

        # Filter data to keep only unselected points
        self.time_channels = self.time_channels[~self.selected_mask]
        self.freqs = self.freqs[~self.selected_mask]

        # Reset mask
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)

        # Replot
        self.ax.clear()
        self.ax.scatter(self.time_channels, self.freqs, marker="o", s=5, color='red')
        self.ax.set_xlabel("Time Channel Number")
        self.ax.set_ylabel("Frequency (MHz)")
        self.ax.set_title("Filtered Max Intensities")
        self.canvas.draw()

    def save_as_csv(self):
        from PySide6.QtWidgets import QFileDialog
        import csv

        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV File", "", "CSV files (*.csv)")
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Time Channel", "Frequency (MHz)"])
                for t, fval in zip(self.time_channels*0.25, self.freqs):
                    writer.writerow([t, fval])
            print(f"Data saved to {file_path}")
        except Exception as e:
            print(f"Error saving file: {e}")

    def export_figure(self):
        from PySide6.QtWidgets import QFileDialog

        if not self.filename:
            print("No base filename available.")
            return

        base_name = self.filename.split(".")[0]
        full_title = f"{base_name}_MaxIntensities"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export PNG",
            f"{full_title}.png",
            "PNG files (*.png)"
        )
        if not file_path:
            return

        self.canvas.fig.savefig(file_path, dpi=300, bbox_inches="tight")
        print(f"Saved image: {file_path}")

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
        self.lower_thresh_input.setText("")
        self.upper_thresh_input.setText("")

        print("Application reset to initial state.")

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            "About e-Callisto FITS Analyzer",
            "This application is for analyzing solar radio data from e-Callisto.\n\n"
            "Developed by Sahan Liyanage — 2025\n\n"
            "All Rights Reserved"
        )







