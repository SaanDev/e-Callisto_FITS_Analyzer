"""
e-CALLISTO FITS Analyzer
Version 2.1
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
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStatusBar,
    QVBoxLayout,
)

from src.UI.dialogs.analyze_dialog import AnalyzeDialog
from src.UI.gui_shared import MplCanvas, pick_export_path
from src.UI.mpl_style import style_axes
from src.version import APP_NAME, APP_VERSION

class MaxIntensityPlotDialog(QDialog):
    sessionChanged = Signal(dict)
    requestOpenAnalyzer = Signal(dict)

    def __init__(self, time_channels, max_freqs, filename, parent=None, session=None, auto_outlier_mode: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Maximum Intensities for Each Time Channel")
        self.resize(1000, 700)
        self.filename = filename
        self.current_plot_type = "MaxIntensityPlot"
        self._analyzer_state = None
        self._suppress_emit = False
        self._auto_outlier_mode = bool(auto_outlier_mode)

        # Data
        self.time_channels = np.asarray(time_channels, dtype=float).reshape(-1)
        self.freqs = np.asarray(max_freqs, dtype=float).reshape(-1)
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self.lasso = None

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

        # Restore optional session state
        if isinstance(session, dict):
            self.restore_session(session, emit_change=False)

    def set_auto_outlier_mode(self, enabled: bool):
        self._auto_outlier_mode = bool(enabled)
        manual_enabled = not self._auto_outlier_mode
        self.select_button.setVisible(manual_enabled)
        self.remove_button.setVisible(manual_enabled)
        self.select_button.setEnabled(manual_enabled)
        self.remove_button.setEnabled(manual_enabled)
        if self._auto_outlier_mode:
            self.status.showMessage("Auto outlier cleaning enabled for isolated burst.", 3500)

    def _redraw_points(self, title: str):
        self.canvas.ax.clear()
        self.canvas.figure.clf()
        self.canvas.ax = self.canvas.figure.add_subplot(111)
        style_axes(self.canvas.ax)
        self.canvas.ax.scatter(self.time_channels, self.freqs, marker="o", s=5, color="red")
        self.canvas.ax.set_xlabel("Time Channel Number")
        self.canvas.ax.set_ylabel("Frequency (MHz)")
        self.canvas.ax.set_title(title)
        self.canvas.draw()

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
        if isinstance(session, dict):
            analyzer_state = session.get("analyzer", None)
            if analyzer_state is None:
                analyzer_state = max_state.get("analyzer", None)

        t = max_state.get("time_channels", None)
        f = max_state.get("freqs", None)
        if t is not None and f is not None:
            t_arr = np.asarray(t, dtype=float).reshape(-1)
            f_arr = np.asarray(f, dtype=float).reshape(-1)
            if len(t_arr) == len(f_arr) and len(t_arr) > 0:
                self.time_channels = t_arr
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

        self._redraw_points("Maximum Intensity for Each Time Channel")
        if emit_change:
            self._emit_session_changed()

    def activate_lasso(self):
        if self._auto_outlier_mode:
            self.status.showMessage("Manual outlier tools are disabled in isolated auto-clean mode.", 3000)
            return
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
        if self._auto_outlier_mode:
            self.status.showMessage("Manual outlier tools are disabled in isolated auto-clean mode.", 3000)
            return
        if not np.any(self.selected_mask):
            self.status.showMessage("No points selected for removal", 3000)
            return

        self.time_channels = self.time_channels[~self.selected_mask]
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
                "freqs": np.asarray(self.freqs, dtype=float),
                "fundamental": bool(self.fundamental_radio.isChecked()),
                "harmonic": bool(self.harmonic_radio.isChecked()),
            },
            "analyzer": dict(self._analyzer_state or {}),
            "ui": {
                "restore_max_window": True,
                "restore_analyzer_window": bool(self._analyzer_state),
            },
        }

    def reset_all(self):
        self.selected_mask = np.zeros_like(self.time_channels, dtype=bool)
        self._analyzer_state = None
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
                writer.writerow(["Time Channel", "Frequency (MHz)"])
                for t, fval in zip(self.time_channels * 0.25, self.freqs):
                    writer.writerow([t, fval])
            self.status.showMessage(f"Saved to {file_path}", 3000)
        except Exception as e:
            self.status.showMessage(f"Error: {e}", 3000)

    def export_figure(self):

        if not self.filename:
            QMessageBox.warning(self, "No File Loaded", "Load a FITS file before exporting.")
            return

        formats = "PNG (*.png);;PDF (*.pdf);;EPS (*.eps);;SVG (*.svg);;TIFF (*.tiff)"

        base_name = self.filename.split(".")[0]
        suffix = self.current_plot_type.replace(" ", "")
        default_name = f"{base_name}_{suffix}"

        file_path, ext = pick_export_path(
            self,
            "Export Figure",
            default_name,
            formats,
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

            self.canvas.figure.savefig(
                file_path,
                dpi=300,
                bbox_inches="tight",
                format=ext
            )

            QMessageBox.information(self, "Export Complete", f"Figure saved:\n{file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"An error occurred:\n{e}")

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
            session={"max_intensity": self.session_state().get("max_intensity"), "analyzer": self._analyzer_state},
        )
        dialog.exec()
        try:
            restored = dialog.session_state()
            self._analyzer_state = dict((restored or {}).get("analyzer") or {})
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
