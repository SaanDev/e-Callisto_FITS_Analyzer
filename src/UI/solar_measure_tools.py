"""
e-CALLISTO FITS Analyzer
Version 2.7.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Interactive measurement tools for the Solar Image Analysis window.

The window exposes checkable tool buttons (ruler, intensity profile, region
stats, CME height–time picking); this module owns the click state machine and
the result dialogs so the already-large window class does not keep growing.
All science math lives in the pure backends (``image_measure``, ``coronagraph``)
— this file only translates canvas clicks into calls and renders the results.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.Backend.coronagraph import (
    HeightTimeFit,
    RSUN_KM,
    fit_height_time,
    pixel_radius_to_rsun,
    solar_center_from_meta,
)
from src.Backend.image_measure import line_profile, region_stats, ruler_measurement
from src.Backend.solar_data_analysis import frame_observation_time


class LineProfileDialog(QDialog):
    """Intensity along a user-drawn segment (distance in arcsec)."""

    def __init__(self, distances_arcsec, intensities, *, title: str, unit: str = "DN", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Intensity Profile")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        self._figure = Figure(figsize=(6.4, 3.6))
        self.canvas = FigureCanvas(self._figure)
        layout.addWidget(self.canvas)

        ax = self._figure.add_subplot(111)
        ax.plot(np.asarray(distances_arcsec, dtype=float), np.asarray(intensities, dtype=float),
                linewidth=1.4, color="#e8a33d")
        ax.set_xlabel("Distance along cut (arcsec)")
        ax.set_ylabel(unit)
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.25)
        self._figure.tight_layout()
        self.canvas.draw_idle()


class HeightTimeDialog(QDialog):
    """CME height–time points with the linear (speed) fit overlaid."""

    def __init__(self, fit: HeightTimeFit, *, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CME Height–Time")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        self._figure = Figure(figsize=(6.4, 3.8))
        self.canvas = FigureCanvas(self._figure)
        layout.addWidget(self.canvas)

        heights_rsun = fit.heights_km / RSUN_KM
        ax = self._figure.add_subplot(111)
        ax.plot(fit.times_s / 60.0, heights_rsun, "o", markersize=5, color="#e8a33d",
                label="Leading-edge picks")
        # Linear fit line (speed) across the sampled interval.
        t_line = np.linspace(fit.times_s.min(), fit.times_s.max(), 50)
        h_line = (fit.intercept_km + fit.speed_km_s * t_line) / RSUN_KM
        ax.plot(t_line / 60.0, h_line, "--", linewidth=1.2, color="#5a8fd6",
                label=f"Linear fit: {fit.speed_km_s:,.0f} km/s")
        ax.set_xlabel("Time since first pick (min)")
        ax.set_ylabel("Plane-of-sky height (R☉)")
        accel_text = (
            f"  ·  a = {fit.acceleration_km_s2 * 1000.0:+,.1f} m/s²"
            if np.isfinite(fit.acceleration_km_s2)
            else ""
        )
        ax.set_title(f"{title}  ·  v = {fit.speed_km_s:,.0f} km/s{accel_text}", fontsize=10)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, loc="best")
        self._figure.tight_layout()
        self.canvas.draw_idle()


class JMapDialog(QDialog):
    """Time–elongation (J-map) image for the Heliospheric Imagers."""

    def __init__(self, jmap_image, radii_arcsec, *, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HI J-map (time–elongation)")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        self._figure = Figure(figsize=(6.4, 3.8))
        self.canvas = FigureCanvas(self._figure)
        layout.addWidget(self.canvas)

        image = np.asarray(jmap_image, dtype=float)
        radii = np.asarray(radii_arcsec, dtype=float)
        ax = self._figure.add_subplot(111)
        extent = (0.0, float(radii[-1] / 3600.0) if radii.size else 1.0, image.shape[0] - 0.5, -0.5)
        finite = image[np.isfinite(image)]
        vmin, vmax = (np.percentile(finite, (2, 98)) if finite.size else (None, None))
        ax.imshow(image, aspect="auto", cmap="gray", extent=extent, vmin=vmin, vmax=vmax)
        ax.set_xlabel("Elongation (degrees)")
        ax.set_ylabel("Frame index (time →)")
        ax.set_title(title, fontsize=10)
        self._figure.tight_layout()
        self.canvas.draw_idle()


class TrackingPanel(QWidget):
    """CME tracking side panel: picks table + live height–time plot.

    Sits right of the map canvas while continuous tracking is active. Every
    leading-edge click adds a row (Time UT, seconds since the first pick,
    height in R☉, position angle) and updates the height–time scatter/fit in
    real time, so the kinematics emerge while you are still clicking.
    """

    def __init__(self, parent: Any = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("CME Tracking")
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        self.auto_advance_check = QCheckBox("Auto-advance frame after each pick")
        self.auto_advance_check.setChecked(True)
        self.auto_advance_check.setToolTip(
            "After you click the CME front, jump straight to the next frame so a\n"
            "whole sequence can be tracked with one click per frame."
        )
        layout.addWidget(self.auto_advance_check)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Time (UT)", "t (s)", "Height (R☉)", "PA (°)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setMinimumWidth(300)
        layout.addWidget(self.table, 1)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "t (s since first pick)")
        self.plot.setLabel("left", "Height (R☉)")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self._scatter = pg.ScatterPlotItem(
            symbol="o", size=8, pen=pg.mkPen("#e8a33d"), brush=pg.mkBrush("#e8a33d")
        )
        self._fit_line = pg.PlotCurveItem(pen=pg.mkPen("#5a8fd6", width=1.6, style=Qt.DashLine))
        self.plot.addItem(self._scatter)
        self.plot.addItem(self._fit_line)
        layout.addWidget(self.plot, 1)

        self.speed_label = QLabel("Click the CME front on each frame.")
        self.speed_label.setWordWrap(True)
        layout.addWidget(self.speed_label)

        buttons = QHBoxLayout()
        self.fit_btn = QPushButton("Fit Height–Time")
        self.fit_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear Picks")
        self.clear_btn.setEnabled(False)
        buttons.addWidget(self.fit_btn)
        buttons.addWidget(self.clear_btn)
        layout.addLayout(buttons)

    def refresh(self, picks: dict[int, tuple]) -> None:
        """Rebuild the table and the live plot from the controller's picks."""
        entries = sorted(picks.values(), key=lambda item: item[0])
        self.table.setRowCount(len(entries))
        if not entries:
            self._scatter.setData(x=[], y=[])
            self._fit_line.setData(x=[], y=[])
            self.speed_label.setText("Click the CME front on each frame.")
            return

        t0 = entries[0][0]
        seconds = [(entry[0] - t0).total_seconds() for entry in entries]
        heights = [float(entry[1]) for entry in entries]
        for row, (entry, t_s) in enumerate(zip(entries, seconds)):
            when, height_rsun = entry[0], float(entry[1])
            pa_deg = float(entry[4]) if len(entry) > 4 else float("nan")
            cells = (
                f"{when:%H:%M:%S}",
                f"{t_s:.0f}",
                f"{height_rsun:.3f}",
                f"{pa_deg:.1f}" if np.isfinite(pa_deg) else "—",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

        self._scatter.setData(x=seconds, y=heights)
        if len(entries) >= 2:
            # Live linear fit: the plane-of-sky speed appears while clicking.
            coeffs = np.polyfit(seconds, heights, 1)
            t_line = np.linspace(min(seconds), max(seconds), 32)
            self._fit_line.setData(x=t_line, y=np.polyval(coeffs, t_line))
            speed_km_s = coeffs[0] * RSUN_KM
            self.speed_label.setText(
                f"{len(entries)} picks  ·  live linear fit: {speed_km_s:,.0f} km/s plane-of-sky"
            )
        else:
            self._fit_line.setData(x=[], y=[])
            self.speed_label.setText("1 pick — step to the next frame and click the front again.")


class MeasurementController(QObject):
    """Click state machine behind the canvas measurement tools.

    One controller per window; the active mode decides what each left click
    does. Right click (or Esc) cancels the in-progress pick without leaving the
    mode. Height–time picks are keyed per frame index so re-clicking a frame
    replaces its point.
    """

    MODES = ("ruler", "profile", "height_time")

    def __init__(self, window: Any):
        super().__init__(window)
        self.window = window
        self.mode: str | None = None
        self._pending: tuple[float, float] | None = None  # first pick (arcsec)
        # Height-time picks: frame_index -> (time, height_rsun, x_arc, y_arc, pa_deg)
        self.picks: dict[int, tuple[datetime, float, float, float, float]] = {}
        self._escape = QShortcut(QKeySequence(Qt.Key_Escape), window)
        self._escape.setContext(Qt.WidgetWithChildrenShortcut)
        self._escape.activated.connect(self.cancel)

    # ------------------------------------------------------------------ mode
    def set_mode(self, mode: str | None) -> None:
        if mode is not None and mode not in self.MODES:
            raise ValueError(f"Unknown measurement mode: {mode}")
        if mode != self.mode:
            self._pending = None
        self.mode = mode
        if mode is None:
            self._refresh_overlay()

    def cancel(self) -> None:
        """Drop the in-progress pick (Esc / right click)."""
        self._pending = None
        self._refresh_overlay()
        self._status("Measurement pick cancelled.")

    # ---------------------------------------------------------------- clicks
    def on_canvas_click(self, x_arc: float, y_arc: float, button: str) -> None:
        if self.mode is None:
            return
        if button == "right":
            self.cancel()
            return
        if button != "left":
            return
        if getattr(self.window, "_current_map_data", None) is None:
            return
        if self.mode == "ruler":
            self._click_ruler(x_arc, y_arc)
        elif self.mode == "profile":
            self._click_profile(x_arc, y_arc)
        elif self.mode == "height_time":
            self._click_height_time(x_arc, y_arc)

    def on_frame_changed(self) -> None:
        """Redraw pick markers for the newly shown frame."""
        self._refresh_overlay()

    # ----------------------------------------------------------------- ruler
    def _click_ruler(self, x_arc: float, y_arc: float) -> None:
        if self._pending is None:
            self._pending = (x_arc, y_arc)
            self._overlay_points([(x_arc, y_arc)])
            self._status("Ruler: click the second point.")
            return
        p0, self._pending = self._pending, None
        frame = self._current_frame()
        rsun = self.window._solar_radius_arcsec(frame) if frame is not None else None
        result = ruler_measurement(p0, (x_arc, y_arc), rsun_arcsec=rsun)
        self._overlay_points([p0, (x_arc, y_arc)], connect=True)

        parts = [f"Distance: {result.distance_arcsec:,.1f}″"]
        if result.distance_rsun is not None:
            parts.append(f"{result.distance_rsun:.3f} R☉")
        if result.distance_km is not None:
            parts.append(f"{result.distance_km / 1e3:,.0f} Mm")
        parts.append(f"PA {result.position_angle_deg:.1f}° (N→E)")
        text = "Ruler  ·  " + "  ·  ".join(parts)
        self._status(text)
        self._append_analysis(text)

    # --------------------------------------------------------------- profile
    def _click_profile(self, x_arc: float, y_arc: float) -> None:
        if self._pending is None:
            self._pending = (x_arc, y_arc)
            self._overlay_points([(x_arc, y_arc)])
            self._status("Profile: click the end of the cut.")
            return
        p0, self._pending = self._pending, None
        self._overlay_points([p0, (x_arc, y_arc)], connect=True)

        win = self.window
        image = win._current_map_data
        p0_pix = (win._axis_x_to_pixel(p0[0]), win._axis_y_to_pixel(p0[1]))
        p1_pix = (win._axis_x_to_pixel(x_arc), win._axis_y_to_pixel(y_arc))
        distances_px, intensity = line_profile(image, p0_pix, p1_pix)
        # Convert the distance axis to arcsec with the display plate scale.
        scale = abs(float(win._current_axis_transform.get("x_scale_arcsec_per_pix", 1.0))) or 1.0
        distances_arcsec = distances_px * scale

        title = f"{win._frames_word()}  ·  cut PA {ruler_measurement(p0, (x_arc, y_arc)).position_angle_deg:.0f}°"
        unit = "DN/s" if getattr(win, "_exposure_varies", False) else "DN"
        dialog = LineProfileDialog(distances_arcsec, intensity, title=title, unit=unit, parent=win)
        win._profile_dialog = dialog  # keep a reference so it is not GC'd
        dialog.show()
        self._status("Intensity profile plotted.")

    # ----------------------------------------------------------- height-time
    def _click_height_time(self, x_arc: float, y_arc: float) -> None:
        win = self.window
        frame = self._current_frame()
        if frame is None:
            return
        when = frame_observation_time(frame)
        if when is None:
            self._status("This frame has no observation time — cannot use it for height–time.")
            return
        try:
            center = solar_center_from_meta(getattr(frame, "meta", None), data_shape=win._current_map_data.shape)
        except Exception:
            ny, nx = win._current_map_data.shape[:2]
            center = ((nx - 1) / 2.0, (ny - 1) / 2.0)
        x_pix = win._axis_x_to_pixel(x_arc)
        y_pix = win._axis_y_to_pixel(y_arc)
        radius_px = float(np.hypot(x_pix - center[0], y_pix - center[1]))
        scale = abs(float(win._current_axis_transform.get("x_scale_arcsec_per_pix", 1.0))) or 1.0
        rsun_arcsec = win._solar_radius_arcsec(frame)
        height_rsun = pixel_radius_to_rsun(radius_px, scale, rsun_arcsec)

        # Position angle of the pick, seen from disk centre (N→E convention).
        pa_deg = ruler_measurement((0.0, 0.0), (x_arc, y_arc)).position_angle_deg

        idx = int(getattr(win, "_current_frame_index", 0))
        self.picks[idx] = (when, height_rsun, x_arc, y_arc, pa_deg)
        self._refresh_overlay()
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        self._status(
            f"Height–time: frame {idx + 1} at {when:%H:%M:%S} → {height_rsun:.2f} R☉, "
            f"PA {pa_deg:.0f}° ({len(self.picks)} pick(s))."
        )

        # Continuous tracking: one click per frame, the timeline advances itself.
        panel = getattr(win, "tracking_panel", None)
        if (
            panel is not None
            and panel.auto_advance_check.isChecked()
            and idx < len(getattr(win, "_map_frames", [])) - 1
        ):
            win.frame_slider.setValue(idx + 1)

    def finish_height_time(self) -> None:
        """Fit the collected picks and show the speed/acceleration dialog."""
        win = self.window
        if len(self.picks) < 2:
            self._status("Height–time needs picks on at least two frames.")
            return
        entries = sorted(self.picks.values(), key=lambda item: item[0])
        times = [entry[0] for entry in entries]
        heights_km = [entry[1] * RSUN_KM for entry in entries]
        fit = fit_height_time(times, heights_km)

        dialog = HeightTimeDialog(fit, title=win._frames_word(), parent=win)
        win._height_time_dialog = dialog
        dialog.show()

        accel = (
            f"{fit.acceleration_km_s2 * 1000.0:+,.1f} m/s²"
            if np.isfinite(fit.acceleration_km_s2)
            else "n/a (needs ≥3 picks)"
        )
        text = (
            f"CME height–time ({len(entries)} picks): plane-of-sky speed "
            f"{fit.speed_km_s:,.0f} km/s, acceleration {accel}."
        )
        self._status(text)
        self._append_analysis(text)

    def clear_height_time(self) -> None:
        self.picks.clear()
        self._refresh_overlay()
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        self._status("Height–time picks cleared.")

    def clear_all(self) -> None:
        """Reset every measurement: pending picks, tracking table and overlays."""
        self._pending = None
        self.picks.clear()
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        canvas = getattr(self.window, "pyqt_canvas", None)
        if canvas is not None:
            canvas.clear_measurement_overlay()
        self._status("All measurements cleared.")

    def _refresh_tracking_panel(self) -> None:
        panel = getattr(self.window, "tracking_panel", None)
        if panel is not None:
            panel.refresh(self.picks)

    # ------------------------------------------------------------ region stats
    def report_region_stats(self) -> None:
        """Summarise the current crop-ROI rectangle of the shown frame."""
        win = self.window
        image = getattr(win, "_current_map_data", None)
        if image is None:
            self._status("Load and plot frames first.")
            return
        try:
            bounds = win._crop_bounds_from_axis_fields(image.shape)
        except Exception:
            self._status("Set the crop rectangle (enable Rectangle crop) to choose the region.")
            return
        stats = region_stats(image, bounds)
        cx_arc, cy_arc = win.pyqt_canvas.map_arcsec_from_pixel(stats.centroid_x_pix, stats.centroid_y_pix)
        unit = "DN/s" if getattr(win, "_exposure_varies", False) else "DN"
        text = (
            f"Region stats ({stats.n_pixels} px)  ·  mean {stats.mean:,.2f} {unit}  ·  "
            f"median {stats.median:,.2f}  ·  min {stats.min:,.2f}  ·  max {stats.max:,.2f}  ·  "
            f"σ {stats.std:,.2f}  ·  centroid X={cx_arc:+.1f}″ Y={cy_arc:+.1f}″"
        )
        self._status("Region statistics written to the analysis panel.")
        self._append_analysis(text)

    # ------------------------------------------------------------------ utils
    def _current_frame(self) -> Any | None:
        frames = getattr(self.window, "_map_frames", None)
        if not frames:
            return None
        idx = max(0, min(int(getattr(self.window, "_current_frame_index", 0)), len(frames) - 1))
        return frames[idx]

    def _overlay_points(self, points: list[tuple[float, float]], *, connect: bool = False) -> None:
        canvas = getattr(self.window, "pyqt_canvas", None)
        if canvas is None:
            return
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        canvas.set_measurement_overlay(xs, ys, connect=connect)

    def _refresh_overlay(self) -> None:
        canvas = getattr(self.window, "pyqt_canvas", None)
        if canvas is None:
            return
        if self.mode == "height_time" and self.picks:
            idx = int(getattr(self.window, "_current_frame_index", 0))
            pick = self.picks.get(idx)
            if pick is not None:
                canvas.set_measurement_overlay([pick[2]], [pick[3]], connect=False)
                return
        if self._pending is not None:
            canvas.set_measurement_overlay([self._pending[0]], [self._pending[1]], connect=False)
            return
        canvas.clear_measurement_overlay()

    def _sync_ht_buttons(self) -> None:
        win = self.window
        if hasattr(win, "ht_fit_btn"):
            win.ht_fit_btn.setEnabled(len(self.picks) >= 2)
        if hasattr(win, "ht_clear_btn"):
            win.ht_clear_btn.setEnabled(bool(self.picks))

    def _status(self, text: str) -> None:
        try:
            self.window.statusBar().showMessage(text, 8000)
        except Exception:
            pass

    def _append_analysis(self, text: str) -> None:
        panel = getattr(self.window, "analysis_text", None)
        if panel is None:
            return
        existing = panel.toPlainText().strip()
        panel.setPlainText(f"{existing}\n{text}".strip() if existing else text)
