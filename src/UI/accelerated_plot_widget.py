"""
Hardware-accelerated plotting widget for dynamic spectrum rendering.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

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
else:
    class _TimeAxisItem:
        pass


class AcceleratedPlotWidget(QWidget):
    mousePositionChanged = Signal(float, float, bool)
    lassoFinished = Signal(list)
    driftPointAdded = Signal(float, float)
    driftCaptureFinished = Signal(list)
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
        self._drift_points = []
        self._drift_scatter_item = None
        self._drift_line_item = None

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
        layout.addWidget(self._graphics)

        self._bottom_axis = _TimeAxisItem(orientation="bottom")
        self._plot = self._graphics.addPlot(axisItems={"bottom": self._bottom_axis})
        self._plot.hideButtons()
        self._plot.setMenuEnabled(False)
        self._plot.invertY(True)
        self._plot.setLabel("left", "Frequency [MHz]")
        self._plot.setLabel("bottom", "Time [s]")

        self._viewbox = self._plot.getViewBox()
        self._viewbox.sigRangeChanged.connect(self._on_range_changed)

        self._image = pg.ImageItem(axisOrder="row-major")
        self._plot.addItem(self._image)

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

    def start_rect_zoom_once(self) -> None:
        if not self.is_available:
            return
        self._rect_zoom_once = True
        self._interaction_start_view = self.get_view()
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

    def _clear_lasso_overlay(self):
        self._lasso_points = []
        if self._lasso_line_item is not None:
            try:
                self._plot.removeItem(self._lasso_line_item)
            except Exception:
                pass
            self._lasso_line_item = None

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

    def clear_overlays(self) -> None:
        if not self.is_available:
            return
        self._clear_lasso_overlay()
        self._clear_drift_overlay()

    def clear(self) -> None:
        if not self.is_available:
            return
        self._image.clear()
        self._plot.setTitle("")
        self._interaction_mode = None
        self._clear_lasso_overlay()
        self._clear_drift_overlay()

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
        self._full_view = {"xlim": (x0, x1), "ylim": (y0, y1)}

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

    def begin_lasso_capture(self) -> None:
        if not self.is_available:
            return
        self._interaction_mode = "lasso"
        self._clear_lasso_overlay()

    def begin_drift_capture(self) -> None:
        if not self.is_available:
            return
        self._interaction_mode = "drift"
        self._clear_drift_overlay()

    def stop_interaction_capture(self) -> None:
        self._interaction_mode = None
        self._clear_lasso_overlay()

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

    def _finish_lasso(self):
        if len(self._lasso_points) >= 3:
            out = [(float(p[0]), float(p[1])) for p in self._lasso_points]
            self.lassoFinished.emit(out)
        self._interaction_mode = None
        self._clear_lasso_overlay()

    def _finish_drift(self):
        out = [(float(p[0]), float(p[1])) for p in self._drift_points]
        self.driftCaptureFinished.emit(out)
        self._interaction_mode = None

    def _on_scene_mouse_clicked(self, ev):
        xy = self._scene_to_plot_xy(ev.scenePos())
        if xy is None:
            return

        button = ev.button()
        try:
            is_double = bool(ev.double())
        except Exception:
            is_double = False

        x, y = xy
        if self._interaction_mode == "lasso":
            if button == Qt.MouseButton.LeftButton:
                self._lasso_points.append((x, y))
                xs = [p[0] for p in self._lasso_points]
                ys = [p[1] for p in self._lasso_points]
                if self._lasso_line_item is None:
                    self._lasso_line_item = pg.PlotDataItem(pen=pg.mkPen(0, 212, 255, width=2))
                    self._plot.addItem(self._lasso_line_item)
                self._lasso_line_item.setData(xs, ys)
                if is_double and len(self._lasso_points) >= 3:
                    self._finish_lasso()
            elif button == Qt.MouseButton.RightButton:
                self._finish_lasso()
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
            self.rectZoomFinished.emit(start, end)
            return

        self.viewInteractionFinished.emit(start, end)
