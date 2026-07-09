"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence

import numpy as np
import pyqtgraph as pg
from pyqtgraph.exporters import SVGExporter
from PySide6.QtCore import QTimer, Qt, QRectF, Signal
from PySide6.QtGui import QAction, QGuiApplication, QPainter, QPalette, QPdfWriter
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


def _opengl_capable() -> bool:
    """True if the platform can actually create an OpenGL context.

    Lets hardware acceleration default ON but fall back to software rendering on
    machines/drivers where GL can't initialise (the cause of the
    'QOpenGLWidget is not supported on this platform' warning), instead of
    enabling it blindly and risking a crash.
    """
    try:
        from PySide6.QtGui import QOpenGLContext

        ctx = QOpenGLContext()
        return bool(ctx.create())
    except Exception:
        return False


def _configure_pyqtgraph_once():
    raw = str(os.environ.get("ECALLISTO_SUNPY_OPENGL", "") or "").strip().lower()
    in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST")) or ("pytest" in sys.modules)
    if raw in {"1", "true", "yes", "on"}:
        desired_use_gl = True
    elif raw in {"0", "false", "no", "off"}:
        desired_use_gl = False
    else:
        # Hardware-accelerate by default in app runs, but only if the platform
        # can actually create a GL context (auto-fallback). Off under pytest.
        desired_use_gl = (not in_pytest) and _opengl_capable()

    if getattr(_configure_pyqtgraph_once, "_configured", False):
        try:
            if bool(pg.getConfigOption("useOpenGL")) != bool(desired_use_gl):
                pg.setConfigOptions(useOpenGL=bool(desired_use_gl))
        except Exception:
            desired_use_gl = False
            try:
                pg.setConfigOptions(useOpenGL=False)
            except Exception:
                pass
        setattr(_configure_pyqtgraph_once, "_opengl_enabled", bool(desired_use_gl))
        return
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)

    try:
        pg.setConfigOptions(useOpenGL=bool(desired_use_gl))
    except Exception:
        pg.setConfigOptions(useOpenGL=False)
        desired_use_gl = False
    setattr(_configure_pyqtgraph_once, "_opengl_enabled", bool(desired_use_gl))
    setattr(_configure_pyqtgraph_once, "_configured", True)


def _fallback_colormap_lut(name: str) -> np.ndarray | None:
    cmap = _fallback_colormap(name)
    if cmap is None:
        return None
    return cmap.getLookupTable(nPts=256)


def _fallback_colormap(name: str) -> pg.ColorMap | None:
    palettes = {
        "sdoaia94": ((0, 0, 0), (16, 91, 64), (64, 142, 128), (145, 196, 192), (255, 255, 255)),
        "sdoaia131": ((0, 0, 0), (0, 92, 92), (15, 185, 185), (136, 255, 255), (255, 255, 255)),
        "sdoaia171": ((0, 0, 0), (92, 64, 0), (185, 128, 0), (255, 192, 7), (255, 255, 255)),
        "sdoaia193": ((0, 0, 0), (128, 64, 16), (181, 128, 64), (221, 192, 145), (255, 255, 255)),
        "sdoaia211": ((0, 0, 0), (128, 64, 91), (181, 128, 142), (221, 192, 196), (255, 255, 255)),
        "sdoaia304": ((0, 0, 0), (70, 0, 18), (170, 28, 20), (255, 128, 34), (255, 244, 180)),
        "sdoaia335": ((0, 0, 0), (16, 64, 128), (64, 128, 181), (145, 192, 221), (255, 255, 255)),
        "sdoaia1600": ((0, 0, 0), (91, 91, 16), (142, 142, 64), (196, 196, 145), (255, 255, 255)),
        "sdoaia1700": ((0, 0, 0), (128, 64, 64), (181, 128, 128), (221, 192, 192), (255, 255, 255)),
        "soholasco2": ((0, 0, 0), (20, 20, 90), (30, 90, 165), (120, 185, 220), (255, 255, 255)),
        "soholasco3": ((0, 0, 0), (60, 20, 12), (150, 62, 22), (222, 150, 60), (255, 252, 220)),
        # STEREO/SECCHI white-light detectors. sunpy ships proper stereocor*/
        # stereohi* colormaps (used via the matplotlib path); these approximate
        # them as an offline fallback so coronagraph frames never render as EUV.
        "stereocor1": ((0, 0, 0), (20, 30, 60), (60, 90, 140), (150, 180, 220), (255, 255, 255)),
        "stereocor2": ((0, 0, 0), (30, 25, 60), (90, 75, 130), (170, 155, 210), (255, 255, 255)),
        "stereohi1": ((0, 0, 0), (40, 40, 50), (100, 100, 110), (180, 180, 190), (255, 255, 255)),
        "stereohi2": ((0, 0, 0), (40, 40, 50), (100, 100, 110), (180, 180, 190), (255, 255, 255)),
    }
    colors = palettes.get(str(name or "").lower())
    if not colors:
        return None
    stops = np.linspace(0.0, 1.0, len(colors))
    return pg.ColorMap(stops, np.asarray(colors, dtype=np.ubyte))


class SunPyPlotCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None, theme: Any | None = None, *, enable_colorbar: bool = False):
        super().__init__(parent)
        _configure_pyqtgraph_once()

        self.theme = theme
        self._colorbar_enabled = bool(enable_colorbar)
        self._roi_bounds: tuple[int, int, int, int] | None = None
        self._roi_callback = None
        self._axis_transform: dict[str, float] = self._default_axis_transform()
        self._last_map_bounds: tuple[float, float, float, float] | None = None
        self._region_overlay_items: list[Any] = []
        self._map_square_enabled = True
        self._colorbar_visible = True
        self._last_map_levels: tuple[float, float] | None = None
        self._square_reflow_pending = False

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
        self._set_map_axis_labels()
        self.map_plot.setTitle("")
        self._unclamp_title_min_width()
        map_left_axis = self.map_plot.getAxis("left")
        map_bottom_axis = self.map_plot.getAxis("bottom")
        for axis in (map_left_axis, map_bottom_axis):
            try:
                axis.enableAutoSIPrefix(False)
            except Exception:
                pass
        map_left_axis.setStyle(autoExpandTextSpace=False, tickTextWidth=60)
        map_left_axis.setWidth(72)
        map_bottom_axis.setStyle(autoExpandTextSpace=False, tickTextHeight=18)
        map_bottom_axis.setHeight(42)

        self.map_image = pg.ImageItem(axisOrder="row-major")
        # Downsample full-resolution (4096²) frames to the viewport before
        # drawing — a large smoothness win when panning/zooming big images.
        try:
            self.map_image.setAutoDownsample(True)
        except Exception:
            pass
        self.map_plot.addItem(self.map_image)
        self._base_map_colormap = pg.colormap.get("inferno", source="matplotlib")
        self._base_map_lut = self._base_map_colormap.getLookupTable(nPts=256)
        self._map_colormap = self._base_map_colormap
        self._inferno_lut = self._base_map_lut
        self._map_lut = self._base_map_lut
        self._colormap_name = "inferno"
        self._colorbar = None
        if self._colorbar_enabled:
            self._colorbar = pg.ColorBarItem(
                values=(0.0, 1.0),
                width=20,
                colorMap=self._map_colormap,
                label="Intensity",
                interactive=False,
                colorMapMenu=False,
            )
            self._colorbar.setImageItem(self.map_image, insert_in=self.map_plot.getPlotItem())
        self._aia_limb_curve = pg.PlotCurveItem(
            pen=pg.mkPen((120, 255, 160), width=1.8),
            antialias=True,
        )
        self._aia_limb_curve.setZValue(20)
        self._aia_limb_curve.hide()
        self.map_plot.addItem(self._aia_limb_curve)

        # HMI vector magnetic field overlay: |B| magnitude layer under the
        # line work, streamlines, and quiver arrows split by Bz polarity
        # (red = toward the observer, blue = away) matching the polarity
        # convention of the magnetogram contour composite.
        self._vector_geometry: Any | None = None
        self._vector_mag_item = pg.ImageItem(axisOrder="row-major")
        self._vector_mag_item.setZValue(12)
        self._vector_mag_item.hide()
        self.map_plot.addItem(self._vector_mag_item)
        self._vector_stream_curve = pg.PlotCurveItem(
            pen=pg.mkPen((255, 210, 80), width=1.1), antialias=True
        )
        self._vector_stream_curve.setZValue(24)
        self._vector_stream_curve.hide()
        self.map_plot.addItem(self._vector_stream_curve)
        self._vector_arrow_pos_curve = pg.PlotCurveItem(
            pen=pg.mkPen((255, 80, 80), width=1.3), antialias=True
        )
        self._vector_arrow_pos_curve.setZValue(26)
        self._vector_arrow_pos_curve.hide()
        self.map_plot.addItem(self._vector_arrow_pos_curve)
        self._vector_arrow_neg_curve = pg.PlotCurveItem(
            pen=pg.mkPen((80, 150, 255), width=1.3), antialias=True
        )
        self._vector_arrow_neg_curve.setZValue(26)
        self._vector_arrow_neg_curve.hide()
        self.map_plot.addItem(self._vector_arrow_neg_curve)

        # Interactive region-of-interest selector (hidden until enabled). The
        # rectangle lives in data (arcsec) coordinates; bounds are converted to
        # pixel indices and pushed through the ROI callback.
        self._roi_active = False
        self._roi_rect = pg.RectROI(
            [0.0, 0.0],
            [1.0, 1.0],
            pen=pg.mkPen((0, 200, 255), width=1.6),
            handlePen=pg.mkPen((0, 200, 255), width=1.6),
            movable=True,
            rotatable=False,
            resizable=True,
        )
        self._roi_rect.setZValue(30)
        self._roi_rect.hide()
        self.map_plot.addItem(self._roi_rect)
        self._roi_rect.sigRegionChangeFinished.connect(self._on_roi_region_changed)

        self.ts_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.ts_plot.setMenuEnabled(False)
        self.ts_plot.hideButtons()
        self.ts_plot.getViewBox().setMouseEnabled(x=False, y=False)
        self.ts_plot.setLabel("bottom", "Time", units="UTC")
        self.ts_plot.setLabel("left", "Flux", units="W/m^2")
        self.ts_plot.setTitle("GOES/XRS Flux")

        self._stack.addWidget(self.map_plot)
        self._stack.addWidget(self.ts_plot)
        self._stack.setAlignment(self.map_plot, Qt.AlignCenter)
        self._stack.setCurrentWidget(self.map_plot)

        self.apply_theme()
        self._update_colorbar_visibility(is_rgb=False)

    def set_roi_callback(self, callback):
        self._roi_callback = callback

    def set_hover_callback(self, callback) -> None:
        """Report the data-space (arcsec) position under the mouse.

        ``callback(x_arcsec, y_arcsec)`` fires as the cursor moves over the map;
        ``callback(None, None)`` fires when it leaves the view, so consumers can
        clear a coordinate readout. Connected lazily so canvases that never ask
        for hover pay nothing.
        """
        self._hover_callback = callback
        if callback is not None and not getattr(self, "_hover_connected", False):
            try:
                self.map_plot.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)
                self._hover_connected = True
            except Exception:
                self._hover_connected = False

    def _on_scene_mouse_moved(self, scene_pos) -> None:
        callback = getattr(self, "_hover_callback", None)
        if callback is None:
            return
        try:
            vb = self.map_plot.getViewBox()
            if not vb.sceneBoundingRect().contains(scene_pos):
                callback(None, None)
                return
            view = vb.mapSceneToView(scene_pos)
            callback(float(view.x()), float(view.y()))
        except Exception:
            callback(None, None)

    def set_click_callback(self, callback) -> None:
        """Report mouse clicks on the map in data-space (arcsec) coordinates.

        ``callback(x_arcsec, y_arcsec, button)`` with ``button`` in
        {"left", "right", "other"}. Used by the measurement tools (ruler,
        profile, CME height-time picking). Connected lazily.
        """
        self._click_callback = callback
        if callback is not None and not getattr(self, "_click_connected", False):
            try:
                self.map_plot.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)
                self._click_connected = True
            except Exception:
                self._click_connected = False

    def _on_scene_mouse_clicked(self, ev) -> None:
        callback = getattr(self, "_click_callback", None)
        if callback is None:
            return
        try:
            vb = self.map_plot.getViewBox()
            scene_pos = ev.scenePos()
            if not vb.sceneBoundingRect().contains(scene_pos):
                return
            view = vb.mapSceneToView(scene_pos)
            qt_button = ev.button()
            if qt_button == Qt.LeftButton:
                button = "left"
            elif qt_button == Qt.RightButton:
                button = "right"
            else:
                button = "other"
            callback(float(view.x()), float(view.y()), button)
            ev.accept()
        except Exception:
            pass

    def set_measurement_overlay(self, xs_arcsec, ys_arcsec, *, connect: bool = True) -> None:
        """Draw the measurement pick markers (crosses) and connecting segment.

        Coordinates are data-space arcsec (the view coordinates the canvas plots
        in). A single reused curve + scatter pair keeps repeated calls cheap.
        """
        xs = np.asarray(list(xs_arcsec), dtype=float)
        ys = np.asarray(list(ys_arcsec), dtype=float)
        if not hasattr(self, "_measure_curve"):
            pen = pg.mkPen((255, 210, 60), width=1.6, style=Qt.DashLine)
            self._measure_curve = pg.PlotCurveItem(pen=pen)
            self._measure_curve.setZValue(40)
            self._measure_points = pg.ScatterPlotItem(
                symbol="+", size=14, pen=pg.mkPen((255, 210, 60), width=1.6), brush=None
            )
            self._measure_points.setZValue(41)
            self.map_plot.addItem(self._measure_curve)
            self.map_plot.addItem(self._measure_points)
        self._measure_points.setData(x=xs, y=ys)
        if connect and xs.size >= 2:
            self._measure_curve.setData(x=xs, y=ys)
            self._measure_curve.show()
        else:
            self._measure_curve.setData(x=[], y=[])
        self._measure_points.show()

    def clear_measurement_overlay(self) -> None:
        if hasattr(self, "_measure_curve"):
            self._measure_curve.setData(x=[], y=[])
            self._measure_points.setData(x=[], y=[])

    def set_colormap_name(self, name: str) -> None:
        text = str(name or "").strip() or "inferno"
        try:
            try:
                import sunpy.visualization.colormaps  # noqa: F401
            except Exception:
                pass
            cmap = pg.colormap.get(text, source="matplotlib")
            lut = cmap.getLookupTable(nPts=256)
        except Exception:
            cmap = _fallback_colormap(text)
            if cmap is None:
                try:
                    cmap = pg.colormap.get(text)
                    lut = cmap.getLookupTable(nPts=256)
                except Exception:
                    cmap = pg.colormap.get("inferno", source="matplotlib")
                    lut = cmap.getLookupTable(nPts=256)
                    text = "inferno"
            else:
                lut = cmap.getLookupTable(nPts=256)
        self._base_map_lut = lut
        self._base_map_colormap = cmap
        self._colormap_name = text
        self._refresh_effective_colormap()
        if self.map_image.image is not None and not (
            self.map_image.image.ndim == 3 and self.map_image.image.shape[-1] in (3, 4)
        ):
            self._apply_image_colormap()
        if self._colorbar is not None:
            self._colorbar.setColorMap(self._map_colormap)

    def colormap_name(self) -> str:
        return self._colormap_name

    def apply_theme(self):
        dark = self._is_dark_ui()
        bg = (12, 12, 12) if dark else (250, 252, 255)
        self.map_plot.setBackground(bg)
        self.ts_plot.setBackground(bg)
        self.map_plot.getViewBox().setBackgroundColor(bg)
        self.ts_plot.getViewBox().setBackgroundColor(bg)

        fg = (225, 232, 240) if dark else (30, 42, 56)
        grid = (95, 110, 128) if dark else (176, 190, 208)
        for plot in (self.map_plot, self.ts_plot):
            plot.getAxis("left").setPen(pg.mkPen(fg))
            plot.getAxis("left").setTextPen(pg.mkPen(fg))
            plot.getAxis("bottom").setPen(pg.mkPen(fg))
            plot.getAxis("bottom").setTextPen(pg.mkPen(fg))
            plot.getPlotItem().titleLabel.item.setDefaultTextColor(pg.mkColor(fg))
        self.map_plot.showGrid(x=True, y=True, alpha=0.22)
        try:
            self.map_plot.getPlotItem().ctrl.xGridCheck.setChecked(True)
            self.map_plot.getPlotItem().ctrl.yGridCheck.setChecked(True)
            self.map_plot.getPlotItem().ctrl.gridAlphaSlider.setValue(22)
            self.map_plot.getViewBox().setBorder(pg.mkPen(grid, width=1))
        except Exception:
            pass
        try:
            if self._colorbar is not None:
                self._colorbar.setPen(pg.mkPen(fg))
                self._colorbar.axis.setTextPen(pg.mkPen(fg))
        except Exception:
            pass
        self._refresh_effective_colormap()

    def _set_map_axis_labels(self) -> None:
        self.map_plot.setLabel("bottom", "Solar X (arcsec)")
        self.map_plot.setLabel("left", "Solar Y (arcsec)")

    def _is_dark_ui(self) -> bool:
        theme = getattr(self, "theme", None)
        if theme is not None and hasattr(theme, "is_dark"):
            try:
                return bool(theme.is_dark())
            except Exception:
                pass
        if theme is not None and hasattr(theme, "_dark"):
            try:
                return bool(getattr(theme, "_dark"))
            except Exception:
                pass
        app = QGuiApplication.instance()
        palette = app.palette() if app is not None else self.palette()
        return palette.color(QPalette.Window).lightness() < 128

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._enforce_square_map_plot()

    def _enforce_square_map_plot(self) -> None:
        if not self._map_square_enabled or not self.is_map_visible():
            return
        available = self.contentsRect()
        available_w = int(available.width())
        available_h = int(available.height())
        if available_w <= 0 or available_h <= 0:
            return
        chrome_w = 0
        chrome_h = 0
        try:
            view_geom = self.map_plot.getViewBox().screenGeometry()
            view_w = int(view_geom.width())
            view_h = int(view_geom.height())
            widget_w = int(self.map_plot.width())
            widget_h = int(self.map_plot.height())
            if view_w > 0 and view_h > 0 and widget_w > 0 and widget_h > 0:
                chrome_w = max(0, widget_w - view_w)
                chrome_h = max(0, widget_h - view_h)
        except Exception:
            chrome_w = 0
            chrome_h = 0
        side = min(max(1, available_w - chrome_w), max(1, available_h - chrome_h))
        target_w = side + chrome_w
        target_h = side + chrome_h
        if abs(int(self.map_plot.width()) - target_w) > 1 or abs(int(self.map_plot.height()) - target_h) > 1:
            self.map_plot.setFixedSize(target_w, target_h)
            if not self._square_reflow_pending:
                self._square_reflow_pending = True
                QTimer.singleShot(0, self._finish_square_reflow)

    def _finish_square_reflow(self) -> None:
        self._square_reflow_pending = False
        self._enforce_square_map_plot()

    def _unclamp_title_min_width(self) -> None:
        """A long plot title must never impose a minimum width on the plot.

        pyqtgraph's LabelItem re-pins its minimum size to the rendered text
        rect on every setText AND every resize (updateMin), so the plot's
        minimum width ratchets up with the longest title seen — after which
        the central layout clamps at that stale minimum and the square-map
        enforcement can no longer shrink the plot. Wrap the title label's
        updateMin so the width component is always released; a long title
        then simply clips instead of blocking the layout.
        """
        try:
            label = self.map_plot.getPlotItem().titleLabel
        except Exception:
            return
        if not getattr(label, "_width_ratchet_removed", False):
            original_update_min = label.updateMin

            def update_min_without_width_ratchet():
                original_update_min()
                self._zero_label_min_width(label)

            label.updateMin = update_min_without_width_ratchet
            label._width_ratchet_removed = True
        self._zero_label_min_width(label)

    @staticmethod
    def _zero_label_min_width(label: Any) -> None:
        try:
            label.setMinimumWidth(0)
            hints = getattr(label, "_sizeHint", None)
            if isinstance(hints, dict):
                key = Qt.SizeHint.MinimumSize
                if key in hints:
                    hints[key] = (0, hints[key][1])
            label.updateGeometry()
        except Exception:
            pass

    def map_background_lightness(self) -> int:
        return int(self.map_plot.backgroundBrush().color().lightness())

    def map_low_color_lightness(self) -> int:
        if self._map_lut is None or len(self._map_lut) == 0:
            return 0
        rgb = np.asarray(self._map_lut[0][:3], dtype=float)
        return int(round(float(np.mean(rgb))))

    def set_colorbar_visible(self, visible: bool) -> None:
        self._colorbar_visible = bool(visible)
        self._update_colorbar_visibility()

    def has_visible_colorbar(self) -> bool:
        return bool(self._colorbar is not None and self._colorbar_visible and self._colorbar.isVisible())

    def _update_colorbar_visibility(self, *, is_rgb: bool | None = None) -> None:
        if is_rgb is None:
            img = self.map_image.image
            is_rgb = bool(img is not None and img.ndim == 3 and img.shape[-1] in (3, 4))
        if self._colorbar is None:
            self._enforce_square_map_plot()
            return
        if self._colorbar_visible and not is_rgb and self.map_image.image is not None:
            self._colorbar.show()
        else:
            self._colorbar.hide()
        self._enforce_square_map_plot()

    def map_viewbox_size(self) -> tuple[int, int]:
        try:
            rect = self.map_plot.getViewBox().screenGeometry()
            return int(rect.width()), int(rect.height())
        except Exception:
            return int(self.map_plot.width()), int(self.map_plot.height())

    def _apply_image_colormap(self) -> None:
        try:
            self.map_image.setColorMap(self._map_colormap)
        except Exception:
            self.map_image.setLookupTable(self._map_lut)

    def _refresh_effective_colormap(self) -> None:
        lut = np.asarray(self._base_map_lut, dtype=np.ubyte).copy()
        if lut.ndim != 2 or lut.shape[0] == 0:
            return
        self._map_lut = lut
        self._map_colormap = pg.ColorMap(np.linspace(0.0, 1.0, int(lut.shape[0])), lut)
        if getattr(self, "map_image", None) is not None and self.map_image.image is not None:
            self._apply_image_colormap()
        if getattr(self, "_colorbar", None) is not None:
            self._colorbar.setColorMap(self._map_colormap)

    def set_vector_field_overlay(self, geometry: Any | None) -> None:
        """Draw (or clear, with None) an HMI vector-field overlay.

        ``geometry`` is a :class:`~src.Backend.hmi_vector_field.VectorOverlayGeometry`
        with NaN-separated polylines and an optional RGBA magnitude layer, all
        in the map's arcsec coordinates.
        """
        self._vector_geometry = geometry
        empty = np.asarray([], dtype=float)

        def _set_curve(curve, x_data, y_data):
            x_arr = np.asarray(x_data if x_data is not None else empty, dtype=float)
            y_arr = np.asarray(y_data if y_data is not None else empty, dtype=float)
            if x_arr.size and y_arr.size and x_arr.size == y_arr.size:
                curve.setData(x_arr, y_arr, connect="finite")
                curve.show()
            else:
                curve.setData([], [])
                curve.hide()

        if geometry is None:
            for curve in (
                self._vector_arrow_pos_curve,
                self._vector_arrow_neg_curve,
                self._vector_stream_curve,
            ):
                curve.setData([], [])
                curve.hide()
            self._vector_mag_item.clear()
            self._vector_mag_item.hide()
            return

        _set_curve(self._vector_arrow_pos_curve, getattr(geometry, "arrows_pos_x", None), getattr(geometry, "arrows_pos_y", None))
        _set_curve(self._vector_arrow_neg_curve, getattr(geometry, "arrows_neg_x", None), getattr(geometry, "arrows_neg_y", None))
        _set_curve(self._vector_stream_curve, getattr(geometry, "stream_x", None), getattr(geometry, "stream_y", None))

        rgba = getattr(geometry, "magnitude_rgba", None)
        rect = getattr(geometry, "magnitude_rect", None)
        if rgba is not None and rect is not None:
            self._vector_mag_item.setImage(np.asarray(rgba, dtype=np.uint8), autoLevels=False)
            x0, y0, width, height = [float(v) for v in rect]
            self._vector_mag_item.setRect(QRectF(x0, y0, width, height))
            self._vector_mag_item.show()
        else:
            self._vector_mag_item.clear()
            self._vector_mag_item.hide()

    def has_vector_field_overlay(self) -> bool:
        return bool(
            self._vector_geometry is not None
            and (
                self._vector_arrow_pos_curve.isVisible()
                or self._vector_arrow_neg_curve.isVisible()
                or self._vector_stream_curve.isVisible()
                or self._vector_mag_item.isVisible()
            )
        )

    def clear_plot(self):
        self.map_image.clear()
        self.set_aia_limb_overlay(None, None, visible=False)
        self.set_vector_field_overlay(None)
        self.clear_region_overlays()
        self._last_map_levels = None
        self._update_colorbar_visibility(is_rgb=False)
        self.ts_plot.clear()
        self._axis_transform = self._default_axis_transform()
        self._last_map_bounds = None

    def reset_map_view(self) -> None:
        """Drop the cached view bounds so the next ``plot_map_data`` re-fits.

        Map renders normally preserve the user's pan/zoom when only the data
        (not its extent) changes. After a crop the extent changes and the view
        must snap to the new region; clearing the cache forces that re-fit
        unconditionally, immune to any stale bounds left by renderer switches
        or earlier panning.
        """
        self._last_map_bounds = None

    def has_plot_content(self) -> bool:
        return self.map_image.image is not None

    def set_grid_visible(self, visible: bool) -> None:
        self.map_plot.showGrid(x=bool(visible), y=bool(visible), alpha=0.25 if visible else 0.0)

    def enable_roi_selector(self):
        """Show an interactive ROI box (default: central quarter) and emit its
        pixel bounds through the ROI callback."""
        img = self.map_image.image
        if img is None:
            return
        ny = int(img.shape[0])
        nx = int(img.shape[1]) if img.ndim >= 2 else 1
        x0, y0, width, height = self._map_rect_from_transform(img.shape)

        # Default ROI covers the central half of the field of view.
        roi_x = x0 + width * 0.25
        roi_y = y0 + height * 0.25
        roi_w = width * 0.5
        roi_h = height * 0.5

        self._roi_active = True
        self._roi_rect.blockSignals(True)
        self._roi_rect.setPos([roi_x, roi_y])
        self._roi_rect.setSize([roi_w, roi_h])
        self._roi_rect.blockSignals(False)
        self._roi_rect.show()
        self._on_roi_region_changed()

    def disable_roi_selector(self):
        self._roi_active = False
        self._roi_rect.hide()

    def roi_selector_active(self) -> bool:
        return bool(self._roi_active and self._roi_rect.isVisible())

    def reset_roi(self):
        self._roi_bounds = None
        self._roi_active = False
        self._roi_rect.hide()

    def set_roi_arcsec_bounds(
        self,
        x0_arcsec: float,
        x1_arcsec: float,
        y0_arcsec: float,
        y1_arcsec: float,
        *,
        emit: bool = True,
    ) -> None:
        """Position the interactive ROI rectangle in map/arcsec coordinates."""
        img = self.map_image.image
        if img is None or img.ndim < 2:
            return
        map_x0, map_y0, width, height = self._map_rect_from_transform(img.shape)
        map_x1 = map_x0 + width
        map_y1 = map_y0 + height
        x_low, x_high = sorted((float(x0_arcsec), float(x1_arcsec)))
        y_low, y_high = sorted((float(y0_arcsec), float(y1_arcsec)))
        x_low = max(min(map_x0, map_x1), min(max(map_x0, map_x1), x_low))
        x_high = max(min(map_x0, map_x1), min(max(map_x0, map_x1), x_high))
        y_low = max(min(map_y0, map_y1), min(max(map_y0, map_y1), y_low))
        y_high = max(min(map_y0, map_y1), min(max(map_y0, map_y1), y_high))
        if x_high <= x_low or y_high <= y_low:
            return

        self._roi_active = True
        self._roi_rect.blockSignals(True)
        self._roi_rect.setPos([x_low, y_low])
        self._roi_rect.setSize([x_high - x_low, y_high - y_low])
        self._roi_rect.blockSignals(False)
        self._roi_rect.show()
        if emit:
            self._on_roi_region_changed()
        else:
            self._roi_bounds = self._roi_pixel_bounds()

    def _on_roi_region_changed(self):
        if not self._roi_active:
            return
        bounds = self._roi_pixel_bounds()
        self._roi_bounds = bounds
        if self._roi_callback is not None and bounds is not None:
            self._roi_callback(bounds)

    def _roi_pixel_bounds(self) -> tuple[int, int, int, int] | None:
        img = self.map_image.image
        if img is None or img.ndim < 2:
            return None
        ny = int(img.shape[0])
        nx = int(img.shape[1])
        try:
            pos = self._roi_rect.pos()
            size = self._roi_rect.size()
            ax0 = float(pos.x())
            ay0 = float(pos.y())
            ax1 = ax0 + float(size.x())
            ay1 = ay0 + float(size.y())
        except Exception:
            return None

        x0, y0, _width, _height = self._map_rect_from_transform(img.shape)
        tx = self._axis_transform
        x_scale = float(tx.get("x_scale_arcsec_per_pix", 1.0)) or 1.0
        y_scale = float(tx.get("y_scale_arcsec_per_pix", 1.0)) or 1.0

        px0 = (ax0 - x0) / x_scale
        px1 = (ax1 - x0) / x_scale
        py0 = (ay0 - y0) / y_scale
        py1 = (ay1 - y0) / y_scale

        x_lo, x_hi = sorted((px0, px1))
        y_lo, y_hi = sorted((py0, py1))
        x_lo = max(0, int(np.floor(x_lo)))
        x_hi = min(nx, int(np.ceil(x_hi)))
        y_lo = max(0, int(np.floor(y_lo)))
        y_hi = min(ny, int(np.ceil(y_hi)))
        if x_hi <= x_lo or y_hi <= y_lo:
            return None
        return (x_lo, x_hi, y_lo, y_hi)

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

    def clear_region_overlays(self) -> None:
        for item in list(self._region_overlay_items):
            try:
                self.map_plot.removeItem(item)
            except Exception:
                pass
        self._region_overlay_items = []

    def set_region_overlays(self, regions: Sequence[Any] | None, *, visible: bool = True) -> None:
        self.clear_region_overlays()
        if not visible:
            return
        for region in list(regions or []):
            bbox = getattr(region, "bbox", None)
            if not bbox or len(bbox) != 4:
                continue
            try:
                x0, x1, y0, y1 = [float(v) for v in bbox]
            except Exception:
                continue
            ax0, ay0 = self.map_arcsec_from_pixel(x0, y0)
            ax1, ay1 = self.map_arcsec_from_pixel(x1, y1)
            x_low, x_high = sorted((ax0, ax1))
            y_low, y_high = sorted((ay0, ay1))
            curve = pg.PlotCurveItem(
                [x_low, x_high, x_high, x_low, x_low],
                [y_low, y_low, y_high, y_high, y_low],
                pen=pg.mkPen((0, 220, 255), width=1.4),
                antialias=True,
            )
            curve.setZValue(35)
            self.map_plot.addItem(curve)
            self._region_overlay_items.append(curve)

            label = str(getattr(region, "label", "") or f"R{getattr(region, 'region_id', '')}").strip()
            if label:
                text_item = pg.TextItem(label, color=(0, 220, 255), anchor=(0, 1))
                text_item.setZValue(36)
                text_item.setPos(x_low, y_high)
                self.map_plot.addItem(text_item)
                self._region_overlay_items.append(text_item)

    def region_overlay_count(self) -> int:
        return len(self._region_overlay_items)

    def show_map(self):
        self._stack.setCurrentWidget(self.map_plot)
        self._enforce_square_map_plot()

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

    def map_pixel_from_arcsec(self, x_arcsec: float, y_arcsec: float) -> tuple[float, float]:
        """Exact inverse of :meth:`map_arcsec_from_pixel`."""
        tx = self._axis_transform
        x_scale = float(tx["x_scale_arcsec_per_pix"]) or 1.0
        y_scale = float(tx["y_scale_arcsec_per_pix"]) or 1.0
        x_pix = tx["x_ref_pix"] + (float(x_arcsec) - tx["x_ref_arcsec"]) / x_scale
        y_pix = tx["y_ref_pix"] + (float(y_arcsec) - tx["y_ref_arcsec"]) / y_scale
        return float(x_pix), float(y_pix)

    def map_view_rect(self) -> tuple[float, float, float, float]:
        rect = self.map_plot.getViewBox().viewRect()
        return (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))

    def backend_name(self) -> str:
        return "pyqtgraph"

    def opengl_enabled(self) -> bool:
        try:
            return bool(pg.getConfigOption("useOpenGL"))
        except Exception:
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

        if is_rgb:
            self.map_image.setLookupTable(None)
            self.map_image.setImage(arr, autoLevels=False)
            self._last_map_levels = None
        else:
            data = np.asarray(arr, dtype=np.float32)
            self._apply_image_colormap()
            self.map_image.setImage(data, autoLevels=False)

            finite = data[np.isfinite(data)]
            if vmin is not None and vmax is not None and float(vmax) > float(vmin):
                levels = (float(vmin), float(vmax))
            elif finite.size > 0:
                lo = float(np.nanmin(finite))
                hi = float(np.nanmax(finite))
                if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                    levels = (lo, hi)
                else:
                    levels = (lo - 1.0, lo + 1.0)
            else:
                levels = (0.0, 1.0)
            self.map_image.setLevels(list(levels))
            self._last_map_levels = levels
            if self._colorbar is not None:
                self._colorbar.setLevels(levels)
                self._colorbar.setColorMap(self._map_colormap)
            self._update_colorbar_visibility(is_rgb=False)

        # Position/scale the image AFTER setImage: pyqtgraph's setRect derives the
        # transform from the *current* image dimensions, so calling it earlier
        # would scale a freshly cropped frame by the previous frame's size and
        # shrink it into a corner.
        self.map_image.setRect(QRectF(x0, y0, width, height))

        self.map_plot.setTitle(title)
        self._unclamp_title_min_width()
        self._set_map_axis_labels()
        self._update_colorbar_visibility(is_rgb=is_rgb)

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
        self._enforce_square_map_plot()

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
        self._region_overlays: list[Any] = []
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
        self.base_diff_check = QCheckBox("Base Diff")
        self.base_diff_check.setEnabled(False)
        self.base_diff_check.setVisible(False)
        self.aia_limb_check = QCheckBox("AIA Limb (EUVI)")
        self.aia_limb_check.setEnabled(False)
        self.nrgf_check = QCheckBox("NRGF")
        self.nrgf_check.setEnabled(False)
        self.nrgf_check.setToolTip(
            "Normalizing-Radial-Graded Filter: flattens the coronagraph's radial "
            "brightness fall-off to reveal faint CME fronts (COR1/COR2, LASCO)."
        )
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
        self.roi_select_check = QCheckBox("Select ROI")
        self.roi_select_check.setEnabled(False)
        self.roi_select_check.setToolTip("Draw a rectangle on the map to analyse a region of interest.")
        self.reset_roi_btn = QPushButton("Reset ROI")
        self.reset_roi_btn.setEnabled(False)

        top_row.addWidget(self.frame_label)
        top_row.addWidget(self.frame_slider, 1)

        bottom_row.addWidget(self.running_diff_check)
        bottom_row.addWidget(self.aia_limb_check)
        bottom_row.addWidget(self.nrgf_check)
        bottom_row.addWidget(self.play_btn)
        bottom_row.addWidget(self.pause_btn)
        bottom_row.addWidget(self.rewind_btn)
        bottom_row.addWidget(self.fps_spin)
        bottom_row.addWidget(self.roi_select_check)
        bottom_row.addWidget(self.reset_roi_btn)
        bottom_row.addStretch(1)

        map_layout.addLayout(top_row)
        map_layout.addLayout(bottom_row)
        layout.addWidget(self.map_controls)

        self.map_controls.setVisible(False)

        difference_menu = self.menuBar().addMenu("Difference")
        self.base_diff_action = QAction("Base Difference", self)
        self.base_diff_action.setCheckable(True)
        self.base_diff_action.setEnabled(False)
        difference_menu.addAction(self.base_diff_action)

    def _connect_signals(self):
        self.frame_slider.valueChanged.connect(self._on_frame_slider_changed)
        self.running_diff_check.toggled.connect(self._on_running_diff_toggled)
        self.base_diff_check.toggled.connect(self._on_base_diff_toggled)
        self.base_diff_action.toggled.connect(self._on_base_diff_action_toggled)
        self.aia_limb_check.toggled.connect(self._on_aia_limb_toggled)
        self.nrgf_check.toggled.connect(self._on_nrgf_toggled)
        self.play_btn.clicked.connect(self._on_play_clicked)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.rewind_btn.clicked.connect(self._on_rewind_clicked)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        self.roi_select_check.toggled.connect(self._on_roi_select_toggled)
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
        self._region_overlays = []

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
        self.base_diff_check.setEnabled(has_multiple)
        self.base_diff_check.setChecked(False)
        self.base_diff_action.setEnabled(has_multiple)
        self.base_diff_action.setChecked(False)
        has_euvi = any(self._is_stereo_euvi_frame(frame) for frame in values)
        has_euvi = bool(has_euvi or self._metadata_indicates_stereo_euvi(self._map_metadata))
        self.aia_limb_check.setEnabled(has_euvi)
        self.aia_limb_check.setChecked(False)
        has_coronagraph = any(self._is_coronagraph_frame(frame) for frame in values)
        has_coronagraph = bool(has_coronagraph or self._metadata_indicates_coronagraph(self._map_metadata))
        self.nrgf_check.setEnabled(has_coronagraph)
        self.nrgf_check.setChecked(False)
        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.rewind_btn.setEnabled(False)
        self.reset_roi_btn.setEnabled(True)
        self.roi_select_check.blockSignals(True)
        self.roi_select_check.setChecked(False)
        self.roi_select_check.setEnabled(True)
        self.roi_select_check.blockSignals(False)

        # Colour each frame with the instrument-appropriate colormap sunpy
        # assigned to its source (AIA/LASCO/EUVI/COR/HI/SUVI/HMI) instead of the
        # generic default; users can still override via the colormap control.
        cmap_name = self._colormap_name_for_frame(values[0])
        if cmap_name:
            self.canvas.set_colormap_name(cmap_name)

        self.canvas.clear_plot()
        self.canvas.reset_roi()
        self.canvas.clear_region_overlays()
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
        self._region_overlays = []

        self._play_timer.stop()
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)
        self.frame_slider.setEnabled(False)
        self.running_diff_check.setEnabled(False)
        self.running_diff_check.setChecked(False)
        self.base_diff_check.setEnabled(False)
        self.base_diff_check.setChecked(False)
        self.base_diff_action.setEnabled(False)
        self.base_diff_action.setChecked(False)
        self.aia_limb_check.setEnabled(False)
        self.aia_limb_check.setChecked(False)
        self.play_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.rewind_btn.setEnabled(False)
        self.reset_roi_btn.setEnabled(False)
        self.roi_select_check.blockSignals(True)
        self.roi_select_check.setChecked(False)
        self.roi_select_check.setEnabled(False)
        self.roi_select_check.blockSignals(False)
        self.canvas.disable_roi_selector()

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

    def current_axis_transform(self) -> dict[str, float]:
        return dict(getattr(self.canvas, "_axis_transform", {}) or {})

    def set_region_overlays(self, regions: Sequence[Any] | None) -> None:
        self._region_overlays = list(regions or [])
        self.canvas.set_region_overlays(self._region_overlays)

    def clear_region_overlays(self) -> None:
        self._region_overlays = []
        self.canvas.clear_region_overlays()

    def region_overlay_count(self) -> int:
        return self.canvas.region_overlay_count()

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
        if self.running_diff_check.isChecked() and self.base_diff_check.isChecked():
            self.base_diff_check.blockSignals(True)
            self.base_diff_check.setChecked(False)
            self.base_diff_check.blockSignals(False)
        if self.running_diff_check.isChecked() and self.base_diff_action.isChecked():
            self.base_diff_action.blockSignals(True)
            self.base_diff_action.setChecked(False)
            self.base_diff_action.blockSignals(False)
        self._render_current_map_frame(emit_signal=True)

    def _on_base_diff_toggled(self, _checked: bool):
        if self._mode != "map":
            return
        if self.base_diff_check.isChecked() and self.running_diff_check.isChecked():
            self.running_diff_check.blockSignals(True)
            self.running_diff_check.setChecked(False)
            self.running_diff_check.blockSignals(False)
        if self.base_diff_action.isChecked() != self.base_diff_check.isChecked():
            self.base_diff_action.blockSignals(True)
            self.base_diff_action.setChecked(self.base_diff_check.isChecked())
            self.base_diff_action.blockSignals(False)
        self._render_current_map_frame(emit_signal=True)

    def _on_base_diff_action_toggled(self, checked: bool):
        if self._mode != "map":
            return
        if self.base_diff_check.isChecked() == bool(checked):
            return
        self.base_diff_check.setChecked(bool(checked))

    def _on_aia_limb_toggled(self, _checked: bool):
        if self._mode != "map":
            self.canvas.set_aia_limb_overlay(None, None, visible=False)
            return
        self._render_current_map_frame(emit_signal=False)

    def _on_nrgf_toggled(self, _checked: bool):
        if self._mode != "map":
            return
        self._render_current_map_frame(emit_signal=False)

    def _is_coronagraph_frame(self, frame: Any) -> bool:
        """True for white-light coronagraph frames (SOHO/LASCO, STEREO COR1/COR2)."""
        instrument = _safe_text(getattr(frame, "instrument", None)).upper()
        detector = _safe_text(getattr(frame, "detector", None)).upper()
        meta = getattr(frame, "meta", None)
        meta_instrument = self._frame_meta_text(meta, ("instrume", "instrument")).upper()
        meta_detector = self._frame_meta_text(meta, ("detector",)).upper()
        text = " ".join((instrument, detector, meta_instrument, meta_detector))
        return ("LASCO" in text) or ("COR1" in text) or ("COR2" in text)

    def _metadata_indicates_coronagraph(self, metadata: dict[str, Any] | None) -> bool:
        if not metadata:
            return False
        text = " ".join(
            _safe_text(metadata.get(key, None)).upper()
            for key in ("instrument", "detector", "query_instrument", "query_detector")
        )
        return ("LASCO" in text) or ("COR1" in text) or ("COR2" in text)

    def _nrgf_filter_frame(self, frame: Any, current: np.ndarray) -> np.ndarray:
        """Return the NRGF-filtered frame, falling back to the raw array on error."""
        try:
            from src.Backend.coronagraph import nrgf, solar_center_from_meta

            center = solar_center_from_meta(getattr(frame, "meta", None), data_shape=current.shape)
            filtered = nrgf(current, center)
            if np.isfinite(filtered).any():
                return filtered
        except Exception:
            pass
        return current

    def _render_current_map_frame(self, *, emit_signal: bool):
        if not self._map_frames:
            return

        idx = max(0, min(self._current_frame_index, len(self._map_frames) - 1))
        frame = self._map_frames[idx]

        current = self._prepare_map_array(getattr(frame, "data"), "current frame")
        title = self._frame_title(frame, idx)

        apply_nrgf = self.nrgf_check.isEnabled() and self.nrgf_check.isChecked()
        if apply_nrgf:
            current = self._nrgf_filter_frame(frame, current)
            title += " (NRGF)"

        if self.base_diff_check.isChecked() and len(self._map_frames) > 1:
            base = self._prepare_map_array(getattr(self._map_frames[0], "data"), "base frame")
            if base.shape == current.shape:
                current = current - base
                title += " (Base Difference)"
        elif self.running_diff_check.isChecked() and len(self._map_frames) > 1:
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
        self.canvas.set_region_overlays(self._region_overlays)
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

    def _on_roi_select_toggled(self, checked: bool):
        if self._mode != "map":
            return
        if checked:
            self.canvas.enable_roi_selector()
        else:
            self.canvas.disable_roi_selector()
            self._roi_bounds = None
            self.mapRoiChanged.emit(None)

    def reset_roi(self):
        self._roi_bounds = None
        self.canvas.reset_roi()
        if self.roi_select_check.isChecked():
            self.roi_select_check.blockSignals(True)
            self.roi_select_check.setChecked(False)
            self.roi_select_check.blockSignals(False)
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
            map_plot = getattr(getattr(self, "canvas", None), "map_plot", None)
            if map_plot is not None:
                try:
                    map_plot.setMinimumSize(0, 0)
                    map_plot.setMaximumSize(16777215, 16777215)
                except Exception:
                    pass
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
        try:
            self.canvas._enforce_square_map_plot()
        except Exception:
            pass

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
                break

            self._resize_guard = True
            try:
                self.resize(next_w, next_h)
            finally:
                self._resize_guard = False
            self._ensure_window_in_screen(available)

        map_w = int(map_plot.width())
        map_h = int(map_plot.height())
        if map_w > 0 and map_h > 0 and abs(map_w - map_h) > 1:
            side = max(220, min(map_w, map_h))
            try:
                map_plot.setFixedSize(side, side)
                self.canvas._enforce_square_map_plot()
            except Exception:
                pass

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

    def _colormap_name_for_frame(self, frame: Any) -> str | None:
        """Return the colormap name sunpy assigned to a map's source.

        Each sunpy map source sets its own ``plot_settings['cmap']`` — ``sdoaia171``,
        ``soholasco2``, ``stereocor2``, ``euvi195``, ``goes-rsuvi171``, ``hmimag`` and
        so on — which is exactly the instrument-appropriate colormap we want,
        instead of the generic ``inferno`` default. Falls back to ``frame.cmap`` and
        returns ``None`` when nothing usable is present.
        """
        cmap: Any = None
        settings = getattr(frame, "plot_settings", None)
        if isinstance(settings, dict):
            cmap = settings.get("cmap")
        if cmap is None:
            cmap = getattr(frame, "cmap", None)
        name = getattr(cmap, "name", None)
        if not name and isinstance(cmap, str):
            name = cmap
        text = str(name or "").strip()
        return text or None

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
