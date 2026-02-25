"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
from typing import Any

import numpy as np
import pyqtgraph as pg
from pyqtgraph.exporters import SVGExporter
from PySide6.QtCore import QTimer, Qt, QRectF, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPdfWriter
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            try:
                return str(value.item()).strip()
            except Exception:
                return str(value).strip()
        return f"array(shape={value.shape})"

    if isinstance(value, (list, tuple, set)):
        if not value:
            return ""
        preview = [str(v).strip() for v in list(value)[:4] if str(v).strip()]
        if not preview:
            return ""
        suffix = "..." if len(value) > 4 else ""
        return ", ".join(preview) + suffix

    try:
        return str(value).strip()
    except Exception:
        return repr(value)


def _configure_pyqtgraph_once():
    if getattr(_configure_pyqtgraph_once, "_configured", False):
        return
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)
    raw = str(os.environ.get("ECALLISTO_SUNPY_OPENGL", "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        use_gl = True
    elif raw in {"0", "false", "no", "off"}:
        use_gl = False
    else:
        # Avoid known crashes in headless/pytest contexts; default to OpenGL in app runs.
        in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST")) or ("pytest" in sys.modules)
        use_gl = not in_pytest

    try:
        pg.setConfigOptions(useOpenGL=bool(use_gl))
    except Exception:
        pg.setConfigOptions(useOpenGL=False)
        use_gl = False
    setattr(_configure_pyqtgraph_once, "_opengl_enabled", bool(use_gl))
    setattr(_configure_pyqtgraph_once, "_configured", True)


class SunPyPlotCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None, theme: Any | None = None):
        super().__init__(parent)
        _configure_pyqtgraph_once()

        self.theme = theme
        self._roi_bounds: tuple[int, int, int, int] | None = None
        self._roi_callback = None
        self._axis_transform: dict[str, float] = self._default_axis_transform()
        self._last_map_bounds: tuple[float, float, float, float] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedLayout()
        layout.addLayout(self._stack)

        self.map_plot = pg.PlotWidget()
        self.map_plot.setMenuEnabled(False)
        self.map_plot.hideButtons()
        map_vb = self.map_plot.getViewBox()
        map_vb.setAspectLocked(True, ratio=1.0)
        map_vb.enableAutoRange(x=False, y=False)
        map_vb.setMouseEnabled(x=False, y=False)
        self.map_plot.setLabel("bottom", "Solar X", units="arcsec")
        self.map_plot.setLabel("left", "Solar Y", units="arcsec")
        self.map_plot.setTitle("")
        map_left_axis = self.map_plot.getAxis("left")
        map_bottom_axis = self.map_plot.getAxis("bottom")
        map_left_axis.setStyle(autoExpandTextSpace=False, tickTextWidth=60)
        map_left_axis.setWidth(72)
        map_bottom_axis.setStyle(autoExpandTextSpace=False, tickTextHeight=18)
        map_bottom_axis.setHeight(42)

        self.map_image = pg.ImageItem(axisOrder="row-major")
        self.map_plot.addItem(self.map_image)
        self._inferno_lut = pg.colormap.get("inferno", source="matplotlib").getLookupTable(nPts=256)
        self._aia_limb_curve = pg.PlotCurveItem(
            pen=pg.mkPen((120, 255, 160), width=1.8),
            antialias=True,
        )
        self._aia_limb_curve.setZValue(20)
        self._aia_limb_curve.hide()
        self.map_plot.addItem(self._aia_limb_curve)

        self.ts_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.ts_plot.setMenuEnabled(False)
        self.ts_plot.hideButtons()
        self.ts_plot.getViewBox().setMouseEnabled(x=False, y=False)
        self.ts_plot.setLabel("bottom", "Time", units="UTC")
        self.ts_plot.setLabel("left", "Flux", units="W/m^2")
        self.ts_plot.setTitle("GOES/XRS Flux")

        self._stack.addWidget(self.map_plot)
        self._stack.addWidget(self.ts_plot)
        self._stack.setCurrentWidget(self.map_plot)

        self.apply_theme()

    def set_roi_callback(self, callback):
        self._roi_callback = callback

    def apply_theme(self):
        theme = self.theme
        if theme is None:
            self.map_plot.setBackground((12, 12, 12))
            self.ts_plot.setBackground((12, 12, 12))
            return

        dark = bool(getattr(theme, "_is_dark", True))
        bg = (12, 12, 12) if dark else (245, 245, 245)
        self.map_plot.setBackground(bg)
        self.ts_plot.setBackground(bg)

    def clear_plot(self):
        self.map_image.clear()
        self.set_aia_limb_overlay(None, None, visible=False)
        self.ts_plot.clear()
        self._axis_transform = self._default_axis_transform()
        self._last_map_bounds = None

    def enable_roi_selector(self):
        # Reserved for future ROI-drawing UX in the OpenGL path.
        return

    def disable_roi_selector(self):
        return

    def reset_roi(self):
        self._roi_bounds = None

    def set_aia_limb_overlay(
        self,
        x_arcsec: np.ndarray | None,
        y_arcsec: np.ndarray | None,
        *,
        visible: bool,
    ):
        if not visible or x_arcsec is None or y_arcsec is None:
            self._aia_limb_curve.setData([], [])
            self._aia_limb_curve.hide()
            return

        x_arr = np.asarray(x_arcsec, dtype=float)
        y_arr = np.asarray(y_arcsec, dtype=float)
        if x_arr.size < 3 or y_arr.size < 3 or x_arr.size != y_arr.size:
            self._aia_limb_curve.setData([], [])
            self._aia_limb_curve.hide()
            return

        mask = np.isfinite(x_arr) & np.isfinite(y_arr)
        if int(mask.sum()) < 3:
            self._aia_limb_curve.setData([], [])
            self._aia_limb_curve.hide()
            return

        # Keep NaN gaps so disconnected projected segments are not bridged by straight lines.
        self._aia_limb_curve.setData(x_arr, y_arr, connect="finite")
        self._aia_limb_curve.show()

    def has_aia_limb_overlay(self) -> bool:
        x_data, y_data = self._aia_limb_curve.getData()
        if not self._aia_limb_curve.isVisible() or x_data is None or y_data is None:
            return False
        finite = np.isfinite(np.asarray(x_data, dtype=float)) & np.isfinite(np.asarray(y_data, dtype=float))
        return bool(int(np.count_nonzero(finite)) >= 3)

    def show_map(self):
        self._stack.setCurrentWidget(self.map_plot)

    def show_timeseries(self):
        self._stack.setCurrentWidget(self.ts_plot)

    def is_map_visible(self) -> bool:
        return self._stack.currentWidget() is self.map_plot

    def is_timeseries_visible(self) -> bool:
        return self._stack.currentWidget() is self.ts_plot

    def map_axis_labels(self) -> tuple[str, str]:
        return ("Solar X (arcsec)", "Solar Y (arcsec)")

    def map_aspect_locked(self) -> bool:
        return bool(self.map_plot.getViewBox().state.get("aspectLocked", False))

    def map_arcsec_from_pixel(self, x_pix: float, y_pix: float) -> tuple[float, float]:
        tx = self._axis_transform
        x_arc = tx["x_ref_arcsec"] + (float(x_pix) - tx["x_ref_pix"]) * tx["x_scale_arcsec_per_pix"]
        y_arc = tx["y_ref_arcsec"] + (float(y_pix) - tx["y_ref_pix"]) * tx["y_scale_arcsec_per_pix"]
        return float(x_arc), float(y_arc)

    def map_view_rect(self) -> tuple[float, float, float, float]:
        rect = self.map_plot.getViewBox().viewRect()
        return (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))

    def backend_name(self) -> str:
        return "pyqtgraph"

    def opengl_enabled(self) -> bool:
        return bool(getattr(_configure_pyqtgraph_once, "_opengl_enabled", False))

    def _default_axis_transform(self) -> dict[str, float]:
        return {
            "x_ref_pix": 0.0,
            "y_ref_pix": 0.0,
            "x_scale_arcsec_per_pix": 1.0,
            "y_scale_arcsec_per_pix": 1.0,
            "x_ref_arcsec": 0.0,
            "y_ref_arcsec": 0.0,
        }

    def plot_map_data(
        self,
        image_data: np.ndarray,
        title: str,
        *,
        vmin: float | None = None,
        vmax: float | None = None,
        axis_transform: dict[str, float] | None = None,
    ):
        self.show_map()

        arr = np.asarray(image_data)
        is_rgb = bool(arr.ndim == 3 and arr.shape[-1] in (3, 4))

        self._axis_transform = dict(axis_transform or self._default_axis_transform())
        x0, y0, width, height = self._map_rect_from_transform(arr.shape)
        self.map_image.setRect(QRectF(x0, y0, width, height))

        if is_rgb:
            self.map_image.setLookupTable(None)
            self.map_image.setImage(arr, autoLevels=False)
        else:
            data = np.asarray(arr, dtype=np.float32)
            self.map_image.setLookupTable(self._inferno_lut)
            self.map_image.setImage(data, autoLevels=False)

            finite = data[np.isfinite(data)]
            if vmin is not None and vmax is not None and float(vmax) > float(vmin):
                self.map_image.setLevels([float(vmin), float(vmax)])
            elif finite.size > 0:
                lo = float(np.nanmin(finite))
                hi = float(np.nanmax(finite))
                if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                    self.map_image.setLevels([lo, hi])
                else:
                    self.map_image.setLevels([lo - 1.0, lo + 1.0])
            else:
                self.map_image.setLevels([0.0, 1.0])

        self.map_plot.setTitle(title)
        self.map_plot.setLabel("bottom", "Solar X", units="arcsec")
        self.map_plot.setLabel("left", "Solar Y", units="arcsec")

        x1 = x0 + width
        y1 = y0 + height
        bounds = (min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1))
        x_span = max(1e-12, bounds[1] - bounds[0])
        y_span = max(1e-12, bounds[3] - bounds[2])
        vb = self.map_plot.getViewBox()
        vb.setAspectLocked(True, ratio=1.0)
        vb.setLimits(
            xMin=bounds[0],
            xMax=bounds[1],
            yMin=bounds[2],
            yMax=bounds[3],
            minXRange=x_span,
            maxXRange=x_span,
            minYRange=y_span,
            maxYRange=y_span,
        )
        if self._last_map_bounds is None or any(abs(bounds[i] - self._last_map_bounds[i]) > 1e-9 for i in range(4)):
            vb.setRange(
                xRange=(bounds[0], bounds[1]),
                yRange=(bounds[2], bounds[3]),
                padding=0.0,
                disableAutoRange=True,
            )
            self._last_map_bounds = bounds

    def _map_rect_from_transform(self, shape: tuple[int, ...]) -> tuple[float, float, float, float]:
        ny = int(shape[0]) if len(shape) >= 1 else 1
        nx = int(shape[1]) if len(shape) >= 2 else 1

        tx = self._axis_transform
        x_scale = float(tx.get("x_scale_arcsec_per_pix", 1.0))
        y_scale = float(tx.get("y_scale_arcsec_per_pix", 1.0))
        x_ref_pix = float(tx.get("x_ref_pix", 0.0))
        y_ref_pix = float(tx.get("y_ref_pix", 0.0))
        x_ref_arcsec = float(tx.get("x_ref_arcsec", 0.0))
        y_ref_arcsec = float(tx.get("y_ref_arcsec", 0.0))

        x0 = x_ref_arcsec - (x_ref_pix + 0.5) * x_scale
        y0 = y_ref_arcsec - (y_ref_pix + 0.5) * y_scale
        width = float(nx) * x_scale
        height = float(ny) * y_scale
        return float(x0), float(y0), float(width), float(height)

    def plot_timeseries(self, times: list[datetime], short_flux: np.ndarray | None, long_flux: np.ndarray | None):
        self.show_timeseries()
        self.set_aia_limb_overlay(None, None, visible=False)
        self.ts_plot.clear()
        self.ts_plot.setTitle("GOES/XRS Flux")
        self.ts_plot.setLabel("bottom", "Time", units="UTC")
        self.ts_plot.setLabel("left", "Flux", units="W/m^2")

        x_values = np.asarray([self._to_unix_seconds(dt) for dt in times], dtype=float)

        plotted = False
        positives = []

        if short_flux is not None and len(short_flux) == len(times):
            arr = np.asarray(short_flux, dtype=float)
            self.ts_plot.plot(
                x_values,
                arr,
                pen=pg.mkPen((79, 195, 247), width=1.4),
                name="XRS short",
            )
            vals = arr[np.isfinite(arr) & (arr > 0)]
            if vals.size > 0:
                positives.append(vals)
            plotted = True

        if long_flux is not None and len(long_flux) == len(times):
            arr = np.asarray(long_flux, dtype=float)
            self.ts_plot.plot(
                x_values,
                arr,
                pen=pg.mkPen((239, 108, 0), width=1.6),
                name="XRS long",
            )
            vals = arr[np.isfinite(arr) & (arr > 0)]
            if vals.size > 0:
                positives.append(vals)
            plotted = True

        self.ts_plot.showGrid(x=True, y=True, alpha=0.25)
        self.ts_plot.setLogMode(x=False, y=bool(positives))
        if plotted:
            self.ts_plot.enableAutoRange()

    @staticmethod
    def _to_unix_seconds(dt: datetime) -> float:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.astimezone(timezone.utc).timestamp()


class SunPyPlotWindow(QMainWindow):
    mapFrameChanged = Signal(int)
    mapRoiChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, theme: Any | None = None):
        super().__init__(parent)
        self.setWindowTitle("SunPy Plot and Animation")
        self.resize(980, 840)

        self.theme = theme
        self._mode = "empty"
        self._map_frames: list[Any] = []
        self._map_metadata: dict[str, Any] = {}
        self._timeseries_metadata: dict[str, Any] = {}
        self._current_map_data: np.ndarray | None = None
        self._current_frame_index = 0
        self._roi_bounds: tuple[int, int, int, int] | None = None
        self._aia_limb_cache: dict[int, tuple[np.ndarray, np.ndarray] | None] = {}
        self._square_window_mode = False
        self._resize_guard = False

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.mode_label = QLabel("No data loaded.")
        layout.addWidget(self.mode_label)

        self.canvas = SunPyPlotCanvas(theme=self.theme)
        self.canvas.set_roi_callback(self._on_roi_selected)
        layout.addWidget(self.canvas, 1)

        self.map_controls = QWidget(self)
        map_layout = QVBoxLayout(self.map_controls)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        self.frame_label = QLabel("Frame 0/0")
        self.frame_label.setMinimumWidth(110)
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setEnabled(False)
        self.running_diff_check = QCheckBox("Run Diff")
        self.running_diff_check.setEnabled(False)
        self.aia_limb_check = QCheckBox("AIA Limb (EUVI)")
        self.aia_limb_check.setEnabled(False)
        self.play_btn = QPushButton("Play")
        self.play_btn.setEnabled(False)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setEnabled(False)
        self.rewind_btn = QPushButton("Rewind")
        self.rewind_btn.setEnabled(False)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1.0, 30.0)
        self.fps_spin.setSingleStep(1.0)
        self.fps_spin.setDecimals(0)
        self.fps_spin.setValue(4.0)
        self.fps_spin.setSuffix(" FPS")
        self.reset_roi_btn = QPushButton("Reset ROI")
        self.reset_roi_btn.setEnabled(False)

        top_row.addWidget(self.frame_label)
        top_row.addWidget(self.frame_slider, 1)

        bottom_row.addWidget(self.running_diff_check)
        bottom_row.addWidget(self.aia_limb_check)
        bottom_row.addWidget(self.play_btn)
        bottom_row.addWidget(self.pause_btn)
        bottom_row.addWidget(self.rewind_btn)
        bottom_row.addWidget(self.fps_spin)
        bottom_row.addWidget(self.reset_roi_btn)
        bottom_row.addStretch(1)

        map_layout.addLayout(top_row)
        map_layout.addLayout(bottom_row)
        layout.addWidget(self.map_controls)

        self.map_controls.setVisible(False)

    def _connect_signals(self):
        self.frame_slider.valueChanged.connect(self._on_frame_slider_changed)
        self.running_diff_check.toggled.connect(self._on_running_diff_toggled)
        self.aia_limb_check.toggled.connect(self._on_aia_limb_toggled)
        self.play_btn.clicked.connect(self._on_play_clicked)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.rewind_btn.clicked.connect(self._on_rewind_clicked)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        self.reset_roi_btn.clicked.connect(self.reset_roi)

    def apply_theme(self):
        self.canvas.theme = self.theme
        self.canvas.apply_theme()

    def set_map_frames(self, frames: list[Any], metadata: dict[str, Any]) -> None:
        values = list(frames or [])
        if not values:
            raise ValueError("No map frames were provided to SunPyPlotWindow.")

        self._mode = "map"
        self._map_frames = values
        self._map_metadata = dict(metadata or {})
        self._timeseries_metadata = {}
        self._current_frame_index = 0
        self._aia_limb_cache = {}

        self._play_timer.stop()
        self._roi_bounds = None
        self._current_map_data = None
        self.mapRoiChanged.emit(None)

        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(0, len(values) - 1))
        self.frame_slider.setValue(0)
        self.frame_slider.setEnabled(len(values) > 1)
        self.frame_slider.blockSignals(False)

        has_multiple = len(values) > 1
        self.running_diff_check.setEnabled(has_multiple)
        self.running_diff_check.setChecked(False)
        has_euvi = any(self._is_stereo_euvi_frame(frame) for frame in values)
        has_euvi = bool(has_euvi or self._metadata_indicates_stereo_euvi(self._map_metadata))
        self.aia_limb_check.setEnabled(has_euvi)
        self.aia_limb_check.setChecked(False)
        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.rewind_btn.setEnabled(False)
        self.reset_roi_btn.setEnabled(True)

        self.canvas.clear_plot()
        self.canvas.reset_roi()
        self.show_map_mode()
        self._render_current_map_frame(emit_signal=True)
        self._refresh_playback_buttons()

    def set_timeseries(
        self,
        times: list[datetime],
        channels: dict[str, np.ndarray],
        metadata: dict[str, Any],
    ) -> None:
        if not times:
            raise ValueError("No time axis values were provided for timeseries plotting.")

        short_flux = channels.get("short")
        long_flux = channels.get("long")

        self._mode = "timeseries"
        self._map_frames = []
        self._map_metadata = {}
        self._timeseries_metadata = dict(metadata or {})
        self._current_map_data = None
        self._roi_bounds = None
        self._current_frame_index = 0
        self._aia_limb_cache = {}

        self._play_timer.stop()
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)
        self.frame_slider.setEnabled(False)
        self.running_diff_check.setEnabled(False)
        self.running_diff_check.setChecked(False)
        self.aia_limb_check.setEnabled(False)
        self.aia_limb_check.setChecked(False)
        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.rewind_btn.setEnabled(False)
        self.reset_roi_btn.setEnabled(False)

        self.canvas.plot_timeseries(times, short_flux=short_flux, long_flux=long_flux)
        self.mapRoiChanged.emit(None)
        self.show_timeseries_mode()

    def show_map_mode(self) -> None:
        self.canvas.show_map()
        self.map_controls.setVisible(True)
        self.mode_label.setText("Map mode")
        self._set_square_window_mode(True)

    def show_timeseries_mode(self) -> None:
        self.canvas.show_timeseries()
        self.map_controls.setVisible(False)
        self.mode_label.setText("Timeseries mode")
        self._set_square_window_mode(False)

    def current_map_data(self) -> np.ndarray | None:
        return self._current_map_data

    def current_roi_bounds(self) -> tuple[int, int, int, int] | None:
        return self._roi_bounds

    def current_frame_index(self) -> int:
        return self._current_frame_index

    def has_plot_content(self) -> bool:
        return self._mode in {"map", "timeseries"}

    def save_current_plot(self, path: str) -> None:
        if not self.has_plot_content():
            raise RuntimeError("No SunPy plot is available to export.")

        ext = Path(path).suffix.lower()
        if ext == ".svg":
            target_plot_item = self.canvas.map_plot.plotItem if self._mode == "map" else self.canvas.ts_plot.plotItem
            exporter = SVGExporter(target_plot_item)
            exporter.export(path)
            return

        widget = self.canvas.map_plot if self._mode == "map" else self.canvas.ts_plot
        pixmap = widget.grab()
        if ext == ".pdf":
            self._save_pdf_from_pixmap(path, pixmap)
            return

        if not pixmap.save(path):
            raise RuntimeError(f"Failed to save plot to '{path}'.")

    @staticmethod
    def _save_pdf_from_pixmap(path: str, pixmap):
        writer = QPdfWriter(path)
        painter = QPainter(writer)
        try:
            viewport = painter.viewport()
            scaled = pixmap.scaled(viewport.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = int((viewport.width() - scaled.width()) / 2)
            y = int((viewport.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        finally:
            painter.end()

    def _on_fps_changed(self, _value: float):
        if self._play_timer.isActive():
            self._play_timer.setInterval(self._frame_interval_ms())

    def _on_play_clicked(self):
        if self._mode != "map" or len(self._map_frames) <= 1:
            return
        if self._current_frame_index >= len(self._map_frames) - 1:
            self._on_rewind_clicked()
        self._play_timer.start(self._frame_interval_ms())
        self._refresh_playback_buttons()

    def _on_pause_clicked(self):
        self._stop_playback()

    def _on_rewind_clicked(self):
        if self._mode != "map" or not self._map_frames:
            return
        self._stop_playback()
        if self._current_frame_index != 0:
            self.frame_slider.setValue(0)
            return
        self._refresh_playback_buttons()

    def _on_play_tick(self):
        if self._mode != "map" or len(self._map_frames) <= 1:
            self._stop_playback()
            return

        if self._current_frame_index >= len(self._map_frames) - 1:
            self._stop_playback()
            return

        self.frame_slider.setValue(self._current_frame_index + 1)
        if self._current_frame_index >= len(self._map_frames) - 1:
            self._stop_playback()

    def _frame_interval_ms(self) -> int:
        fps = max(1.0, float(self.fps_spin.value() or 1.0))
        return max(1, int(round(1000.0 / fps)))

    def _stop_playback(self):
        self._play_timer.stop()
        self._refresh_playback_buttons()

    def _refresh_playback_buttons(self):
        has_multiple = self._mode == "map" and len(self._map_frames) > 1
        if not has_multiple:
            self.play_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.rewind_btn.setEnabled(False)
            return

        is_playing = bool(self._play_timer.isActive())
        at_last = self._current_frame_index >= (len(self._map_frames) - 1)
        self.play_btn.setEnabled((not is_playing) and (not at_last))
        self.pause_btn.setEnabled(is_playing)
        self.rewind_btn.setEnabled(True)

    def _on_frame_slider_changed(self, value: int):
        if self._mode != "map":
            return
        self._current_frame_index = int(max(0, min(value, len(self._map_frames) - 1)))
        self._render_current_map_frame(emit_signal=True)
        if self._current_frame_index >= len(self._map_frames) - 1:
            self._stop_playback()
        else:
            self._refresh_playback_buttons()

    def _on_running_diff_toggled(self, _checked: bool):
        if self._mode != "map":
            return
        self._render_current_map_frame(emit_signal=True)

    def _on_aia_limb_toggled(self, _checked: bool):
        if self._mode != "map":
            self.canvas.set_aia_limb_overlay(None, None, visible=False)
            return
        self._render_current_map_frame(emit_signal=False)

    def _render_current_map_frame(self, *, emit_signal: bool):
        if not self._map_frames:
            return

        idx = max(0, min(self._current_frame_index, len(self._map_frames) - 1))
        frame = self._map_frames[idx]

        current = self._prepare_map_array(getattr(frame, "data"), "current frame")
        title = self._frame_title(frame, idx)

        if self.running_diff_check.isChecked() and len(self._map_frames) > 1:
            if idx > 0:
                prev = self._prepare_map_array(getattr(self._map_frames[idx - 1], "data"), "previous frame")
                if prev.shape == current.shape:
                    current = current - prev
                    title += " (Running Difference)"
            else:
                nxt = self._prepare_map_array(getattr(self._map_frames[1], "data"), "next frame")
                if nxt.shape == current.shape:
                    current = nxt - current
                    title += " (Running Difference)"

        finite = current[np.isfinite(current)]
        vmin = None
        vmax = None
        if finite.size > 0:
            try:
                vmin = float(np.nanpercentile(finite, 1.0))
                vmax = float(np.nanpercentile(finite, 99.0))
                if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
                    vmin = None
                    vmax = None
            except Exception:
                vmin = None
                vmax = None

        self._current_map_data = current
        self._current_frame_index = idx
        self.frame_label.setText(f"Frame {idx + 1}/{len(self._map_frames)}")
        axis_transform = self._axis_transform_for_arcsec(frame=frame, data_shape=current.shape)
        self.canvas.plot_map_data(current, title=title, vmin=vmin, vmax=vmax, axis_transform=axis_transform)
        self._update_aia_limb_overlay(frame=frame, frame_index=idx, data_shape=current.shape)

        if emit_signal:
            self.mapFrameChanged.emit(idx)

    def _frame_title(self, frame: Any, frame_index: int) -> str:
        obs = _safe_text(getattr(frame, "observatory", None))
        inst = _safe_text(getattr(frame, "instrument", None))
        det = _safe_text(getattr(frame, "detector", None))
        wl = _safe_text(getattr(frame, "wavelength", None))

        chunks = []
        name = "/".join([x for x in (obs, inst) if x])
        if name:
            chunks.append(name)
        if det:
            chunks.append(det)
        if wl:
            chunks.append(wl)
        if not chunks:
            chunks.append("SunPy Map")
        return " | ".join(chunks)

    def _prepare_map_array(self, raw_data: Any, label: str) -> np.ndarray:
        arr = np.asarray(raw_data)
        arr = np.squeeze(arr)

        if arr.ndim == 2:
            return np.asarray(arr, dtype=float)
        if arr.ndim == 3 and arr.shape[-1] in (3, 4):
            return np.asarray(arr, dtype=float)
        if arr.ndim > 2:
            arr2 = np.asarray(arr[0]).squeeze()
            if arr2.ndim == 2:
                return np.asarray(arr2, dtype=float)

        raise ValueError(f"Unsupported map array shape for {label}: {arr.shape}")

    def _on_roi_selected(self, bounds: tuple[int, int, int, int]):
        self._roi_bounds = bounds
        self.mapRoiChanged.emit(bounds)

    def reset_roi(self):
        self._roi_bounds = None
        self.canvas.reset_roi()
        self.mapRoiChanged.emit(None)

    def closeEvent(self, event):
        self._play_timer.stop()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._square_window_mode and not self._resize_guard:
            self._apply_square_window_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._reflow_visible_geometry)

    def _reflow_visible_geometry(self):
        if self._square_window_mode and not self._resize_guard:
            self._apply_square_window_geometry()
            return
        self._ensure_window_in_screen(self._available_screen_geometry())

    def _set_square_window_mode(self, enabled: bool):
        self._square_window_mode = bool(enabled)
        if self._square_window_mode:
            self._apply_square_window_geometry()
        else:
            self._ensure_window_in_screen(self._available_screen_geometry())

    def _available_screen_geometry(self):
        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        if screen is None:
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        return screen.availableGeometry()

    def _apply_square_window_geometry(self):
        available = self._available_screen_geometry()
        screen_margin = 24

        map_plot = getattr(getattr(self, "canvas", None), "map_plot", None)
        if map_plot is None:
            self._ensure_window_in_screen(available)
            return

        map_w = int(map_plot.width())
        map_h = int(map_plot.height())
        if map_w <= 0 or map_h <= 0:
            self._ensure_window_in_screen(available)
            return

        map_side = min(map_w, map_h)
        frame_dw = max(0, int(self.width()) - map_w)
        frame_dh = max(0, int(self.height()) - map_h)
        if available is not None:
            max_map_w = max(220, int(available.width()) - frame_dw - screen_margin)
            max_map_h = max(220, int(available.height()) - frame_dh - screen_margin)
            map_side = min(map_side, max_map_w, max_map_h)
        target_w = max(220, map_side + frame_dw)
        target_h = max(220, map_side + frame_dh)
        if abs(int(self.width()) - target_w) <= 1 and abs(int(self.height()) - target_h) <= 1:
            self._ensure_window_in_screen(available)
            self._enforce_square_map_plot(available)
            return

        self._resize_guard = True
        try:
            self.resize(target_w, target_h)
        finally:
            self._resize_guard = False
        self._ensure_window_in_screen(available)
        self._enforce_square_map_plot(available)

    def _enforce_square_map_plot(self, available) -> None:
        map_plot = getattr(getattr(self, "canvas", None), "map_plot", None)
        if map_plot is None:
            return

        for _ in range(4):
            map_w = int(map_plot.width())
            map_h = int(map_plot.height())
            diff = map_w - map_h
            if abs(diff) <= 1:
                return

            next_w = int(self.width())
            next_h = int(self.height())
            if diff > 1:
                next_w = max(220, next_w - diff)
            else:
                next_h = max(220, next_h + diff)

            if available is not None:
                next_w = min(next_w, max(220, int(available.width()) - 8))
                next_h = min(next_h, max(220, int(available.height()) - 8))

            if next_w == int(self.width()) and next_h == int(self.height()):
                return

            self._resize_guard = True
            try:
                self.resize(next_w, next_h)
            finally:
                self._resize_guard = False
            self._ensure_window_in_screen(available)

    def _ensure_window_in_screen(self, available) -> None:
        if available is None:
            return

        max_w = max(220, int(available.width()) - 8)
        max_h = max(220, int(available.height()) - 8)
        needs_resize = int(self.width()) > max_w or int(self.height()) > max_h
        if needs_resize:
            self._resize_guard = True
            try:
                self.resize(min(int(self.width()), max_w), min(int(self.height()), max_h))
            finally:
                self._resize_guard = False

        geo = self.frameGeometry()
        left = int(available.left())
        top = int(available.top())
        max_x = left + max(0, int(available.width()) - int(geo.width()))
        max_y = top + max(0, int(available.height()) - int(geo.height()))
        x = min(max(int(geo.x()), left), max_x)
        y = min(max(int(geo.y()), top), max_y)
        if x != int(geo.x()) or y != int(geo.y()):
            self.move(x, y)

    def _axis_transform_for_arcsec(self, frame: Any, data_shape: tuple[int, ...]) -> dict[str, float]:
        ny = int(data_shape[0]) if len(data_shape) >= 1 else 0
        nx = int(data_shape[1]) if len(data_shape) >= 2 else 0

        x_ref_pix = (max(nx, 1) - 1) / 2.0
        y_ref_pix = (max(ny, 1) - 1) / 2.0
        x_scale = 1.0
        y_scale = 1.0
        x_ref_arcsec = 0.0
        y_ref_arcsec = 0.0

        scale = getattr(frame, "scale", None)
        x_scale_raw = self._pick_component(scale, ("axis1", "x"))
        y_scale_raw = self._pick_component(scale, ("axis2", "y"))
        x_scale_val = self._as_float(x_scale_raw, unit_hint="arcsec / pix")
        y_scale_val = self._as_float(y_scale_raw, unit_hint="arcsec / pix")
        if x_scale_val is not None and np.isfinite(x_scale_val):
            x_scale = x_scale_val
        if y_scale_val is not None and np.isfinite(y_scale_val):
            y_scale = y_scale_val

        ref_pixel = getattr(frame, "reference_pixel", None)
        x_ref_pix_raw = self._pick_component(ref_pixel, ("x", "axis1"))
        y_ref_pix_raw = self._pick_component(ref_pixel, ("y", "axis2"))
        x_ref_pix_val = self._as_float(x_ref_pix_raw, unit_hint="pix")
        y_ref_pix_val = self._as_float(y_ref_pix_raw, unit_hint="pix")
        if x_ref_pix_val is not None and np.isfinite(x_ref_pix_val):
            x_ref_pix = x_ref_pix_val
        if y_ref_pix_val is not None and np.isfinite(y_ref_pix_val):
            y_ref_pix = y_ref_pix_val

        ref_coord = getattr(frame, "reference_coordinate", None)
        x_ref_arcsec_raw = self._pick_component(ref_coord, ("Tx", "x", "lon"))
        y_ref_arcsec_raw = self._pick_component(ref_coord, ("Ty", "y", "lat"))
        x_ref_arcsec_val = self._as_float(x_ref_arcsec_raw, unit_hint="arcsec")
        y_ref_arcsec_val = self._as_float(y_ref_arcsec_raw, unit_hint="arcsec")
        if x_ref_arcsec_val is not None and np.isfinite(x_ref_arcsec_val):
            x_ref_arcsec = x_ref_arcsec_val
        if y_ref_arcsec_val is not None and np.isfinite(y_ref_arcsec_val):
            y_ref_arcsec = y_ref_arcsec_val

        return {
            "x_ref_pix": float(x_ref_pix),
            "y_ref_pix": float(y_ref_pix),
            "x_scale_arcsec_per_pix": float(x_scale),
            "y_scale_arcsec_per_pix": float(y_scale),
            "x_ref_arcsec": float(x_ref_arcsec),
            "y_ref_arcsec": float(y_ref_arcsec),
        }

    def _update_aia_limb_overlay(self, *, frame: Any, frame_index: int, data_shape: tuple[int, ...]):
        if not self.aia_limb_check.isChecked():
            self.canvas.set_aia_limb_overlay(None, None, visible=False)
            return
        has_euvi_context = bool(
            self._is_stereo_euvi_frame(frame)
            or self._metadata_indicates_stereo_euvi(self._map_metadata)
        )
        if not has_euvi_context:
            self.canvas.set_aia_limb_overlay(None, None, visible=False)
            return

        if frame_index not in self._aia_limb_cache:
            self._aia_limb_cache[frame_index] = self._compute_aia_limb_arcsec(frame=frame, data_shape=data_shape)
        overlay = self._aia_limb_cache.get(frame_index)
        if overlay is None:
            self.canvas.set_aia_limb_overlay(None, None, visible=False)
            return
        x_arc, y_arc = overlay
        self.canvas.set_aia_limb_overlay(x_arc, y_arc, visible=True)

    def _is_stereo_euvi_frame(self, frame: Any) -> bool:
        detector = _safe_text(getattr(frame, "detector", None)).upper()
        instrument = _safe_text(getattr(frame, "instrument", None)).upper()
        nickname = _safe_text(getattr(frame, "nickname", None)).upper()
        observatory = _safe_text(getattr(frame, "observatory", None)).upper()
        source = _safe_text(getattr(frame, "source", None)).upper()

        meta = getattr(frame, "meta", None)
        meta_observatory = self._frame_meta_text(meta, ("obsrvtry", "observat", "telescop", "obsrvtr"))
        meta_instrument = self._frame_meta_text(meta, ("instrume", "instrument"))
        meta_detector = self._frame_meta_text(meta, ("detector",))

        is_stereo = any(
            "STEREO" in value
            for value in (
                observatory,
                source,
                nickname,
                meta_observatory,
            )
        )
        has_euvi_tag = any(
            "EUVI" in value
            for value in (
                detector,
                instrument,
                nickname,
                meta_instrument,
                meta_detector,
            )
        )
        return bool(is_stereo and has_euvi_tag)

    def _metadata_indicates_stereo_euvi(self, metadata: dict[str, Any] | None) -> bool:
        if not metadata:
            return False
        obs_text = " ".join(
            [
                _safe_text(metadata.get("observatory", None)).upper(),
                _safe_text(metadata.get("source", None)).upper(),
                _safe_text(metadata.get("query_spacecraft", None)).upper(),
            ]
        )
        inst_text = " ".join(
            [
                _safe_text(metadata.get("instrument", None)).upper(),
                _safe_text(metadata.get("detector", None)).upper(),
                _safe_text(metadata.get("query_instrument", None)).upper(),
                _safe_text(metadata.get("query_detector", None)).upper(),
            ]
        )
        return bool(("STEREO" in obs_text or "STEREO_A" in obs_text) and ("EUVI" in inst_text))

    def _frame_meta_text(self, meta: Any, keys: tuple[str, ...]) -> str:
        if meta is None:
            return ""
        try:
            for key in keys:
                for variant in (key, key.upper(), key.lower()):
                    if variant in meta:
                        return _safe_text(meta.get(variant, None)).upper()
        except Exception:
            return ""
        return ""

    def _compute_aia_limb_arcsec(self, *, frame: Any, data_shape: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray] | None:
        # Follow SunPy's AIA-on-EUVI limb approach:
        # get Earth-observer limb coordinates, transform into the EUVI frame,
        # and keep only points visible from the EUVI observer.
        try:
            from astropy.constants import R_sun
            import astropy.units as u
            from sunpy.coordinates.ephemeris import get_earth
            from sunpy.coordinates.utils import get_limb_coordinates
        except Exception:
            return None

        frame_coord = getattr(frame, "coordinate_frame", None)
        if frame_coord is None:
            return None

        obstime = getattr(frame_coord, "obstime", None)
        if obstime is None:
            obstime = getattr(frame, "date", None)
        if obstime is None:
            return None
        to_datetime = getattr(obstime, "to_datetime", None)
        if callable(to_datetime):
            try:
                obstime = to_datetime()
            except Exception:
                pass

        try:
            earth_observer = get_earth(obstime)
            rsun = getattr(frame_coord, "rsun", None)
            if rsun is None:
                rsun = getattr(frame, "rsun_meters", None)
            if rsun is None:
                rsun = R_sun
            limb = get_limb_coordinates(earth_observer, rsun=rsun, resolution=1200)
            limb_in_frame = limb.transform_to(frame_coord)

            x_arc = np.asarray(limb_in_frame.spherical.lon.to_value(u.arcsec), dtype=float)
            y_arc = np.asarray(limb_in_frame.spherical.lat.to_value(u.arcsec), dtype=float)
            to_pixel = getattr(frame, "world_to_pixel", None)
            if callable(to_pixel):
                try:
                    x_pix_q, y_pix_q = to_pixel(limb_in_frame)
                    x_pix = np.asarray(getattr(x_pix_q, "value", x_pix_q), dtype=float)
                    y_pix = np.asarray(getattr(y_pix_q, "value", y_pix_q), dtype=float)
                    axis_tx = self._axis_transform_for_arcsec(frame=frame, data_shape=data_shape)
                    x_arc = axis_tx["x_ref_arcsec"] + (x_pix - axis_tx["x_ref_pix"]) * axis_tx["x_scale_arcsec_per_pix"]
                    y_arc = axis_tx["y_ref_arcsec"] + (y_pix - axis_tx["y_ref_pix"]) * axis_tx["y_scale_arcsec_per_pix"]
                except Exception:
                    # Fall back to transformed world long/lat arcsec.
                    pass

            norm = limb_in_frame.spherical.norm()
            is_2d = bool(getattr(norm, "unit", None) is u.one and u.allclose(norm, 1 * u.one))
            is_visible_method = getattr(limb_in_frame, "is_visible", None)
            if callable(is_visible_method):
                is_visible = np.asarray(is_visible_method(), dtype=bool)
            elif not is_2d and hasattr(frame_coord, "observer") and getattr(frame_coord, "observer", None) is not None:
                reference_distance = np.sqrt(frame_coord.observer.radius**2 - rsun**2)
                is_visible = np.asarray(limb_in_frame.spherical.distance <= reference_distance, dtype=bool)
            else:
                is_visible = np.ones_like(x_arc, dtype=bool)

            if x_arc.size >= 3:
                step = np.sqrt((x_arc[1:] - x_arc[:-1]) ** 2 + (y_arc[1:] - y_arc[:-1]) ** 2)
                continuous = np.concatenate([[True, True], step[1:] < 100 * step[:-1]])
            else:
                continuous = np.ones_like(x_arc, dtype=bool)

            finite = np.isfinite(x_arc) & np.isfinite(y_arc)
            keep = np.asarray(is_visible & continuous & finite, dtype=bool)
            if int(np.count_nonzero(keep)) < 3:
                keep = np.asarray(continuous & finite, dtype=bool)
            if int(np.count_nonzero(keep)) < 3:
                keep = np.asarray(finite, dtype=bool)
            if int(np.count_nonzero(keep)) < 3:
                return None

            x_arc = x_arc.copy()
            y_arc = y_arc.copy()
            x_arc[~keep] = np.nan
            y_arc[~keep] = np.nan
            return x_arc, y_arc
        except Exception:
            return None

    def _pick_component(self, obj: Any, attrs: tuple[str, ...]) -> Any:
        if obj is None:
            return None
        for attr in attrs:
            if hasattr(obj, attr):
                try:
                    value = getattr(obj, attr)
                    if value is not None:
                        return value
                except Exception:
                    continue
        return None

    def _as_float(self, value: Any, unit_hint: str | None = None) -> float | None:
        if value is None:
            return None
        to_value = getattr(value, "to_value", None)
        if callable(to_value):
            if unit_hint:
                try:
                    return float(to_value(unit_hint))
                except Exception:
                    pass
            try:
                return float(to_value())
            except Exception:
                pass
        raw = getattr(value, "value", None)
        if raw is not None:
            try:
                return float(raw)
            except Exception:
                pass
        try:
            return float(value)
        except Exception:
            pass
        text = str(value)
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
        if match is None:
            return None
        try:
            return float(match.group(0))
        except Exception:
            return None
