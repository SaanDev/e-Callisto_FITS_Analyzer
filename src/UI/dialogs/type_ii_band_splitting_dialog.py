"""
Type II band-splitting analysis dialog.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from matplotlib import colormaps
import numpy as np
from PySide6.QtCore import Signal, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPageLayout, QPalette, QPdfWriter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.Backend.frequency_axis import axis_edges, finite_data_limits, frequency_edges
from src.Backend.type_ii_band_splitting import calculate_b_vs_r_profile, calculate_type_ii_parameters, fit_power_law, power_law
from src.UI.accelerated_plot_widget import _mpl_cmap_to_lookup, _rgba_image_from_cmap, pg
from src.UI.gui_shared import pick_export_path, resource_path


class TypeIIBandSplittingDialog(QDialog):
    sessionChanged = Signal(dict)
    _ICON_SIZE = QSize(40, 40)
    _ICON_BUTTON_SIZE = QSize(52, 52)

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
        self.theme = QApplication.instance().property("theme_manager") if QApplication.instance() else None
        if self.theme and hasattr(self.theme, "themeChanged"):
            self.theme.themeChanged.connect(self._on_theme_changed)

        self._upper_points: list[tuple[float, float]] = []
        self._lower_points: list[tuple[float, float]] = []
        self._upper_fit: dict[str, Any] | None = None
        self._lower_fit: dict[str, Any] | None = None
        self._results: dict[str, Any] = {}
        self._plot_mode = "spectrum"

        self.plot_widget = None
        self.plot_item = None
        self.image_item = None
        self.upper_scatter_item = None
        self.lower_scatter_item = None
        self.upper_curve_item = None
        self.lower_curve_item = None
        self.bvr_scatter_item = None
        self.bvr_curve_item = None
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
        self.bvr_scatter_item = pg.ScatterPlotItem(
            size=8,
            pen=pg.mkPen("#10b981", width=1.3),
            brush=pg.mkBrush("#34d399"),
            pxMode=True,
        )
        self.bvr_curve_item = pg.PlotCurveItem(pen=pg.mkPen("#f59e0b", width=2.2), antialias=True)
        self.plot_item.addItem(self.upper_curve_item)
        self.plot_item.addItem(self.lower_curve_item)
        self.plot_item.addItem(self.upper_scatter_item)
        self.plot_item.addItem(self.lower_scatter_item)
        self.plot_item.addItem(self.bvr_curve_item)
        self.plot_item.addItem(self.bvr_scatter_item)
        self.bvr_scatter_item.hide()
        self.bvr_curve_item.hide()

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

        self.add_points_button = self._build_icon_button("Add Points", "add_points.svg", checkable=True)
        self.add_points_button.toggled.connect(self._on_add_points_toggled)

        self.undo_button = self._build_icon_button("Undo Last Point", "undo_last_point.svg")
        self.undo_button.clicked.connect(self._undo_last_point)

        self.clear_button = self._build_icon_button("Clear Active Band", "clear_active_band.svg")
        self.clear_button.clicked.connect(self._clear_active_band)

        self.fit_active_button = self._build_icon_button("Fit Active Band", "fit_active_band.svg")
        self.fit_active_button.clicked.connect(self._fit_active_band)

        self.fit_both_button = self._build_icon_button("Fit Both Bands", "fit_both_bands.svg")
        self.fit_both_button.clicked.connect(self._fit_both_bands)

        self.save_plot_button = self._build_icon_button("Save Plot", "save_plot.svg")
        self.save_plot_button.clicked.connect(self._save_plot)

        self.bvr_button = self._build_icon_button("BvR", "plot_BvR.svg", checkable=True)
        self.bvr_button.toggled.connect(self._on_bvr_toggled)

        self.settings_button = self._build_icon_button("Settings", "settings.svg")
        self.settings_button.clicked.connect(lambda: self._show_placeholder_action("Settings"))

        self.speed_mode_combo = QComboBox()
        self.speed_mode_combo.addItem("Initial Shock Speed", "initial")
        self.speed_mode_combo.addItem("Average Shock Speed", "average")
        self.speed_mode_combo.currentIndexChanged.connect(self._on_speed_mode_changed)

        self.calculate_button = QPushButton("Calculate")
        self.calculate_button.clicked.connect(self._calculate_parameters)

        controls = QHBoxLayout()
        controls.addWidget(self.add_points_button)
        controls.addWidget(self.undo_button)
        controls.addWidget(self.clear_button)
        controls.addWidget(self.fit_active_button)
        controls.addWidget(self.fit_both_button)
        controls.addWidget(self.save_plot_button)
        controls.addWidget(self.bvr_button)
        controls.addWidget(self.settings_button)
        controls.addStretch(1)

        left_layout = QVBoxLayout()
        left_layout.addLayout(controls)
        left_layout.addWidget(self.plot_widget)

        self.upper_fit_label = QLabel("")
        self.upper_stats_label = QLabel("")
        self.lower_fit_label = QLabel("")
        self.lower_stats_label = QLabel("")
        self.active_band_label = QLabel("Active Band:")
        self.speed_mode_label = QLabel("Shock Speed:")
        self.analyzer_fold_label = QLabel("")
        self.analyzer_start_freq_label = QLabel("")
        self.analyzer_avg_drift_label = QLabel("")
        self.analyzer_initial_speed_label = QLabel("")
        self.analyzer_avg_speed_label = QLabel("")
        self.analyzer_initial_height_label = QLabel("")
        self.analyzer_avg_height_label = QLabel("")
        self.analyzer_status_label = QLabel("")
        self.interval_label = QLabel("")
        self.start_time_label = QLabel("")
        self.start_freq_label = QLabel("")
        self.avg_freqs_label = QLabel("")
        self.bandwidth_label = QLabel("")
        self.upper_drift_label = QLabel("")
        self.compression_label = QLabel("")
        self.mach_label = QLabel("")
        self.alfven_speed_label = QLabel("")
        self.magnetic_field_label = QLabel("")
        self.warning_label = QLabel("")

        for label in (
            self.upper_fit_label,
            self.upper_stats_label,
            self.lower_fit_label,
            self.lower_stats_label,
            self.analyzer_fold_label,
            self.analyzer_start_freq_label,
            self.analyzer_avg_drift_label,
            self.analyzer_initial_speed_label,
            self.analyzer_avg_speed_label,
            self.analyzer_initial_height_label,
            self.analyzer_avg_height_label,
            self.analyzer_status_label,
            self.interval_label,
            self.start_time_label,
            self.start_freq_label,
            self.avg_freqs_label,
            self.bandwidth_label,
            self.upper_drift_label,
            self.compression_label,
            self.mach_label,
            self.alfven_speed_label,
            self.magnetic_field_label,
            self.warning_label,
        ):
            self._configure_detail_label(label)

        controls_panel = QGroupBox("Controls")
        controls_panel_layout = QVBoxLayout(controls_panel)
        controls_panel_layout.setContentsMargins(0, 0, 0, 0)
        controls_panel_layout.setSpacing(8)
        controls_panel_layout.addWidget(self.active_band_label)
        controls_panel_layout.addWidget(self.band_combo)
        controls_panel_layout.addWidget(self.speed_mode_label)
        controls_panel_layout.addWidget(self.speed_mode_combo)
        controls_panel_layout.addWidget(self.calculate_button)

        analyzer_panel = self._make_section_box(
            "Analyzer Reference",
            (
                self.analyzer_status_label,
                self.analyzer_fold_label,
                self.analyzer_start_freq_label,
                self.analyzer_avg_drift_label,
                self.analyzer_initial_speed_label,
                self.analyzer_avg_speed_label,
                self.analyzer_initial_height_label,
                self.analyzer_avg_height_label,
            ),
        )
        fit_panel = self._make_section_box(
            "Fitted Bands",
            (
                self.upper_fit_label,
                self.upper_stats_label,
                self.lower_fit_label,
                self.lower_stats_label,
            ),
        )
        averaging_panel = self._make_section_box(
            "Averaged Band-Splitting Quantities",
            (
                self.interval_label,
                self.start_time_label,
                self.start_freq_label,
                self.avg_freqs_label,
                self.bandwidth_label,
                self.upper_drift_label,
                self.warning_label,
            ),
        )
        plasma_panel = self._make_section_box(
            "Plasma Parameters",
            (
                self.compression_label,
                self.mach_label,
                self.alfven_speed_label,
                self.magnetic_field_label,
            ),
        )

        right_inner = QVBoxLayout()
        for widget in (
            controls_panel,
            analyzer_panel,
            fit_panel,
            averaging_panel,
            plasma_panel,
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
        self._apply_toolbar_icons()
        self._apply_plot_theme()

        if isinstance(session, dict):
            self.restore_session(session, emit_change=False)
        else:
            self._refresh_plot()
            self._update_fit_labels()
            self._update_analysis_input_labels()
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

    def _is_dark_ui(self) -> bool:
        theme = getattr(self, "theme", None)
        if theme is not None:
            flag = getattr(theme, "is_dark", None)
            try:
                if callable(flag):
                    return bool(flag())
                return bool(flag)
            except Exception:
                pass

        app = QApplication.instance()
        if not app:
            return False
        return app.palette().color(QPalette.Window).lightness() < 128

    def _load_icon_file(self, path: str) -> QIcon:
        icon = QIcon(path)
        if not icon.isNull():
            return icon

        if not str(path).lower().endswith(".svg"):
            return QIcon()

        try:
            from PySide6.QtSvg import QSvgRenderer
        except Exception:
            return QIcon()

        try:
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return QIcon()

            size = renderer.defaultSize()
            width = max(32, int(size.width())) if size.isValid() else 48
            height = max(32, int(size.height())) if size.isValid() else 48

            from PySide6.QtGui import QPainter, QPixmap

            pixmap = QPixmap(width, height)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon()

    def _band_splitting_icon(self, filename: str) -> QIcon:
        folder = "dark" if self._is_dark_ui() else "light"
        rels = [
            os.path.join("assets", "band_splitting_icons", folder, filename),
            os.path.join("assets", "band_splitting_icons", "light", filename),
        ]

        bases = []
        if getattr(sys, "frozen", False):
            bases.append(os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "Resources")))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(os.path.abspath(meipass))

        here = os.path.abspath(os.path.dirname(__file__))
        bases.extend(
            [
                os.path.abspath(os.path.join(here, "..", "..", "..")),
                os.path.abspath(os.getcwd()),
                os.path.abspath(os.path.join(os.getcwd(), "..")),
            ]
        )

        seen = set()
        for base in bases:
            if not base or base in seen:
                continue
            seen.add(base)
            for rel in rels:
                path = os.path.normpath(os.path.join(base, rel))
                if os.path.exists(path):
                    icon = self._load_icon_file(path)
                    if not icon.isNull():
                        return icon

        for rel in rels:
            try:
                path = resource_path(rel)
                if os.path.exists(path):
                    icon = self._load_icon_file(path)
                    if not icon.isNull():
                        return icon
            except Exception:
                pass
        return QIcon()

    def _build_icon_button(self, label: str, icon_filename: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setText("")
        button.setCheckable(checkable)
        button.setAutoRaise(False)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(self._ICON_SIZE)
        button.setToolTip(label)
        button.setStatusTip(label)
        button.setAccessibleName(label)
        button.setProperty("band_icon_filename", icon_filename)
        button.setMinimumSize(self._ICON_BUTTON_SIZE)
        button.setMaximumSize(self._ICON_BUTTON_SIZE)
        return button

    @staticmethod
    def _configure_detail_label(label: QLabel) -> None:
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setMargin(2)

    @staticmethod
    def _make_section_box(title: str, widgets: tuple[QWidget, ...]) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        for widget in widgets:
            layout.addWidget(widget)
        return box

    @staticmethod
    def _detail_block(title: str, value_html: str, *, muted: bool = False) -> str:
        color = "#7a7a7a" if muted else "#5f7693"
        return (
            f"<div style='margin-bottom:6px;'>"
            f"<span style='font-size:11px; color:{color};'><b>{title}</b></span><br>"
            f"<span style='font-size:13px;'><b>{value_html}</b></span>"
            f"</div>"
        )

    def _apply_toolbar_icons(self) -> None:
        for button in (
            getattr(self, "add_points_button", None),
            getattr(self, "undo_button", None),
            getattr(self, "clear_button", None),
            getattr(self, "fit_active_button", None),
            getattr(self, "fit_both_button", None),
            getattr(self, "save_plot_button", None),
            getattr(self, "bvr_button", None),
            getattr(self, "settings_button", None),
        ):
            if button is None:
                continue
            filename = str(button.property("band_icon_filename") or "")
            if not filename:
                continue
            button.setIcon(self._band_splitting_icon(filename))

    def _plot_theme_colors(self) -> tuple[str, str]:
        if self._is_dark_ui():
            return "#111111", "#f2f2f2"
        return "#ffffff", "#101010"

    def _apply_plot_theme(self) -> None:
        if self.plot_widget is None or self.plot_item is None or pg is None:
            return

        bg, fg = self._plot_theme_colors()
        self.plot_widget.setBackground(bg)

        try:
            view_box = self.plot_item.getViewBox()
            if view_box is not None and hasattr(view_box, "setBackgroundColor"):
                view_box.setBackgroundColor(bg)
        except Exception:
            pass

        axis_pen = pg.mkPen(fg, width=1.0)
        for axis_name in ("bottom", "left"):
            try:
                axis = self.plot_item.getAxis(axis_name)
                if axis is None:
                    continue
                axis.setPen(axis_pen)
                axis.setTextPen(axis_pen)
            except Exception:
                pass

        try:
            self.plot_item.titleLabel.item.setDefaultTextColor(QColor(fg))
        except Exception:
            pass

        if self.color_bar is not None:
            try:
                self.color_bar.axis.setPen(axis_pen)
                self.color_bar.axis.setTextPen(axis_pen)
            except Exception:
                pass

    def _show_placeholder_action(self, label: str) -> None:
        self.status.showMessage(f"{label} is not implemented yet.", 3000)

    def _on_theme_changed(self, _dark: bool) -> None:
        self._apply_toolbar_icons()
        self._apply_plot_theme()

    def _set_bvr_button_checked(self, checked: bool) -> None:
        if self.bvr_button is None:
            return
        blocked = self.bvr_button.blockSignals(True)
        try:
            self.bvr_button.setChecked(bool(checked))
        finally:
            self.bvr_button.blockSignals(blocked)

    def _build_bvr_profile(self) -> dict[str, Any]:
        if self._upper_fit is None or self._lower_fit is None:
            raise ValueError("Fit both bands before plotting B versus R.")

        analysis_inputs = self._analysis_inputs_from_context()
        if not self._analysis_inputs_ready(analysis_inputs):
            raise ValueError(
                "Run the usual Analyzer window first so the shock-speed and height references are available."
            )

        selected_shock_speed = self._selected_analysis_shock_speed(analysis_inputs)
        if selected_shock_speed is None:
            raise ValueError("The selected analyzer shock speed is unavailable.")

        upper_t, upper_f = self._points_to_arrays(self._upper_points)
        lower_t, lower_f = self._points_to_arrays(self._lower_points)
        return calculate_b_vs_r_profile(
            upper_time_seconds=upper_t,
            upper_freqs_mhz=upper_f,
            lower_time_seconds=lower_t,
            lower_freqs_mhz=lower_f,
            upper_fit=self._upper_fit,
            lower_fit=self._lower_fit,
            analysis_shock_speed_km_s=float(selected_shock_speed),
            fold=int(analysis_inputs.get("fold", 1) or 1),
            available_time_seconds=self.time_seconds,
        )

    def _on_bvr_toggled(self, checked: bool) -> None:
        if checked:
            try:
                self._build_bvr_profile()
            except ValueError as exc:
                QMessageBox.information(self, "Type II Band-splitting", str(exc))
                self.status.showMessage(str(exc), 4000)
                self._set_bvr_button_checked(False)
                return
            self._plot_mode = "bvr"
            self.add_points_button.setChecked(False)
            self.status.showMessage("Showing magnetic field versus shock height.", 3000)
        else:
            self._plot_mode = "spectrum"
            self.status.showMessage("Returned to the Type II dynamic spectrum.", 3000)
        self._refresh_plot()
        self._sync_controls()

    @staticmethod
    def _normalize_export_extension(ext_value: str) -> str:
        if not ext_value:
            return "png"
        value = str(ext_value).strip().lower()
        if value.startswith("."):
            value = value[1:]
        if "(*." in value:
            try:
                value = value.split("(*.", 1)[1].split(")", 1)[0].split()[0].strip()
            except Exception:
                pass
        return value or "png"

    def _default_export_name(self) -> str:
        return f"{self.filename}_Type_II_Band_Splitting"

    def _time_step_seconds(self) -> float:
        arr = np.asarray(self.time_seconds, dtype=float).reshape(-1)
        if arr.size < 2:
            return 1.0
        diffs = np.diff(arr)
        diffs = np.abs(diffs[np.isfinite(diffs)])
        diffs = diffs[diffs > 1e-9]
        if diffs.size == 0:
            return 1.0
        return float(np.nanmedian(diffs))

    def _plot_exporter_module(self):
        if self.plot_item is None:
            raise RuntimeError("Type II plot is not available for export.")
        try:
            import pyqtgraph.exporters as pg_exporters
        except Exception as exc:
            raise RuntimeError("PyQtGraph exporters are unavailable.") from exc
        return pg_exporters

    def _render_export_image(self, *, min_width: int = 2400) -> QImage:
        pg_exporters = self._plot_exporter_module()
        QApplication.processEvents()
        exporter = pg_exporters.ImageExporter(self.plot_item)
        params = exporter.parameters()
        try:
            width = max(
                min_width,
                int(self.plot_widget.width() * self.plot_widget.devicePixelRatioF()),
                int(self.plot_item.sceneBoundingRect().width()),
            )
        except Exception:
            width = max(min_width, int(self.plot_widget.width()))
        params["width"] = max(1, width)
        image = exporter.export(toBytes=True)
        if image is None or image.isNull():
            raise RuntimeError("Could not render the Type II plot image.")
        return image

    def _export_plot_raster(self, file_path: str, ext: str) -> None:
        pg_exporters = self._plot_exporter_module()
        QApplication.processEvents()
        exporter = pg_exporters.ImageExporter(self.plot_item)
        params = exporter.parameters()
        try:
            width = max(
                2400,
                int(self.plot_widget.width() * self.plot_widget.devicePixelRatioF()),
                int(self.plot_item.sceneBoundingRect().width()),
            )
        except Exception:
            width = max(2400, int(self.plot_widget.width()))
        params["width"] = max(1, width)
        if not exporter.export(file_path):
            raise RuntimeError(f"Could not save raster export as {ext}.")

    def _export_plot_pdf(self, file_path: str) -> None:
        image = self._render_export_image(min_width=2600)
        writer = QPdfWriter(file_path)
        writer.setResolution(300)
        writer.setPageOrientation(QPageLayout.Orientation.Landscape)
        painter = QPainter(writer)
        try:
            target = writer.pageLayout().paintRectPixels(writer.resolution())
            scaled = image.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            x = int(target.x() + (target.width() - scaled.width()) / 2)
            y = int(target.y() + (target.height() - scaled.height()) / 2)
            painter.drawImage(x, y, scaled)
        finally:
            painter.end()

    def _export_plot_svg(self, file_path: str) -> None:
        pg_exporters = self._plot_exporter_module()
        QApplication.processEvents()
        exporter = pg_exporters.SVGExporter(self.plot_item)
        params = exporter.parameters()
        try:
            params["width"] = max(2400.0, float(self.plot_item.sceneBoundingRect().width()))
        except Exception:
            pass
        exporter.export(file_path)

    def _save_plot(self) -> None:
        formats = "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tiff);;JPG (*.jpg *.jpeg)"
        file_path, ext = pick_export_path(
            self,
            "Export Figure",
            self._default_export_name(),
            formats,
            default_filter="PNG (*.png)",
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
                ext_final = self._normalize_export_extension(ext)
                file_path = f"{file_path}.{ext_final}"
            else:
                ext_final = current_ext.lower().lstrip(".")

            if ext_final in {"png", "tif", "tiff", "jpg", "jpeg"}:
                self._export_plot_raster(file_path, ext_final)
            elif ext_final == "pdf":
                self._export_plot_pdf(file_path)
            elif ext_final == "svg":
                self._export_plot_svg(file_path)
            else:
                raise RuntimeError(f"Unsupported export format: {ext_final}")

            QMessageBox.information(self, "Export Complete", f"Plot saved:\n{file_path}")
            self.status.showMessage("Export successful!", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not save file:\n{exc}")
            self.status.showMessage("Export failed!", 3000)

    @staticmethod
    def _safe_float_value(value) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_speed_mode(value: Any) -> str:
        mode = str(value or "initial").strip().lower()
        return mode if mode in {"initial", "average"} else "initial"

    def _selected_speed_mode(self) -> str:
        return self._normalize_speed_mode(self.speed_mode_combo.currentData())

    def _analysis_inputs_from_context(self) -> dict[str, Any]:
        type_ii = dict((self._session_context or {}).get("type_ii") or {})
        saved = dict(type_ii.get("analysis_inputs") or {})
        analyzer = dict((self._session_context or {}).get("analyzer") or {})
        shock = dict(analyzer.get("shock_summary") or {})

        def _pick_numeric(key: str) -> float | None:
            value = self._safe_float_value(shock.get(key))
            if value is None:
                value = self._safe_float_value(saved.get(key))
            return value

        fold_value = analyzer.get("fold", shock.get("fold", saved.get("fold", type_ii.get("fold", 1))))
        try:
            fold = max(1, min(4, int(fold_value)))
        except Exception:
            fold = 1

        return {
            "initial_shock_speed_km_s": _pick_numeric("initial_shock_speed_km_s"),
            "avg_shock_speed_km_s": _pick_numeric("avg_shock_speed_km_s"),
            "initial_shock_height_rs": _pick_numeric("initial_shock_height_rs"),
            "avg_shock_height_rs": _pick_numeric("avg_shock_height_rs"),
            "start_freq_mhz": _pick_numeric("start_freq_mhz"),
            "avg_drift_mhz_s": _pick_numeric("avg_drift_mhz_s"),
            "avg_drift_err_mhz_s": _pick_numeric("avg_drift_err_mhz_s"),
            "fold": fold,
            "speed_mode": self._selected_speed_mode(),
        }

    def _analysis_inputs_ready(self, inputs: dict[str, Any] | None = None) -> bool:
        data = dict(inputs or self._analysis_inputs_from_context())
        required = (
            "initial_shock_speed_km_s",
            "avg_shock_speed_km_s",
            "initial_shock_height_rs",
            "avg_shock_height_rs",
            "start_freq_mhz",
        )
        for key in required:
            value = self._safe_float_value(data.get(key))
            if value is None or not np.isfinite(value):
                return False
        return True

    def _selected_analysis_shock_speed(self, inputs: dict[str, Any] | None = None) -> float | None:
        data = dict(inputs or self._analysis_inputs_from_context())
        key = "avg_shock_speed_km_s" if self._selected_speed_mode() == "average" else "initial_shock_speed_km_s"
        value = self._safe_float_value(data.get(key))
        if value is None or not np.isfinite(value) or value <= 0.0:
            return None
        return value

    def _analysis_inputs_match_saved(self, saved: dict[str, Any], current: dict[str, Any]) -> bool:
        if self._normalize_speed_mode(saved.get("speed_mode")) != self._normalize_speed_mode(current.get("speed_mode")):
            return False
        try:
            if int(saved.get("fold")) != int(current.get("fold")):
                return False
        except Exception:
            return False
        for key in (
            "initial_shock_speed_km_s",
            "avg_shock_speed_km_s",
            "initial_shock_height_rs",
            "avg_shock_height_rs",
            "start_freq_mhz",
        ):
            saved_value = self._safe_float_value(saved.get(key))
            current_value = self._safe_float_value(current.get(key))
            if saved_value is None or current_value is None:
                return False
            if not np.isclose(saved_value, current_value, rtol=0.0, atol=1e-9):
                return False
        return True

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

    @classmethod
    def _format_pm(cls, value, error, digits: int = 4, suffix: str = "") -> str:
        base = cls._format_value(value, digits)
        if not base:
            return "—"
        err = cls._format_value(error, digits)
        if err:
            return f"{base} ± {err}{suffix}"
        return f"{base}{suffix}"

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
        analysis_inputs = dict(self._analysis_inputs_from_context())
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
                "analysis_inputs": analysis_inputs,
                "fold": int(analysis_inputs.get("fold", 1) or 1),
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

            analysis_inputs = dict(type_ii.get("analysis_inputs") or {})
            speed_mode = self._normalize_speed_mode(analysis_inputs.get("speed_mode"))
            self.speed_mode_combo.setCurrentIndex(1 if speed_mode == "average" else 0)
            current_inputs = self._analysis_inputs_from_context()
            if self._results and not self._analysis_inputs_match_saved(analysis_inputs, current_inputs):
                self._results = {}

            self._refresh_plot()
            self._update_fit_labels()
            self._update_analysis_input_labels()
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

    def _on_speed_mode_changed(self, _index: int) -> None:
        if self._results:
            self._invalidate_results()
            self._update_result_labels()
            self.status.showMessage("Shock speed source changed. Recalculate plasma parameters.", 3000)
            self._emit_session_changed()
        if self._plot_mode == "bvr":
            self._refresh_plot()
        self._sync_controls()

    def _on_plot_clicked(self, event) -> None:
        if self._plot_mode != "spectrum":
            return
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

        analysis_inputs = self._analysis_inputs_from_context()
        if not self._analysis_inputs_ready(analysis_inputs):
            message = (
                "Run the usual analysis window first so the initial/average shock speeds, "
                "shock heights, and starting frequency are available."
            )
            QMessageBox.information(self, "Type II Band-splitting", message)
            self.status.showMessage(message, 5000)
            return

        selected_shock_speed = self._selected_analysis_shock_speed(analysis_inputs)
        if selected_shock_speed is None:
            message = "The selected analyzer shock speed is unavailable."
            QMessageBox.information(self, "Type II Band-splitting", message)
            self.status.showMessage(message, 4000)
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
                analysis_start_freq_mhz=float(analysis_inputs["start_freq_mhz"]),
                analysis_shock_speed_km_s=float(selected_shock_speed),
                available_time_seconds=self.time_seconds,
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

    def _show_spectrum_items(self) -> None:
        self.image_item.show()
        self.upper_scatter_item.show()
        self.lower_scatter_item.show()
        self.upper_curve_item.show()
        self.lower_curve_item.show()
        self.bvr_scatter_item.hide()
        self.bvr_curve_item.hide()
        if self.color_bar is not None:
            self.color_bar.show()

    def _show_bvr_items(self) -> None:
        self.image_item.hide()
        self.upper_scatter_item.hide()
        self.lower_scatter_item.hide()
        self.upper_curve_item.hide()
        self.lower_curve_item.hide()
        self.bvr_scatter_item.show()
        self.bvr_curve_item.show()
        if self.color_bar is not None:
            self.color_bar.hide()

    def _refresh_spectrum_plot(self) -> None:
        arr = np.ascontiguousarray(np.asarray(self.display_data, dtype=np.float32))
        freq_edges = frequency_edges(self.freqs, default_step=self.frequency_step_mhz or 1.0)
        time_edges = axis_edges(self.time_seconds, default_step=self._time_step_seconds())
        x0 = float(time_edges[0])
        x1 = float(time_edges[-1])
        y0 = float(freq_edges[0])
        y1 = float(freq_edges[-1])
        _, fg = self._plot_theme_colors()

        self._show_spectrum_items()

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

            self.image_item.setRect(x0, y0, x1 - x0, y1 - y0)

            if self.color_bar is not None:
                try:
                    self.color_bar.setLevels((vmin, vmax))
                    self.color_bar.axis.setLabel(f"Intensity [{self.display_unit}]", color=fg)
                    color_map, _lut = _mpl_cmap_to_lookup(self.cmap)
                    if color_map is not None:
                        self.color_bar.setColorMap(color_map)
                except Exception:
                    pass

        self.plot_item.setTitle(f"{self.filename}_Type_II_Band_Splitting")
        self.plot_item.setLabel("bottom", "Time (s)", color=fg)
        self.plot_item.setLabel("left", "Frequency (MHz)", color=fg)

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

    def _refresh_bvr_plot(self) -> None:
        _, fg = self._plot_theme_colors()
        profile = self._build_bvr_profile()
        heights = np.asarray(profile.get("heights_rs", []), dtype=float).reshape(-1)
        magnetic = np.asarray(profile.get("magnetic_field_g", []), dtype=float).reshape(-1)
        fit = dict(profile.get("fit") or {})
        if heights.size < 2 or magnetic.size < 2 or not fit:
            raise ValueError("B versus R plotting requires a valid magnetic-field profile.")

        self._show_bvr_items()
        self.bvr_scatter_item.setData(x=heights, y=magnetic)
        xs = np.linspace(float(np.min(heights)), float(np.max(heights)), 400)
        ys = np.asarray(power_law(xs, float(fit["a"]), float(fit["b"])), dtype=float)
        self.bvr_curve_item.setData(xs, ys)

        self.plot_item.setTitle(f"{self.filename}_Magnetic_Field_vs_Shock_Height")
        self.plot_item.setLabel("bottom", "Shock Height (R<sub>s</sub>)", color=fg)
        self.plot_item.setLabel("left", "Magnetic Field (G)", color=fg)

    def _refresh_plot(self) -> None:
        if self.plot_item is None or self.image_item is None:
            return
        if self._plot_mode == "bvr":
            try:
                self._refresh_bvr_plot()
            except ValueError as exc:
                self._plot_mode = "spectrum"
                self._set_bvr_button_checked(False)
                self._show_spectrum_items()
                self.status.showMessage(str(exc), 4000)
                self._refresh_spectrum_plot()
        else:
            self._refresh_spectrum_plot()

        self._apply_plot_theme()
        self.plot_item.enableAutoRange()

    def _update_fit_labels(self) -> None:
        def _set_for(band_fit: dict[str, Any] | None, eq_label: QLabel, stats_label: QLabel, band_name: str) -> None:
            band_symbol = "u" if "Upper" in band_name else "l"
            if not band_fit:
                eq_label.setText(self._detail_block(
                    f"{band_name}",
                    f"f<sub>{band_symbol}</sub>(t) not fitted",
                    muted=True,
                ))
                stats_label.setText(self._detail_block(
                    "Selected points",
                    str(len(self._band_points('upper' if 'Upper' in band_name else 'lower'))),
                ))
                return
            eq_label.setText(self._detail_block(
                f"{band_name}",
                f"f<sub>{band_symbol}</sub>(t) = {float(band_fit['a']):.2f} t<sup>-{float(band_fit['b']):.2f}</sup>",
            ))
            stats_label.setText(
                self._detail_block("R<sup>2</sup>", self._format_value(band_fit.get('r2'), 4) or "—")
                + self._detail_block("RMSE", self._format_value(band_fit.get('rmse'), 4) or "—")
                + self._detail_block("Selected points", str(int(band_fit.get('point_count', 0) or 0)))
            )

        _set_for(self._upper_fit, self.upper_fit_label, self.upper_stats_label, "Upper Fit")
        _set_for(self._lower_fit, self.lower_fit_label, self.lower_stats_label, "Lower Fit")

    def _update_analysis_input_labels(self) -> None:
        inputs = self._analysis_inputs_from_context()
        self.analyzer_fold_label.setText(self._detail_block("Newkirk model", f"{int(inputs.get('fold', 1) or 1)}-fold"))
        self.analyzer_start_freq_label.setText(self._detail_block(
            "f<sub>start</sub>",
            self._format_value(inputs.get('start_freq_mhz'), 4, ' MHz') or "—",
        ))
        self.analyzer_avg_drift_label.setText(
            self._detail_block(
                "⟨df/dt⟩",
                self._format_pm(inputs.get('avg_drift_mhz_s'), inputs.get('avg_drift_err_mhz_s'), 4, ' MHz/s'),
            )
        )
        self.analyzer_initial_speed_label.setText(self._detail_block(
            "V<sub>s,0</sub>",
            self._format_value(inputs.get('initial_shock_speed_km_s'), 2, ' km/s') or "—",
        ))
        self.analyzer_avg_speed_label.setText(self._detail_block(
            "⟨V<sub>s</sub>⟩",
            self._format_value(inputs.get('avg_shock_speed_km_s'), 2, ' km/s') or "—",
        ))
        self.analyzer_initial_height_label.setText(self._detail_block(
            "R<sub>p,0</sub>",
            self._format_value(inputs.get('initial_shock_height_rs'), 4, ' R<sub>s</sub>') or "—",
        ))
        self.analyzer_avg_height_label.setText(self._detail_block(
            "⟨R<sub>p</sub>⟩",
            self._format_value(inputs.get('avg_shock_height_rs'), 4, ' R<sub>s</sub>') or "—",
        ))

        if self._analysis_inputs_ready(inputs):
            self.analyzer_status_label.setText(
                "<span style='color:#2e7d32;'><b>Analyzer reference data loaded.</b></span>"
            )
        else:
            self.analyzer_status_label.setText(
                "<span style='color:#b26a00;'><b>Analyzer input missing.</b></span><br>"
                "Run the usual Analyzer window first to populate the shock speeds, shock heights, and starting frequency."
            )

    def _update_result_labels(self) -> None:
        if not self._results:
            self.interval_label.setText(self._detail_block("Averaging interval", "—"))
            self.start_time_label.setText(self._detail_block("t<sub>start</sub>", "—"))
            self.start_freq_label.setText(
                self._detail_block("f<sub>u</sub>(t<sub>start</sub>)", "—")
                + self._detail_block("f<sub>l</sub>(t<sub>start</sub>)", "—")
            )
            self.avg_freqs_label.setText(
                self._detail_block("⟨f<sub>u</sub>⟩", "—")
                + self._detail_block("⟨f<sub>l</sub>⟩", "—")
            )
            self.bandwidth_label.setText(self._detail_block("⟨Δf⟩ = ⟨f<sub>u</sub> - f<sub>l</sub>⟩", "—"))
            self.upper_drift_label.setText(self._detail_block("⟨df<sub>u</sub>/dt⟩", "—"))
            self.compression_label.setText(self._detail_block("X = (⟨f<sub>u</sub>⟩ / ⟨f<sub>l</sub>⟩)<sup>2</sup>", "—"))
            self.mach_label.setText(self._detail_block("M<sub>A</sub>", "—"))
            self.alfven_speed_label.setText(self._detail_block("V<sub>A</sub>", "—"))
            self.magnetic_field_label.setText(self._detail_block("B", "—"))
            self.warning_label.setText("")
            return

        result = dict(self._results)
        self.interval_label.setText(self._detail_block(
            "Averaging interval",
            f"[{self._format_value(result.get('start_time_s'), 4, ' s')}, {self._format_value(result.get('end_time_s'), 4, ' s')}]",
        ))
        self.start_time_label.setText(self._detail_block(
            "t<sub>start</sub>",
            self._format_value(result.get('start_time_s'), 4, ' s') or "—",
        ))
        self.start_freq_label.setText(
            self._detail_block(
                "f<sub>u</sub>(t<sub>start</sub>)",
                self._format_value(result.get('upper_start_freq_mhz'), 4, ' MHz') or "—",
            )
            + self._detail_block(
                "f<sub>l</sub>(t<sub>start</sub>)",
                self._format_value(result.get('lower_start_freq_mhz'), 4, ' MHz') or "—",
            )
        )
        self.avg_freqs_label.setText(
            self._detail_block(
                "⟨f<sub>u</sub>⟩",
                self._format_value(result.get('avg_upper_freq_mhz'), 4, ' MHz') or "—",
            )
            + self._detail_block(
                "⟨f<sub>l</sub>⟩",
                self._format_value(result.get('avg_lower_freq_mhz'), 4, ' MHz') or "—",
            )
        )
        self.bandwidth_label.setText(self._detail_block(
            "⟨Δf⟩ = ⟨f<sub>u</sub> - f<sub>l</sub>⟩",
            self._format_value(result.get('bandwidth_mhz'), 4, ' MHz') or "—",
        ))
        self.upper_drift_label.setText(self._detail_block(
            "⟨df<sub>u</sub>/dt⟩",
            self._format_value(result.get('upper_avg_drift_mhz_s'), 4, ' MHz/s') or "—",
        ))
        self.compression_label.setText(self._detail_block(
            "X = (⟨f<sub>u</sub>⟩ / ⟨f<sub>l</sub>⟩)<sup>2</sup>",
            self._format_value(result.get('compression_ratio'), 4) or "—",
        ))
        self.mach_label.setText(self._detail_block(
            "M<sub>A</sub>",
            self._format_value(result.get('alfven_mach_number'), 4) or "—",
        ))
        self.alfven_speed_label.setText(self._detail_block(
            "V<sub>A</sub>",
            self._format_value(result.get('alfven_speed_km_s'), 2, ' km/s') or "—",
        ))
        self.magnetic_field_label.setText(self._detail_block(
            "B",
            self._format_value(result.get('magnetic_field_g'), 4, ' G') or "—",
        ))
        warning = str(result.get("warning") or "").strip()
        self.warning_label.setText(
            f"<span style='color:#b26a00;'><b>Warning:</b> {warning}</span>" if warning else ""
        )

    def _sync_controls(self) -> None:
        active_points = len(self._band_points(self._active_band_key()))
        spectrum_mode = self._plot_mode == "spectrum"
        self.add_points_button.setEnabled(spectrum_mode)
        self.undo_button.setEnabled(spectrum_mode and active_points > 0)
        self.clear_button.setEnabled(spectrum_mode and active_points > 0)
        self.fit_active_button.setEnabled(spectrum_mode and active_points >= 2)
        self.fit_both_button.setEnabled(spectrum_mode and len(self._upper_points) >= 2 and len(self._lower_points) >= 2)
        self.bvr_button.setEnabled(self._upper_fit is not None and self._lower_fit is not None and self._analysis_inputs_ready())
        inputs_ready = self._analysis_inputs_ready()
        self.speed_mode_combo.setEnabled(inputs_ready)
        self.calculate_button.setEnabled(self._upper_fit is not None and self._lower_fit is not None and inputs_ready)

    def closeEvent(self, event) -> None:
        try:
            self._emit_session_changed()
        except Exception:
            pass
        event.accept()
