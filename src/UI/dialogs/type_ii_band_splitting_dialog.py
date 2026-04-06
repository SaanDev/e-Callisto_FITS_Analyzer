"""
Type II band-splitting analysis dialog.
"""

from __future__ import annotations

import os
from typing import Any

from matplotlib import colormaps
import numpy as np
from PySide6.QtCore import QRectF, Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from src.Backend.frequency_axis import finite_data_limits, pyqtgraph_extent
from src.Backend.type_ii_band_splitting import calculate_type_ii_parameters, fit_power_law, power_law
from src.UI.accelerated_plot_widget import _mpl_cmap_to_lookup, _rgba_image_from_cmap, pg


class TypeIIBandSplittingDialog(QDialog):
    sessionChanged = Signal(dict)

    def __init__(
        self,
        spectrum_data,
        freqs,
        time_seconds,
        filename,
        *,
        parent=None,
        session=None,
        display_data=None,
        display_unit: str = "Digits",
        cmap=None,
        frequency_step_mhz=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Type II Band-splitting")
        self.resize(1280, 760)

        self.spectrum_data = np.asarray(spectrum_data, dtype=float).copy()
        self.display_data = np.asarray(display_data if display_data is not None else spectrum_data, dtype=float).copy()
        self.freqs = np.asarray(freqs, dtype=float).reshape(-1)
        self.time_seconds = np.asarray(time_seconds, dtype=float).reshape(-1)
        self.filename = os.path.splitext(os.path.basename(str(filename or "dynamic_spectrum")))[0]
        self.display_unit = str(display_unit or "Digits")
        self.cmap = cmap if cmap is not None else colormaps.get_cmap("viridis")
        self.frequency_step_mhz = frequency_step_mhz
        self._session_context = dict(session or {})
        self._suppress_emit = False
        self._using_pyqtgraph = pg is not None

        self._upper_points: list[tuple[float, float]] = []
        self._lower_points: list[tuple[float, float]] = []
        self._upper_fit: dict[str, Any] | None = None
        self._lower_fit: dict[str, Any] | None = None
        self._results: dict[str, Any] = {}

        self.plot_widget = None
        self.plot_item = None
        self.image_item = None
        self.upper_scatter_item = None
        self.lower_scatter_item = None
        self.upper_curve_item = None
        self.lower_curve_item = None
        self.color_bar = None

        if not self._using_pyqtgraph:
            raise RuntimeError("PyQtGraph is required for the Type II band-splitting analyzer.")

        self.plot_widget = pg.GraphicsLayoutWidget()
        self.plot_item = self.plot_widget.addPlot()
        self.plot_item.hideButtons()
        self.plot_item.setMenuEnabled(False)
        self.plot_item.invertY(False)
        self.plot_item.setLabel("bottom", "Time (s)")
        self.plot_item.setLabel("left", "Frequency (MHz)")
        self.plot_item.showGrid(x=True, y=True, alpha=0.25)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.plot_item.addItem(self.image_item)

        self.upper_scatter_item = pg.ScatterPlotItem(
            size=9,
            pen=pg.mkPen("#ff5a1f", width=1.3),
            brush=pg.mkBrush("#ff8c42"),
            pxMode=True,
        )
        self.lower_scatter_item = pg.ScatterPlotItem(
            size=9,
            pen=pg.mkPen("#0ea5e9", width=1.3),
            brush=pg.mkBrush("#38bdf8"),
            pxMode=True,
        )
        self.upper_curve_item = pg.PlotCurveItem(pen=pg.mkPen("#ff5a1f", width=2.0), antialias=True)
        self.lower_curve_item = pg.PlotCurveItem(pen=pg.mkPen("#0ea5e9", width=2.0), antialias=True)
        self.plot_item.addItem(self.upper_curve_item)
        self.plot_item.addItem(self.lower_curve_item)
        self.plot_item.addItem(self.upper_scatter_item)
        self.plot_item.addItem(self.lower_scatter_item)

        try:
            sample = np.linspace(0.0, 1.0, 256)
            color_map, _lut = _mpl_cmap_to_lookup(self.cmap)
            if color_map is None:
                rgba = np.asarray(self.cmap(sample), dtype=float)
                rgba = np.clip(rgba, 0.0, 1.0)
                rgba = (rgba * 255.0).astype(np.ubyte)
                colors = [tuple(int(v) for v in row[:4]) for row in rgba]
                color_map = pg.ColorMap(sample, colors)
            self.color_bar = pg.ColorBarItem(values=(0.0, 1.0), colorMap=color_map, interactive=False)
            self.color_bar.setImageItem(self.image_item, insert_in=self.plot_item)
        except Exception:
            self.color_bar = None

        self.plot_widget.scene().sigMouseClicked.connect(self._on_plot_clicked)

        self.band_combo = QComboBox()
        self.band_combo.addItem("Upper Band", "upper")
        self.band_combo.addItem("Lower Band", "lower")
        self.band_combo.currentIndexChanged.connect(self._on_active_band_changed)

        self.add_points_button = QPushButton("Add Points")
        self.add_points_button.setCheckable(True)
        self.add_points_button.toggled.connect(self._on_add_points_toggled)

        self.undo_button = QPushButton("Undo Last Point")
        self.undo_button.clicked.connect(self._undo_last_point)

        self.clear_button = QPushButton("Clear Active Band")
        self.clear_button.clicked.connect(self._clear_active_band)

        self.fit_active_button = QPushButton("Fit Active Band")
        self.fit_active_button.clicked.connect(self._fit_active_band)

        self.fit_both_button = QPushButton("Fit Both Bands")
        self.fit_both_button.clicked.connect(self._fit_both_bands)

        self.fold_combo = QComboBox()
        self.fold_combo.addItems(["1", "2", "3", "4"])
        self.fold_combo.currentIndexChanged.connect(self._on_fold_changed)

        self.calculate_button = QPushButton("Calculate")
        self.calculate_button.clicked.connect(self._calculate_parameters)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Active Band:"))
        controls.addWidget(self.band_combo)
        controls.addWidget(self.add_points_button)
        controls.addWidget(self.undo_button)
        controls.addWidget(self.clear_button)
        controls.addWidget(self.fit_active_button)
        controls.addWidget(self.fit_both_button)
        controls.addWidget(QLabel("Fold-number:"))
        controls.addWidget(self.fold_combo)
        controls.addWidget(self.calculate_button)
        controls.addStretch(1)

        left_layout = QVBoxLayout()
        left_layout.addLayout(controls)
        left_layout.addWidget(self.plot_widget)

        self.upper_fit_label = QLabel("")
        self.upper_stats_label = QLabel("")
        self.lower_fit_label = QLabel("")
        self.lower_stats_label = QLabel("")
        self.start_time_label = QLabel("")
        self.start_freq_label = QLabel("")
        self.bandwidth_label = QLabel("")
        self.compression_label = QLabel("")
        self.drift_label = QLabel("")
        self.shock_speed_label = QLabel("")
        self.shock_height_label = QLabel("")
        self.mach_label = QLabel("")
        self.alfven_speed_label = QLabel("")
        self.magnetic_field_label = QLabel("")
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)

        right_inner = QVBoxLayout()
        for widget in (
            QLabel("<b>Upper Band Fit</b>"),
            self.upper_fit_label,
            self.upper_stats_label,
            QLabel("<b>Lower Band Fit</b>"),
            self.lower_fit_label,
            self.lower_stats_label,
            QLabel("<b>Derived Parameters</b>"),
            self.start_time_label,
            self.start_freq_label,
            self.bandwidth_label,
            self.compression_label,
            self.drift_label,
            self.shock_speed_label,
            self.shock_height_label,
            self.mach_label,
            self.alfven_speed_label,
            self.magnetic_field_label,
            self.warning_label,
        ):
            right_inner.addWidget(widget)
        right_inner.addStretch(1)

        right_widget = QWidget()
        right_widget.setLayout(right_inner)
        right_scroll = QScrollArea()
        right_scroll.setWidget(right_widget)
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(340)

        content = QHBoxLayout()
        content.addLayout(left_layout, stretch=3)
        content.addWidget(right_scroll, stretch=1)

        self.status = QStatusBar()
        self.status.showMessage("Select an active band, enable 'Add Points', and click along the split lanes.")

        root = QVBoxLayout()
        root.addLayout(content)
        root.addWidget(self.status)
        self.setLayout(root)

        if isinstance(session, dict):
            self.restore_session(session, emit_change=False)
        else:
            self._refresh_plot()
            self._update_fit_labels()
            self._update_result_labels()
            self._sync_controls()

    def _active_band_key(self) -> str:
        return str(self.band_combo.currentData() or "upper")

    def _band_points(self, band: str) -> list[tuple[float, float]]:
        return self._upper_points if band == "upper" else self._lower_points

    def _band_fit(self, band: str) -> dict[str, Any] | None:
        return self._upper_fit if band == "upper" else self._lower_fit

    def _set_band_fit(self, band: str, fit: dict[str, Any] | None) -> None:
        if band == "upper":
            self._upper_fit = fit
        else:
            self._lower_fit = fit

    def _selected_fold(self) -> int:
        try:
            return max(1, min(4, int(self.fold_combo.currentText())))
        except Exception:
            return 1

    @staticmethod
    def _compact_fit_state(fit: dict[str, Any] | None) -> dict[str, Any]:
        src = dict(fit or {})
        if not src:
            return {}
        return {
            "a": float(src.get("a")),
            "b": float(src.get("b")),
            "std_errs": [
                float(v) if v is not None and np.isfinite(v) else None
                for v in list(src.get("std_errs") or [None, None])[:2]
            ],
            "r2": float(src.get("r2")) if src.get("r2") is not None else None,
            "rmse": float(src.get("rmse")) if src.get("rmse") is not None else None,
            "point_count": int(src.get("point_count", 0) or 0) or None,
        }

    @staticmethod
    def _points_to_arrays(points: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
        if not points:
            return np.array([], dtype=float), np.array([], dtype=float)
        arr = np.asarray(points, dtype=float)
        return arr[:, 0].reshape(-1), arr[:, 1].reshape(-1)

    @staticmethod
    def _format_value(value, digits: int = 4, suffix: str = "") -> str:
        if value is None:
            return ""
        try:
            return f"{float(value):.{digits}f}{suffix}"
        except Exception:
            return ""

    def _emit_session_changed(self) -> None:
        if self._suppress_emit:
            return
        try:
            self.sessionChanged.emit(self.session_state())
        except Exception:
            pass

    def session_state(self) -> dict:
        base = dict(self._session_context or {})
        upper_t, upper_f = self._points_to_arrays(self._upper_points)
        lower_t, lower_f = self._points_to_arrays(self._lower_points)
        ui_block = dict(base.get("ui") or {})
        return {
            "source": dict(base.get("source") or {}),
            "max_intensity": dict(base.get("max_intensity") or {}),
            "analyzer": dict(base.get("analyzer") or {}),
            "type_ii": {
                "upper": {
                    "time_seconds": upper_t,
                    "freqs": upper_f,
                },
                "lower": {
                    "time_seconds": lower_t,
                    "freqs": lower_f,
                },
                "upper_fit": self._compact_fit_state(self._upper_fit),
                "lower_fit": self._compact_fit_state(self._lower_fit),
                "fold": self._selected_fold(),
                "results": dict(self._results or {}),
            },
            "ui": {
                "restore_max_window": bool(ui_block.get("restore_max_window", bool(base.get("max_intensity")))),
                "restore_analyzer_window": bool(ui_block.get("restore_analyzer_window", bool(base.get("analyzer")))),
                "restore_type_ii_window": True,
                "auto_outlier_cleaned": bool(ui_block.get("auto_outlier_cleaned", False)),
                "auto_removed_count": int(ui_block.get("auto_removed_count", 0) or 0),
            },
        }

    def restore_session(self, session: dict, *, emit_change: bool = True) -> None:
        self._suppress_emit = True
        try:
            self._session_context = dict(session or {})
            type_ii = dict((session or {}).get("type_ii") or {})
            upper = dict(type_ii.get("upper") or {})
            lower = dict(type_ii.get("lower") or {})
            upper_times = np.asarray(upper.get("time_seconds", []), dtype=float).reshape(-1)
            upper_freqs = np.asarray(upper.get("freqs", []), dtype=float).reshape(-1)
            lower_times = np.asarray(lower.get("time_seconds", []), dtype=float).reshape(-1)
            lower_freqs = np.asarray(lower.get("freqs", []), dtype=float).reshape(-1)

            self._upper_points = list(zip(upper_times.tolist(), upper_freqs.tolist())) if upper_times.size == upper_freqs.size else []
            self._lower_points = list(zip(lower_times.tolist(), lower_freqs.tolist())) if lower_times.size == lower_freqs.size else []
            self._upper_fit = self._compact_fit_state(type_ii.get("upper_fit")) or None
            self._lower_fit = self._compact_fit_state(type_ii.get("lower_fit")) or None
            self._results = dict(type_ii.get("results") or {})

            fold = int(type_ii.get("fold", self._selected_fold()) or self._selected_fold())
            fold = max(1, min(4, fold))
            self.fold_combo.setCurrentIndex(fold - 1)

            self._refresh_plot()
            self._update_fit_labels()
            self._update_result_labels()
            self._sync_controls()
        finally:
            self._suppress_emit = False
        if emit_change:
            self._emit_session_changed()

    def _invalidate_results(self) -> None:
        self._results = {}

    def _invalidate_band_state(self, band: str) -> None:
        self._set_band_fit(band, None)
        self._invalidate_results()
        self._update_fit_labels()
        self._update_result_labels()
        self._sync_controls()

    def _on_active_band_changed(self, _index: int) -> None:
        self._sync_controls()

    def _on_add_points_toggled(self, checked: bool) -> None:
        if checked:
            self.status.showMessage(f"Click along the {self.band_combo.currentText().lower()} on the dynamic spectrum.")
        else:
            self.status.showMessage("Point capture paused.")

    def _on_fold_changed(self, _index: int) -> None:
        if self._results:
            self._invalidate_results()
            self._update_result_labels()
            self.status.showMessage("Fold number changed. Recalculate derived parameters.", 3000)
            self._emit_session_changed()
        self._sync_controls()

    def _on_plot_clicked(self, event) -> None:
        if not self.add_points_button.isChecked() or event is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.plot_item is None:
            return
        pos = event.scenePos()
        if not self.plot_item.sceneBoundingRect().contains(pos):
            return
        mapped = self.plot_item.vb.mapSceneToView(pos)
        x = float(mapped.x())
        y = float(mapped.y())
        if not (np.isfinite(x) and np.isfinite(y)):
            return

        band = self._active_band_key()
        self._band_points(band).append((x, y))
        self._invalidate_band_state(band)
        self._refresh_plot()
        self.status.showMessage(f"Added point to the {self.band_combo.currentText().lower()}.", 2500)
        self._emit_session_changed()

    def _undo_last_point(self) -> None:
        band = self._active_band_key()
        points = self._band_points(band)
        if not points:
            self.status.showMessage("No points to undo for the active band.", 2500)
            return
        points.pop()
        self._invalidate_band_state(band)
        self._refresh_plot()
        self.status.showMessage(f"Removed last point from the {self.band_combo.currentText().lower()}.", 2500)
        self._emit_session_changed()

    def _clear_active_band(self) -> None:
        band = self._active_band_key()
        points = self._band_points(band)
        if not points:
            self.status.showMessage("The active band has no points to clear.", 2500)
            return
        points.clear()
        self._invalidate_band_state(band)
        self._refresh_plot()
        self.status.showMessage(f"Cleared all points from the {self.band_combo.currentText().lower()}.", 2500)
        self._emit_session_changed()

    def _fit_band(self, band: str) -> None:
        times, freqs = self._points_to_arrays(self._band_points(band))
        fit = fit_power_law(times, freqs)
        self._set_band_fit(band, self._compact_fit_state(fit))
        self._invalidate_results()

    def _fit_active_band(self) -> None:
        band = self._active_band_key()
        try:
            self._fit_band(band)
        except ValueError as exc:
            QMessageBox.warning(self, "Type II Band-splitting", str(exc))
            self.status.showMessage(str(exc), 3500)
            return
        self._refresh_plot()
        self._update_fit_labels()
        self._update_result_labels()
        self._sync_controls()
        self.status.showMessage(f"Fitted the {self.band_combo.currentText().lower()}.", 2500)
        self._emit_session_changed()

    def _fit_both_bands(self) -> None:
        try:
            self._fit_band("upper")
            self._fit_band("lower")
        except ValueError as exc:
            QMessageBox.warning(self, "Type II Band-splitting", str(exc))
            self.status.showMessage(str(exc), 3500)
            return
        self._refresh_plot()
        self._update_fit_labels()
        self._update_result_labels()
        self._sync_controls()
        self.status.showMessage("Fitted both split bands.", 2500)
        self._emit_session_changed()

    def _calculate_parameters(self) -> None:
        if self._upper_fit is None or self._lower_fit is None:
            QMessageBox.information(self, "Type II Band-splitting", "Fit both bands before calculating.")
            return

        upper_t, upper_f = self._points_to_arrays(self._upper_points)
        lower_t, lower_f = self._points_to_arrays(self._lower_points)
        try:
            self._results = calculate_type_ii_parameters(
                upper_time_seconds=upper_t,
                upper_freqs_mhz=upper_f,
                lower_time_seconds=lower_t,
                lower_freqs_mhz=lower_f,
                upper_fit=self._upper_fit,
                lower_fit=self._lower_fit,
                fold=self._selected_fold(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Type II Band-splitting", str(exc))
            self.status.showMessage(str(exc), 3500)
            return

        self._update_result_labels()
        self._sync_controls()
        warning = str(self._results.get("warning") or "")
        if warning:
            self.status.showMessage(warning, 5000)
        else:
            self.status.showMessage("Calculated Type II band-splitting parameters.", 3000)
        self._emit_session_changed()

    def _fit_curve_samples(self, band: str) -> tuple[np.ndarray, np.ndarray] | None:
        fit = self._band_fit(band)
        points = self._band_points(band)
        if not fit or not points:
            return None
        times, _freqs = self._points_to_arrays(points)
        valid_times = times[np.isfinite(times) & (times > 0.0)]
        if valid_times.size < 2:
            return None
        xs = np.linspace(float(np.min(valid_times)), float(np.max(valid_times)), 400)
        ys = np.asarray(power_law(xs, float(fit["a"]), float(fit["b"])), dtype=float)
        return xs, ys

    def _refresh_plot(self) -> None:
        if self.plot_item is None or self.image_item is None:
            return

        arr = np.ascontiguousarray(np.asarray(self.display_data, dtype=np.float32))
        extent = pyqtgraph_extent(self.freqs, self.time_seconds, default_step=self.frequency_step_mhz)
        x0, x1, y0, y1 = (float(extent[0]), float(extent[1]), float(extent[2]), float(extent[3]))
        self.image_item.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

        vmin, vmax = finite_data_limits(arr)
        if vmin is None or vmax is None:
            self.image_item.clear()
        else:
            has_invalid = bool(np.any(~np.isfinite(arr)))
            if has_invalid:
                rgba = _rgba_image_from_cmap(arr, self.cmap, vmin=vmin, vmax=vmax)
                try:
                    self.image_item.setLookupTable(None, update=False)
                except Exception:
                    pass
                self.image_item.setImage(rgba, autoLevels=False)
            else:
                self.image_item.setImage(arr, autoLevels=False)
                self.image_item.setLevels((vmin, vmax))
                _color_map, lut = _mpl_cmap_to_lookup(self.cmap)
                if lut is not None:
                    self.image_item.setLookupTable(lut, update=False)

            if self.color_bar is not None:
                try:
                    self.color_bar.setLevels((vmin, vmax))
                    self.color_bar.axis.setLabel(f"Intensity [{self.display_unit}]")
                    color_map, _lut = _mpl_cmap_to_lookup(self.cmap)
                    if color_map is not None:
                        self.color_bar.setColorMap(color_map)
                except Exception:
                    pass

        self.plot_item.setTitle(f"{self.filename}_Type_II_Band_Splitting")
        self.plot_item.setLabel("bottom", "Time (s)")
        self.plot_item.setLabel("left", "Frequency (MHz)")

        if self._upper_points:
            upper = np.asarray(self._upper_points, dtype=float)
            self.upper_scatter_item.setData(x=upper[:, 0], y=upper[:, 1])
        else:
            self.upper_scatter_item.setData([], [])
        if self._lower_points:
            lower = np.asarray(self._lower_points, dtype=float)
            self.lower_scatter_item.setData(x=lower[:, 0], y=lower[:, 1])
        else:
            self.lower_scatter_item.setData([], [])

        upper_curve = self._fit_curve_samples("upper")
        if upper_curve is not None:
            self.upper_curve_item.setData(upper_curve[0], upper_curve[1])
        else:
            self.upper_curve_item.setData([], [])
        lower_curve = self._fit_curve_samples("lower")
        if lower_curve is not None:
            self.lower_curve_item.setData(lower_curve[0], lower_curve[1])
        else:
            self.lower_curve_item.setData([], [])

        self.plot_item.enableAutoRange()

    def _update_fit_labels(self) -> None:
        def _set_for(band_fit: dict[str, Any] | None, eq_label: QLabel, stats_label: QLabel, band_name: str) -> None:
            if not band_fit:
                eq_label.setText(f"{band_name}: not fitted.")
                stats_label.setText(f"{band_name} points: {len(self._band_points('upper' if 'Upper' in band_name else 'lower'))}")
                return
            eq_label.setText(
                f"{band_name}: <b>f(x) = {float(band_fit['a']):.2f} · x<sup>-{float(band_fit['b']):.2f}</sup></b>"
            )
            stats_label.setText(
                "R² = "
                f"{self._format_value(band_fit.get('r2'), 4)} | RMSE = {self._format_value(band_fit.get('rmse'), 4)} | "
                f"Points = {int(band_fit.get('point_count', 0) or 0)}"
            )

        _set_for(self._upper_fit, self.upper_fit_label, self.upper_stats_label, "Upper Fit")
        _set_for(self._lower_fit, self.lower_fit_label, self.lower_stats_label, "Lower Fit")

    def _update_result_labels(self) -> None:
        if not self._results:
            self.start_time_label.setText("Start time: ")
            self.start_freq_label.setText("Start frequencies: ")
            self.bandwidth_label.setText("Bandwidth: ")
            self.compression_label.setText("Compression ratio X: ")
            self.drift_label.setText("Upper-band drift rate: ")
            self.shock_speed_label.setText("Shock speed V_s: ")
            self.shock_height_label.setText("Shock height: ")
            self.mach_label.setText("Alfven Mach number M_A: ")
            self.alfven_speed_label.setText("Alfven speed V_A: ")
            self.magnetic_field_label.setText("Magnetic field B: ")
            self.warning_label.setText("")
            return

        result = dict(self._results)
        self.start_time_label.setText(f"Start time: <b>{self._format_value(result.get('start_time_s'), 4, ' s')}</b>")
        self.start_freq_label.setText(
            "Start frequencies: "
            f"<b>f_u = {self._format_value(result.get('upper_start_freq_mhz'), 4, ' MHz')}</b>, "
            f"<b>f_l = {self._format_value(result.get('lower_start_freq_mhz'), 4, ' MHz')}</b>"
        )
        self.bandwidth_label.setText(f"Bandwidth (f_u - f_l): <b>{self._format_value(result.get('bandwidth_mhz'), 4, ' MHz')}</b>")
        self.compression_label.setText(f"Compression ratio X: <b>{self._format_value(result.get('compression_ratio'), 4)}</b>")
        self.drift_label.setText(
            f"Upper-band drift rate: <b>{self._format_value(result.get('upper_drift_mhz_s'), 4, ' MHz/s')}</b>"
        )
        self.shock_speed_label.setText(f"Shock speed V_s: <b>{self._format_value(result.get('shock_speed_km_s'), 2, ' km/s')}</b>")
        self.shock_height_label.setText(f"Shock height: <b>{self._format_value(result.get('shock_height_rs'), 4, ' R_s')}</b>")
        self.mach_label.setText(f"Alfven Mach number M_A: <b>{self._format_value(result.get('alfven_mach_number'), 4)}</b>")
        self.alfven_speed_label.setText(
            f"Alfven speed V_A: <b>{self._format_value(result.get('alfven_speed_km_s'), 2, ' km/s')}</b>"
        )
        self.magnetic_field_label.setText(
            f"Magnetic field B: <b>{self._format_value(result.get('magnetic_field_g'), 4, ' G')}</b>"
        )
        self.warning_label.setText(str(result.get("warning") or ""))

    def _sync_controls(self) -> None:
        active_points = len(self._band_points(self._active_band_key()))
        self.undo_button.setEnabled(active_points > 0)
        self.clear_button.setEnabled(active_points > 0)
        self.fit_active_button.setEnabled(active_points >= 2)
        self.fit_both_button.setEnabled(len(self._upper_points) >= 2 and len(self._lower_points) >= 2)
        self.calculate_button.setEnabled(self._upper_fit is not None and self._lower_fit is not None)

    def closeEvent(self, event) -> None:
        try:
            self._emit_session_changed()
        except Exception:
            pass
        event.accept()
