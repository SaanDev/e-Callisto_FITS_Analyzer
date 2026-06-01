"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QSplitter,
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
    export_comparison_png,
    load_comparison_dataset,
    render_comparison_figure,
    seconds_of_day_range_to_unwrapped,
    shared_extent,
)
from src.Backend.view_config import normalize_display_range, normalize_view_config, parse_view_config_json
from src.UI.dialogs.display_range_dialog import DisplayRangeDialog
from src.UI.gui_shared import MplCanvas


_FITS_FILTER = "FITS files (*.fit *.fits *.fit.gz *.fits.gz)"
_VIEW_CONFIG_FILTER = "e-CALLISTO View Config (*.efaview.json);;JSON Files (*.json)"


class MultiStationComparisonDialog(QDialog):
    def __init__(
        self,
        *,
        initial_paths: list[str] | None = None,
        view_config_provider: Callable[..., dict | None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Multi-Station Comparison")
        self.setModal(False)
        self.resize(1180, 760)

        self._view_config_provider = view_config_provider
        self._datasets: list[ComparisonDataset] = []
        self._display_range: dict[str, float] | None = None
        self._user_changed_alignment = False
        self._last_render_warnings: tuple[str, ...] = tuple()

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
        self.export_btn = QPushButton("Export PNG...", self)

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

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 860])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._apply_initial_view_config()
        self._on_color_scale_changed()
        self._sync_actions()
        if initial_paths:
            self.add_files(initial_paths)

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
        self._rebuild_file_list()
        self._choose_default_alignment_after_load()
        self._sync_actions()
        self.schedule_redraw()

    def clear_files(self) -> None:
        self._datasets.clear()
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

    def _choose_default_alignment_after_load(self) -> None:
        if self._user_changed_alignment:
            if self.current_alignment_mode() == TIME_ALIGNMENT_UT and not self._all_files_have_ut():
                self._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
                self.status_label.setText("UT alignment requires TIME-OBS in every selected file. Switched to seconds.")
            return
        self._set_alignment_mode(TIME_ALIGNMENT_UT if self._datasets and self._all_files_have_ut() else TIME_ALIGNMENT_SECONDS)

    def _all_files_have_ut(self) -> bool:
        return bool(self._datasets) and all(dataset.ut_start_sec is not None for dataset in self._datasets)

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

    def _render_now(self) -> None:
        self._sync_actions()
        if not self._datasets:
            self.canvas.fig.clear()
            ax = self.canvas.fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.5, "Add FITS files to build a comparison.", ha="center", va="center")
            self.canvas.draw_idle()
            self.status_label.setText("Add FITS files to build a comparison.")
            return

        requested_mode = self.current_alignment_mode()
        if requested_mode == TIME_ALIGNMENT_UT and not self._all_files_have_ut():
            self._set_alignment_mode(TIME_ALIGNMENT_SECONDS)
            requested_mode = TIME_ALIGNMENT_SECONDS

        try:
            result = render_comparison_figure(
                self._datasets,
                figure=self.canvas.fig,
                alignment_mode=requested_mode,
                display_range=self._display_range,
                visual=self._visual_payload(),
                color_scale_mode=self.current_color_scale_mode(),
                manual_limits=self._manual_limits(),
            )
        except Exception as exc:
            self.canvas.fig.clear()
            ax = self.canvas.fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.5, f"Could not render comparison:\n{exc}", ha="center", va="center")
            self.canvas.draw_idle()
            self.status_label.setText(f"Could not render comparison: {exc}")
            return

        self.canvas.draw_idle()
        self._last_render_warnings = result.warnings
        parts = [f"{len(self._datasets)} station/file panel(s)."]
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
        if not self._datasets:
            return None
        try:
            xlim, ylim, effective_mode, warnings = shared_extent(self._datasets, self.current_alignment_mode())
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

    def export_png(self) -> None:
        if len(self._datasets) < 2:
            QMessageBox.information(self, "Export Comparison PNG", "Add at least two FITS files before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Comparison PNG", "multi_station_comparison.png", "PNG Image (*.png)")
        if not path:
            return
        if not str(path).lower().endswith(".png"):
            path = f"{path}.png"

        try:
            result = export_comparison_png(
                self._datasets,
                str(Path(path)),
                alignment_mode=self.current_alignment_mode(),
                display_range=self._display_range,
                visual=self._visual_payload(),
                color_scale_mode=self.current_color_scale_mode(),
                manual_limits=self._manual_limits(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Comparison PNG Failed", f"Could not export comparison PNG:\n{exc}")
            return
        message = f"Comparison PNG saved:\n{path}"
        if result.warnings:
            message += "\n\nWarnings:\n" + "\n".join(result.warnings[:8])
        QMessageBox.information(self, "Export Comparison PNG", message)
