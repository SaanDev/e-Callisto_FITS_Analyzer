"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import csv
import os
import sys

import numpy as np
from matplotlib.path import Path
from matplotlib.widgets import LassoSelector
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
)

from src.backend.common.figure_export import FIGURE_EXPORT_FILTERS, save_figure
from src.backend.radio.radio_figures import fit_graph_figure
from src.backend.radio.ridge_tracking import DRIFT_ANY, DRIFT_FALLING, RidgeSettings, track_ridge
from src.ui.radio.dialogs.analyze_dialog import AnalyzeDialog
from src.ui.common.gui_shared import MplCanvas, fit_window_to_screen, pick_export_path
from src.ui.common.mpl_style import style_axes
from src.version import APP_NAME, APP_VERSION

class MaxIntensityPlotDialog(QDialog):
    sessionChanged = Signal(dict)
    requestOpenAnalyzer = Signal(dict)

    def __init__(
        self,
        time_channels,
        max_freqs,
        filename,
        parent=None,
        session=None,
        auto_outlier_mode: bool = False,
        time_seconds=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Maximum Intensities for Each Time Channel")
        fit_window_to_screen(self, 1000, 700)
        self.filename = filename
        self.current_plot_type = "MaxIntensityPlot"
        self._analyzer_state = None
        self._type_ii_state = None
        self._suppress_emit = False
        self._auto_outlier_mode = bool(auto_outlier_mode)

        # Data
        self.time_channels = np.asarray(time_channels, dtype=float).reshape(-1)
        self.time_seconds = self._resolve_time_seconds(self.time_channels, time_seconds)
        self.freqs = np.asarray(max_freqs, dtype=float).reshape(-1)
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self.lasso = None
        # Ridge tracking: the spectrum to follow, where to start, and the
        # per-column maxima it replaced (so they can be brought back).
        self._ridge_source = None
        self._ridge_settings = RidgeSettings()
        self._ridge_seed = None
        self._ridge_seed_cid = None
        self._pre_ridge_points = None

        # Canvas
        self.canvas = MplCanvas(self, width=10, height=6)
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)
        style_axes(self.canvas.ax)
        self._redraw_points("Maximum Intensity for Each Time Channel")

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
        self.fundamental_radio.toggled.connect(self._on_mode_toggled)
        self.harmonic_radio.toggled.connect(self._on_mode_toggled)
        self.analyze_button.clicked.connect(self.open_analyze_window)

        self.track_ridge_button = QPushButton("Track Ridge")
        self.track_ridge_button.setToolTip(
            "Follow the burst's peak frequency column by column, starting from the brightest point\n"
            "(or from the point picked with 'Pick Start'), and use it in place of the per-column maxima."
        )
        self.ridge_start_button = QPushButton("Pick Start")
        self.ridge_start_button.setCheckable(True)
        self.ridge_start_button.setToolTip("Click a point on the burst to start tracking there instead of the brightest point.")
        self.track_ridge_button.clicked.connect(self.track_ridge)
        self.ridge_start_button.toggled.connect(self._on_ridge_start_toggled)

        # Layouts
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.remove_button)
        button_layout.addWidget(self.track_ridge_button)
        button_layout.addWidget(self.ridge_start_button)
        button_layout.addWidget(self.fundamental_radio)
        button_layout.addWidget(self.harmonic_radio)
        button_layout.addWidget(self.analyze_button)
        button_layout.addStretch()

        # Status bar
        self.status = QStatusBar()
        self.status.showMessage("Ready")
        self.set_auto_outlier_mode(self._auto_outlier_mode)

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
        self.restore_maxima_action = QAction("Restore Per-Column Maxima", self)
        self.restore_maxima_action.setEnabled(False)
        edit_menu.addAction(self.restore_maxima_action)
        self.restore_maxima_action.triggered.connect(self.restore_per_column_maxima)

        analyze_menu = menubar.addMenu("Analyze")
        analyze_action = QAction("Open Analyzer", self)
        analyze_menu.addAction(analyze_action)
        analyze_action.triggered.connect(self.open_analyze_window)
        self.ridge_settings_action = QAction("Ridge Tracking Settings...", self)
        analyze_menu.addAction(self.ridge_settings_action)
        self.ridge_settings_action.triggered.connect(self.edit_ridge_settings)

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

        self._sync_ridge_controls()

        # Restore optional session state
        if isinstance(session, dict):
            self.restore_session(session, emit_change=False)

    @staticmethod
    def _resolve_time_seconds(time_channels, time_seconds=None):
        channels = np.asarray(time_channels, dtype=float).reshape(-1)
        if time_seconds is not None:
            arr = np.asarray(time_seconds, dtype=float).reshape(-1)
            if arr.shape == channels.shape:
                return arr
        return channels * 0.25

    def _plot_time_values(self) -> np.ndarray:
        if getattr(self, "time_seconds", None) is not None:
            arr = np.asarray(self.time_seconds, dtype=float).reshape(-1)
            if arr.shape == self.time_channels.shape:
                return arr
        return self.time_channels

    def _time_axis_label(self) -> str:
        if getattr(self, "time_seconds", None) is not None:
            arr = np.asarray(self.time_seconds, dtype=float).reshape(-1)
            if arr.shape == self.time_channels.shape:
                return "Time (s)"
        return "Time Channel Number"

    def set_auto_outlier_mode(self, enabled: bool):
        self._auto_outlier_mode = bool(enabled)
        if self._auto_outlier_mode:
            self.status.showMessage("Auto outlier cleaning enabled for isolated burst; manual outlier tools remain available.", 3500)

    def _redraw_points(self, title: str):
        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)
        style_axes(self.canvas.ax)
        self.canvas.ax.scatter(self._plot_time_values(), self.freqs, marker="o", s=5, color="red")
        self.canvas.ax.set_xlabel(self._time_axis_label())
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.set_title(title)
        self.canvas.draw()
        self._plot_title = title

    def _on_mode_toggled(self, _checked=False):
        self._emit_session_changed()

    def _emit_session_changed(self):
        if self._suppress_emit:
            return
        try:
            self.sessionChanged.emit(self.session_state())
        except Exception:
            pass

    def restore_session(self, session: dict, *, emit_change: bool = False):
        max_state = dict(session.get("max_intensity") or session) if isinstance(session, dict) else {}
        analyzer_state = None
        type_ii_state = None
        if isinstance(session, dict):
            analyzer_state = session.get("analyzer", None)
            if analyzer_state is None:
                analyzer_state = max_state.get("analyzer", None)
            type_ii_state = session.get("type_ii", None)

        t = max_state.get("time_channels", None)
        f = max_state.get("freqs", None)
        if t is not None and f is not None:
            t_arr = np.asarray(t, dtype=float).reshape(-1)
            ts_arr = max_state.get("time_seconds", None)
            f_arr = np.asarray(f, dtype=float).reshape(-1)
            if len(t_arr) == len(f_arr) and len(t_arr) > 0:
                self.time_channels = t_arr
                self.time_seconds = self._resolve_time_seconds(self.time_channels, ts_arr)
                self.freqs = f_arr
                self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)

        self._suppress_emit = True
        try:
            harmonic = bool(max_state.get("harmonic", False))
            if harmonic:
                self.harmonic_radio.setChecked(True)
            else:
                self.fundamental_radio.setChecked(True)
        finally:
            self._suppress_emit = False

        if isinstance(analyzer_state, dict):
            self._analyzer_state = dict(analyzer_state)
        elif analyzer_state is None:
            self._analyzer_state = None
        if isinstance(type_ii_state, dict):
            self._type_ii_state = dict(type_ii_state)
        elif type_ii_state is None:
            self._type_ii_state = None

        self._redraw_points("Maximum Intensity for Each Time Channel")
        if emit_change:
            self._emit_session_changed()

    # ---- ridge tracking ------------------------------------------------------
    def set_ridge_source(self, data, freqs, time_seconds) -> None:
        """The dynamic spectrum Track Ridge follows: (frequency, time) data and its axes."""
        source = None
        try:
            arr = np.asarray(data, dtype=float)
            freq_arr = np.asarray(freqs, dtype=float).reshape(-1)
            time_arr = np.asarray(time_seconds, dtype=float).reshape(-1)
            if arr.ndim == 2 and arr.shape == (freq_arr.size, time_arr.size) and arr.size:
                source = (arr, freq_arr, time_arr)
        except Exception:
            source = None
        self._ridge_source = source
        self._sync_ridge_controls()

    def _sync_ridge_controls(self) -> None:
        available = self._ridge_source is not None
        self.track_ridge_button.setEnabled(available)
        self.ridge_start_button.setEnabled(available)
        if not available:
            self.ridge_start_button.setChecked(False)
        self.restore_maxima_action.setEnabled(self._pre_ridge_points is not None)

    def _on_ridge_start_toggled(self, checked: bool) -> None:
        if self._ridge_seed_cid is not None:
            self.canvas.mpl_disconnect(self._ridge_seed_cid)
            self._ridge_seed_cid = None
        if checked:
            if self.lasso:
                self.lasso.disconnect_events()
                self.lasso = None
            self._ridge_seed_cid = self.canvas.mpl_connect("button_press_event", self._on_ridge_seed_click)
            self.status.showMessage("Click a point on the burst to start tracking from there.", 5000)

    def _on_ridge_seed_click(self, event) -> None:
        if event.inaxes is not self.canvas.ax or event.xdata is None or event.ydata is None:
            return
        self._ridge_seed = (float(event.xdata), float(event.ydata))
        self.ridge_start_button.setChecked(False)
        self._draw_ridge_seed()
        self.status.showMessage(
            f"Tracking will start near t = {self._ridge_seed[0]:.2f} s, f = {self._ridge_seed[1]:.2f} MHz. "
            "Press Track Ridge.",
            6000,
        )

    def _draw_ridge_seed(self) -> None:
        if self._ridge_seed is None:
            return
        self.canvas.ax.plot(
            [self._ridge_seed[0]], [self._ridge_seed[1]], marker="*", markersize=14,
            color="#1f77b4", markeredgecolor="black", linestyle="none", zorder=5,
        )
        self.canvas.draw()

    def _ridge_seed_index(self):
        """The picked start as a (frequency row, time column) of the source spectrum."""
        if self._ridge_seed is None or self._ridge_source is None:
            return None
        _data, freq_arr, time_arr = self._ridge_source
        column = int(np.nanargmin(np.abs(time_arr - self._ridge_seed[0])))
        row = int(np.nanargmin(np.abs(freq_arr - self._ridge_seed[1])))
        return row, column

    def track_ridge(self) -> bool:
        """Replace the points with the burst ridge followed through the spectrum."""
        if self._ridge_source is None:
            self.status.showMessage("Ridge tracking needs the dynamic spectrum; reopen this window from the main plot.", 5000)
            return False
        data, freq_arr, time_arr = self._ridge_source
        try:
            result = track_ridge(data, freq_arr, seed=self._ridge_seed_index(), settings=self._ridge_settings)
        except ValueError as exc:
            QMessageBox.warning(self, "Track Ridge", str(exc))
            return False

        if self._pre_ridge_points is None:
            self._pre_ridge_points = (self.time_channels.copy(), self.time_seconds.copy(), self.freqs.copy())
        self.time_channels = np.asarray(result.time_indices, dtype=float)
        self.time_seconds = np.asarray(time_arr[result.time_indices], dtype=float)
        self.freqs = np.asarray(result.freqs_mhz, dtype=float)
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self._analyzer_state = None

        self._redraw_points("Tracked Burst Ridge")
        self._draw_ridge_seed()
        self._sync_ridge_controls()
        message = (
            f"Tracked {result.count} points from t = {self.time_seconds[0]:.2f} s "
            f"to {self.time_seconds[-1]:.2f} s."
        )
        if result.warnings:
            message += " " + " ".join(result.warnings)
        self.status.showMessage(message, 6000)
        self._emit_session_changed()
        return True

    def restore_per_column_maxima(self) -> None:
        """Bring back the per-column maxima that Track Ridge replaced."""
        if self._pre_ridge_points is None:
            return
        self.time_channels, self.time_seconds, self.freqs = (arr.copy() for arr in self._pre_ridge_points)
        self._pre_ridge_points = None
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self._analyzer_state = None
        self._redraw_points("Maximum Intensity for Each Time Channel")
        self._sync_ridge_controls()
        self.status.showMessage("Restored the per-column maxima.", 3000)
        self._emit_session_changed()

    def edit_ridge_settings(self) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Ridge Tracking Settings")
        form = QFormLayout(dialog)

        window_spin = QSpinBox(dialog)
        window_spin.setRange(1, 100)
        window_spin.setValue(int(self._ridge_settings.search_channels))
        window_spin.setToolTip("How many channels the ridge may move between two consecutive time columns.")
        threshold_spin = QDoubleSpinBox(dialog)
        threshold_spin.setRange(0.0, 50.0)
        threshold_spin.setDecimals(1)
        threshold_spin.setSingleStep(0.5)
        threshold_spin.setValue(float(self._ridge_settings.threshold_sigma))
        threshold_spin.setToolTip("A column counts when its peak is this many robust standard deviations above the background.")
        gap_spin = QSpinBox(dialog)
        gap_spin.setRange(0, 1000)
        gap_spin.setValue(int(self._ridge_settings.max_gap))
        gap_spin.setToolTip("Columns in a row below the threshold before tracking stops.")
        falling_check = QCheckBox("Only follow drift to lower frequency", dialog)
        falling_check.setChecked(self._ridge_settings.drift == DRIFT_FALLING)

        form.addRow("Search window (channels):", window_spin)
        form.addRow("Threshold (σ):", threshold_spin)
        form.addRow("Allowed gap (columns):", gap_spin)
        form.addRow(falling_check)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return False
        self._ridge_settings = RidgeSettings(
            search_channels=int(window_spin.value()),
            threshold_sigma=float(threshold_spin.value()),
            max_gap=int(gap_spin.value()),
            drift=DRIFT_FALLING if falling_check.isChecked() else DRIFT_ANY,
        )
        return True

    def activate_lasso(self):
        self.ridge_start_button.setChecked(False)
        self.canvas.ax.set_title("Draw around outliers to remove")
        self.canvas.draw()

        if self.lasso:
            self.lasso.disconnect_events()

        self.lasso = LassoSelector(self.canvas.ax, onselect=self.on_lasso_select)

    def on_lasso_select(self, verts):
        path = Path(verts)
        points = np.column_stack((self._plot_time_values(), self.freqs))
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
        self.time_seconds = self.time_seconds[~self.selected_mask]
        self.freqs = self.freqs[~self.selected_mask]
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self._analyzer_state = None

        self._redraw_points("Filtered Max Intensities")
        self.status.showMessage("Selected outliers removed", 3000)
        self._emit_session_changed()

    def session_state(self) -> dict:
        return {
            "source": {"filename": str(self.filename or "")},
            "max_intensity": {
                "time_channels": np.asarray(self.time_channels, dtype=float),
                "time_seconds": np.asarray(self.time_seconds, dtype=float),
                "freqs": np.asarray(self.freqs, dtype=float),
                "fundamental": bool(self.fundamental_radio.isChecked()),
                "harmonic": bool(self.harmonic_radio.isChecked()),
            },
            "analyzer": dict(self._analyzer_state or {}),
            "type_ii": dict(self._type_ii_state or {}),
            "ui": {
                "restore_max_window": True,
                "restore_analyzer_window": bool(self._analyzer_state),
                "restore_type_ii_window": bool(self._type_ii_state),
            },
        }

    def reset_all(self):
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self._analyzer_state = None
        self._ridge_seed = None
        self.ridge_start_button.setChecked(False)
        self._redraw_points("Maximum Intensity for Each Time Channel")
        self.status.showMessage("Reset selections.", 3000)
        self._emit_session_changed()

    def save_as_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV File", "", "CSV files (*.csv)")
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Time Channel", "Time (s)", "Frequency (MHz)"])
                for channel, time_s, fval in zip(self.time_channels, self.time_seconds, self.freqs):
                    writer.writerow([channel, time_s, fval])
            self.status.showMessage(f"Saved to {file_path}", 3000)
        except Exception as e:
            self.status.showMessage(f"Error: {e}", 3000)

    def export_figure(self):

        if not self.filename:
            QMessageBox.warning(self, "No File Loaded", "Load a FITS file before exporting.")
            return

        base_name = self.filename.split(".")[0]
        suffix = self.current_plot_type.replace(" ", "")
        default_name = f"{base_name}_{suffix}"

        file_path, ext = pick_export_path(
            self,
            "Export Figure",
            default_name,
            FIGURE_EXPORT_FILTERS,
            default_filter="PNG (*.png)"
        )

        if not file_path:
            return

        if sys.platform.startswith("win") and file_path.lower().startswith("c:\\program files"):
            QMessageBox.warning(
                self,
                "Permission Denied",
                "Windows does not allow saving files inside Program Files.\n"
                "Please choose another folder such as Documents or Desktop."
            )
            return

        try:
            root, current_ext = os.path.splitext(file_path)
            if current_ext == "":
                ext = ext.lower().lstrip(".")
                file_path = f"{file_path}.{ext}"
            else:
                ext = current_ext.lower().lstrip(".")

            save_figure(self.origin_figure(), file_path, tight=True)

            QMessageBox.information(self, "Export Complete", f"Figure saved:\n{file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"An error occurred:\n{e}")

    def origin_figure(self):
        """The points on show as an OriginPro graph, for Export; the window keeps its look."""
        return fit_graph_figure(
            self._plot_time_values(),
            self.freqs,
            data_label="",
            title=self._plot_title,
            x_label=self._time_axis_label(),
            y_label="Frequency (MHz)",
        )

    def show_about_dialog(self):
        QMessageBox.information(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} version {APP_VERSION}.\n\n"
            "Developed by Sahan S Liyanage\n\n"
            "Astronomical and Space Science Unit\n"
            "University of Colombo, Sri Lanka\n\n"
            "2026©Copyright, All Rights Reserved."
        )

    def open_analyze_window(self, fundamental=None, harmonic=None):
        if fundamental is None and harmonic is None:
            fundamental = bool(self.fundamental_radio.isChecked())
            harmonic = bool(self.harmonic_radio.isChecked())

        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_or_focus_analyzer_dialog"):
            self.requestOpenAnalyzer.emit(self.session_state())
            return

        dialog = AnalyzeDialog(
            self.time_channels,
            self.freqs,
            self.filename,
            fundamental=bool(fundamental),
            harmonic=bool(harmonic),
            parent=self,
            time_seconds=self.time_seconds,
            session={
                "max_intensity": self.session_state().get("max_intensity"),
                "analyzer": self._analyzer_state,
                "type_ii": self._type_ii_state,
            },
        )
        dialog.exec()
        try:
            restored = dialog.session_state()
            self._analyzer_state = dict((restored or {}).get("analyzer") or {})
            self._type_ii_state = dict((restored or {}).get("type_ii") or {})
            self._emit_session_changed()
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self._emit_session_changed()
            if hasattr(self.canvas, "ax"):
                self.canvas.ax.clear()
            self.canvas.figure.clf()
            self.canvas.deleteLater()

            if self.lasso:
                self.lasso.disconnect_events()
                self.lasso = None
        except Exception:
            pass
        event.accept()
