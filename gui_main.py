from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QLabel, QLineEdit, QDialog, QMenuBar, QMenu, QMessageBox, QComboBox
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.widgets import LassoSelector
from matplotlib.path import Path
import matplotlib.pyplot as plt
from astropy.io import fits
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.colors as mcolors
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score
from scipy.optimize import curve_fit


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

        cmap = LinearSegmentedColormap.from_list('custom_cmap', [(0, 'darkblue'), (1, 'orange')])
        colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
        custom_cmap = mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)

        self.canvas.ax.clear()
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]
        self.canvas.ax.imshow(data, aspect='auto', extent=extent, cmap=custom_cmap)
        self.canvas.ax.set_xlabel("Time [s]")
        self.canvas.ax.set_ylabel("Frequency [MHz]")
        self.canvas.ax.set_title(f"{self.filename} - {title}", fontsize=14)
        self.canvas.draw()
        self.current_plot_type = title

    def activate_lasso(self):
        if self.noise_reduced_data is None:
            print("Apply noise reduction first.")
            return

        cmap = LinearSegmentedColormap.from_list('custom_cmap', [(0, 'darkblue'), (1, 'orange')])
        colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
        custom_cmap = mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)

        self.canvas.ax.clear()
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]
        self.canvas.ax.imshow(self.noise_reduced_data, aspect='auto', extent=extent, cmap=custom_cmap)
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

        cmap = LinearSegmentedColormap.from_list('custom_cmap', [(0, 'darkblue'), (1, 'orange')])
        colors = [(0.0, 'blue'), (0.5, 'red'), (1.0, 'yellow')]
        custom_cmap = mcolors.LinearSegmentedColormap.from_list('custom_RdYlBu', colors)

        self.canvas.ax.clear()
        extent = [0, self.time[-1], self.freqs[-1], self.freqs[0]]
        self.canvas.ax.imshow(burst_isolated, aspect='auto', extent=extent, cmap=custom_cmap)
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

        # Analyze Menu
        analyze_menu = menubar.addMenu("Analyze")
        analyze_action = QAction("Open Analyzer", self)
        analyze_menu.addAction(analyze_action)
        analyze_action.triggered.connect(self.open_analyze_window)

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

    def open_analyze_window(self):
        dialog = AnalyzeDialog(self.time_channels, self.freqs, self.filename, self)
        dialog.exec()

class AnalyzeDialog(QDialog):
    def __init__(self, time_channels, freqs, filename, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analyzer")
        self.resize(1100, 700)

        self.time = np.array(time_channels) * 0.25
        self.freq = np.array(freqs)
        self.filename = filename.split(".")[0]

        # Canvas
        self.canvas = MplCanvas(self, width=8, height=5)

        # Equation and metrics labels
        self.current_plot_title = f"{self.filename}_Best_Fit"  # default
        self.equation_label = QLabel("Best Fit Equation:")
        self.equation_display = QLabel("")
        self.equation_display.setTextFormat(Qt.RichText)
        self.equation_display.setStyleSheet("font-size: 18px; padding: 4px;")

        self.stats_label = QLabel("Fit Quality Metrics:")
        self.r2_display = QLabel("R² = ")
        self.rmse_display = QLabel("RMSE = ")

        self.metrics_label = QLabel("Shock Parameters:")
        self.drift_display = QLabel("")
        self.drift_display.setObjectName("value")

        self.start_freq_display = QLabel("")
        self.start_freq_display.setObjectName("value")

        self.initial_shock_speed_display = QLabel("")
        self.initial_shock_speed_display.setObjectName("value")

        self.initial_shock_height_display = QLabel("")
        self.initial_shock_height_display.setObjectName("value")

        self.avg_shock_speed_display = QLabel("")
        self.avg_shock_speed_display.setObjectName("value")

        self.avg_shock_height_display = QLabel("")
        self.avg_shock_height_display.setObjectName("value")

        # Buttons
        self.max_button = QPushButton("Maximum Intensities")
        self.fit_button = QPushButton("Best Fit")
        self.max_button.clicked.connect(self.plot_max)
        self.fit_button.clicked.connect(self.plot_fit)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.max_button)
        button_layout.addWidget(self.fit_button)

        # Vertical layout for plot + button
        left_layout = QVBoxLayout()
        left_layout.addLayout(button_layout)
        left_layout.addWidget(self.canvas)

        # Vertical layout for all text labels
        right_layout = QVBoxLayout()
        right_layout.addWidget(self.equation_label)
        right_layout.addWidget(self.equation_display)
        right_layout.addWidget(self.stats_label)
        right_layout.addWidget(self.r2_display)
        right_layout.addWidget(self.rmse_display)
        right_layout.addWidget(self.metrics_label)
        right_layout.addWidget(self.drift_display)
        right_layout.addWidget(self.start_freq_display)
        right_layout.addWidget(self.initial_shock_speed_display)
        right_layout.addWidget(self.initial_shock_height_display)
        right_layout.addWidget(self.avg_shock_speed_display)
        right_layout.addWidget(self.avg_shock_height_display)
        self.save_plot_button = QPushButton("Save Graph")
        self.save_data_button = QPushButton("Save Data")

        self.save_plot_button.clicked.connect(self.save_graph)
        self.save_data_button.clicked.connect(self.save_data)

        right_layout.addWidget(self.save_plot_button)
        right_layout.addWidget(self.save_data_button)

        self.extra_plot_label = QLabel("Extra Plots:")
        self.extra_plot_combo = QComboBox()
        self.extra_plot_combo.addItems([
            "Shock Speed vs Shock Height",
            "Shock Speed vs Frequency",
            "Shock Height vs Frequency"
        ])
        self.extra_plot_button = QPushButton("Plot")
        self.extra_plot_button.clicked.connect(self.plot_extra)

        right_layout.addWidget(self.extra_plot_label)
        right_layout.addWidget(self.extra_plot_combo)
        right_layout.addWidget(self.extra_plot_button)

        right_layout.addStretch(1)  # pushes content to the top

        # Combine both sections into a horizontal layout
        main_layout = QHBoxLayout()
        main_layout.addLayout(left_layout, stretch=3)
        main_layout.addLayout(right_layout, stretch=2)

        self.setLayout(main_layout)

        # Styling
        self.setStyleSheet("""
            QLabel {
                font-size: 14px;
                padding: 2px;
            }
            QLabel#value {
                font-weight: bold;
            }
        """)

    def plot_max(self):
        self.canvas.ax.clear()
        self.canvas.ax.scatter(self.time, self.freq, s=10, color='blue')
        self.canvas.ax.set_title(f"{self.filename}_Maximum_Intensity")
        self.canvas.ax.set_xlabel("Time (s)")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.draw()
        self.equation_display.setText("")  # clear equation

    def plot_fit(self):
        def model_func(t, a, b):
            return a * t ** (-b)

        def drift_rate(t, a_, b_):
            return -a_ * b_ * t ** (-(b_ + 1))

        # Nonlinear curve fit
        params, covariance = curve_fit(model_func, self.time, self.freq, maxfev=10000)
        a, b = params
        std_errs = np.sqrt(np.diag(covariance))

        # Best fit curve
        time_fit = np.linspace(self.time.min(), self.time.max(), 400)
        freq_fit = model_func(time_fit, a, b)

        # Plot
        self.canvas.ax.clear()
        self.canvas.ax.scatter(self.time, self.freq, s=10, color='blue', label="Original Data")
        self.canvas.ax.plot(time_fit, freq_fit, color='red', label=fr"Best Fit: $f = {a:.2f} \cdot t^{{-{b:.2f}}}$")
        self.canvas.ax.set_title(f"{self.filename}_Best_Fit")
        self.canvas.ax.set_xlabel("Time (s)")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.legend()
        self.canvas.draw()

        # Metrics
        predicted_freqs = model_func(self.time, a, b)
        r_squared = r2_score(self.freq, predicted_freqs)
        rmse = np.sqrt(mean_squared_error(self.freq, predicted_freqs))
        self.equation_display.setText(f"<b>f(t) = {a:.2f} · t<sup>-{b:.2f}</sup></b>")
        self.r2_display.setText(f"R² = {r_squared:.4f}")
        self.rmse_display.setText(f"RMSE = {rmse:.4f}")

        # Drift rate & errors
        drift_vals = drift_rate(self.time, a, b)
        residuals = self.freq - predicted_freqs
        freq_err = np.std(residuals)
        drift_errs = np.abs(drift_vals) * np.sqrt((std_errs[0] / a) ** 2 + (std_errs[1] / b) ** 2)

        # Shock speed and height
        shock_speed = (13853221.38 * np.abs(drift_vals)) / (self.freq * (np.log(self.freq ** 2 / 3.385)) ** 2)
        R_p = 4.32 * np.log(10) / np.log(self.freq ** 2 / 3.385)

        # Starting point
        percentile_threshold = 90
        start_freq = np.percentile(self.freq, percentile_threshold)
        start_index = np.abs(self.freq - start_freq).argmin()
        start_shock_speed = shock_speed[start_index]
        start_height = R_p[start_index]
        drift0 = drift_vals[start_index]
        drift_err0 = drift_errs[start_index]
        f0 = self.freq[start_index]

        # Errors at starting point
        shock_speed_error = (13853221.38 * drift_err0) / (f0 * (np.log(f0 ** 2 / 3.385)) ** 2)
        dRp_dFreq0 = (8.64 / f0) / np.log(10) / np.log(f0 ** 2 / 3.385)
        error_R_p = np.abs(dRp_dFreq0 * freq_err)

        # Averages
        avg_drift = np.mean(drift_vals)
        avg_drift_err = np.std(drift_vals) / np.sqrt(len(drift_vals))
        avg_speed = np.mean(shock_speed)
        avg_speed_err = np.std(shock_speed) / np.sqrt(len(shock_speed))
        avg_height = np.mean(R_p)
        avg_height_err = np.std(R_p) / np.sqrt(len(R_p))

        self.shock_speed = shock_speed
        self.R_p = R_p
        self.start_freq = start_freq
        self.start_height = start_height

        # Display
        self.drift_display.setText(
            f"Average Drift Rate: <b>{avg_drift:.4f} ± {avg_drift_err:.4f}</b> MHz/s")

        self.start_freq_display.setText(
            f"Starting Frequency: <b>{start_freq:.2f} ± {freq_err:.2f}</b> MHz")

        self.initial_shock_speed_display.setText(
            f"Initial Shock Speed: <b>{start_shock_speed:.2f} ± {shock_speed_error:.2f}</b> km/s")

        self.initial_shock_height_display.setText(
            f"Initial Shock Height: <b>{start_height:.3f} ± {error_R_p:.3f}</b> Rₛ")

        self.avg_shock_speed_display.setText(
            f"Average Shock Speed: <b>{avg_speed:.2f} ± {avg_speed_err:.2f}</b> km/s")

        self.avg_shock_height_display.setText(
            f"Average Shock Height: <b>{avg_height:.3f} ± {avg_height_err:.3f}</b> Rₛ")

    def save_graph(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
    "Save Plot",
    f"{self.current_plot_title}.png",
            "PNG Files (*.png)",
            options=options
        )
        if file_path:
            self.canvas.fig.savefig(file_path, dpi=300, bbox_inches='tight')

    def save_data(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Data",
            f"{self.filename}_data.txt",
            "Text Files (*.txt)",
            options=options
        )
        if not file_path:
            return

        # Collect data
        lines = [
            f"{self.equation_label.text()} {self.equation_display.text().strip()}",
            f"{self.stats_label.text()}",
            self.r2_display.text(),
            self.rmse_display.text(),
            f"{self.metrics_label.text()}",
            self.drift_display.text(),
            self.start_freq_display.text(),
            self.initial_shock_speed_display.text(),
            self.initial_shock_height_display.text(),
            self.avg_shock_speed_display.text(),
            self.avg_shock_height_display.text(),
        ]

        # Clean HTML from bold if present
        cleaned_lines = [line.replace("<b>", "").replace("</b>", "") for line in lines]

        with open(file_path, 'w') as f:
            f.write("\n".join(cleaned_lines))

    def plot_extra(self):
        choice = self.extra_plot_combo.currentText()
        self.canvas.ax.clear()

        if choice == "Shock Speed vs Shock Height":
            self.canvas.ax.scatter(self.R_p, self.shock_speed, color='green', s=10)
            self.canvas.ax.set_xlabel("Shock Height (Rₛ)")
            self.canvas.ax.set_ylabel("Shock Speed (km/s)")
            self.canvas.ax.set_title(f"{self.filename}_Shock_Speed_vs_Shock_Height")
            self.current_plot_title = f"{self.filename}_Shock_Speed_vs_Shock_Height"

        elif choice == "Shock Speed vs Frequency":
            self.canvas.ax.scatter(self.freq, self.shock_speed, color='purple', s=10)
            self.canvas.ax.set_xlabel("Frequency (MHz)")
            self.canvas.ax.set_ylabel("Shock Speed (km/s)")
            self.canvas.ax.set_title(f"{self.filename}_Shock_Speed_vs_Frequency")
            self.current_plot_title = f"{self.filename}_Shock_Speed_vs_Frequency"

        elif choice == "Shock Height vs Frequency":
            self.canvas.ax.scatter(self.R_p, self.freq, color='red', marker='o', s=50)
            self.canvas.ax.set_xlabel("Shock Height (Rₛ)")
            self.canvas.ax.set_ylabel("Frequency (MHz)")
            self.canvas.ax.set_title(f"{self.filename}_Rs_vs_Freq")
            self.current_plot_title = f"{self.filename}_Rs_vs_Freq"

        self.canvas.ax.grid(True)
        self.canvas.draw()
















