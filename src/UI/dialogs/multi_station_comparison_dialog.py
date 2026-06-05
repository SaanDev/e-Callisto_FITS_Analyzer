"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
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
    QCheckBox,
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
    QSlider,
    QSizePolicy,
    QSplitter,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.Backend.multi_station_comparison import (
    COLOR_SCALE_MANUAL,
    COLOR_SCALE_PER_STATION,
    COLOR_SCALE_SHARED,
    DEFAULT_DB_SCALE,
    NOISE_METHOD_CLIP,
    NOISE_METHOD_MEAN,
    NOISE_METHOD_MEDIAN,
    NOISE_METHOD_NONE,
    NOISE_METHOD_ROBUST,
    TIME_ALIGNMENT_SECONDS,
    TIME_ALIGNMENT_UT,
    ComparisonDataset,
    ComparisonNoiseSettings,
    ComparisonPanelPayload,
    apply_comparison_noise,
    combined_comparison_datasets_from_paths,
    comparison_cmap,
    comparison_panel_payloads,
    load_comparison_dataset,
    normalize_comparison_noise_settings,
    render_comparison_figure,
    seconds_of_day_range_to_unwrapped,
    shared_extent,
)
from src.Backend.frequency_axis import masked_display_data
from src.Backend.measurements import MeasurementResult, calculate_two_point_measurement
from src.Backend.view_config import normalize_display_range, normalize_view_config, parse_view_config_json
from src.UI.accelerated_plot_widget import AcceleratedPlotWidget
from src.UI.dialogs.display_range_dialog import DisplayRangeDialog
from src.UI.gui_shared import MplCanvas, pick_export_path
from src.UI.widgets.measurement_readout import MeasurementReadout


_FITS_FILTER = "FITS files (*.fit *.fits *.fit.gz *.fits.gz)"
_VIEW_CONFIG_FILTER = "e-CALLISTO View Config (*.efaview.json);;JSON Files (*.json)"
_EXPORT_FILTERS = "PNG (*.png);;PDF (*.pdf);;EPS (*.eps);;SVG (*.svg);;TIFF (*.tiff)"
_NOISE_TARGET_ALL = "__all__"
_NOISE_SLIDER_STEPS = 4000
_NOISE_SLIDER_MID = _NOISE_SLIDER_STEPS // 2
_NOISE_CLIP_MIN = -100.0
_NOISE_CLIP_MAX = 100.0
_NOISE_CLIP_SCALE_LINEAR = "linear"
_NOISE_CLIP_SCALE_SIGNED_LOG = "signed_log"
_COLORMAP_NAMES = (
    "Custom",
    "viridis",
    "plasma",
    "inferno",
    "magma",
    "cividis",
    "turbo",
    "RdYlBu",
    "jet",
    "cubehelix",
    "gray",
    "bone_r",
)


@dataclass
class _VisiblePanelState:
    payload: ComparisonPanelPayload
    settings: ComparisonNoiseSettings


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
        self._noise_all_settings = ComparisonNoiseSettings()
        self._noise_overrides: dict[tuple[str, ...], ComparisonNoiseSettings] = {}
        self._noise_slider_min = _NOISE_CLIP_MIN
        self._noise_slider_max = _NOISE_CLIP_MAX
        self._noise_sync_guard = False
        self._noise_base_cache: dict[tuple, np.ndarray] = {}
        self._colormap_sync_guard = False
        self._visible_panel_states: dict[tuple[str, ...], _VisiblePanelState] = {}
        self._visible_panel_order: list[tuple[str, ...]] = []
        self._visible_effective_alignment_mode = TIME_ALIGNMENT_SECONDS
        self._mpl_axes_by_key: dict[tuple[str, ...], object] = {}
        self._mpl_images_by_key: dict[tuple[str, ...], object] = {}
        self._measurement_results: dict[tuple[str, ...], MeasurementResult] = {}
        self._measurement_capture_key: tuple[str, ...] | None = None
        self._measurement_capture_points: list[tuple[float, float]] = []
        self._measurement_mpl_cid = None
        self._measurement_artists_by_key: dict[tuple[str, ...], list[object]] = {}

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(35)
        self._redraw_timer.timeout.connect(self._render_now)

        self.file_list = QListWidget(self)
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.itemSelectionChanged.connect(self._on_file_selection_changed)

        self.add_btn = QPushButton("Add Files...", self)
        self.remove_btn = QPushButton("Remove", self)
        self.clear_btn = QPushButton("Clear", self)

        self.add_btn.clicked.connect(self._choose_files)
        self.remove_btn.clicked.connect(self.remove_selected_files)
        self.clear_btn.clicked.connect(self.clear_files)

        file_buttons = QHBoxLayout()
        file_buttons.setSpacing(6)
        for button in (self.add_btn, self.remove_btn, self.clear_btn):
            button.setMinimumHeight(34)
            file_buttons.addWidget(button)

        file_panel = QGroupBox("Stations / Files", self)
        file_layout = QVBoxLayout(file_panel)
        file_layout.setSpacing(8)
        self.file_list.setMinimumHeight(96)
        self.file_list.setMaximumHeight(150)
        self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        file_layout.addWidget(self.file_list)
        file_layout.addLayout(file_buttons)

        self.alignment_combo = QComboBox(self)
        self.alignment_combo.addItem("UT clock", TIME_ALIGNMENT_UT)
        self.alignment_combo.addItem("Seconds from file start", TIME_ALIGNMENT_SECONDS)
        self.alignment_combo.currentIndexChanged.connect(self._on_alignment_changed)

        self.units_combo = QComboBox(self)
        self.units_combo.addItem("Digits", False)
        self.units_combo.addItem("dB", True)
        self.units_combo.currentIndexChanged.connect(self._on_units_changed)

        self.colormap_combo = QComboBox(self)
        for name in _COLORMAP_NAMES:
            self.colormap_combo.addItem(name)
        self.colormap_combo.currentIndexChanged.connect(self._on_colormap_changed)

        self.color_scale_combo = QComboBox(self)
        self.color_scale_combo.addItem("Shared scale", COLOR_SCALE_SHARED)
        self.color_scale_combo.addItem("Per-station auto scale", COLOR_SCALE_PER_STATION)
        self.color_scale_combo.addItem("Manual scale", COLOR_SCALE_MANUAL)
        self.color_scale_combo.currentIndexChanged.connect(self._on_color_scale_changed)

        self.manual_low_spin = self._manual_spin(-10.0)
        self.manual_high_spin = self._manual_spin(10.0)
        self.manual_low_spin.valueChanged.connect(self.schedule_redraw)
        self.manual_high_spin.valueChanged.connect(self.schedule_redraw)

        self.noise_target_combo = QComboBox(self)
        self.noise_target_combo.addItem("All panels", _NOISE_TARGET_ALL)
        self.noise_target_combo.currentIndexChanged.connect(self._on_noise_target_changed)

        self.noise_method_combo = QComboBox(self)
        self.noise_method_combo.addItem("None", NOISE_METHOD_NONE)
        self.noise_method_combo.addItem("Mean background", NOISE_METHOD_MEAN)
        self.noise_method_combo.addItem("Median background", NOISE_METHOD_MEDIAN)
        self.noise_method_combo.addItem("Robust background", NOISE_METHOD_ROBUST)
        self.noise_method_combo.addItem("Noise clipping", NOISE_METHOD_CLIP)
        self.noise_method_combo.currentIndexChanged.connect(self._on_noise_method_changed)

        self.noise_colormap_combo = QComboBox(self)
        for name in _COLORMAP_NAMES:
            self.noise_colormap_combo.addItem(name)
        self.noise_colormap_combo.currentIndexChanged.connect(self._on_noise_colormap_changed)

        self.noise_low_slider = QSlider(Qt.Horizontal, self)
        self.noise_low_slider.setRange(0, _NOISE_SLIDER_STEPS)
        self.noise_low_slider.valueChanged.connect(self._on_noise_slider_changed)
        self.noise_high_slider = QSlider(Qt.Horizontal, self)
        self.noise_high_slider.setRange(0, _NOISE_SLIDER_STEPS)
        self.noise_high_slider.valueChanged.connect(self._on_noise_slider_changed)
        self.noise_low_value_label = QLabel("", self)
        self.noise_high_value_label = QLabel("", self)
        self.noise_low_sub_value_label = QLabel("", self)
        self.noise_high_sub_value_label = QLabel("", self)
        self.noise_low_sub_value_label.setVisible(False)
        self.noise_high_sub_value_label.setVisible(False)
        self.noise_log_scale_chk = QCheckBox("Logarithmic Threshold Scale", self)
        self.noise_log_scale_chk.toggled.connect(self._on_noise_scale_mode_toggled)

        self.noise_clip_panel = QWidget(self)
        noise_clip_layout = QVBoxLayout(self.noise_clip_panel)
        noise_clip_layout.setContentsMargins(0, 0, 0, 0)
        low_clip_row = QHBoxLayout()
        low_clip_row.addWidget(QLabel("Lower threshold", self))
        low_value_col = QVBoxLayout()
        low_value_col.setSpacing(0)
        low_value_col.addWidget(self.noise_low_value_label)
        low_value_col.addWidget(self.noise_low_sub_value_label)
        low_clip_row.addLayout(low_value_col)
        noise_clip_layout.addLayout(low_clip_row)
        noise_clip_layout.addWidget(self.noise_low_slider)
        high_clip_row = QHBoxLayout()
        high_clip_row.addWidget(QLabel("Upper threshold", self))
        high_value_col = QVBoxLayout()
        high_value_col.setSpacing(0)
        high_value_col.addWidget(self.noise_high_value_label)
        high_value_col.addWidget(self.noise_high_sub_value_label)
        high_clip_row.addLayout(high_value_col)
        noise_clip_layout.addLayout(high_clip_row)
        noise_clip_layout.addWidget(self.noise_high_slider)
        noise_clip_layout.addWidget(self.noise_log_scale_chk)

        self.set_range_btn = QPushButton("Set Range...", self)
        self.reset_range_btn = QPushButton("Reset Range", self)
        self.load_config_btn = QPushButton("Load View Config...", self)
        self.export_btn = QPushButton("Export...", self)
        self.measure_start_btn = QPushButton("Start Ruler", self)
        self.measure_clear_btn = QPushButton("Clear", self)

        self.set_range_btn.clicked.connect(self.open_display_range_dialog)
        self.reset_range_btn.clicked.connect(self.reset_display_range)
        self.load_config_btn.clicked.connect(self._load_view_config_file)
        self.export_btn.clicked.connect(self.export_png)
        self.measure_start_btn.clicked.connect(self.start_ruler_measurement)
        self.measure_clear_btn.clicked.connect(self.clear_ruler_measurements)

        self.status_label = QLabel("Add FITS files to build a comparison.", self)
        self.status_label.setWordWrap(True)
        self.measurement_readout = MeasurementReadout("Readout", self)
        self.measurement_readout.clearRequested.connect(self.clear_ruler_measurements)

        view_group = QGroupBox("View", self)
        view_layout = QVBoxLayout(view_group)
        view_layout.setSpacing(6)
        view_layout.addWidget(QLabel("Time alignment", self))
        view_layout.addWidget(self.alignment_combo)

        appearance_group = QGroupBox("Appearance", self)
        appearance_layout = QVBoxLayout(appearance_group)
        appearance_layout.setSpacing(6)
        appearance_layout.addWidget(QLabel("Units", self))
        appearance_layout.addWidget(self.units_combo)
        appearance_layout.addWidget(QLabel("Colormap", self))
        appearance_layout.addWidget(self.colormap_combo)
        appearance_layout.addWidget(QLabel("Color scale", self))
        appearance_layout.addWidget(self.color_scale_combo)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(6)
        manual_row.addWidget(QLabel("Low", self))
        manual_row.addWidget(self.manual_low_spin)
        manual_row.addWidget(QLabel("High", self))
        manual_row.addWidget(self.manual_high_spin)
        appearance_layout.addLayout(manual_row)

        noise_group = QGroupBox("Noise Reduction", self)
        noise_layout = QVBoxLayout(noise_group)
        noise_layout.setSpacing(6)
        noise_layout.addWidget(QLabel("Target", self))
        noise_layout.addWidget(self.noise_target_combo)
        noise_layout.addWidget(QLabel("Method", self))
        noise_layout.addWidget(self.noise_method_combo)
        noise_layout.addWidget(QLabel("Colormap", self))
        noise_layout.addWidget(self.noise_colormap_combo)
        noise_layout.addWidget(self.noise_clip_panel)

        measurement_group = QGroupBox("Measurement", self)
        measurement_layout = QVBoxLayout(measurement_group)
        measurement_layout.setSpacing(6)
        measure_button_row = QHBoxLayout()
        measure_button_row.setSpacing(6)
        measure_button_row.addWidget(self.measure_start_btn)
        measure_button_row.addWidget(self.measure_clear_btn)
        measurement_layout.addLayout(measure_button_row)
        measurement_layout.addWidget(self.measurement_readout)

        actions_group = QGroupBox("Actions", self)
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setSpacing(6)
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        range_row.addWidget(self.set_range_btn)
        range_row.addWidget(self.reset_range_btn)
        actions_layout.addLayout(range_row)
        actions_layout.addWidget(self.load_config_btn)
        actions_layout.addWidget(self.export_btn)
        actions_layout.addWidget(self.status_label)

        controls_content = QWidget(self)
        controls_layout = QVBoxLayout(controls_content)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        controls_layout.addWidget(view_group)
        controls_layout.addWidget(appearance_group)
        controls_layout.addWidget(noise_group)
        controls_layout.addWidget(measurement_group)
        controls_layout.addWidget(actions_group)
        controls_layout.addStretch(1)

        controls_scroll = QScrollArea(self)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QScrollArea.NoFrame)
        controls_scroll.setWidget(controls_content)

        left_panel = QWidget(self)
        left_panel.setMinimumWidth(360)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        left_layout.addWidget(file_panel)
        left_layout.addWidget(controls_scroll, 1)

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
        splitter.setSizes([380, 1020])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._apply_initial_view_config()
        self._rebuild_noise_targets()
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

        if added:
            self._invalidate_noise_base_cache()
            self._invalidate_visible_panel_cache()
        self._refresh_display_datasets()
        self._rebuild_noise_targets()
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
        self._invalidate_noise_base_cache()
        self._invalidate_visible_panel_cache()
        self._display_range = None
        self._refresh_display_datasets()
        self._rebuild_noise_targets()
        self._rebuild_file_list()
        self._choose_default_alignment_after_load()
        self._sync_actions()
        self.schedule_redraw()

    def clear_files(self) -> None:
        self._datasets.clear()
        self._display_datasets.clear()
        self._combined_mode = None
        self._display_range = None
        self.clear_ruler_measurements(reset_readout=True)
        self._invalidate_noise_base_cache()
        self._invalidate_visible_panel_cache()
        self._rebuild_noise_targets()
        self._rebuild_file_list()
        self._sync_actions()
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

    @staticmethod
    def _path_key(path: str) -> str:
        try:
            return os.path.abspath(str(path or ""))
        except Exception:
            return str(path or "")

    @staticmethod
    def _noise_sources_for_dataset(dataset: ComparisonDataset) -> tuple[str, ...]:
        return tuple(str(path) for path in (dataset.sources or ()) if str(path or "").strip())

    @staticmethod
    def _noise_key_for_dataset(dataset: ComparisonDataset) -> tuple[str, ...]:
        sources = MultiStationComparisonDialog._noise_sources_for_dataset(dataset)
        identity = [
            "__comparison_panel__",
            str(dataset.combine_type or "single"),
            str(dataset.label or ""),
            str(dataset.path or ""),
        ]
        identity.extend(sources)
        return tuple(identity)

    def _dataset_sources_for_matching(self, dataset: ComparisonDataset) -> set[str]:
        sources = self._noise_sources_for_dataset(dataset) or (str(dataset.path),)
        out = {self._path_key(path) for path in sources}
        out.add(self._path_key(dataset.path))
        return {path for path in out if path}

    def _noise_key_for_source_path(self, path: str) -> tuple[str, ...] | None:
        target = self._path_key(path)
        if not target:
            return None
        for dataset in self._active_datasets():
            if target in self._dataset_sources_for_matching(dataset):
                return self._noise_key_for_dataset(dataset)
        return None

    def _visible_dataset_for_key(self, key: tuple[str, ...] | None) -> ComparisonDataset | None:
        if key is None:
            return None
        target = tuple(key)
        for dataset in self._active_datasets():
            if self._noise_key_for_dataset(dataset) == target:
                return dataset
        return None

    def _active_panel_keys(self) -> set[tuple[str, ...]]:
        return {self._noise_key_for_dataset(dataset) for dataset in self._active_datasets()}

    def _disconnect_measurement_mpl(self) -> None:
        cid = getattr(self, "_measurement_mpl_cid", None)
        if cid is not None:
            try:
                self.canvas.mpl_disconnect(cid)
            except Exception:
                pass
            self._measurement_mpl_cid = None

    def _clear_measurement_artists(self, key: tuple[str, ...] | None = None) -> None:
        keys = [tuple(key)] if key is not None else list(self._measurement_artists_by_key.keys())
        for item_key in keys:
            for artist in list(self._measurement_artists_by_key.get(item_key, []) or []):
                try:
                    artist.remove()
                except Exception:
                    pass
            self._measurement_artists_by_key.pop(item_key, None)

    def _prune_measurements_for_active_panels(self) -> None:
        valid = self._active_panel_keys()
        self._measurement_results = {key: result for key, result in self._measurement_results.items() if key in valid}
        if self._measurement_capture_key not in valid:
            self._measurement_capture_key = None
            self._measurement_capture_points = []
        for key in list(self._measurement_artists_by_key.keys()):
            if key not in valid:
                self._clear_measurement_artists(key)

    def _format_measurement_time(self, value: float) -> str:
        seconds = float(value)
        if self._visible_effective_alignment_mode == TIME_ALIGNMENT_UT:
            total = int(round(seconds))
            hours = int(total // 3600) % 24
            minutes = int((total % 3600) // 60)
            secs = int(total % 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d} UT"
        return f"{seconds:.3f} s"

    @staticmethod
    def _measurement_points_for_plot(result: MeasurementResult) -> list[tuple[float, float]]:
        return [
            (float(result.point1.time_s), float(result.point1.frequency_mhz)),
            (float(result.point2.time_s), float(result.point2.frequency_mhz)),
        ]

    @staticmethod
    def _measurement_overlay_label(result: MeasurementResult) -> str:
        return (
            f"dt={result.duration_s:.3f} s\n"
            f"df={result.frequency_delta_mhz:.3f} MHz\n"
            f"{result.slope_mhz_s:.6f} MHz/s"
        )

    def _measurement_panel_label(self, key: tuple[str, ...] | None) -> str:
        dataset = self._visible_dataset_for_key(key)
        return str(getattr(dataset, "label", "") or "Panel")

    def _render_measurement_overlays(self) -> None:
        self._prune_measurements_for_active_panels()

        if self._hardware_canvases:
            for widget, key in zip(self._hardware_canvases, self._visible_panel_order):
                result = self._measurement_results.get(key)
                if result is not None:
                    widget.show_measurement(
                        self._measurement_points_for_plot(result),
                        self._measurement_overlay_label(result),
                    )
                elif key == self._measurement_capture_key and self._measurement_capture_points:
                    widget.show_measurement(self._measurement_capture_points)
                else:
                    widget.clear_measurement()

        self._clear_measurement_artists()
        for key, result in self._measurement_results.items():
            ax = self._mpl_axes_by_key.get(key)
            if ax is None:
                continue
            points = self._measurement_points_for_plot(result)
            self._draw_mpl_measurement(key, ax, points, self._measurement_overlay_label(result))

        if self._measurement_capture_key and self._measurement_capture_points:
            ax = self._mpl_axes_by_key.get(self._measurement_capture_key)
            if ax is not None:
                self._draw_mpl_measurement(self._measurement_capture_key, ax, self._measurement_capture_points, "")
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    def _draw_mpl_measurement(self, key: tuple[str, ...], ax, points, label: str) -> None:
        clean = [(float(x), float(y)) for x, y in points if np.isfinite([float(x), float(y)]).all()]
        if not clean:
            return
        xs = [p[0] for p in clean]
        ys = [p[1] for p in clean]
        artists = self._measurement_artists_by_key.setdefault(tuple(key), [])
        try:
            scatter = ax.scatter(
                xs,
                ys,
                marker="o",
                s=42,
                facecolors="white",
                edgecolors="#18b4ff",
                linewidths=1.4,
                zorder=20,
            )
            artists.append(scatter)
            if len(clean) >= 2:
                line, = ax.plot(xs[:2], ys[:2], color="#18b4ff", linewidth=1.7, zorder=19)
                artists.append(line)
                if label:
                    text = ax.text(
                        xs[-1],
                        ys[-1],
                        label,
                        color="white",
                        fontsize=8,
                        ha="left",
                        va="bottom",
                        bbox={"boxstyle": "round,pad=0.22", "facecolor": "#0f172a", "edgecolor": "#18b4ff", "alpha": 0.82},
                        zorder=21,
                    )
                    artists.append(text)
        except Exception:
            self._measurement_artists_by_key.pop(tuple(key), None)

    def _set_measurement_result(self, key: tuple[str, ...], result: MeasurementResult) -> None:
        panel_key = tuple(key)
        self._measurement_results[panel_key] = result
        self._measurement_capture_key = None
        self._measurement_capture_points = []
        self.measurement_readout.set_measurement(
            result,
            title=self._measurement_panel_label(panel_key),
            time_formatter=self._format_measurement_time,
        )
        self._render_measurement_overlays()
        self.status_label.setText(
            f"Measurement for {self._measurement_panel_label(panel_key)}: "
            f"dt={result.duration_s:.3f} s, df={result.frequency_delta_mhz:.3f} MHz, "
            f"slope={result.slope_mhz_s:.6f} MHz/s."
        )
        self._sync_actions()

    def clear_ruler_measurements(self, *_args, reset_readout: bool = True) -> None:
        self._disconnect_measurement_mpl()
        self._measurement_results.clear()
        self._measurement_capture_key = None
        self._measurement_capture_points = []
        self._clear_measurement_artists()
        for widget in list(self._hardware_canvases):
            try:
                widget.clear_measurement()
                widget.stop_interaction_capture()
            except Exception:
                pass
        if reset_readout:
            self.measurement_readout.set_empty()
            self.status_label.setText("Ruler measurement cleared.")
        self._sync_actions()
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    def start_ruler_measurement(self) -> None:
        active = self._active_datasets()
        if not active:
            QMessageBox.information(self, "Ruler Measurement", "Add FITS files first.")
            return
        if not self._visible_cache_is_complete(active):
            self._redraw_timer.stop()
            self._render_now()
        if self._use_hardware_view() and not self._hardware_canvases:
            self._redraw_timer.stop()
            self._render_now()
        self._disconnect_measurement_mpl()
        self._measurement_capture_key = None
        self._measurement_capture_points = []
        self.measurement_readout.set_pending("Click the first point in a comparison panel.")

        if self._use_hardware_view():
            for widget in self._hardware_canvases:
                try:
                    widget.begin_measurement_capture()
                except Exception:
                    pass
            self.status_label.setText("Click two points in one panel to measure duration, frequency drift, and slope.")
            self._sync_actions()
            return

        self._measurement_mpl_cid = self.canvas.mpl_connect("button_press_event", self._on_measurement_mpl_click)
        self.status_label.setText("Click two points in one panel to measure duration, frequency drift, and slope.")
        self._sync_actions()

    def _key_for_mpl_axes(self, axes) -> tuple[str, ...] | None:
        for key, ax in self._mpl_axes_by_key.items():
            if ax is axes:
                return tuple(key)
        return None

    def _on_measurement_mpl_click(self, event) -> None:
        key = self._key_for_mpl_axes(getattr(event, "inaxes", None))
        if key is None:
            return
        if event.button == 3:
            self._measurement_capture_key = None
            self._measurement_capture_points = []
            self.measurement_readout.set_pending("Ruler capture cancelled.")
            self._disconnect_measurement_mpl()
            self._render_measurement_overlays()
            self._sync_actions()
            return
        if event.button not in (None, 1) or event.xdata is None or event.ydata is None:
            return

        point = (float(event.xdata), float(event.ydata))
        if self._measurement_capture_key != key or not self._measurement_capture_points:
            self._measurement_capture_key = key
            self._measurement_capture_points = [point]
            self.measurement_readout.set_pending(
                f"{self._measurement_panel_label(key)}\nClick the second point in the same panel."
            )
            self._render_measurement_overlays()
            return

        points = [self._measurement_capture_points[0], point]
        try:
            result = calculate_two_point_measurement(points[0], points[1])
        except ValueError as exc:
            self._measurement_capture_key = None
            self._measurement_capture_points = []
            self.measurement_readout.set_error(str(exc))
            self.status_label.setText(str(exc))
            self._disconnect_measurement_mpl()
            self._render_measurement_overlays()
            self._sync_actions()
            return

        self._disconnect_measurement_mpl()
        self._set_measurement_result(key, result)

    def _on_hardware_measurement_finished(self, widget: AcceleratedPlotWidget, points) -> None:
        try:
            index = self._hardware_canvases.index(widget)
            key = self._visible_panel_order[index]
        except Exception:
            return
        pts = [(float(x), float(y)) for (x, y) in (points or [])]
        if len(pts) < 2:
            return
        for candidate in self._hardware_canvases:
            if candidate is not widget:
                try:
                    candidate.stop_interaction_capture()
                except Exception:
                    pass
        try:
            result = calculate_two_point_measurement(pts[0], pts[1])
        except ValueError as exc:
            self._measurement_capture_key = None
            self._measurement_capture_points = []
            widget.clear_measurement()
            self.measurement_readout.set_error(str(exc))
            self.status_label.setText(str(exc))
            self._sync_actions()
            return
        self._set_measurement_result(tuple(key), result)

    def _combo_index_for_noise_key(self, key: tuple[str, ...] | None) -> int:
        if key is None:
            return 0
        for idx in range(self.noise_target_combo.count()):
            data = self.noise_target_combo.itemData(idx)
            if data == key or (isinstance(data, (tuple, list)) and tuple(data) == tuple(key)):
                return idx
        return -1

    def _set_noise_target_key(self, key: tuple[str, ...] | None) -> bool:
        idx = self._combo_index_for_noise_key(key)
        if idx < 0:
            return False
        if idx == self.noise_target_combo.currentIndex():
            return True
        self.noise_target_combo.setCurrentIndex(idx)
        return True

    def _on_file_selection_changed(self) -> None:
        items = self.file_list.selectedItems()
        if not items:
            return
        key = self._noise_key_for_source_path(str(items[0].data(Qt.UserRole) or ""))
        if key is not None:
            self._set_noise_target_key(key)

    def _invalidate_visible_panel_cache(self) -> None:
        self._visible_panel_states.clear()
        self._visible_panel_order.clear()
        self._mpl_axes_by_key.clear()
        self._mpl_images_by_key.clear()

    def _current_noise_target_key(self) -> tuple[str, ...] | None:
        if getattr(self, "noise_target_combo", None) is None:
            return None
        data = self.noise_target_combo.currentData()
        if data in (None, _NOISE_TARGET_ALL):
            return None
        if isinstance(data, tuple):
            return tuple(str(item) for item in data)
        if isinstance(data, list):
            return tuple(str(item) for item in data)
        return (str(data),)

    def _noise_settings_for_key(self, key: tuple[str, ...] | None) -> ComparisonNoiseSettings:
        if key is None:
            return self._noise_all_settings
        return self._noise_overrides.get(tuple(key), self._noise_all_settings)

    def _effective_noise_settings(self) -> tuple[ComparisonNoiseSettings, ...]:
        return tuple(self._noise_settings_for_key(self._noise_key_for_dataset(dataset)) for dataset in self._active_datasets())

    def _set_noise_target_settings(self, settings: ComparisonNoiseSettings) -> None:
        normalized = normalize_comparison_noise_settings(settings)
        self._expand_noise_clip_bounds_for_values(normalized.clip_low, normalized.clip_high)
        key = self._current_noise_target_key()
        if key is None:
            self._noise_all_settings = normalized
            self._noise_overrides.clear()
        else:
            self._noise_overrides[tuple(key)] = normalized

    def _rebuild_noise_targets(self) -> None:
        if getattr(self, "noise_target_combo", None) is None:
            return
        current_key = self._current_noise_target_key()
        active = self._active_datasets()
        valid_keys = {self._noise_key_for_dataset(dataset) for dataset in active}
        self._noise_overrides = {key: value for key, value in self._noise_overrides.items() if key in valid_keys}

        blocked = self.noise_target_combo.blockSignals(True)
        try:
            self.noise_target_combo.clear()
            self.noise_target_combo.addItem("All panels", _NOISE_TARGET_ALL)
            for dataset in active:
                key = self._noise_key_for_dataset(dataset)
                self.noise_target_combo.addItem(dataset.label, key)
                idx = self.noise_target_combo.count() - 1
                sources = self._noise_sources_for_dataset(dataset) or (str(dataset.path),)
                if sources:
                    tooltip = "\n".join(os.path.basename(path) for path in sources)
                    self.noise_target_combo.setItemData(idx, tooltip, Qt.ToolTipRole)
            if current_key in valid_keys:
                idx = self.noise_target_combo.findData(current_key)
                if idx >= 0:
                    self.noise_target_combo.setCurrentIndex(idx)
        finally:
            self.noise_target_combo.blockSignals(blocked)
        self._sync_noise_controls_from_target()

    def _set_noise_method_combo(self, method: str) -> None:
        idx = self.noise_method_combo.findData(str(method or NOISE_METHOD_NONE))
        blocked = self.noise_method_combo.blockSignals(True)
        try:
            self.noise_method_combo.setCurrentIndex(max(0, idx))
        finally:
            self.noise_method_combo.blockSignals(blocked)

    def _noise_clip_bounds(self) -> tuple[float, float]:
        try:
            low = float(self._noise_slider_min)
            high = float(self._noise_slider_max)
        except Exception:
            return _NOISE_CLIP_MIN, _NOISE_CLIP_MAX
        if not np.isfinite([low, high]).all() or high <= low:
            return _NOISE_CLIP_MIN, _NOISE_CLIP_MAX
        return float(low), float(high)

    def _expand_noise_clip_bounds_for_values(self, *values) -> None:
        finite_values: list[float] = []
        for value in values:
            try:
                val = float(value)
            except Exception:
                continue
            if np.isfinite(val):
                finite_values.append(float(val))
        if not finite_values:
            return
        cur_min, cur_max = self._noise_clip_bounds()
        cur_abs = max(abs(cur_min), abs(cur_max), 1.0)
        required_abs = max(abs(value) for value in finite_values)
        if required_abs <= cur_abs:
            return
        max_abs = max(required_abs * 1.05, cur_abs)
        self._noise_slider_min = -float(max_abs)
        self._noise_slider_max = float(max_abs)

    def _clamp_noise_threshold(self, value) -> float:
        try:
            out = float(value)
        except Exception:
            out = 0.0
        clip_min, clip_max = self._noise_clip_bounds()
        return float(max(clip_min, min(clip_max, out)))

    @staticmethod
    def _normalize_noise_clip_scale(scale) -> str:
        return (
            _NOISE_CLIP_SCALE_SIGNED_LOG
            if str(scale or "").strip().lower() == _NOISE_CLIP_SCALE_SIGNED_LOG
            else _NOISE_CLIP_SCALE_LINEAR
        )

    def _noise_clip_scale_is_log(self, scale: str | None = None) -> bool:
        normalized = self._normalize_noise_clip_scale(scale if scale is not None else self._noise_settings_for_key(self._current_noise_target_key()).clip_scale)
        return normalized == _NOISE_CLIP_SCALE_SIGNED_LOG

    def _noise_threshold_to_slider(self, value: float) -> int:
        val = self._clamp_noise_threshold(value)
        clip_min, clip_max = self._noise_clip_bounds()
        if self._noise_clip_scale_is_log():
            max_abs = max(abs(clip_min), abs(clip_max))
            if abs(val) <= 1e-12:
                unit = 0.0
            else:
                denom = math.log10(1.0 + float(max_abs))
                unit = math.copysign(math.log10(1.0 + abs(val)) / denom, val) if denom > 0.0 else 0.0
            pos = _NOISE_SLIDER_MID + int(round(unit * _NOISE_SLIDER_MID))
            return int(max(0, min(_NOISE_SLIDER_STEPS, pos)))
        fraction = (val - clip_min) / (clip_max - clip_min)
        return int(max(0, min(_NOISE_SLIDER_STEPS, round(fraction * _NOISE_SLIDER_STEPS))))

    def _noise_slider_to_threshold(self, value: int) -> float:
        raw = int(max(0, min(_NOISE_SLIDER_STEPS, int(value))))
        clip_min, clip_max = self._noise_clip_bounds()
        if self._noise_clip_scale_is_log():
            unit = (raw - _NOISE_SLIDER_MID) / float(_NOISE_SLIDER_MID)
            unit = max(-1.0, min(1.0, float(unit)))
            max_abs = max(abs(clip_min), abs(clip_max))
            magnitude = (10.0 ** (abs(unit) * math.log10(1.0 + float(max_abs)))) - 1.0
            return self._clamp_noise_threshold(math.copysign(magnitude, unit))
        fraction = raw / float(_NOISE_SLIDER_STEPS)
        return self._clamp_noise_threshold(clip_min + fraction * (clip_max - clip_min))

    def _noise_target_datasets(self, key: tuple[str, ...] | None) -> list[ComparisonDataset]:
        active = self._active_datasets()
        if key is None:
            return active
        target = tuple(key)
        return [dataset for dataset in active if self._noise_key_for_dataset(dataset) == target]

    def _invalidate_noise_base_cache(self) -> None:
        self._noise_base_cache.clear()

    def _noise_base_cache_key(self, dataset: ComparisonDataset, method: str) -> tuple:
        data = np.asarray(dataset.data)
        gap = dataset.gap_row_mask
        return (
            self._noise_key_for_dataset(dataset),
            id(dataset.data),
            tuple(data.shape),
            str(data.dtype),
            id(gap) if gap is not None else None,
            str(method),
        )

    def _noise_base_for_dataset(self, dataset: ComparisonDataset, method: str) -> np.ndarray:
        key = self._noise_base_cache_key(dataset, method)
        cached = self._noise_base_cache.get(key)
        if cached is not None:
            return cached
        data = apply_comparison_noise(
            dataset.data,
            ComparisonNoiseSettings(method=method),
            gap_row_mask=dataset.gap_row_mask,
        )
        self._noise_base_cache[key] = data
        return data

    def _processed_data_for_dataset(self, dataset: ComparisonDataset, settings: ComparisonNoiseSettings) -> np.ndarray:
        normalized = normalize_comparison_noise_settings(settings)
        if normalized.method == NOISE_METHOD_NONE:
            return np.asarray(dataset.data, dtype=np.float32)
        if normalized.method == NOISE_METHOD_CLIP:
            base = self._noise_base_for_dataset(dataset, NOISE_METHOD_ROBUST)
            out = np.empty_like(base, dtype=np.float32)
            np.clip(base, float(normalized.clip_low), float(normalized.clip_high), out=out)
            return out
        return self._noise_base_for_dataset(dataset, normalized.method)

    def _processed_datasets_for_render(self, datasets: list[ComparisonDataset]) -> list[ComparisonDataset]:
        out: list[ComparisonDataset] = []
        for dataset in datasets:
            settings = self._noise_settings_for_key(self._noise_key_for_dataset(dataset))
            if settings.method == NOISE_METHOD_NONE:
                out.append(dataset)
                continue
            out.append(replace(dataset, data=self._processed_data_for_dataset(dataset, settings)))
        return out

    def _effective_noise_cold_digits(self, datasets: list[ComparisonDataset]) -> tuple[float, ...]:
        values: list[float] = []
        for dataset in datasets:
            settings = self._noise_settings_for_key(self._noise_key_for_dataset(dataset))
            values.append(float(settings.clip_low))
        return tuple(values)

    def _refresh_noise_slider_range(self, settings: ComparisonNoiseSettings) -> None:
        self._expand_noise_clip_bounds_for_values(0.0, settings.clip_low, settings.clip_high)

    def _set_noise_scale_checkbox(self, scale: str | None = None) -> None:
        checkbox = getattr(self, "noise_log_scale_chk", None)
        if checkbox is None:
            return
        blocked = checkbox.blockSignals(True)
        try:
            checkbox.setChecked(self._normalize_noise_clip_scale(scale) == _NOISE_CLIP_SCALE_SIGNED_LOG)
        finally:
            checkbox.blockSignals(blocked)

    def _sync_noise_sliders(self, settings: ComparisonNoiseSettings) -> None:
        self._noise_sync_guard = True
        try:
            self._set_noise_scale_checkbox(settings.clip_scale)
            self.noise_low_slider.setValue(self._noise_threshold_to_slider(float(settings.clip_low)))
            self.noise_high_slider.setValue(self._noise_threshold_to_slider(float(settings.clip_high)))
        finally:
            self._noise_sync_guard = False
        self._update_noise_value_labels(float(settings.clip_low), float(settings.clip_high))

    def _update_noise_value_labels(self, low: float, high: float) -> None:
        self.noise_low_value_label.setText(f"{float(low):.2f} Digits")
        self.noise_high_value_label.setText(f"{float(high):.2f} Digits")
        use_db = bool(self.units_combo.currentData())
        if use_db:
            cold = float(low)
            low_db = (float(low) - cold) * DEFAULT_DB_SCALE
            high_db = (float(high) - cold) * DEFAULT_DB_SCALE
            self.noise_low_sub_value_label.setText(f"{low_db:.2f} dB")
            self.noise_high_sub_value_label.setText(f"{high_db:.2f} dB")
            self.noise_low_sub_value_label.setVisible(True)
            self.noise_high_sub_value_label.setVisible(True)
            tooltip = "Primary readout shows the clipping threshold in Digits; secondary line shows the display value in dB."
        else:
            self.noise_low_sub_value_label.clear()
            self.noise_high_sub_value_label.clear()
            self.noise_low_sub_value_label.setVisible(False)
            self.noise_high_sub_value_label.setVisible(False)
            tooltip = "Clipping threshold in Digits."
        self.noise_low_value_label.setToolTip(tooltip)
        self.noise_high_value_label.setToolTip(tooltip)
        self.noise_low_sub_value_label.setToolTip(tooltip)
        self.noise_high_sub_value_label.setToolTip(tooltip)

    def _sync_noise_controls_from_target(self) -> None:
        if getattr(self, "noise_method_combo", None) is None:
            return
        settings = self._noise_settings_for_key(self._current_noise_target_key())
        self._noise_sync_guard = True
        try:
            self._set_noise_method_combo(settings.method)
            self._refresh_noise_slider_range(settings)
            self._sync_noise_sliders(settings)
            self.noise_clip_panel.setVisible(settings.method == NOISE_METHOD_CLIP)
        finally:
            self._noise_sync_guard = False
        self._sync_actions()

    def _on_noise_target_changed(self) -> None:
        if self._noise_sync_guard:
            return
        self._sync_noise_controls_from_target()

    def _on_noise_method_changed(self) -> None:
        if self._noise_sync_guard:
            return
        target_key = self._current_noise_target_key()
        self._ensure_visible_cache_for_individual_edit(target_key)
        current = self._noise_settings_for_key(target_key)
        method = str(self.noise_method_combo.currentData() or NOISE_METHOD_NONE)
        settings = ComparisonNoiseSettings(
            method=method,
            clip_low=current.clip_low,
            clip_high=current.clip_high,
            clip_scale=self._normalize_noise_clip_scale(current.clip_scale),
        )
        self._set_noise_target_settings(settings)
        self._sync_noise_controls_from_target()
        if target_key is None:
            self.schedule_redraw(immediate=True)
        elif not self._render_noise_target_preview():
            self.schedule_redraw(immediate=True)

    def _on_noise_slider_changed(self, _value: int) -> None:
        if self._noise_sync_guard:
            return
        target_key = self._current_noise_target_key()
        self._ensure_visible_cache_for_individual_edit(target_key)
        low = self._noise_slider_to_threshold(self.noise_low_slider.value())
        high = self._noise_slider_to_threshold(self.noise_high_slider.value())
        sender = self.sender()
        if low > high:
            if sender is self.noise_low_slider:
                low = high
                self._noise_sync_guard = True
                try:
                    self.noise_low_slider.setValue(self.noise_high_slider.value())
                finally:
                    self._noise_sync_guard = False
            else:
                high = low
                self._noise_sync_guard = True
                try:
                    self.noise_high_slider.setValue(self.noise_low_slider.value())
                finally:
                    self._noise_sync_guard = False
        method = str(self.noise_method_combo.currentData() or NOISE_METHOD_CLIP)
        current = self._noise_settings_for_key(target_key)
        settings = ComparisonNoiseSettings(
            method=method,
            clip_low=float(low),
            clip_high=float(high),
            clip_scale=self._normalize_noise_clip_scale(current.clip_scale),
        )
        self._set_noise_target_settings(settings)
        self._update_noise_value_labels(float(low), float(high))
        if target_key is None:
            self.schedule_redraw(immediate=True)
        elif not self._render_noise_target_preview():
            self.schedule_redraw(immediate=True)

    def _on_noise_scale_mode_toggled(self, checked: bool) -> None:
        if self._noise_sync_guard:
            return
        current = self._noise_settings_for_key(self._current_noise_target_key())
        scale = _NOISE_CLIP_SCALE_SIGNED_LOG if checked else _NOISE_CLIP_SCALE_LINEAR
        settings = ComparisonNoiseSettings(
            method=current.method,
            clip_low=current.clip_low,
            clip_high=current.clip_high,
            clip_scale=scale,
        )
        self._set_noise_target_settings(settings)
        self._sync_noise_sliders(settings)

    def _on_units_changed(self) -> None:
        settings = self._noise_settings_for_key(self._current_noise_target_key())
        self._update_noise_value_labels(settings.clip_low, settings.clip_high)
        self.schedule_redraw(immediate=True)

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
        self.clear_ruler_measurements(reset_readout=True)
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

    def _set_combo_text(self, combo: QComboBox, text: str) -> None:
        value = str(text or "Custom")
        idx = combo.findText(value)
        if idx < 0:
            combo.addItem(value)
            idx = combo.findText(value)
        combo.setCurrentIndex(max(0, idx))

    def _sync_colormap_combo(self, combo: QComboBox, text: str) -> None:
        blocked = combo.blockSignals(True)
        try:
            self._set_combo_text(combo, text)
        finally:
            combo.blockSignals(blocked)

    def _on_colormap_changed(self) -> None:
        if self._colormap_sync_guard:
            return
        self._colormap_sync_guard = True
        try:
            self._sync_colormap_combo(self.noise_colormap_combo, str(self.colormap_combo.currentText() or "Custom"))
        finally:
            self._colormap_sync_guard = False
        self.schedule_redraw(immediate=True)

    def _on_noise_colormap_changed(self) -> None:
        if self._colormap_sync_guard:
            return
        self._colormap_sync_guard = True
        try:
            self._sync_colormap_combo(self.colormap_combo, str(self.noise_colormap_combo.currentText() or "Custom"))
        finally:
            self._colormap_sync_guard = False
        self.schedule_redraw(immediate=True)

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

    def schedule_redraw(self, *_args, immediate: bool = False) -> None:
        self._invalidate_visible_panel_cache()
        if immediate:
            self._redraw_timer.stop()
            self._render_now()
            return
        self._redraw_timer.start()

    def _sync_actions(self) -> None:
        has_files = bool(self._datasets)
        self.remove_btn.setEnabled(has_files)
        self.clear_btn.setEnabled(has_files)
        self.set_range_btn.setEnabled(has_files)
        self.reset_range_btn.setEnabled(has_files and self._display_range is not None)
        self.export_btn.setEnabled(len(self._datasets) >= 2)
        self.noise_target_combo.setEnabled(has_files)
        self.noise_method_combo.setEnabled(has_files)
        self.noise_colormap_combo.setEnabled(has_files)
        self.measure_start_btn.setEnabled(has_files)
        self.measure_clear_btn.setEnabled(bool(self._measurement_results or self._measurement_capture_points))
        clip_enabled = has_files and str(self.noise_method_combo.currentData() or NOISE_METHOD_NONE) == NOISE_METHOD_CLIP
        self.noise_low_slider.setEnabled(clip_enabled)
        self.noise_high_slider.setEnabled(clip_enabled)
        self.noise_log_scale_chk.setEnabled(clip_enabled)

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
            widget.measurementCaptureFinished.connect(
                lambda points, widget=widget: self._on_hardware_measurement_finished(widget, points)
            )
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

    def _store_visible_panel_payloads(
        self,
        datasets: list[ComparisonDataset],
        payloads: list[ComparisonPanelPayload] | tuple[ComparisonPanelPayload, ...],
        effective_mode: str,
    ) -> None:
        self._visible_panel_states.clear()
        self._visible_panel_order.clear()
        for dataset, payload in zip(datasets, payloads):
            key = self._noise_key_for_dataset(dataset)
            settings = self._noise_settings_for_key(key)
            self._visible_panel_states[key] = _VisiblePanelState(payload=payload, settings=settings)
            self._visible_panel_order.append(key)
        self._visible_effective_alignment_mode = str(effective_mode or TIME_ALIGNMENT_SECONDS)

    def _store_mpl_artists(
        self,
        datasets: list[ComparisonDataset],
        axes: tuple[object, ...],
    ) -> None:
        self._mpl_axes_by_key.clear()
        self._mpl_images_by_key.clear()
        for dataset, ax in zip(datasets, axes):
            key = self._noise_key_for_dataset(dataset)
            self._mpl_axes_by_key[key] = ax
            images = getattr(ax, "images", None)
            if images:
                self._mpl_images_by_key[key] = images[0]

    def _visible_cache_is_complete(self, datasets: list[ComparisonDataset]) -> bool:
        keys = [self._noise_key_for_dataset(dataset) for dataset in datasets]
        return bool(keys) and keys == self._visible_panel_order and all(key in self._visible_panel_states for key in keys)

    def _ensure_visible_cache_for_individual_edit(self, key: tuple[str, ...] | None) -> None:
        if key is None:
            return
        active = self._active_datasets()
        if not active or self._visible_cache_is_complete(active):
            return
        self._redraw_timer.stop()
        self._render_now()

    def _payload_for_single_dataset(
        self,
        dataset: ComparisonDataset,
        settings: ComparisonNoiseSettings,
        alignment_mode: str,
    ) -> ComparisonPanelPayload | None:
        processed = replace(dataset, data=self._processed_data_for_dataset(dataset, settings))
        payloads, _effective_mode, _warnings = comparison_panel_payloads(
            [processed],
            alignment_mode=alignment_mode,
            visual=self._visual_payload(),
            color_scale_mode=self.current_color_scale_mode(),
            manual_limits=self._manual_limits(),
            cold_digits=(float(settings.clip_low),),
        )
        return payloads[0] if payloads else None

    def _render_matplotlib(self, datasets: list[ComparisonDataset], requested_mode: str):
        self._clear_hardware_canvases()
        self.plot_stack.setCurrentWidget(self.canvas)
        render_datasets = self._processed_datasets_for_render(datasets)
        result = render_comparison_figure(
            render_datasets,
            figure=self.canvas.fig,
            alignment_mode=requested_mode,
            display_range=self._display_range,
            visual=self._visual_payload(),
            color_scale_mode=self.current_color_scale_mode(),
            manual_limits=self._manual_limits(),
            cold_digits=self._effective_noise_cold_digits(datasets),
        )
        self._store_visible_panel_payloads(datasets, result.panel_payloads, result.effective_alignment_mode)
        self._store_mpl_artists(datasets, result.axes)
        self._render_measurement_overlays()
        self.canvas.draw_idle()
        return result

    def _render_hardware(self, datasets: list[ComparisonDataset], requested_mode: str):
        self.canvas.fig.clear()
        self._ensure_hardware_canvases(len(datasets))
        self.plot_stack.setCurrentWidget(self.hardware_scroll)
        visual = self._visual_payload()
        render_datasets = self._processed_datasets_for_render(datasets)
        payloads, effective_mode, warnings = comparison_panel_payloads(
            render_datasets,
            alignment_mode=requested_mode,
            visual=visual,
            color_scale_mode=self.current_color_scale_mode(),
            manual_limits=self._manual_limits(),
            cold_digits=self._effective_noise_cold_digits(datasets),
        )
        self._store_visible_panel_payloads(datasets, payloads, effective_mode)
        self._mpl_axes_by_key.clear()
        self._mpl_images_by_key.clear()
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
        self._render_measurement_overlays()
        return SimpleNamespace(
            warnings=tuple(dict.fromkeys(all_warnings)),
            effective_alignment_mode=effective_mode,
        )

    def _render_noise_target_preview(self, key: tuple[str, ...] | None = None) -> bool:
        target_key = tuple(key or self._current_noise_target_key() or ())
        if not target_key:
            return False

        active = self._active_datasets()
        if not active:
            return False
        if not self._visible_cache_is_complete(active):
            self._redraw_timer.stop()
            self._render_now()
            active = self._active_datasets()
            if not self._visible_cache_is_complete(active):
                return False

        dataset = self._visible_dataset_for_key(target_key)
        if dataset is None:
            return False

        settings = self._noise_settings_for_key(target_key)
        try:
            payload = self._payload_for_single_dataset(dataset, settings, self._visible_effective_alignment_mode)
        except Exception:
            return False
        if payload is None:
            return False

        self._visible_panel_states[target_key] = _VisiblePanelState(payload=payload, settings=settings)

        if self.plot_stack.currentWidget() is self.hardware_scroll or self._hardware_canvases:
            try:
                index = self._visible_panel_order.index(target_key)
            except ValueError:
                return False
            if index < 0 or index >= len(self._hardware_canvases):
                return False
            widget = self._hardware_canvases[index]
            visual = self._visual_payload()
            x_label = "Time [UT]" if self._visible_effective_alignment_mode == TIME_ALIGNMENT_UT else "Time [s]"
            unit_label = "Intensity [dB]" if bool(visual.get("use_db", False)) else "Intensity [Digits]"
            try:
                widget.set_dark(bool(self._dark_mode_provider() if callable(self._dark_mode_provider) else False))
            except Exception:
                pass
            widget.update_image(
                payload.display_data,
                extent=payload.pg_extent,
                cmap=comparison_cmap(str(visual.get("cmap") or "Custom")),
                gap_row_mask=payload.dataset.gap_row_mask,
                levels=payload.levels,
                title=payload.dataset.label,
                x_label=x_label,
                y_label="Frequency [MHz]",
                colorbar_label=unit_label,
                view=self._display_view_payload(),
            )
            widget.set_time_mode(self._visible_effective_alignment_mode == TIME_ALIGNMENT_UT, 0.0)
        else:
            image = self._mpl_images_by_key.get(target_key)
            if image is None:
                return False
            try:
                image.set_data(masked_display_data(payload.display_data))
                image.set_extent(payload.mpl_extent)
                image.set_clim(float(payload.levels[0]), float(payload.levels[1]))
            except Exception:
                return False
            self.canvas.draw_idle()

        self._sync_actions()
        if any(setting.method != NOISE_METHOD_NONE for setting in self._effective_noise_settings()):
            text = self.status_label.text()
            if "Noise reduction active." not in text:
                self.status_label.setText((text + " Noise reduction active.").strip())
        return True

    def _render_now(self) -> None:
        self._sync_actions()
        active = self._active_datasets()
        if not active:
            self.clear_ruler_measurements(reset_readout=True)
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
        if any(setting.method != NOISE_METHOD_NONE for setting in self._effective_noise_settings()):
            parts.append("Noise reduction active.")
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
        self._colormap_sync_guard = True
        try:
            self._sync_colormap_combo(self.colormap_combo, text)
            self._sync_colormap_combo(self.noise_colormap_combo, text)
        finally:
            self._colormap_sync_guard = False

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
