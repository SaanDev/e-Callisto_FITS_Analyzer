"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.Backend.goes_overlay import GOES_OVERLAY_CHANNEL_ORDER, goes_class_ticks_for_limits, goes_flux_axis_limits

try:
    import pyqtgraph as pg
except Exception:
    pg = None


def _mpl_cmap_to_lookup(cmap):
    if pg is None or cmap is None:
        return None, None
    try:
        sample = np.linspace(0.0, 1.0, 256)
        rgba = np.asarray(cmap(sample), dtype=float)
        rgba = np.clip(rgba, 0.0, 1.0)
        rgba = (rgba * 255.0).astype(np.ubyte)
        colors = [tuple(int(v) for v in row[:4]) for row in rgba]
        color_map = pg.ColorMap(sample, colors)
        lut = color_map.getLookupTable(0.0, 1.0, 256)
        return color_map, lut
    except Exception:
        return None, None


if pg is not None:
    class _TimeAxisItem(pg.AxisItem):
        def __init__(self, orientation="bottom", parent=None):
            super().__init__(orientation=orientation, parent=parent)
            self._use_utc = False
            self._ut_start_sec = None

        def set_time_mode(self, use_utc: bool, ut_start_sec):
            self._use_utc = bool(use_utc)
            self._ut_start_sec = ut_start_sec if ut_start_sec is not None else None
            self.picture = None
            self.update()

        def tickStrings(self, values, scale, spacing):
            if not self._use_utc or self._ut_start_sec is None:
                return super().tickStrings(values, scale, spacing)

            try:
                show_seconds = float(spacing) <= 30.0
            except Exception:
                show_seconds = False

            out = []
            for val in values:
                try:
                    total_seconds = int(round(float(self._ut_start_sec) + float(val)))
                except Exception:
                    out.append("")
                    continue

                hours = int(total_seconds // 3600) % 24
                minutes = int((total_seconds % 3600) // 60)
                seconds = int(total_seconds % 60)
                if show_seconds:
                    out.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
                else:
                    out.append(f"{hours:02d}:{minutes:02d}")
            return out


    class _LogFluxAxisItem(pg.AxisItem):
        def tickStrings(self, values, scale, spacing):
            return ["" for _ in values]
else:
    class _TimeAxisItem:
        pass


    class _LogFluxAxisItem:
        pass


GOES_OVERLAY_CHANNEL_COLORS = {
    "xrsa": "#67f2ff",
    "xrsb": "#ffffff",
}
GOES_OVERLAY_LINE_WIDTH = 3.0


class _SceneEventFilter(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner

    def eventFilter(self, obj, event):
        try:
            return bool(self._owner._handle_scene_event(event))
        except Exception:
            return False


class AcceleratedPlotWidget(QWidget):
    mousePositionChanged = Signal(float, float, bool)
    lassoFinished = Signal(list)
    driftPointAdded = Signal(float, float)
    driftCaptureFinished = Signal(list)
    annotationCaptureFinished = Signal(str, object)
    annotationCaptureCancelled = Signal(str)
    viewInteractionFinished = Signal(dict, dict)
    rectZoomFinished = Signal(dict, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available = pg is not None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._graphics = None
        self._plot = None
        self._viewbox = None
        self._image = None
        self._color_bar = None
        self._bottom_axis = None
        self._goes_axis = None
        self._goes_view = None
        self._goes_curve_items = {}
        self._goes_curve_item = None
        self._goes_overlay_payload = None
        self._goes_visible_channels = ()
        self._goes_overlay_rect_zoom_hidden = False
        self._goes_axis_label = "GOES X-Ray Class"
        self._title = ""
        self._x_label = "Time [s]"
        self._y_label = "Frequency [MHz]"
        self._colorbar_label = ""
        self._fg = "#101010"
        self._full_view = None
        self._block_range_signals = False
        self._interaction_start_view = None
        self._navigation_locked = False
        self._rect_zoom_once = False

        self._font_family = ""
        self._tick_font_px = 11
        self._axis_label_font_px = 12
        self._title_font_px = 14
        self._title_bold = False
        self._title_italic = False
        self._axis_bold = False
        self._axis_italic = False
        self._ticks_bold = False
        self._ticks_italic = False

        self._interaction_mode = None  # None | lasso | drift
        self._lasso_points = []
        self._lasso_line_item = None
        self._lasso_drag_active = False
        self._drift_points = []
        self._drift_scatter_item = None
        self._drift_line_item = None
        self._annotation_items = []
        self._annotation_capture_points = []
        self._annotation_capture_line_item = None
        self._annotation_capture_vertex_item = None
        self._annotation_capture_drag_active = False
        self._annotation_capture_press_scene_pos = None
        self._annotation_capture_press_xy = None
        self._annotation_capture_drag_last_xy = None
        self._annotation_capture_suppress_next_click = False
        self._scene_filter = None

        self._interaction_timer = QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.setInterval(140)
        self._interaction_timer.timeout.connect(self._emit_interaction_finished)

        if not self._available:
            label = QLabel("Hardware-accelerated plotting unavailable.")
            label.setStyleSheet("padding: 6px;")
            layout.addWidget(label)
            return

        try:
            pg.setConfigOptions(useOpenGL=True, antialias=False, imageAxisOrder="row-major")
        except Exception:
            pass

        self._graphics = pg.GraphicsLayoutWidget()
        self.setMouseTracking(True)
        try:
            self._graphics.setMouseTracking(True)
        except Exception:
            pass
        try:
            viewport = self._graphics.viewport()
            if viewport is not None:
                viewport.setMouseTracking(True)
        except Exception:
            pass
        layout.addWidget(self._graphics)

        self._bottom_axis = _TimeAxisItem(orientation="bottom")
        self._plot = self._graphics.addPlot(axisItems={"bottom": self._bottom_axis})
        self._plot.hideButtons()
        self._plot.setMenuEnabled(False)
        # Keep Y increasing upward so axis ticks match Matplotlib ordering.
        self._plot.invertY(False)
        self._plot.setLabel("left", "Frequency [MHz]")
        self._plot.setLabel("bottom", "Time [s]")

        self._viewbox = self._plot.getViewBox()
        self._viewbox.sigRangeChanged.connect(self._on_range_changed)
        try:
            self._viewbox.sigResized.connect(self._sync_goes_overlay_geometry)
        except Exception:
            pass

        self._image = pg.ImageItem(axisOrder="row-major")
        self._plot.addItem(self._image)

        try:
            self._goes_axis = _LogFluxAxisItem("right")
            self._plot.layout.addItem(self._goes_axis, 2, 3)
            self._goes_view = pg.ViewBox(enableMenu=False)
            self._goes_view.setMouseEnabled(x=False, y=False)
            self._goes_view.setZValue(10)
            try:
                self._goes_view.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            except Exception:
                pass
            try:
                self._goes_view.setAcceptHoverEvents(False)
            except Exception:
                pass
            self._plot.scene().addItem(self._goes_view)
            self._goes_axis.linkToView(self._goes_view)
            self._goes_view.setXLink(self._viewbox)
            for idx, key in enumerate(GOES_OVERLAY_CHANNEL_ORDER, start=1):
                curve_item = pg.PlotCurveItem(
                    pen=pg.mkPen(color=GOES_OVERLAY_CHANNEL_COLORS.get(key, "#ffffff"), width=GOES_OVERLAY_LINE_WIDTH + (0.2 if key == "xrsb" else 0.0)),
                    antialias=True,
                )
                curve_item.setZValue(11 + idx)
                try:
                    curve_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                except Exception:
                    pass
                try:
                    curve_item.setAcceptHoverEvents(False)
                except Exception:
                    pass
                self._goes_view.addItem(curve_item)
                self._goes_curve_items[key] = curve_item
            self._goes_curve_item = self._goes_curve_items.get("xrsb") or next(iter(self._goes_curve_items.values()), None)
            self._goes_axis.hide()
            self._sync_goes_overlay_geometry()
        except Exception:
            self._goes_axis = None
            self._goes_view = None
            self._goes_curve_items = {}
            self._goes_curve_item = None

        self._plot.setMouseEnabled(x=True, y=True)
        try:
            self._viewbox.setMouseMode(pg.ViewBox.PanMode)
        except Exception:
            pass

        try:
            cmap = pg.colormap.get("viridis")
            self._color_bar = pg.ColorBarItem(values=(0.0, 1.0), colorMap=cmap, interactive=False)
            self._color_bar.setImageItem(self._image, insert_in=self._plot)
        except Exception:
            self._color_bar = None

        scene = self._graphics.scene()
        self._scene_filter = _SceneEventFilter(self)
        scene.installEventFilter(self._scene_filter)
        scene.sigMouseMoved.connect(self._on_scene_mouse_moved)
        scene.sigMouseClicked.connect(self._on_scene_mouse_clicked)

    @property
    def is_available(self) -> bool:
        return bool(self._available and self._plot is not None and self._image is not None and self._viewbox is not None)

    def _views_close(self, a, b, tol: float = 1e-6) -> bool:
        if not a or not b:
            return False
        try:
            ax0, ax1 = a.get("xlim")
            ay0, ay1 = a.get("ylim")
            bx0, bx1 = b.get("xlim")
            by0, by1 = b.get("ylim")
            return (
                abs(float(ax0) - float(bx0)) <= tol
                and abs(float(ax1) - float(bx1)) <= tol
                and abs(float(ay0) - float(by0)) <= tol
                and abs(float(ay1) - float(by1)) <= tol
            )
        except Exception:
            return False

    def get_view(self):
        if not self.is_available:
            return None
        try:
            x_range, y_range = self._viewbox.viewRange()
            return {
                "xlim": (float(x_range[0]), float(x_range[1])),
                "ylim": (float(y_range[0]), float(y_range[1])),
            }
        except Exception:
            return None

    def set_view(self, view) -> None:
        if not self.is_available or not view:
            return
        try:
            xlim = view.get("xlim")
            ylim = view.get("ylim")
            if xlim is None or ylim is None:
                return
            self._block_range_signals = True
            self._viewbox.setRange(
                xRange=(float(xlim[0]), float(xlim[1])),
                yRange=(float(ylim[0]), float(ylim[1])),
                padding=0.0,
            )
        except Exception:
            pass
        finally:
            self._block_range_signals = False

    def set_navigation_locked(self, locked: bool) -> None:
        if not self.is_available:
            return
        self._navigation_locked = bool(locked)
        if self._navigation_locked and not self._rect_zoom_once:
            self._plot.setMouseEnabled(x=False, y=False)
        else:
            self._plot.setMouseEnabled(x=True, y=True)
            try:
                self._viewbox.setMouseMode(pg.ViewBox.PanMode)
            except Exception:
                pass

    def _set_goes_overlay_rect_zoom_hidden(self, hidden: bool) -> None:
        hidden = bool(hidden)
        self._goes_overlay_rect_zoom_hidden = hidden
        if not self.is_available:
            return
        if hidden:
            if self._goes_axis is not None:
                try:
                    self._goes_axis.hide()
                except Exception:
                    pass
            if self._goes_view is not None:
                try:
                    self._goes_view.hide()
                except Exception:
                    pass
            for curve_item in self._goes_curve_items.values():
                try:
                    curve_item.hide()
                except Exception:
                    pass
            return

        payload = self._goes_overlay_payload
        channels = self._goes_visible_channels
        if payload is not None and channels:
            self.set_goes_overlay(payload, visible_channels=channels)
            return

        if self._goes_view is not None:
            try:
                self._goes_view.show()
            except Exception:
                pass

    def start_rect_zoom_once(self) -> None:
        if not self.is_available:
            return
        self._rect_zoom_once = True
        self._interaction_start_view = self.get_view()
        self._set_goes_overlay_rect_zoom_hidden(True)
        self._plot.setMouseEnabled(x=True, y=True)
        try:
            self._viewbox.setMouseMode(pg.ViewBox.RectMode)
        except Exception:
            pass

    def cancel_rect_zoom(self) -> None:
        if not self.is_available:
            return
        self._rect_zoom_once = False
        self._interaction_start_view = None
        self._interaction_timer.stop()
        try:
            self._viewbox.setMouseMode(pg.ViewBox.PanMode)
        except Exception:
            pass
        self._set_goes_overlay_rect_zoom_hidden(False)
        if self._navigation_locked:
            self._plot.setMouseEnabled(x=False, y=False)

    def set_time_mode(self, use_utc: bool, ut_start_sec) -> None:
        if not self.is_available:
            return
        try:
            self._bottom_axis.set_time_mode(use_utc, ut_start_sec)
        except Exception:
            pass

    def set_text_style(
        self,
        *,
        font_family: str = "",
        tick_font_px: int = 11,
        axis_label_font_px: int = 12,
        title_font_px: int = 14,
        title_bold: bool = False,
        title_italic: bool = False,
        axis_bold: bool = False,
        axis_italic: bool = False,
        ticks_bold: bool = False,
        ticks_italic: bool = False,
    ) -> None:
        self._font_family = str(font_family or "")
        self._tick_font_px = max(1, int(tick_font_px))
        self._axis_label_font_px = max(1, int(axis_label_font_px))
        self._title_font_px = max(1, int(title_font_px))
        self._title_bold = bool(title_bold)
        self._title_italic = bool(title_italic)
        self._axis_bold = bool(axis_bold)
        self._axis_italic = bool(axis_italic)
        self._ticks_bold = bool(ticks_bold)
        self._ticks_italic = bool(ticks_italic)
        self._apply_text_style()

    def _build_font(self, px: int, bold: bool, italic: bool) -> QFont:
        font = QFont()
        if self._font_family:
            font.setFamily(self._font_family)
        font.setPixelSize(max(1, int(px)))
        font.setBold(bool(bold))
        font.setItalic(bool(italic))
        return font

    def _axis_label_style(self):
        style = {
            "color": self._fg,
            "font-size": f"{int(self._axis_label_font_px)}px",
            "font-weight": "bold" if self._axis_bold else "normal",
            "font-style": "italic" if self._axis_italic else "normal",
        }
        if self._font_family:
            style["font-family"] = self._font_family
        return style

    def _title_style(self):
        style = {
            "color": self._fg,
            "size": f"{int(self._title_font_px)}px",
        }
        return style

    def _apply_text_style(self):
        if not self.is_available:
            return

        tick_font = self._build_font(self._tick_font_px, self._ticks_bold, self._ticks_italic)
        label_style = self._axis_label_style()
        for axis_name in ("left", "bottom"):
            axis = self._plot.getAxis(axis_name)
            axis.setTextPen(self._fg)
            axis.setPen(self._fg)
            try:
                axis.setStyle(tickFont=tick_font)
            except Exception:
                pass
            try:
                text = self._y_label if axis_name == "left" else self._x_label
                axis.setLabel(text, **label_style)
            except Exception:
                pass

        if self._color_bar is not None:
            cbar_axis = self._color_bar.axis
            try:
                cbar_axis.setTextPen(self._fg)
                cbar_axis.setPen(self._fg)
                cbar_axis.setStyle(tickFont=tick_font)
                cbar_axis.setLabel(self._colorbar_label, **label_style)
            except Exception:
                pass

        if self._goes_axis is not None:
            try:
                self._goes_axis.setTextPen(self._fg)
                self._goes_axis.setPen(pg.mkPen(color=self._fg, width=1.25) if pg is not None else self._fg)
                self._goes_axis.setStyle(tickFont=tick_font, tickLength=10)
                self._goes_axis.setLabel(self._goes_axis_label, **label_style)
            except Exception:
                pass

        self._plot.setTitle(self._title, **self._title_style())
        try:
            title_font = self._build_font(self._title_font_px, self._title_bold, self._title_italic)
            self._plot.titleLabel.item.setFont(title_font)
            self._plot.titleLabel.item.setDefaultTextColor(QColor(self._fg))
        except Exception:
            pass

    def set_dark(self, is_dark: bool) -> None:
        if not self.is_available:
            return
        bg = "#111111" if is_dark else "#ffffff"
        self._fg = "#f2f2f2" if is_dark else "#101010"
        self._graphics.setBackground(bg)
        self._apply_text_style()

    def _sync_goes_overlay_geometry(self):
        if not self.is_available or self._goes_view is None or self._goes_axis is None:
            return
        try:
            self._goes_view.setGeometry(self._viewbox.sceneBoundingRect())
            self._goes_view.linkedViewChanged(self._viewbox, self._goes_view.XAxis)
        except Exception:
            pass

    def _payload_field(self, payload, name: str, default=None):
        if payload is None:
            return default
        if isinstance(payload, dict):
            return payload.get(name, default)
        return getattr(payload, name, default)

    def _goes_series_arrays(self, series):
        xs = np.asarray(self._payload_field(series, "x_seconds", []), dtype=float)
        ys = np.asarray(self._payload_field(series, "flux_wm2", []), dtype=float)
        if xs.size == 0 or ys.size == 0 or xs.size != ys.size:
            return None, None
        mask = np.isfinite(xs) & np.isfinite(ys) & (ys > 0.0)
        if not np.any(mask):
            return None, None
        return np.asarray(xs[mask], dtype=float), np.asarray(ys[mask], dtype=float)

    def _goes_payload_series(self, payload, visible_channels=None):
        series_map = self._payload_field(payload, "series", {}) or {}
        if not series_map:
            return []
        selected = tuple(visible_channels or GOES_OVERLAY_CHANNEL_ORDER)
        out = []
        for key in GOES_OVERLAY_CHANNEL_ORDER:
            if key in selected and key in series_map:
                out.append((key, series_map[key]))
        for key, series in series_map.items():
            if key in selected and key not in {item[0] for item in out}:
                out.append((key, series))
        return out

    def _goes_log_limits(self, flux):
        flux_limits = goes_flux_axis_limits(flux)
        if flux_limits is None:
            return None
        return float(np.log10(flux_limits[0])), float(np.log10(flux_limits[1]))

    def _goes_axis_ticks(self, limits):
        try:
            flux_min = float(10.0 ** float(limits[0]))
            flux_max = float(10.0 ** float(limits[1]))
        except Exception:
            return []
        major = [(float(np.log10(value)), label) for value, label in goes_class_ticks_for_limits(flux_min, flux_max)]
        minor = []
        lo_exp = int(np.floor(float(limits[0])))
        hi_exp = int(np.ceil(float(limits[1])))
        for exponent in range(lo_exp, hi_exp + 1):
            base = 10.0 ** exponent
            for factor in range(2, 10):
                value = float(factor) * base
                if flux_min <= value <= flux_max:
                    minor.append((float(np.log10(value)), ""))
        return [major, minor]

    def clear_goes_overlay(self) -> None:
        self._goes_overlay_payload = None
        self._goes_visible_channels = ()
        for curve_item in self._goes_curve_items.values():
            try:
                curve_item.setData([], [])
            except Exception:
                pass
        if self._goes_axis is not None:
            try:
                self._goes_axis.setTicks([])
                self._goes_axis.hide()
            except Exception:
                pass

    def set_goes_overlay(self, payload, visible_channels=None) -> None:
        self._goes_overlay_payload = payload
        self._goes_visible_channels = tuple(visible_channels or ())
        if not self.is_available or self._goes_view is None or self._goes_axis is None or not self._goes_curve_items:
            return
        plot_series = []
        flux_arrays = []
        for key, series in self._goes_payload_series(payload, visible_channels=self._goes_visible_channels):
            xs, flux = self._goes_series_arrays(series)
            if xs is None or flux is None:
                continue
            plot_series.append((key, xs, flux))
            flux_arrays.append(flux)
        if not plot_series or not flux_arrays:
            self.clear_goes_overlay()
            return

        limits = self._goes_log_limits(np.concatenate(flux_arrays))
        if limits is None:
            self.clear_goes_overlay()
            return

        self._goes_axis_label = "GOES X-Ray Class"
        self._apply_text_style()
        for key, curve_item in self._goes_curve_items.items():
            curve_item.setData([], [])
        for key, xs, flux in plot_series:
            curve_item = self._goes_curve_items.get(key)
            if curve_item is None:
                continue
            curve_item.setData(xs, np.log10(flux))
        try:
            self._goes_view.enableAutoRange(x=False, y=False)
        except Exception:
            pass
        try:
            self._goes_view.setRange(yRange=(float(limits[0]), float(limits[1])), padding=0.0)
        except Exception:
            pass
        try:
            self._goes_axis.setTicks(self._goes_axis_ticks(limits))
        except Exception:
            pass
        self._sync_goes_overlay_geometry()
        if self._goes_overlay_rect_zoom_hidden:
            for curve_item in self._goes_curve_items.values():
                try:
                    curve_item.hide()
                except Exception:
                    pass
            try:
                self._goes_view.hide()
            except Exception:
                pass
            try:
                self._goes_axis.hide()
            except Exception:
                pass
            return
        try:
            self._goes_view.show()
        except Exception:
            pass
        for curve_item in self._goes_curve_items.values():
            try:
                x_data, y_data = curve_item.getData()
                x_count = 0 if x_data is None else int(len(x_data))
                y_count = 0 if y_data is None else int(len(y_data))
                curve_item.setVisible(bool(x_count and y_count))
            except Exception:
                pass
        try:
            self._goes_axis.show()
        except Exception:
            pass

    def _clear_lasso_overlay(self):
        self._lasso_points = []
        self._lasso_drag_active = False
        if self._lasso_line_item is not None:
            try:
                self._plot.removeItem(self._lasso_line_item)
            except Exception:
                pass
            self._lasso_line_item = None

    def _apply_navigation_state(self):
        if not self.is_available:
            return
        if self._navigation_locked:
            self._plot.setMouseEnabled(x=False, y=False)
        else:
            self._plot.setMouseEnabled(x=True, y=True)
            try:
                self._viewbox.setMouseMode(pg.ViewBox.PanMode)
            except Exception:
                pass

    def _clear_drift_overlay(self):
        self._drift_points = []
        if self._drift_scatter_item is not None:
            try:
                self._plot.removeItem(self._drift_scatter_item)
            except Exception:
                pass
            self._drift_scatter_item = None
        if self._drift_line_item is not None:
            try:
                self._plot.removeItem(self._drift_line_item)
            except Exception:
                pass
            self._drift_line_item = None

    def _annotation_capture_kind(self) -> str | None:
        mode = str(self._interaction_mode or "")
        if not mode.startswith("annotation:"):
            return None
        return mode.split(":", 1)[1] or None

    def _clear_annotation_capture_overlay(self) -> None:
        self._annotation_capture_points = []
        self._annotation_capture_drag_active = False
        self._annotation_capture_press_scene_pos = None
        self._annotation_capture_press_xy = None
        self._annotation_capture_drag_last_xy = None
        if self._annotation_capture_line_item is not None:
            try:
                self._plot.removeItem(self._annotation_capture_line_item)
            except Exception:
                pass
            self._annotation_capture_line_item = None
        if self._annotation_capture_vertex_item is not None:
            try:
                self._plot.removeItem(self._annotation_capture_vertex_item)
            except Exception:
                pass
            self._annotation_capture_vertex_item = None

    def _ensure_annotation_capture_items(self) -> None:
        if self._annotation_capture_line_item is None:
            self._annotation_capture_line_item = pg.PlotDataItem(
                pen=pg.mkPen(0, 212, 255, 220, width=2)
            )
            self._plot.addItem(self._annotation_capture_line_item)
        if self._annotation_capture_vertex_item is None:
            self._annotation_capture_vertex_item = pg.ScatterPlotItem(
                size=8,
                brush=pg.mkBrush(0, 212, 255, 230),
                pen=pg.mkPen(255, 255, 255, 220, width=1),
            )
            self._plot.addItem(self._annotation_capture_vertex_item)

    def _points_close(self, a, b, tol: float = 1e-6) -> bool:
        try:
            return ((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) <= tol
        except Exception:
            return False

    def _append_annotation_capture_point(self, xy) -> bool:
        try:
            point = (float(xy[0]), float(xy[1]))
        except Exception:
            return False
        if self._annotation_capture_points and self._points_close(self._annotation_capture_points[-1], point):
            return False
        self._annotation_capture_points.append(point)
        return True

    def _update_annotation_capture_overlay(self, cursor_xy=None) -> None:
        if not self.is_available:
            return
        kind = self._annotation_capture_kind()
        if kind is None:
            return

        line_points: list[tuple[float, float]] = []
        vertex_points = list(self._annotation_capture_points)

        if kind == "polygon":
            line_points = list(self._annotation_capture_points)
            if cursor_xy is not None and self._annotation_capture_points:
                try:
                    line_points.append((float(cursor_xy[0]), float(cursor_xy[1])))
                    if len(self._annotation_capture_points) >= 2:
                        line_points.append(self._annotation_capture_points[0])
                except Exception:
                    pass
        elif kind == "line":
            if self._annotation_capture_points:
                if cursor_xy is not None:
                    try:
                        line_points = [
                            self._annotation_capture_points[0],
                            (float(cursor_xy[0]), float(cursor_xy[1])),
                        ]
                    except Exception:
                        line_points = []
                vertex_points = self._annotation_capture_points[:1]
        elif kind == "text":
            if cursor_xy is not None:
                try:
                    vertex_points = [(float(cursor_xy[0]), float(cursor_xy[1]))]
                except Exception:
                    vertex_points = []
            else:
                vertex_points = []

        self._ensure_annotation_capture_items()

        if line_points:
            xs = [p[0] for p in line_points]
            ys = [p[1] for p in line_points]
            self._annotation_capture_line_item.setData(xs, ys)
        else:
            self._annotation_capture_line_item.setData([], [])

        if vertex_points:
            xs = [p[0] for p in vertex_points]
            ys = [p[1] for p in vertex_points]
            self._annotation_capture_vertex_item.setData(xs, ys)
        else:
            self._annotation_capture_vertex_item.setData([], [])

    def _finish_annotation_capture(self, kind: str, payload) -> None:
        kind_norm = str(kind or "").strip().lower()
        if kind_norm == "polygon":
            out = [(float(x), float(y)) for x, y in list(payload or [])]
        elif kind_norm == "line":
            out = [(float(x), float(y)) for x, y in list(payload or [])[:2]]
        else:
            x, y = payload
            out = (float(x), float(y))

        self._interaction_mode = None
        self._clear_annotation_capture_overlay()
        self._apply_navigation_state()
        self.annotationCaptureFinished.emit(kind_norm, out)

    def _cancel_annotation_capture(self, kind: str | None = None) -> None:
        kind_norm = str(kind or self._annotation_capture_kind() or "").strip().lower()
        if not kind_norm:
            return
        self._interaction_mode = None
        self._clear_annotation_capture_overlay()
        self._apply_navigation_state()
        self.annotationCaptureCancelled.emit(kind_norm)

    def clear_overlays(self) -> None:
        if not self.is_available:
            return
        self._clear_lasso_overlay()
        self._clear_drift_overlay()
        self._clear_annotation_capture_overlay()

    def clear(self) -> None:
        if not self.is_available:
            return
        self._image.clear()
        self._plot.setTitle("")
        self._interaction_mode = None
        self._clear_lasso_overlay()
        self._clear_drift_overlay()
        self._clear_annotation_capture_overlay()
        self._clear_annotation_overlay()
        self.clear_goes_overlay()

    def export_plot_item(self):
        if not self.is_available:
            return None
        return self._plot

    def update_image(
        self,
        data: np.ndarray,
        extent,
        cmap,
        title: str = "",
        x_label: str = "Time [s]",
        y_label: str = "Frequency [MHz]",
        colorbar_label: str = "",
        view=None,
    ) -> None:
        if not self.is_available or data is None:
            return

        arr = np.asarray(data)
        if arr.ndim != 2 or arr.size == 0:
            return

        arr = np.ascontiguousarray(arr, dtype=np.float32)
        self._image.setImage(arr, autoLevels=False)

        x0, x1, y0, y1 = (float(extent[0]), float(extent[1]), float(extent[2]), float(extent[3]))
        self._image.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        self._full_view = {
            "xlim": (min(x0, x1), max(x0, x1)),
            "ylim": (min(y0, y1), max(y0, y1)),
        }

        finite = np.isfinite(arr)
        if np.any(finite):
            vmin = float(np.nanmin(arr))
            vmax = float(np.nanmax(arr))
            if vmax <= vmin:
                vmax = vmin + 1e-6
            self._image.setLevels((vmin, vmax))
            if self._color_bar is not None:
                try:
                    self._color_bar.setLevels((vmin, vmax))
                except Exception:
                    pass

        color_map, lut = _mpl_cmap_to_lookup(cmap)
        if lut is not None:
            self._image.setLookupTable(lut, update=False)
        if self._color_bar is not None and color_map is not None:
            try:
                self._color_bar.setColorMap(color_map)
            except Exception:
                pass
        if self._color_bar is not None and colorbar_label:
            try:
                self._color_bar.axis.setLabel(colorbar_label)
            except Exception:
                pass

        self._title = str(title or "")
        self._x_label = str(x_label or "")
        self._y_label = str(y_label or "")
        self._colorbar_label = str(colorbar_label or "")
        self._apply_text_style()

        if view:
            self.set_view(view)
        elif self._full_view is not None:
            self.set_view(self._full_view)
        self._sync_goes_overlay_geometry()

    def begin_lasso_capture(self) -> None:
        if not self.is_available:
            return
        self._interaction_mode = "lasso"
        self._clear_lasso_overlay()
        self._clear_annotation_capture_overlay()
        # Avoid panning while drawing freehand lasso
        self._plot.setMouseEnabled(x=False, y=False)

    def begin_drift_capture(self) -> None:
        if not self.is_available:
            return
        self._interaction_mode = "drift"
        self._clear_drift_overlay()
        self._clear_annotation_capture_overlay()

    def begin_annotation_capture(self, kind: str) -> None:
        if not self.is_available:
            return
        kind_norm = str(kind or "").strip().lower()
        if kind_norm not in {"polygon", "line", "text"}:
            return
        self._interaction_mode = f"annotation:{kind_norm}"
        self._annotation_capture_suppress_next_click = False
        self._clear_lasso_overlay()
        self._clear_annotation_capture_overlay()
        self._plot.setMouseEnabled(x=False, y=False)

    def stop_interaction_capture(self) -> None:
        self._interaction_mode = None
        self._annotation_capture_suppress_next_click = False
        self._clear_lasso_overlay()
        self._clear_annotation_capture_overlay()
        self._apply_navigation_state()

    def show_drift_points(self, points, with_segments: bool = False) -> None:
        if not self.is_available:
            return
        pts = np.asarray(points, dtype=float) if points is not None else np.empty((0, 2), dtype=float)
        if pts.size == 0:
            self._clear_drift_overlay()
            return

        if self._drift_scatter_item is None:
            self._drift_scatter_item = pg.ScatterPlotItem(
                size=10,
                brush=pg.mkBrush(255, 255, 255, 230),
                pen=pg.mkPen(40, 40, 40, 240),
            )
            self._plot.addItem(self._drift_scatter_item)
        self._drift_scatter_item.setData(pts[:, 0], pts[:, 1])

        if with_segments:
            if self._drift_line_item is None:
                self._drift_line_item = pg.PlotDataItem(pen=pg.mkPen(40, 255, 120, width=2))
                self._plot.addItem(self._drift_line_item)
            self._drift_line_item.setData(pts[:, 0], pts[:, 1])
        elif self._drift_line_item is not None:
            self._drift_line_item.setData([], [])

    def _clear_annotation_overlay(self) -> None:
        if not self.is_available:
            return
        for item in self._annotation_items:
            try:
                self._plot.removeItem(item)
            except Exception:
                pass
        self._annotation_items = []

    def set_annotations(self, serialized_annotations) -> None:
        """Render non-interactive annotation overlays."""
        if not self.is_available:
            return
        self._clear_annotation_overlay()

        for ann in serialized_annotations or []:
            try:
                if not isinstance(ann, dict):
                    continue
                if not bool(ann.get("visible", True)):
                    continue

                kind = str(ann.get("kind", "")).strip().lower()
                points = ann.get("points") or []
                color = str(ann.get("color") or "#00d4ff")
                width = float(ann.get("line_width", 1.5))
                pen = pg.mkPen(color=color, width=max(1.0, width))

                if kind in {"polygon", "line"}:
                    if len(points) < 2:
                        continue
                    xs = [float(p[0]) for p in points]
                    ys = [float(p[1]) for p in points]
                    if kind == "polygon":
                        xs.append(xs[0])
                        ys.append(ys[0])
                    item = pg.PlotDataItem(xs, ys, pen=pen)
                    item.setZValue(20)
                    self._plot.addItem(item)
                    self._annotation_items.append(item)
                    continue

                if kind == "text":
                    if not points:
                        continue
                    x, y = points[0]
                    txt = str(ann.get("text") or "")
                    item = pg.TextItem(text=txt, color=color, anchor=(0, 1))
                    font = QFont()
                    font_family = str(ann.get("font_family") or "").strip()
                    if font_family:
                        font.setFamily(font_family)
                    try:
                        font.setPixelSize(max(6, int(ann.get("font_size", 12))))
                    except Exception:
                        font.setPixelSize(12)
                    font.setBold(bool(ann.get("font_bold", False)))
                    font.setItalic(bool(ann.get("font_italic", False)))
                    try:
                        item.setFont(font)
                    except Exception:
                        try:
                            item.textItem.setFont(font)
                        except Exception:
                            pass
                    item.setPos(float(x), float(y))
                    item.setZValue(20)
                    self._plot.addItem(item)
                    self._annotation_items.append(item)
            except Exception:
                continue

    def _scene_to_plot_xy(self, scene_pos):
        if not self.is_available:
            return None
        try:
            if not self._plot.sceneBoundingRect().contains(scene_pos):
                return None
            point = self._viewbox.mapSceneToView(scene_pos)
            return float(point.x()), float(point.y())
        except Exception:
            return None

    def _on_scene_mouse_moved(self, scene_pos):
        xy = self._scene_to_plot_xy(scene_pos)
        if xy is None:
            self.mousePositionChanged.emit(0.0, 0.0, False)
            return
        self.mousePositionChanged.emit(xy[0], xy[1], True)
        if self._annotation_capture_kind() is not None:
            self._update_annotation_capture_overlay(cursor_xy=xy)

    def _update_lasso_curve(self):
        if self._lasso_line_item is None:
            self._lasso_line_item = pg.PlotDataItem(pen=pg.mkPen(0, 212, 255, width=2))
            self._plot.addItem(self._lasso_line_item)
        if not self._lasso_points:
            self._lasso_line_item.setData([], [])
            return
        xs = [p[0] for p in self._lasso_points]
        ys = [p[1] for p in self._lasso_points]
        self._lasso_line_item.setData(xs, ys)

    def _handle_annotation_drag_scene_event(self, event, annotation_kind: str) -> bool:
        if not self.is_available or annotation_kind != "line":
            return False

        etype = event.type()
        if etype == QEvent.Type.GraphicsSceneMousePress:
            if event.button() == Qt.MouseButton.RightButton:
                self._annotation_capture_suppress_next_click = True
                self._cancel_annotation_capture(annotation_kind)
                return True
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            xy = self._scene_to_plot_xy(event.scenePos())
            if xy is None:
                return False
            self._annotation_capture_press_scene_pos = QPointF(event.scenePos())
            self._annotation_capture_press_xy = (float(xy[0]), float(xy[1]))
            self._annotation_capture_drag_last_xy = self._annotation_capture_press_xy
            self._annotation_capture_drag_active = False
            return False

        if etype == QEvent.Type.GraphicsSceneMouseMove:
            if self._annotation_capture_press_scene_pos is None or self._annotation_capture_press_xy is None:
                return False
            dx = float(event.scenePos().x() - self._annotation_capture_press_scene_pos.x())
            dy = float(event.scenePos().y() - self._annotation_capture_press_scene_pos.y())
            if not self._annotation_capture_drag_active and (dx * dx + dy * dy) < 16.0:
                return False
            self._annotation_capture_drag_active = True
            xy = self._scene_to_plot_xy(event.scenePos())
            if xy is None:
                return True
            self._annotation_capture_drag_last_xy = (float(xy[0]), float(xy[1]))
            self._annotation_capture_points = [self._annotation_capture_press_xy]
            self._update_annotation_capture_overlay(cursor_xy=self._annotation_capture_drag_last_xy)
            return True

        if etype == QEvent.Type.GraphicsSceneMouseRelease:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if self._annotation_capture_press_scene_pos is None or self._annotation_capture_press_xy is None:
                return False

            origin = self._annotation_capture_press_xy
            drag_point = self._scene_to_plot_xy(event.scenePos()) or self._annotation_capture_drag_last_xy
            was_drag = bool(self._annotation_capture_drag_active)

            self._annotation_capture_press_scene_pos = None
            self._annotation_capture_press_xy = None
            self._annotation_capture_drag_active = False
            self._annotation_capture_drag_last_xy = None

            if not was_drag:
                return False

            self._annotation_capture_suppress_next_click = True
            if drag_point is None or self._points_close(origin, drag_point):
                self._clear_annotation_capture_overlay()
                return True
            self._finish_annotation_capture(annotation_kind, [origin, drag_point])
            return True

        return False

    def _handle_scene_event(self, event) -> bool:
        if not self.is_available:
            return False

        annotation_kind = self._annotation_capture_kind()
        if annotation_kind == "line":
            return self._handle_annotation_drag_scene_event(event, annotation_kind)
        if self._interaction_mode != "lasso":
            return False

        etype = event.type()
        if etype == QEvent.Type.GraphicsSceneMousePress:
            if event.button() == Qt.MouseButton.LeftButton:
                xy = self._scene_to_plot_xy(event.scenePos())
                if xy is None:
                    return False
                self._lasso_drag_active = True
                self._lasso_points = [xy]
                self._update_lasso_curve()
                return True
            if event.button() == Qt.MouseButton.RightButton:
                self._interaction_mode = None
                self._clear_lasso_overlay()
                self._apply_navigation_state()
                return True

        if etype == QEvent.Type.GraphicsSceneMouseMove:
            if not self._lasso_drag_active:
                return False
            xy = self._scene_to_plot_xy(event.scenePos())
            if xy is None:
                return True
            if self._lasso_points:
                last_x, last_y = self._lasso_points[-1]
                if ((xy[0] - last_x) ** 2 + (xy[1] - last_y) ** 2) < 1e-6:
                    return True
            self._lasso_points.append(xy)
            self._update_lasso_curve()
            return True

        if etype == QEvent.Type.GraphicsSceneMouseRelease:
            if event.button() != Qt.MouseButton.LeftButton or not self._lasso_drag_active:
                return False

            self._lasso_drag_active = False
            xy = self._scene_to_plot_xy(event.scenePos())
            if xy is not None:
                self._lasso_points.append(xy)
                self._update_lasso_curve()

            self._finish_lasso()
            return True

        return False

    def _finish_lasso(self):
        if len(self._lasso_points) >= 3:
            out = [(float(p[0]), float(p[1])) for p in self._lasso_points]
            self.lassoFinished.emit(out)
        self._interaction_mode = None
        self._clear_lasso_overlay()
        self._apply_navigation_state()

    def _finish_drift(self):
        out = [(float(p[0]), float(p[1])) for p in self._drift_points]
        self.driftCaptureFinished.emit(out)
        self._interaction_mode = None

    def _on_scene_mouse_clicked(self, ev):
        if self._annotation_capture_suppress_next_click:
            self._annotation_capture_suppress_next_click = False
            return

        xy = self._scene_to_plot_xy(ev.scenePos())
        if xy is None:
            return

        button = ev.button()
        try:
            is_double = bool(ev.double())
        except Exception:
            is_double = False

        x, y = xy

        annotation_kind = self._annotation_capture_kind()
        if annotation_kind is not None:
            if button == Qt.MouseButton.RightButton:
                if annotation_kind == "polygon" and len(self._annotation_capture_points) >= 3:
                    self._finish_annotation_capture(annotation_kind, list(self._annotation_capture_points))
                else:
                    self._cancel_annotation_capture(annotation_kind)
                return

            if button != Qt.MouseButton.LeftButton:
                return

            if annotation_kind == "text":
                self._finish_annotation_capture(annotation_kind, (x, y))
                return

            self._append_annotation_capture_point((x, y))
            self._update_annotation_capture_overlay(cursor_xy=(x, y))

            if annotation_kind == "line" and len(self._annotation_capture_points) >= 2:
                self._finish_annotation_capture(annotation_kind, self._annotation_capture_points[:2])
            elif annotation_kind == "polygon" and is_double and len(self._annotation_capture_points) >= 3:
                self._finish_annotation_capture(annotation_kind, list(self._annotation_capture_points))
            return

        if self._interaction_mode == "drift":
            if button == Qt.MouseButton.LeftButton:
                self._drift_points.append((x, y))
                self.show_drift_points(self._drift_points, with_segments=False)
                self.driftPointAdded.emit(x, y)
                if is_double and len(self._drift_points) >= 2:
                    self._finish_drift()
            elif button == Qt.MouseButton.RightButton:
                self._finish_drift()

    def _on_range_changed(self, *_):
        if self._block_range_signals:
            return
        if self._interaction_start_view is None:
            self._interaction_start_view = self.get_view()
        self._interaction_timer.start()

    def _emit_interaction_finished(self):
        start = self._interaction_start_view
        end = self.get_view()
        self._interaction_start_view = None
        if not start or not end or self._views_close(start, end):
            return

        if self._rect_zoom_once:
            self._rect_zoom_once = False
            try:
                self._viewbox.setMouseMode(pg.ViewBox.PanMode)
            except Exception:
                pass
            if self._navigation_locked:
                self._plot.setMouseEnabled(x=False, y=False)
            self._set_goes_overlay_rect_zoom_hidden(False)
            self.rectZoomFinished.emit(start, end)
            return

        self.viewInteractionFinished.emit(start, end)
