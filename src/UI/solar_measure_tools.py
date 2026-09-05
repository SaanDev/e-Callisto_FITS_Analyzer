"""
e-CALLISTO FITS Analyzer
Version 3.0.0
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
from typing import Any, Mapping, NamedTuple, Sequence

import numpy as np
import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    MAX_HEIGHT_TIME_ORDER,
    RSUN_KM,
    fit_height_time,
    pixel_radius_to_rsun,
    solar_center_from_meta,
)
from src.Backend.image_measure import (
    CircleFit,
    fit_circle,
    line_profile,
    region_stats,
    ruler_measurement,
)
from src.Backend.solar_data_analysis import frame_observation_time


# Height-time polynomial degrees offered in the tracking panel.
_FIT_ORDER_LABELS = {
    degree: label
    for degree, label in (
        (1, "Linear (1st order)"),
        (2, "Quadratic (2nd order)"),
        (3, "Cubic (3rd order)"),
    )
    if degree <= MAX_HEIGHT_TIME_ORDER
}
_FIT_ORDER_NAMES = {1: "linear", 2: "quadratic", 3: "cubic"}


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
    """CME tracking side panel: results table + live height–time plot.

    Sits right of the map canvas while continuous tracking is active. Every
    leading-edge click adds a row (Time UT, seconds since the first pick,
    height in R☉, position angle) and updates the height–time scatter/fit in
    real time, so the kinematics emerge while you are still clicking.

    The panel serves both tracking tools. :meth:`set_source` points the table,
    the plot and the export at one of them; the two result stores live in the
    controller and are never touched by the switch, so stepping between Track
    CME and Circle Fit never costs you the other tool's work.
    """

    # Column layout per tracking source.
    _HEADERS = {
        "height_time": ["Time (UT)", "t (s)", "Height (R☉)", "PA (°)"],
        "circle_fit": ["Time (UT)", "t (s)", "Radius (R☉)", "Lead (R☉)", "PA (°)", "N"],
    }

    def __init__(self, parent: Any = None):
        super().__init__(parent)
        self._source = "height_time"
        # The series last plotted, so changing the fit order can re-render it.
        # Set before any widget exists: the order combo's signal reaches back here.
        self._series: tuple[list[float], list[float], str, str] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(6)

        # No section title here: the panel already reads as the CME tracking area
        # from the graph's own labels, and the map header carries the frame title.
        self.auto_advance_check = QCheckBox("Auto-advance frame after each pick")
        self.auto_advance_check.setChecked(True)
        self.auto_advance_check.setToolTip(
            "After you click the CME front, jump straight to the next frame so a\n"
            "whole sequence can be tracked with one click per frame."
        )
        layout.addWidget(self.auto_advance_check)

        # Circle-fit-only controls. They sit here rather than in the measure
        # toolbar because they pair with auto-advance and the bar is already full.
        self.lock_center_check = QCheckBox("Lock centre")
        self.lock_center_check.setToolTip(
            "Freeze the circle centre at its current fitted value; later frames then\n"
            "fit the radius only. Steadier once the dome centre has settled."
        )
        self.commit_btn = QPushButton("Commit Circle")
        self.commit_btn.setToolTip(
            "Record this frame's fitted circle in the table (Ctrl+Return)."
        )
        # Ctrl+Return, never plain Return: a bare Return shortcut on this window
        # would swallow Enter from the sidebar's date and number editors.
        self.commit_btn.setShortcut(QKeySequence("Ctrl+Return"))
        circle_row = QHBoxLayout()
        circle_row.addWidget(self.lock_center_check)
        circle_row.addWidget(self.commit_btn)
        circle_row.addStretch(1)
        layout.addLayout(circle_row)
        for widget in (self.lock_center_check, self.commit_btn):
            widget.setVisible(False)  # circle-fit source only

        headers = self._HEADERS[self._source]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setMinimumWidth(300)
        layout.addWidget(self.table, 1)

        # Fit order, right under the table it applies to. A straight line is the
        # catalogue default; the curved fits are what an accelerating eruption
        # actually needs, and they report the speed at both ends of the track.
        order_row = QHBoxLayout()
        order_row.addWidget(QLabel("Fit:"))
        self.fit_order_combo = QComboBox()
        for degree, label in _FIT_ORDER_LABELS.items():
            self.fit_order_combo.addItem(label, degree)
        self.fit_order_combo.setToolTip(
            "Degree of the height–time polynomial:\n"
            "  Linear — constant speed; the acceleration comes from a companion\n"
            "    quadratic fit, as in the CDAW CME catalogue.\n"
            "  Quadratic — constant acceleration; speed reported at both ends.\n"
            "  Cubic — constant jerk; speed and acceleration at both ends.\n"
            "Every value carries a 1σ error from the fit covariance. A degree-n\n"
            "fit needs n+1 points, and n+2 before an error bar can be estimated."
        )
        self.fit_order_combo.currentIndexChanged.connect(self._on_fit_order_changed)
        order_row.addWidget(self.fit_order_combo, 1)
        layout.addLayout(order_row)

        # A GL viewport created while this panel is hidden in a splitter renders
        # solid black on many Windows drivers (the app enables OpenGL globally
        # for the big image canvas). NOTE: PlotWidget(useOpenGL=...) does NOT
        # work — PlotWidget forwards extra kwargs to its PlotItem, not the view,
        # and the view falls back to the global config. The view's useOpenGL()
        # METHOD is the only reliable opt-out: it swaps the viewport for a
        # software-rendered widget. This small scatter plot needs no GL anyway.
        self.plot = pg.PlotWidget()
        try:
            self.plot.useOpenGL(False)
        except Exception:
            pass
        self.plot.setLabel("bottom", "t (s since first pick)")
        self.plot.setLabel("left", "Height (R☉)")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMinimumHeight(160)
        self._scatter = pg.ScatterPlotItem(
            symbol="o", size=8, pen=pg.mkPen("#e8a33d"), brush=pg.mkBrush("#e8a33d")
        )
        self._fit_line = pg.PlotCurveItem(pen=pg.mkPen("#5a8fd6", width=1.6, style=Qt.DashLine))
        self.plot.addItem(self._scatter)
        self.plot.addItem(self._fit_line)
        layout.addWidget(self.plot, 1)
        self.apply_theme(dark=self._detect_dark())

        self.speed_label = QLabel("Click the CME front on each frame.")
        self.speed_label.setWordWrap(True)
        layout.addWidget(self.speed_label)

        buttons = QHBoxLayout()
        self.fit_btn = QPushButton("Fit Height–Time")
        self.fit_btn.setEnabled(False)
        self.fit_btn.setToolTip(
            "Fit the picks: the fitted line is drawn in the graph above and the\n"
            "plane-of-sky speed and acceleration appear below it."
        )
        self.clear_btn = QPushButton("Clear Picks")
        self.clear_btn.setEnabled(False)
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setEnabled(False)
        self.export_btn.setToolTip("Save the tracking table (UT, t, height, PA) to a CSV file.")
        self.export_btn.clicked.connect(self.export_csv)
        buttons.addWidget(self.fit_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addWidget(self.export_btn)
        layout.addLayout(buttons)

        self._entries: list[tuple] = []

    @staticmethod
    def _detect_dark() -> bool:
        """Match the app theme from the palette (same heuristic as the canvases)."""
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is None:
                return False
            return app.palette().window().color().lightness() < 128
        except Exception:
            return False

    def apply_theme(self, *, dark: bool) -> None:
        """Light/dark styling so the graph never renders as a black hole."""
        background = (12, 12, 12) if dark else (250, 252, 255)
        foreground = (220, 224, 228) if dark else (40, 44, 52)
        self.plot.setBackground(background)
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(foreground))
            axis.setTextPen(pg.mkPen(foreground))

    def refresh(self, picks: dict[int, tuple]) -> None:
        """Rebuild the table and the live plot from the controller's picks."""
        entries = sorted(picks.values(), key=lambda item: item[0])
        self._entries = entries
        self.table.setRowCount(len(entries))
        self.export_btn.setEnabled(bool(entries))
        if not entries:
            self._render_empty("Click the CME front on each frame.")
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

        self._render_series(
            seconds,
            heights,
            noun="picks",
            single_text="1 pick — step to the next frame and click the front again.",
        )

    def set_source(self, source: str) -> None:
        """Point the table, plot and export at one tracking tool's results.

        Only the *view* is reset here — the controller owns one result store per
        tool and pushes the active one back in, so switching tools never discards
        the other tool's rows.
        """
        if source not in self._HEADERS:
            raise ValueError(f"Unknown tracking source: {source}")
        self._source = source
        headers = self._HEADERS[source]
        self.table.setRowCount(0)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        # The resize mode has to be re-applied after the column count changes.
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        circle = source == "circle_fit"
        self.lock_center_check.setVisible(circle)
        self.commit_btn.setVisible(circle)
        self.plot.setLabel("left", "Radius (R☉)" if circle else "Height (R☉)")
        self.fit_btn.setText("Fit Radius–Time" if circle else "Fit Height–Time")
        self.clear_btn.setText("Clear Circles" if circle else "Clear Picks")
        self._entries = []
        self.export_btn.setEnabled(False)
        self._render_empty(
            "Click ≥3 points along the CME front, then Commit."
            if circle
            else "Click the CME front on each frame."
        )

    def refresh_circles(self, circles: Mapping[int, Any]) -> None:
        """Rebuild the table and the live plot from the controller's circle fits."""
        entries = sorted(
            (entry for entry in circles.values() if entry[0] is not None),
            key=lambda item: item[0],
        )
        self._entries = entries
        self.table.setRowCount(len(entries))
        self.export_btn.setEnabled(bool(entries))
        if not entries:
            self._render_empty("Click ≥3 points along the CME front, then Commit.")
            return

        t0 = entries[0][0]
        seconds = [(entry[0] - t0).total_seconds() for entry in entries]
        radii = [float(entry[1]) for entry in entries]
        for row, (entry, t_s) in enumerate(zip(entries, seconds)):
            cells = (
                f"{entry[0]:%H:%M:%S}",
                f"{t_s:.0f}",
                f"{float(entry[1]):.3f}",
                f"{float(entry[5]):.3f}",
                f"{float(entry[8]):.1f}",
                f"{int(entry[7])}",
            )
            # Fit quality and the centre stay in the tooltip: the panel is narrow
            # and six visible columns is already its limit.
            tip = (
                f"rms {float(entry[6]):.2f}″  ·  "
                f"centre ({float(entry[2]):+.1f}″, {float(entry[3]):+.1f}″)  ·  "
                f"R {float(entry[4]):,.1f}″"
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(tip)
                self.table.setItem(row, col, item)

        self._render_series(
            seconds,
            radii,
            noun="frames",
            single_text="1 frame — step to the next frame and fit the front again.",
        )

    def _render_empty(self, text: str) -> None:
        self._series = None
        self._scatter.setData(x=[], y=[])
        self._fit_line.setData(x=[], y=[])
        self.speed_label.setText(text)

    def _render_series(self, seconds, values, *, noun: str, single_text: str) -> None:
        """Scatter + live fit at the selected order, shared by both sources.

        The live fit is the same call the Fit button makes, so the numbers on
        screen while clicking are the ones the fit will report — and switching
        the order dropdown re-runs this from the stored series.
        """
        self._series = (list(seconds), list(values), noun, single_text)
        self._scatter.setData(x=seconds, y=values)
        order = self.fit_order()
        if len(seconds) < 2:
            self._fit_line.setData(x=[], y=[])
            self.speed_label.setText(single_text)
            self.plot.autoRange()
            return
        if max(seconds) <= min(seconds):
            # Frames that share one timestamp have no time baseline to fit, and
            # this runs straight off a click — so say so instead of raising.
            self._fit_line.setData(x=[], y=[])
            self.speed_label.setText(
                f"{len(seconds)} {noun} share one observation time — no speed to fit."
            )
            self.plot.autoRange()
            return
        if len(seconds) < order + 1:
            self._fit_line.setData(x=[], y=[])
            self.speed_label.setText(
                f"{len(seconds)} {noun}  ·  a {_FIT_ORDER_NAMES.get(order, '')} fit needs "
                f"{order + 1} — add more or drop the order."
            )
            self.plot.autoRange()
            return

        fit = fit_height_time(seconds, [float(v) * RSUN_KM for v in values], order=order)
        self._draw_fit_curve(fit)
        self.speed_label.setText(
            f"{len(seconds)} {noun}  ·  live {self.fit_order_name(fit)} fit:  "
            f"{self.fit_summary(fit, noun=noun)}"
        )
        self.plot.autoRange()

    def fit_order(self) -> int:
        """The polynomial degree currently selected in the dropdown."""
        value = self.fit_order_combo.currentData()
        try:
            return max(1, min(int(value), MAX_HEIGHT_TIME_ORDER))
        except (TypeError, ValueError):
            return 1

    def set_fit_order(self, order: int) -> None:
        """Select a degree without re-triggering a render (session restore)."""
        index = self.fit_order_combo.findData(int(order))
        if index < 0 or index == self.fit_order_combo.currentIndex():
            return
        was = self.fit_order_combo.blockSignals(True)
        self.fit_order_combo.setCurrentIndex(index)
        self.fit_order_combo.blockSignals(was)

    def _on_fit_order_changed(self, _index: int) -> None:
        """Re-fit the points already on the plot at the newly chosen degree."""
        if self._series is None:
            return
        seconds, values, noun, single_text = self._series
        self._render_series(seconds, values, noun=noun, single_text=single_text)

    def show_fit(self, fit: HeightTimeFit) -> None:
        """Render a full height–time fit into the embedded graph (no dialogs)."""
        self._draw_fit_curve(fit)
        noun = "frames" if self._source == "circle_fit" else "picks"
        self.speed_label.setText(
            f"Fit ({fit.times_s.size} {noun}, {self.fit_order_name(fit)}):  "
            f"{self.fit_summary(fit, noun=noun)}"
        )

    def _draw_fit_curve(self, fit: HeightTimeFit) -> None:
        """Draw the fitted polynomial itself, so a curved fit reads as a curve."""
        if fit.coeffs_km is None or fit.times_s.size < 2:
            self._fit_line.setData(x=[], y=[])
            return
        t_line = np.linspace(float(fit.times_s.min()), float(fit.times_s.max()), 96)
        self._fit_line.setData(x=t_line, y=np.polyval(fit.coeffs_km, t_line) / RSUN_KM)

    @staticmethod
    def _with_error(value: float, error: float, spec: str) -> str:
        """``value ± error``, dropping the error when the fit could not size it."""
        text = format(value, spec)
        if np.isfinite(error):
            # A magnitude never carries the value's explicit sign flag.
            text += f" ± {format(abs(error), spec.replace('+', ''))}"
        return text

    @staticmethod
    def fit_order_name(fit: HeightTimeFit) -> str:
        return _FIT_ORDER_NAMES.get(fit.order, f"order-{fit.order}")

    def fit_summary(self, fit: HeightTimeFit, *, noun: str = "picks") -> str:
        """One-line kinematics for ``fit``: speeds and accelerations with 1σ errors.

        A straight line has one speed; the curved fits change speed along the
        track, so those report it at the first and last sample rather than
        implying a single number covers the whole eruption.
        """
        parts: list[str] = []
        if fit.order == 1:
            parts.append("v = " + self._with_error(fit.speed_km_s, fit.speed_err_km_s, ",.0f") + " km/s")
        else:
            parts.append(
                "v₀ = " + self._with_error(fit.speed_km_s, fit.speed_err_km_s, ",.0f") + " km/s"
            )
            parts.append(
                "v_end = "
                + self._with_error(fit.speed_final_km_s, fit.speed_final_err_km_s, ",.0f")
                + " km/s"
            )
        if np.isfinite(fit.acceleration_km_s2):
            label = "a₀" if fit.order >= 3 else "a"
            parts.append(
                f"{label} = "
                + self._with_error(
                    fit.acceleration_km_s2 * 1000.0, fit.acceleration_err_km_s2 * 1000.0, "+,.1f"
                )
                + " m/s²"
            )
            if fit.order >= 3 and np.isfinite(fit.acceleration_final_km_s2):
                parts.append(
                    "a_end = "
                    + self._with_error(
                        fit.acceleration_final_km_s2 * 1000.0,
                        fit.acceleration_final_err_km_s2 * 1000.0,
                        "+,.1f",
                    )
                    + " m/s²"
                )
        else:
            parts.append(f"a: needs ≥3 {noun}")
        if fit.order >= 3 and np.isfinite(fit.jerk_km_s3):
            parts.append(
                "jerk = "
                + self._with_error(fit.jerk_km_s3 * 1000.0, fit.jerk_err_km_s3 * 1000.0, "+,.3f")
                + " m/s³"
            )
        return "  ·  ".join(parts)

    def export_csv(self) -> None:
        """Save the active tracking table to CSV."""
        if not self._entries:
            return
        from PySide6.QtWidgets import QFileDialog

        circle = self._source == "circle_fit"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CME Circle Fit CSV" if circle else "Export CME Tracking CSV",
            "cme_circle_fit.csv" if circle else "cme_tracking.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        import csv

        t0 = self._entries[0][0]
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.csv_header())
            for entry in self._entries:
                writer.writerow(self.csv_row(entry, t0))

    def csv_header(self) -> list[str]:
        """Column names for the active source (split out so tests can read them)."""
        if self._source == "circle_fit":
            return [
                "time_utc",
                "t_seconds",
                "radius_rsun",
                "radius_arcsec",
                "center_x_arcsec",
                "center_y_arcsec",
                "leading_edge_rsun",
                "center_pa_deg",
                "rms_arcsec",
                "n_points",
            ]
        return ["time_utc", "t_seconds", "height_rsun", "position_angle_deg"]

    def csv_row(self, entry: Sequence[Any], t0: datetime) -> list[Any]:
        """One CSV row for ``entry``, relative to the first row's time ``t0``."""
        when = entry[0]
        seconds = f"{(when - t0).total_seconds():.1f}"
        if self._source == "circle_fit":
            return [
                when.isoformat(),
                seconds,
                f"{float(entry[1]):.4f}",
                f"{float(entry[4]):.2f}",
                f"{float(entry[2]):.2f}",
                f"{float(entry[3]):.2f}",
                f"{float(entry[5]):.4f}",
                f"{float(entry[8]):.2f}",
                f"{float(entry[6]):.3f}",
                int(entry[7]),
            ]
        pa = float(entry[4]) if len(entry) > 4 else float("nan")
        return [when.isoformat(), seconds, f"{float(entry[1]):.4f}", f"{pa:.2f}"]


class CircleFitEntry(NamedTuple):
    """One committed circle fit, keyed by frame index in ``controller.circles``.

    Fields 0 and 1 deliberately mirror the height-time pick tuple (time first,
    then the quantity that is plotted and fitted) so the tracking panel's shared
    sort/plot code works on either store. It is a ``NamedTuple`` rather than a
    dataclass so ``solar_session`` can serialise it positionally, exactly like a
    pick, without importing anything from the UI layer.
    """

    when: datetime | None
    radius_rsun: float  # the height that drives the kinematic fit
    center_x_arc: float
    center_y_arc: float
    radius_arcsec: float
    leading_edge_rsun: float  # hypot(cx, cy) + R — reported, never fitted
    rms_arcsec: float
    n_points: int
    center_pa_deg: float


class MeasurementController(QObject):
    """Click state machine behind the canvas measurement tools.

    One controller per window; the active mode decides what each left click
    does. Right click (or Esc) cancels the in-progress pick without leaving the
    mode. Height–time picks and circle fits are both keyed per frame index, so
    re-measuring a frame replaces its entry.
    """

    MODES = ("ruler", "profile", "height_time", "circle_fit")

    def __init__(self, window: Any):
        super().__init__(window)
        self.window = window
        self.mode: str | None = None
        self._pending: tuple[float, float] | None = None  # first pick (arcsec)
        # Height-time picks: frame_index -> (time, height_rsun, x_arc, y_arc, pa_deg)
        self.picks: dict[int, tuple[datetime, float, float, float, float]] = {}
        # Circle fits: frame_index -> CircleFitEntry (committed), plus the
        # in-progress clicks per frame so stepping back restores what you drew.
        self.circles: dict[int, CircleFitEntry] = {}
        self._circle_points: dict[int, list[tuple[float, float]]] = {}
        self._locked_center: tuple[float, float] | None = None
        # Which store the shared tracking panel is currently showing.
        self._panel_source = "height_time"
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
        # The tracking panel follows the last tracking tool picked up; reaching
        # for the ruler leaves the table showing whatever it showed before.
        if mode in ("height_time", "circle_fit"):
            self._panel_source = mode
            panel = getattr(self.window, "tracking_panel", None)
            if panel is not None:
                panel.set_source(mode)
            self._refresh_tracking_panel()
            self._sync_ht_buttons()
        if mode is None:
            self._refresh_overlay()

    def cancel(self) -> None:
        """Drop the in-progress pick (Esc / right click)."""
        self._pending = None
        if self.mode == "circle_fit":
            # Only the arc being drawn goes; a committed circle survives, just
            # as Esc never removes a recorded height-time pick.
            self._circle_points.pop(self._frame_index(), None)
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
        elif self.mode == "circle_fit":
            self._click_circle(x_arc, y_arc)

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
        """Fit the collected picks; the result renders inside the tracking panel."""
        if len(self.picks) < 2:
            self._status("Height–time needs picks on at least two frames.")
            return
        entries = sorted(self.picks.values(), key=lambda item: item[0])
        times = [entry[0] for entry in entries]
        if not self._has_time_baseline(times):
            self._status("Every pick shares one observation time — there is no speed to fit.")
            return
        heights_km = [entry[1] * RSUN_KM for entry in entries]
        fit = self._fit_kinematics(times, heights_km, noun="picks")
        if fit is None:
            return
        text = (
            f"CME height–time ({len(entries)} picks, {self._fit_order_name(fit)} fit): "
            f"plane-of-sky speed {self._fit_text(fit, noun='picks')}"
        )
        self._status(text)
        self._append_analysis(text)

    def clear_height_time(self) -> None:
        self.picks.clear()
        self._refresh_overlay()
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        self._status("Height–time picks cleared.")

    # ------------------------------------------------------------ circle fit
    def _min_points(self) -> int:
        """A free circle needs three points; a locked centre needs only one."""
        return 1 if self._locked_center is not None else 3

    def _try_fit(self, idx: int, *, report: bool = False) -> CircleFit | None:
        """Fit the arc drawn on frame ``idx``; ``None`` when it is not fittable.

        Every fit goes through here, so a degenerate click set is reported in the
        status bar instead of raising out of a Qt click handler.
        """
        points = self._circle_points.get(idx) or []
        if len(points) < self._min_points():
            return None
        try:
            return fit_circle(points, center=self._locked_center)
        except ValueError as exc:
            if report:
                self._status(f"Circle fit: {exc}")
            return None

    def _click_circle(self, x_arc: float, y_arc: float) -> None:
        """Add a point to this frame's arc and re-fit live."""
        idx = self._frame_index()
        self._circle_points.setdefault(idx, []).append((float(x_arc), float(y_arc)))
        self._refresh_overlay()

        n_points = len(self._circle_points[idx])
        fit = self._try_fit(idx)
        if fit is None:
            self._status(
                f"Circle fit: {n_points} point(s) — click at least "
                f"{self._min_points()} along the front."
            )
            return
        frame = self._current_frame()
        rsun_arcsec = self.window._solar_radius_arcsec(frame) if frame is not None else 960.0
        # A short arc constrains the radius very weakly; say so rather than let
        # the number look as confident as a well-sampled front.
        warn = (
            f"  ·  arc only {fit.arc_span_deg:.0f}° — click wider"
            if fit.arc_span_deg < 30.0
            else ""
        )
        self._status(
            f"Circle fit: {n_points} points  ·  R = {fit.radius / rsun_arcsec:.3f} R☉  ·  "
            f"rms {fit.rms_residual:.1f}″{warn}  —  Commit Circle records this frame."
        )

    def commit_circle(self) -> None:
        """Record this frame's fitted circle, then step to the next frame."""
        win = self.window
        idx = self._frame_index()
        fit = self._try_fit(idx, report=True)
        if fit is None:
            if len(self._circle_points.get(idx) or []) < self._min_points():
                self._status(
                    f"Circle fit needs at least {self._min_points()} point(s) on this frame."
                )
            return
        frame = self._current_frame()
        when = frame_observation_time(frame) if frame is not None else None
        if when is None:
            self._status("This frame has no observation time — cannot use it for radius–time.")
            return

        rsun_arcsec = win._solar_radius_arcsec(frame)
        # Disk centre is (0, 0) in helioprojective arcsec — the same assumption
        # the height-time tool already makes for its position angles. Frames with
        # a large CRVAL offset would need solar_center_from_meta mapped to arcsec.
        offset_arcsec = float(np.hypot(fit.center_x, fit.center_y))
        pa_deg = ruler_measurement((0.0, 0.0), (fit.center_x, fit.center_y)).position_angle_deg
        self.circles[idx] = CircleFitEntry(
            when=when,
            radius_rsun=fit.radius / rsun_arcsec,
            center_x_arc=fit.center_x,
            center_y_arc=fit.center_y,
            radius_arcsec=fit.radius,
            leading_edge_rsun=(offset_arcsec + fit.radius) / rsun_arcsec,
            rms_arcsec=fit.rms_residual,
            n_points=fit.n_points,
            center_pa_deg=pa_deg,
        )
        if self._lock_center_requested() and self._locked_center is None:
            self._locked_center = (fit.center_x, fit.center_y)
        self._refresh_overlay()
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        entry = self.circles[idx]
        self._status(
            f"Circle fit: frame {idx + 1} at {when:%H:%M:%S} → R = {entry.radius_rsun:.3f} R☉ "
            f"({len(self.circles)} frame(s))."
        )

        # Continuous tracking: commit, and the timeline moves itself on.
        panel = getattr(win, "tracking_panel", None)
        if (
            panel is not None
            and panel.auto_advance_check.isChecked()
            and idx < len(getattr(win, "_map_frames", [])) - 1
        ):
            win.frame_slider.setValue(idx + 1)

    def set_lock_center(self, on: bool) -> None:
        """Freeze the circle centre at the value on screen, or free it again."""
        if not on:
            self._locked_center = None
            self._status("Circle centre unlocked — full three-parameter fit per frame.")
            self._refresh_overlay()
            return
        idx = self._frame_index()
        fit = self._try_fit(idx)  # still a free fit: nothing is locked yet
        committed = self.circles.get(idx)
        if fit is not None:
            self._locked_center = (fit.center_x, fit.center_y)
        elif committed is not None:
            self._locked_center = (committed.center_x_arc, committed.center_y_arc)
        else:
            self._locked_center = None
        if self._locked_center is None:
            self._status("Circle centre will lock to the next fit you commit.")
        else:
            cx_arc, cy_arc = self._locked_center
            self._status(
                f"Circle centre locked at ({cx_arc:+.1f}″, {cy_arc:+.1f}″) — "
                "later frames fit the radius only."
            )
        self._refresh_overlay()

    def finish_circle_fit(self) -> None:
        """Fit radius(t) over the committed circles; renders in the tracking panel."""
        entries = sorted(
            (entry for entry in self.circles.values() if entry[0] is not None),
            key=lambda item: item[0],
        )
        if len(entries) < 2:
            self._status("Radius–time needs committed circles on at least two frames.")
            return
        times = [entry[0] for entry in entries]
        if not self._has_time_baseline(times):
            self._status("Every circle shares one observation time — there is no speed to fit.")
            return
        heights_km = [float(entry[1]) * RSUN_KM for entry in entries]
        fit = self._fit_kinematics(times, heights_km, noun="frames")
        if fit is None:
            return
        text = (
            f"CME circle fit ({len(entries)} frames, {self._fit_order_name(fit)} fit): "
            f"plane-of-sky radial expansion speed {self._fit_text(fit, noun='frames')}"
        )
        self._status(text)
        self._append_analysis(text)

    def clear_circle_fits(self) -> None:
        self.circles.clear()
        self._circle_points.clear()
        self._locked_center = None
        self._refresh_overlay()
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        self._status("Circle fits cleared.")

    def restore_circle_fits(
        self,
        circles: Mapping[int, Any],
        points: Mapping[int, Any] | None = None,
        locked_center: Sequence[float] | None = None,
    ) -> None:
        """Replace the circle fits with a saved set (session open)."""
        self.circles = {
            int(idx): CircleFitEntry(*tuple(entry))
            for idx, entry in dict(circles or {}).items()
        }
        self._circle_points = {
            int(idx): [(float(p[0]), float(p[1])) for p in list(pts or [])]
            for idx, pts in dict(points or {}).items()
        }
        self._locked_center = (
            (float(locked_center[0]), float(locked_center[1]))
            if locked_center is not None and len(locked_center) >= 2
            else None
        )
        self._refresh_tracking_panel()
        self._sync_ht_buttons()
        self._refresh_overlay()

    # --------------------------------------------------- active-source actions
    def finish_active_fit(self) -> None:
        """Fit whichever tracking store the panel is currently showing."""
        if self._panel_source == "circle_fit":
            self.finish_circle_fit()
        else:
            self.finish_height_time()

    def clear_active(self) -> None:
        """Clear whichever tracking store the panel is currently showing."""
        if self._panel_source == "circle_fit":
            self.clear_circle_fits()
        else:
            self.clear_height_time()

    def fit_order(self) -> int:
        """The polynomial degree chosen in the tracking panel (1 when there is none)."""
        panel = getattr(self.window, "tracking_panel", None)
        return panel.fit_order() if panel is not None else 1

    def _fit_kinematics(self, times, heights_km, *, noun: str) -> HeightTimeFit | None:
        """Fit at the selected order and render it; None (with a reason) on failure."""
        try:
            fit = fit_height_time(times, heights_km, order=self.fit_order())
        except ValueError as exc:
            self._status(f"Fit: {exc}")
            return None
        panel = getattr(self.window, "tracking_panel", None)
        if panel is not None:
            panel.show_fit(fit)
        return fit

    def _fit_text(self, fit: HeightTimeFit, *, noun: str) -> str:
        panel = getattr(self.window, "tracking_panel", None)
        if panel is not None:
            return panel.fit_summary(fit, noun=noun)
        return f"v = {fit.speed_km_s:,.0f} km/s"

    @staticmethod
    def _fit_order_name(fit: HeightTimeFit) -> str:
        return TrackingPanel.fit_order_name(fit)

    def _lock_center_requested(self) -> bool:
        panel = getattr(self.window, "tracking_panel", None)
        return bool(panel is not None and panel.lock_center_check.isChecked())

    def clear_all(self) -> None:
        """Reset every measurement: pending picks, tracking table and overlays."""
        self._pending = None
        self.picks.clear()
        self.circles.clear()
        self._circle_points.clear()
        self._locked_center = None
        self._sync_ht_buttons()
        self._refresh_tracking_panel()
        canvas = getattr(self.window, "pyqt_canvas", None)
        if canvas is not None:
            canvas.clear_measurement_overlay()
        self._status("All measurements cleared.")

    def restore_picks(self, picks: Mapping[int, Any]) -> None:
        """Replace the current height-time picks with a saved set (session open).

        Picks are stored exactly as clicked — ``{frame_index: (when, height_rsun,
        x_arc, y_arc, pa_deg)}`` — so the tracking table, overlay and fit all pick
        up where the saved analysis left off.
        """
        self.picks = {int(idx): tuple(entry) for idx, entry in dict(picks or {}).items()}
        self._refresh_tracking_panel()
        self._sync_ht_buttons()
        self._refresh_overlay()

    def _refresh_tracking_panel(self) -> None:
        panel = getattr(self.window, "tracking_panel", None)
        if panel is None:
            return
        if self._panel_source == "circle_fit":
            panel.refresh_circles(self.circles)
        else:
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
    def _frame_index(self) -> int:
        return int(getattr(self.window, "_current_frame_index", 0))

    @staticmethod
    def _has_time_baseline(times: Sequence[datetime]) -> bool:
        """False when every entry shares one timestamp, which no line can fit."""
        return len(times) >= 2 and max(times) > min(times)

    def _current_frame(self) -> Any | None:
        frames = getattr(self.window, "_map_frames", None)
        if not frames:
            return None
        idx = max(0, min(self._frame_index(), len(frames) - 1))
        return frames[idx]

    @staticmethod
    def _circle_curve(cx_arc: float, cy_arc: float, radius_arcsec: float):
        """Sample a closed circle in arcsec for the canvas overlay."""
        theta = np.linspace(0.0, 2.0 * np.pi, 181)
        return cx_arc + radius_arcsec * np.cos(theta), cy_arc + radius_arcsec * np.sin(theta)

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
        if self.mode == "circle_fit":
            idx = self._frame_index()
            points = self._circle_points.get(idx) or []
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            fit = self._try_fit(idx)
            if fit is not None:
                curve_x, curve_y = self._circle_curve(fit.center_x, fit.center_y, fit.radius)
                canvas.set_measurement_overlay(xs, ys, curve_x=curve_x, curve_y=curve_y)
                return
            if points:
                canvas.set_measurement_overlay(xs, ys, connect=False)
                return
            committed = self.circles.get(idx)
            if committed is not None:
                curve_x, curve_y = self._circle_curve(
                    committed.center_x_arc, committed.center_y_arc, committed.radius_arcsec
                )
                canvas.set_measurement_overlay([], [], curve_x=curve_x, curve_y=curve_y)
                return
            canvas.clear_measurement_overlay()
            return
        if self.mode == "height_time" and self.picks:
            idx = self._frame_index()
            pick = self.picks.get(idx)
            if pick is not None:
                canvas.set_measurement_overlay([pick[2]], [pick[3]], connect=False)
                return
        if self._pending is not None:
            canvas.set_measurement_overlay([self._pending[0]], [self._pending[1]], connect=False)
            return
        canvas.clear_measurement_overlay()

    def _sync_ht_buttons(self) -> None:
        """Gate Fit/Clear on the store the panel is showing, not on both."""
        win = self.window
        count = len(self.circles) if self._panel_source == "circle_fit" else len(self.picks)
        if hasattr(win, "ht_fit_btn"):
            win.ht_fit_btn.setEnabled(count >= 2)
        if hasattr(win, "ht_clear_btn"):
            win.ht_clear_btn.setEnabled(bool(count))

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
        # Newest result must be the visible one, not buried below the fold.
        scrollbar = panel.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
