"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
from pathlib import Path
import base64
import tempfile
from types import SimpleNamespace
from typing import Callable

from matplotlib.figure import Figure
import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPdfWriter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.Backend.multi_station_comparison import (
    COLOR_SCALE_MANUAL,
    COLOR_SCALE_PER_STATION,
    COLOR_SCALE_SHARED,
    TIME_ALIGNMENT_SECONDS,
    TIME_ALIGNMENT_UT,
    ComparisonDataset,
    combined_comparison_datasets_from_paths,
    comparison_cmap,
    comparison_panel_payloads,
    load_comparison_dataset,
    render_comparison_figure,
    seconds_of_day_range_to_unwrapped,
    shared_extent,
)
from src.Backend.view_config import normalize_display_range, normalize_view_config, parse_view_config_json
from src.UI.accelerated_plot_widget import AcceleratedPlotWidget
from src.UI.dialogs.display_range_dialog import DisplayRangeDialog
from src.UI.gui_shared import MplCanvas, pick_export_path


_FITS_FILTER = "FITS files (*.fit *.fits *.fit.gz *.fits.gz)"
_VIEW_CONFIG_FILTER = "e-CALLISTO View Config (*.efaview.json);;JSON Files (*.json)"
_EXPORT_FILTERS = "PNG (*.png);;PDF (*.pdf);;EPS (*.eps);;SVG (*.svg);;TIFF (*.tiff)"


class MultiStationComparisonDialog(QDialog):
    def __init__(
        self,
        *,
        initial_paths: list[str] | None = None,
        view_config_provider: Callable[..., dict | None] | None = None,
        plot_mode_provider: Callable[[], str] | None = None,
        dark_mode_provider: Callable[[], bool] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Multi-Station Comparison")
        self.setModal(False)
        self.resize(1400, 900)

        self._view_config_provider = view_config_provider
        self._plot_mode_provider = plot_mode_provider
        self._dark_mode_provider = dark_mode_provider
        self._datasets: list[ComparisonDataset] = []
        self._display_datasets: list[ComparisonDataset] = []
        self._display_range: dict[str, float] | None = None
        self._user_changed_alignment = False
        self._last_render_warnings: tuple[str, ...] = tuple()
        self._combined_mode: str | None = None
        self._hardware_canvases: list[AcceleratedPlotWidget] = []
        self._hardware_available: bool | None = None

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(120)
        self._redraw_timer.timeout.connect(self._render_now)

        self.file_list = QListWidget(self)
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.add_btn = QPushButton("Add Files...", self)
        self.remove_btn = QPushButton("Remove", self)
        self.clear_btn = QPushButton("Clear", self)
        self.up_btn = QPushButton("Move Up", self)
        self.down_btn = QPushButton("Move Down", self)

        self.add_btn.clicked.connect(self._choose_files)
        self.remove_btn.clicked.connect(self.remove_selected_files)
        self.clear_btn.clicked.connect(self.clear_files)
        self.up_btn.clicked.connect(lambda: self._move_selected(-1))
        self.down_btn.clicked.connect(lambda: self._move_selected(1))

        file_buttons = QHBoxLayout()
        for button in (self.add_btn, self.remove_btn, self.clear_btn):
            file_buttons.addWidget(button)
        move_buttons = QHBoxLayout()
        move_buttons.addWidget(self.up_btn)
        move_buttons.addWidget(self.down_btn)

        file_panel = QWidget(self)
        file_layout = QVBoxLayout(file_panel)
        file_layout.addWidget(QLabel("Stations / Files", self))
        file_layout.addWidget(self.file_list)
        file_layout.addLayout(file_buttons)
        file_layout.addLayout(move_buttons)

        self.alignment_combo = QComboBox(self)
        self.alignment_combo.addItem("UT clock", TIME_ALIGNMENT_UT)
        self.alignment_combo.addItem("Seconds from file start", TIME_ALIGNMENT_SECONDS)
        self.alignment_combo.currentIndexChanged.connect(self._on_alignment_changed)

        self.units_combo = QComboBox(self)
        self.units_combo.addItem("Digits", False)
        self.units_combo.addItem("dB", True)
        self.units_combo.currentIndexChanged.connect(self.schedule_redraw)

        self.colormap_combo = QComboBox(self)
        for name in ("Custom", "viridis", "plasma", "inferno", "magma", "cividis", "gray", "bone_r"):
            self.colormap_combo.addItem(name)
        self.colormap_combo.currentIndexChanged.connect(self.schedule_redraw)

        self.color_scale_combo = QComboBox(self)
        self.color_scale_combo.addItem("Shared scale", COLOR_SCALE_SHARED)
        self.color_scale_combo.addItem("Per-station auto scale", COLOR_SCALE_PER_STATION)
        self.color_scale_combo.addItem("Manual scale", COLOR_SCALE_MANUAL)
        self.color_scale_combo.currentIndexChanged.connect(self._on_color_scale_changed)

        self.manual_low_spin = self._manual_spin(-10.0)
        self.manual_high_spin = self._manual_spin(10.0)
        self.manual_low_spin.valueChanged.connect(self.schedule_redraw)
        self.manual_high_spin.valueChanged.connect(self.schedule_redraw)

        self.set_range_btn = QPushButton("Set Range...", self)
        self.reset_range_btn = QPushButton("Reset Range", self)
        self.load_config_btn = QPushButton("Load View Config...", self)
        self.export_btn = QPushButton("Export...", self)

        self.set_range_btn.clicked.connect(self.open_display_range_dialog)
        self.reset_range_btn.clicked.connect(self.reset_display_range)
        self.load_config_btn.clicked.connect(self._load_view_config_file)
        self.export_btn.clicked.connect(self.export_png)

        self.status_label = QLabel("Add FITS files to build a comparison.", self)
        self.status_label.setWordWrap(True)

        controls = QGroupBox("Comparison Controls", self)
        controls_layout = QVBoxLayout(controls)
        controls_layout.addWidget(QLabel("Time alignment", self))
        controls_layout.addWidget(self.alignment_combo)
        controls_layout.addWidget(QLabel("Units", self))
        controls_layout.addWidget(self.units_combo)
        controls_layout.addWidget(QLabel("Colormap", self))
        controls_layout.addWidget(self.colormap_combo)
        controls_layout.addWidget(QLabel("Color scale", self))
        controls_layout.addWidget(self.color_scale_combo)

        manual_row = QHBoxLayout()
        manual_row.addWidget(QLabel("Low", self))
        manual_row.addWidget(self.manual_low_spin)
        manual_row.addWidget(QLabel("High", self))
        manual_row.addWidget(self.manual_high_spin)
        controls_layout.addLayout(manual_row)

        range_row = QHBoxLayout()
        range_row.addWidget(self.set_range_btn)
        range_row.addWidget(self.reset_range_btn)
        controls_layout.addLayout(range_row)
        controls_layout.addWidget(self.load_config_btn)
        controls_layout.addWidget(self.export_btn)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.status_label)

        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(file_panel)
        left_layout.addWidget(controls)

        self.canvas = MplCanvas(self, width=10, height=6, dpi=100)
        self.hardware_panel = QWidget(self)
        self.hardware_layout = QVBoxLayout(self.hardware_panel)
        self.hardware_layout.setContentsMargins(0, 0, 0, 0)
        self.hardware_layout.setSpacing(6)
        self.hardware_scroll = QScrollArea(self)
        self.hardware_scroll.setWidgetResizable(True)
        self.hardware_scroll.setWidget(self.hardware_panel)

        self.plot_area = QWidget(self)
        self.plot_stack = QStackedLayout(self.plot_area)
        self.plot_stack.setContentsMargins(0, 0, 0, 0)
        self.plot_stack.addWidget(self.canvas)
        self.plot_stack.addWidget(self.hardware_scroll)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.plot_area)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 1100])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._apply_initial_view_config()
        self._on_color_scale_changed()
        self._sync_actions()
        if initial_paths:
            self.add_files(initial_paths)

    def showEvent(self, event):
        super().showEvent(event)
        if not bool(getattr(self, "_maximized_once", False)):
            self._maximized_once = True
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.availableGeometry())
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

    @staticmethod
    def _manual_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
        spin.setSingleStep(1.0)
        spin.setValue(float(value))
        return spin

    def _apply_initial_view_config(self) -> None:
        if not callable(self._view_config_provider):
            return
        try:
            config = self._view_config_provider(include_range=False, include_visual=True)
        except Exception:
            config = None
        if isinstance(config, dict):
            self._apply_view_config_payload(config, apply_range=False, show_status=False)

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add FITS Files", "", _FITS_FILTER)
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        existing = {item.path for item in self._datasets}
        errors: list[str] = []
        added = 0
        for path in paths:
            text = str(path or "").strip()
            if not text or text in existing:
                continue
            try:
                dataset = load_comparison_dataset(text, memmap=False)
            except Exception as exc:
                errors.append(f"{os.path.basename(text)}: {exc}")
                continue
            self._datasets.append(dataset)
            existing.add(dataset.path)
            added += 1

        self._refresh_display_datasets()
        self._rebuild_file_list()
        self._choose_default_alignment_after_load()
        self._sync_actions()
        self.schedule_redraw()
        if errors:
            QMessageBox.warning(self, "Add FITS Files", "Some files could not be loaded:\n" + "\n".join(errors[:8]))
        elif added:
            self.status_label.setText(f"Added {added} FITS file(s).")

    def remove_selected_files(self) -> None:
        rows = sorted({index.row() for index in self.file_list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self._datasets):
                del self._datasets[row]
        self._display_range = None
        self._refresh_display_datasets()
        self._rebuild_file_list()
        self._choose_default_alignment_after_load()
        self._sync_actions()
        self.schedule_redraw()

    def clear_files(self) -> None:
        self._datasets.clear()
        self._display_datasets.clear()
        self._combined_mode = None
        self._display_range = None
        self._rebuild_file_list()
        self._sync_actions()
        self.schedule_redraw()

    def _move_selected(self, delta: int) -> None:
        selected = self.file_list.currentRow()
        target = selected + int(delta)
        if selected < 0 or target < 0 or target >= len(self._datasets):
            return
        self._datasets[selected], self._datasets[target] = self._datasets[target], self._datasets[selected]
        self._refresh_display_datasets()
        self._rebuild_file_list()
        self.file_list.setCurrentRow(target)
        self.schedule_redraw()

    def _rebuild_file_list(self) -> None:
        self.file_list.clear()
        for dataset in self._datasets:
            item = QListWidgetItem(f"{dataset.label}  -  {os.path.basename(dataset.path)}")
            item.setData(Qt.UserRole, dataset.path)
            if dataset.warnings:
                item.setToolTip("\n".join(dataset.warnings))
            self.file_list.addItem(item)

    def _refresh_display_datasets(self) -> None:
        self._combined_mode = None
        self._display_datasets = list(self._datasets)
        if len(self._datasets) < 2:
            return
        active = combined_comparison_datasets_from_paths([dataset.path for dataset in self._datasets])
        if active:
            self._display_datasets = active
            modes = {dataset.combine_type for dataset in active if dataset.combine_type}
            if len(modes) == 1:
                self._combined_mode = next(iter(modes))
            elif modes:
                self._combined_mode = "mixed"

    def _active_datasets(self) -> list[ComparisonDataset]:
        if not self._display_datasets and self._datasets:
            self._refresh_display_datasets()
        return list(self._display_datasets)

    def _choose_default_alignment_after_load(self) -> None:
        if self._user_changed_alignment:
            if self.current_alignment_mode() == TIME_ALIGNMENT_UT and not self._all_files_have_ut():
                self._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
                self.status_label.setText("UT alignment requires TIME-OBS in every selected file. Switched to seconds.")
            return
        self._set_alignment_mode(TIME_ALIGNMENT_UT if self._datasets and self._all_files_have_ut() else TIME_ALIGNMENT_SECONDS)

    def _all_files_have_ut(self) -> bool:
        active = self._active_datasets()
        return bool(active) and all(dataset.ut_start_sec is not None for dataset in active)

    def _set_alignment_mode(self, mode: str) -> None:
        idx = self.alignment_combo.findData(mode)
        if idx >= 0 and idx != self.alignment_combo.currentIndex():
            blocked = self.alignment_combo.blockSignals(True)
            try:
                self.alignment_combo.setCurrentIndex(idx)
            finally:
                self.alignment_combo.blockSignals(blocked)

    def _on_alignment_changed(self) -> None:
        self._user_changed_alignment = True
        if self.current_alignment_mode() == TIME_ALIGNMENT_UT and not self._all_files_have_ut():
            self._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
            self.status_label.setText("UT alignment requires TIME-OBS in every selected file. Switched to seconds.")
        self._display_range = None
        self.schedule_redraw()

    def current_alignment_mode(self) -> str:
        return str(self.alignment_combo.currentData() or TIME_ALIGNMENT_SECONDS)

    def _on_color_scale_changed(self) -> None:
        enabled = self.current_color_scale_mode() == COLOR_SCALE_MANUAL
        self.manual_low_spin.setEnabled(enabled)
        self.manual_high_spin.setEnabled(enabled)
        self.schedule_redraw()

    def current_color_scale_mode(self) -> str:
        return str(self.color_scale_combo.currentData() or COLOR_SCALE_SHARED)

    def _visual_payload(self) -> dict:
        return {
            "use_db": bool(self.units_combo.currentData()),
            "use_utc": self.current_alignment_mode() == TIME_ALIGNMENT_UT,
            "noise_clip_low": float(self.manual_low_spin.value()),
            "noise_clip_high": float(self.manual_high_spin.value()),
            "noise_clip_scale": "linear",
            "cmap": str(self.colormap_combo.currentText() or "Custom"),
            "graph": {},
        }

    def _manual_limits(self) -> tuple[float, float] | None:
        if self.current_color_scale_mode() != COLOR_SCALE_MANUAL:
            return None
        low = float(self.manual_low_spin.value())
        high = float(self.manual_high_spin.value())
        if not np.isfinite([low, high]).all() or abs(high - low) <= 1e-9:
            return None
        return tuple(sorted((low, high)))

    def schedule_redraw(self) -> None:
        self._redraw_timer.start()

    def _sync_actions(self) -> None:
        has_files = bool(self._datasets)
        self.remove_btn.setEnabled(has_files)
        self.clear_btn.setEnabled(has_files)
        self.up_btn.setEnabled(has_files)
        self.down_btn.setEnabled(has_files)
        self.set_range_btn.setEnabled(has_files)
        self.reset_range_btn.setEnabled(has_files and self._display_range is not None)
        self.export_btn.setEnabled(len(self._datasets) >= 2)

    def _requested_plot_mode(self) -> str:
        if callable(self._plot_mode_provider):
            try:
                mode = str(self._plot_mode_provider() or "")
            except Exception:
                mode = ""
        else:
            mode = ""
        return "modern" if mode.strip().lower() == "modern" else "classic"

    def _use_hardware_view(self) -> bool:
        if self._requested_plot_mode() != "modern":
            return False
        if self._hardware_available is not None:
            return bool(self._hardware_available)
        probe = AcceleratedPlotWidget(self)
        self._hardware_available = bool(probe.is_available)
        probe.deleteLater()
        return bool(self._hardware_available)

    def _clear_hardware_canvases(self) -> None:
        while self._hardware_canvases:
            widget = self._hardware_canvases.pop()
            try:
                self.hardware_layout.removeWidget(widget)
                widget.deleteLater()
            except Exception:
                pass

    def _ensure_hardware_canvases(self, count: int) -> None:
        while len(self._hardware_canvases) < int(count):
            widget = AcceleratedPlotWidget(self.hardware_panel)
            try:
                widget.set_dark(bool(self._dark_mode_provider() if callable(self._dark_mode_provider) else False))
            except Exception:
                pass
            widget.setMinimumHeight(260)
            self.hardware_layout.addWidget(widget)
            self._hardware_canvases.append(widget)
        while len(self._hardware_canvases) > int(count):
            widget = self._hardware_canvases.pop()
            self.hardware_layout.removeWidget(widget)
            widget.deleteLater()

    def _display_view_payload(self) -> dict | None:
        if not self._display_range:
            return None
        return {
            "xlim": (float(self._display_range["time_start_s"]), float(self._display_range["time_stop_s"])),
            "ylim": (float(self._display_range["freq_min_mhz"]), float(self._display_range["freq_max_mhz"])),
        }

    def _render_matplotlib(self, datasets: list[ComparisonDataset], requested_mode: str):
        self._clear_hardware_canvases()
        self.plot_stack.setCurrentWidget(self.canvas)
        result = render_comparison_figure(
            datasets,
            figure=self.canvas.fig,
            alignment_mode=requested_mode,
            display_range=self._display_range,
            visual=self._visual_payload(),
            color_scale_mode=self.current_color_scale_mode(),
            manual_limits=self._manual_limits(),
        )
        self.canvas.draw_idle()
        return result

    def _render_hardware(self, datasets: list[ComparisonDataset], requested_mode: str):
        self.canvas.fig.clear()
        self._ensure_hardware_canvases(len(datasets))
        self.plot_stack.setCurrentWidget(self.hardware_scroll)
        visual = self._visual_payload()
        payloads, effective_mode, warnings = comparison_panel_payloads(
            datasets,
            alignment_mode=requested_mode,
            visual=visual,
            color_scale_mode=self.current_color_scale_mode(),
            manual_limits=self._manual_limits(),
        )
        cmap = comparison_cmap(str(visual.get("cmap") or "Custom"))
        view = self._display_view_payload()
        all_warnings = list(warnings)
        for payload in payloads:
            all_warnings.extend(payload.dataset.warnings)
            if self._display_range:
                x0, x1, y0, y1 = payload.mpl_extent
                rx0 = float(self._display_range["time_start_s"])
                rx1 = float(self._display_range["time_stop_s"])
                ry0 = float(self._display_range["freq_min_mhz"])
                ry1 = float(self._display_range["freq_max_mhz"])
                x_overlap = min(max(x0, x1), rx1) - max(min(x0, x1), rx0)
                y_overlap = min(max(y0, y1), ry1) - max(min(y0, y1), ry0)
                if x_overlap <= 0.0 or y_overlap <= 0.0:
                    all_warnings.append(f"{payload.dataset.label}: no data inside the locked display range.")
        x_label = "Time [UT]" if effective_mode == TIME_ALIGNMENT_UT else "Time [s]"
        unit_label = "Intensity [dB]" if bool(visual.get("use_db", False)) else "Intensity [Digits]"
        for widget, payload in zip(self._hardware_canvases, payloads):
            try:
                widget.set_dark(bool(self._dark_mode_provider() if callable(self._dark_mode_provider) else False))
            except Exception:
                pass
            widget.update_image(
                payload.display_data,
                extent=payload.pg_extent,
                cmap=cmap,
                gap_row_mask=payload.dataset.gap_row_mask,
                levels=payload.levels,
                title=payload.dataset.label,
                x_label=x_label,
                y_label="Frequency [MHz]",
                colorbar_label=unit_label,
                view=view,
            )
            widget.set_time_mode(effective_mode == TIME_ALIGNMENT_UT, 0.0)
        return SimpleNamespace(
            warnings=tuple(dict.fromkeys(all_warnings)),
            effective_alignment_mode=effective_mode,
        )

    def _render_now(self) -> None:
        self._sync_actions()
        active = self._active_datasets()
        if not active:
            self.canvas.fig.clear()
            self._clear_hardware_canvases()
            ax = self.canvas.fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.5, "Add FITS files to build a comparison.", ha="center", va="center")
            self.plot_stack.setCurrentWidget(self.canvas)
            self.canvas.draw_idle()
            self.status_label.setText("Add FITS files to build a comparison.")
            return

        requested_mode = self.current_alignment_mode()
        if requested_mode == TIME_ALIGNMENT_UT and not self._all_files_have_ut():
            self._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
            requested_mode = TIME_ALIGNMENT_SECONDS

        try:
            if self._use_hardware_view():
                result = self._render_hardware(active, requested_mode)
            else:
                result = self._render_matplotlib(active, requested_mode)
        except Exception as exc:
            self.canvas.fig.clear()
            self._clear_hardware_canvases()
            self.plot_stack.setCurrentWidget(self.canvas)
            ax = self.canvas.fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.5, f"Could not render comparison:\n{exc}", ha="center", va="center")
            self.canvas.draw_idle()
            self.status_label.setText(f"Could not render comparison: {exc}")
            return

        self._last_render_warnings = result.warnings
        panel_count = len(active)
        parts = [f"{panel_count} rendered panel(s) from {len(self._datasets)} selected file(s)."]
        if self._combined_mode:
            parts.append(f"Combined {self._combined_mode} view.")
        parts.append("Hardware-accelerated." if self._use_hardware_view() else "Matplotlib.")
        if self._display_range:
            parts.append("Locked display range.")
        if result.effective_alignment_mode == TIME_ALIGNMENT_UT:
            parts.append("UT aligned.")
        else:
            parts.append("Seconds aligned.")
        if result.warnings:
            parts.append(f"{len(result.warnings)} warning(s).")
        self.status_label.setText(" ".join(parts))
        self._sync_actions()

    def _current_full_extent(self) -> tuple[tuple[float, float], tuple[float, float], str] | None:
        active = self._active_datasets()
        if not active:
            return None
        try:
            xlim, ylim, effective_mode, warnings = shared_extent(active, self.current_alignment_mode())
        except Exception:
            return None
        if warnings and effective_mode == TIME_ALIGNMENT_SECONDS and self.current_alignment_mode() == TIME_ALIGNMENT_UT:
            self._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
        return xlim, ylim, effective_mode

    def open_display_range_dialog(self) -> None:
        full = self._current_full_extent()
        if full is None:
            QMessageBox.information(self, "Set Comparison Range", "Add at least one FITS file before setting the range.")
            return
        full_xlim, full_ylim, effective_mode = full
        current = self._display_range
        time_start = float(current["time_start_s"]) if current else float(full_xlim[0])
        time_stop = float(current["time_stop_s"]) if current else float(full_xlim[1])
        freq_min = float(current["freq_min_mhz"]) if current else float(full_ylim[0])
        freq_max = float(current["freq_max_mhz"]) if current else float(full_ylim[1])

        dlg = DisplayRangeDialog(
            time_min_s=full_xlim[0],
            time_max_s=full_xlim[1],
            freq_min_mhz=full_ylim[0],
            freq_max_mhz=full_ylim[1],
            initial_time_start_s=time_start,
            initial_time_stop_s=time_stop,
            initial_freq_start_mhz=freq_min,
            initial_freq_stop_mhz=freq_max,
            ut_start_sec=0.0 if effective_mode == TIME_ALIGNMENT_UT else None,
            initial_mode="ut" if effective_mode == TIME_ALIGNMENT_UT else "seconds",
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        if dlg.uses_ut():
            ut_start, ut_stop = dlg.ut_seconds_of_day_range()
            converted = seconds_of_day_range_to_unwrapped(ut_start, ut_stop, full_xlim)
            if converted is None:
                QMessageBox.warning(self, "Set Comparison Range", "Enter a valid UT start and stop time for this comparison.")
                return
            time_start, time_stop = converted
        else:
            time_start, time_stop = dlg.seconds_range()
        freq_start, freq_stop = dlg.frequency_range()
        try:
            self._display_range = normalize_display_range(
                {
                    "time_start_s": time_start,
                    "time_stop_s": time_stop,
                    "freq_min_mhz": freq_start,
                    "freq_max_mhz": freq_stop,
                }
            )
        except Exception as exc:
            QMessageBox.warning(self, "Set Comparison Range", str(exc))
            return
        self._sync_actions()
        self.schedule_redraw()

    def reset_display_range(self) -> None:
        self._display_range = None
        self._sync_actions()
        self.schedule_redraw()

    def _set_colormap(self, name: str) -> None:
        text = str(name or "Custom")
        idx = self.colormap_combo.findText(text)
        if idx < 0:
            self.colormap_combo.addItem(text)
            idx = self.colormap_combo.findText(text)
        self.colormap_combo.setCurrentIndex(max(0, idx))

    def _apply_view_config_payload(
        self,
        config: dict,
        *,
        apply_range: bool = True,
        show_status: bool = True,
    ) -> bool:
        try:
            normalized = normalize_view_config(config)
        except Exception as exc:
            if show_status:
                QMessageBox.warning(self, "Load View Config", str(exc))
            return False

        visual = dict(normalized.get("visual") or {})
        self.units_combo.setCurrentIndex(1 if bool(visual.get("use_db", False)) else 0)
        self._set_colormap(str(visual.get("cmap") or "Custom"))

        low = float(visual.get("noise_clip_low", self.manual_low_spin.value()))
        high = float(visual.get("noise_clip_high", self.manual_high_spin.value()))
        self.manual_low_spin.setValue(low)
        self.manual_high_spin.setValue(high)
        if abs(high - low) > 1e-9 and (abs(high) > 1e-9 or abs(low) > 1e-9):
            idx = self.color_scale_combo.findData(COLOR_SCALE_MANUAL)
            if idx >= 0:
                self.color_scale_combo.setCurrentIndex(idx)

        range_payload = normalized.get("range")
        if apply_range and isinstance(range_payload, dict):
            if self.current_alignment_mode() == TIME_ALIGNMENT_SECONDS:
                self._display_range = normalize_display_range(range_payload)
            elif show_status:
                self.status_label.setText("View config visual settings loaded. Set UT bounds from the comparison range dialog.")

        self._sync_actions()
        self.schedule_redraw()
        return True

    def _load_view_config_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load View Config", "", _VIEW_CONFIG_FILTER)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                config = parse_view_config_json(handle.read())
        except Exception as exc:
            QMessageBox.warning(self, "Load View Config", f"Could not read view config:\n{exc}")
            return
        if self._apply_view_config_payload(config, apply_range=True):
            self.status_label.setText(f"Loaded view config: {os.path.basename(path)}")

    @staticmethod
    def _normalize_export_ext(ext_value: str) -> str:
        text = str(ext_value or "").strip().lower()
        if text.startswith("."):
            text = text[1:]
        if "*." in text:
            text = text.split("*.", 1)[1].split(")", 1)[0].strip().lower()
        return text or "png"

    @staticmethod
    def _qt_image_format_for_ext(ext: str) -> str:
        normalized = str(ext or "png").lower().lstrip(".")
        mapping = {
            "png": "PNG",
            "tif": "TIFF",
            "tiff": "TIFF",
            "jpg": "JPG",
            "jpeg": "JPG",
            "bmp": "BMP",
            "webp": "WEBP",
        }
        return mapping.get(normalized, normalized.upper() or "PNG")

    @staticmethod
    def _qimage_to_png_bytes(image: QImage) -> bytes:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        return bytes(data)

    @staticmethod
    def _qimage_to_rgba_array(image: QImage) -> np.ndarray:
        rgba = image.convertToFormat(QImage.Format_RGBA8888)
        width = int(rgba.width())
        height = int(rgba.height())
        ptr = rgba.bits()
        arr = np.frombuffer(ptr, dtype=np.uint8, count=int(rgba.sizeInBytes()))
        arr = arr.reshape((height, int(rgba.bytesPerLine()) // 4, 4))
        return arr[:, :width, :].copy()

    def _image_looks_blank(self, image: QImage) -> bool:
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return True
        try:
            rgba = self._qimage_to_rgba_array(image)
        except Exception:
            return False
        if rgba.size == 0:
            return True
        step_y = max(1, rgba.shape[0] // 300)
        step_x = max(1, rgba.shape[1] // 300)
        sample = rgba[::step_y, ::step_x, :3].astype(np.float32)
        if sample.size == 0:
            return True
        channel_range = float(np.nanmax(sample) - np.nanmin(sample))
        channel_std = float(np.nanstd(sample))
        mean = float(np.nanmean(sample))
        # Blank failed OpenGL captures are usually a single dark gray surface.
        return bool(mean < 80.0 and channel_range < 4.0 and channel_std < 1.5)

    def _capture_widget_image(self, widget) -> QImage:
        if widget is None:
            return QImage()
        QApplication.processEvents()

        viewport = None
        graphics = getattr(widget, "_graphics", None)
        if graphics is not None:
            try:
                viewport = graphics.viewport()
            except Exception:
                viewport = None

        candidates = []
        if viewport is not None:
            try:
                grab_framebuffer = getattr(viewport, "grabFramebuffer", None)
                if callable(grab_framebuffer):
                    candidates.append(grab_framebuffer())
            except Exception:
                pass
            try:
                pixmap = viewport.grab()
                if not pixmap.isNull():
                    candidates.append(pixmap.toImage())
            except Exception:
                pass
            try:
                size = viewport.size()
                if size.width() > 0 and size.height() > 0:
                    image = QImage(size, QImage.Format_ARGB32)
                    image.fill(Qt.transparent)
                    painter = QPainter(image)
                    try:
                        viewport.render(painter)
                    finally:
                        painter.end()
                    candidates.append(image)
            except Exception:
                pass
        try:
            pixmap = widget.grab()
            if not pixmap.isNull():
                candidates.append(pixmap.toImage())
        except Exception:
            pass

        for image in candidates:
            if isinstance(image, QImage) and not image.isNull() and not self._image_looks_blank(image):
                return image
        for image in candidates:
            if isinstance(image, QImage) and not image.isNull():
                return image
        return QImage()

    def _export_hardware_panel_to_image(self, widget: AcceleratedPlotWidget) -> QImage:
        plot_item = widget.export_plot_item() if widget is not None else None
        if plot_item is not None:
            try:
                import pyqtgraph.exporters as pg_exporters
            except Exception:
                pg_exporters = None
            if pg_exporters is not None:
                temp_png = ""
                try:
                    fd, temp_png = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    exporter = pg_exporters.ImageExporter(plot_item)
                    try:
                        params = exporter.parameters()
                        width = max(1, int(widget.width() * widget.devicePixelRatioF()))
                        params["width"] = max(width, 1400)
                    except Exception:
                        pass
                    exporter.export(temp_png)
                    image = QImage(temp_png)
                    if not image.isNull() and not self._image_looks_blank(image):
                        return image
                except Exception:
                    pass
                finally:
                    if temp_png:
                        try:
                            os.remove(temp_png)
                        except Exception:
                            pass

        image = self._capture_widget_image(widget)
        if not image.isNull() and not self._image_looks_blank(image):
            return image
        return QImage()

    def _compose_hardware_panel_images(self) -> QImage:
        images: list[QImage] = []
        for widget in self._hardware_canvases:
            if not widget.isVisible():
                continue
            image = self._export_hardware_panel_to_image(widget)
            if not image.isNull():
                images.append(image.convertToFormat(QImage.Format_ARGB32))
        if not images:
            return QImage()
        width = max(int(image.width()) for image in images)
        height = sum(int(image.height()) for image in images)
        if width <= 0 or height <= 0:
            return QImage()
        composed = QImage(width, height, QImage.Format_ARGB32)
        composed.fill(Qt.white)
        painter = QPainter(composed)
        try:
            y = 0
            for image in images:
                x = (width - int(image.width())) // 2
                painter.drawImage(x, y, image)
                y += int(image.height())
        finally:
            painter.end()
        return composed

    def _capture_visible_plot_image(self) -> QImage:
        if self._redraw_timer.isActive():
            self._redraw_timer.stop()
            if self._datasets:
                self._render_now()
        QApplication.processEvents()
        if self.plot_stack.currentWidget() is self.hardware_scroll or self._hardware_canvases:
            image = self._compose_hardware_panel_images()
            if not image.isNull() and not self._image_looks_blank(image):
                return image
        widget = self.plot_stack.currentWidget() or self.plot_area
        try:
            pixmap = self.plot_area.grab()
            if not pixmap.isNull() and not self._image_looks_blank(pixmap.toImage()):
                return pixmap.toImage()
        except Exception:
            pass
        try:
            pixmap = widget.grab()
            if not pixmap.isNull() and not self._image_looks_blank(pixmap.toImage()):
                return pixmap.toImage()
        except Exception:
            pass
        return QImage()

    def _save_visible_image_as_pdf(self, image: QImage, file_path: str) -> None:
        writer = QPdfWriter(file_path)
        writer.setResolution(300)
        painter = QPainter(writer)
        try:
            target = writer.pageLayout().paintRectPixels(writer.resolution())
            scaled = image.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = target.x() + (target.width() - scaled.width()) // 2
            y = target.y() + (target.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        finally:
            painter.end()

    def _save_visible_image_as_eps(self, image: QImage, file_path: str) -> None:
        rgba = self._qimage_to_rgba_array(image)
        fig = Figure(figsize=(rgba.shape[1] / 300.0, rgba.shape[0] / 300.0), dpi=300)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.imshow(rgba)
        ax.axis("off")
        fig.savefig(file_path, dpi=300, bbox_inches="tight", pad_inches=0, format="eps")

    def _save_visible_image_as_svg(self, image: QImage, file_path: str) -> None:
        png = base64.b64encode(self._qimage_to_png_bytes(image)).decode("ascii")
        width = max(1, int(image.width()))
        height = max(1, int(image.height()))
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">\n'
            f'<image width="{width}" height="{height}" href="data:image/png;base64,{png}"/>\n'
            "</svg>\n"
        )
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(svg)

    def _export_visible_plot(self, file_path: str, ext: str) -> None:
        image = self._capture_visible_plot_image()
        if image.isNull():
            raise RuntimeError("Could not capture the visible comparison plot.")

        ext_final = self._normalize_export_ext(ext)
        if ext_final in {"png", "tif", "tiff", "jpg", "jpeg", "bmp", "webp"}:
            image_format = self._qt_image_format_for_ext(ext_final)
            if not image.save(file_path, image_format):
                raise RuntimeError(f"Failed to save image as {ext_final}.")
            return
        if ext_final == "pdf":
            self._save_visible_image_as_pdf(image, file_path)
            return
        if ext_final == "eps":
            self._save_visible_image_as_eps(image, file_path)
            return
        if ext_final == "svg":
            self._save_visible_image_as_svg(image, file_path)
            return
        if not image.save(file_path, self._qt_image_format_for_ext(ext_final)):
            raise RuntimeError(f"Unsupported export format: {ext_final}.")

    def export_png(self) -> None:
        if len(self._datasets) < 2:
            QMessageBox.information(self, "Export Comparison", "Add at least two FITS files before exporting.")
            return
        path, ext = pick_export_path(
            self,
            "Export Comparison",
            "multi_station_comparison",
            _EXPORT_FILTERS,
            default_filter="PNG (*.png)",
        )
        if not path:
            return

        try:
            root, current_ext = os.path.splitext(path)
            ext_final = self._normalize_export_ext(current_ext or ext)
            if not current_ext:
                path = f"{path}.{ext_final}"
            self._export_visible_plot(str(Path(path)), ext_final)
        except Exception as exc:
            QMessageBox.critical(self, "Export Comparison Failed", f"Could not export comparison image:\n{exc}")
            return
        QMessageBox.information(self, "Export Comparison", f"Comparison image saved:\n{path}")
