"""
e-CALLISTO FITS Analyzer
Version 2.3.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import os
import sys
import traceback
from typing import Optional

BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_PATH not in sys.path:
    sys.path.insert(0, BASE_PATH)

import matplotlib.dates as mdates
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector
from PySide6.QtCore import QDate, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.Backend.sep_proton import SepProtonRangeData, load_sep_proton_range
from src.UI.mpl_style import apply_origin_style, style_axes
from src.UI.theme_manager import AppTheme

_LOW_CHANNEL_COLOR = "#1F77B4"
_HIGH_CHANNEL_COLOR = "#D62728"


def _get_theme():
    app = QApplication.instance()
    if not app:
        return None
    return app.property("theme_manager")


def _build_time_combo(minute_step: int = 1) -> tuple[QComboBox, QComboBox]:
    hour_cb = QComboBox()
    for hour in range(24):
        hour_cb.addItem(f"{hour:02d}", hour)

    minute_cb = QComboBox()
    for minute in range(0, 60, minute_step):
        minute_cb.addItem(f"{minute:02d}", minute)
    return hour_cb, minute_cb


def _get_dt(date_edit: QDateEdit, hour_cb: QComboBox, minute_cb: QComboBox) -> datetime:
    qdate = date_edit.date()
    return datetime(
        qdate.year(),
        qdate.month(),
        qdate.day(),
        int(hour_cb.currentData()),
        int(minute_cb.currentData()),
        0,
    )


def _set_dt(date_edit: QDateEdit, hour_cb: QComboBox, minute_cb: QComboBox, dt: datetime) -> None:
    date_edit.setDate(QDate(dt.year, dt.month, dt.day))

    hour_index = hour_cb.findData(int(dt.hour))
    if hour_index >= 0:
        hour_cb.setCurrentIndex(hour_index)

    minute_index = minute_cb.findData(int(dt.minute))
    if minute_index >= 0:
        minute_cb.setCurrentIndex(minute_index)


def fmt_timedelta_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:d}m {secs:02d}s"


class DataWorker(QObject):
    progress = Signal(object, object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, start_dt: datetime, end_dt: datetime, spacecraft):
        super().__init__()
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.spacecraft = spacecraft

    @Slot()
    def run(self):
        try:
            result = load_sep_proton_range(
                self.start_dt,
                self.end_dt,
                spacecraft=self.spacecraft,
                progress_cb=lambda value, text: self.progress.emit(value, text),
            )
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class PlotCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None, theme=None):
        self.fig = Figure(figsize=(8, 5), tight_layout=True)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

        self.theme = theme
        self._times: Optional[np.ndarray] = None
        self._time_nums: Optional[np.ndarray] = None
        self._low_flux: Optional[np.ndarray] = None
        self._high_flux: Optional[np.ndarray] = None
        self._low_label: str = "P(low)"
        self._high_label: str = "P(high)"
        self._units: str = ""
        self._spacecraft: str = ""
        self._source_files: tuple[str, ...] = ()

        self.on_event_info = None
        self.selector: Optional[RectangleSelector] = None
        self.enable_selector(True)
        self.apply_theme()

    def apply_theme(self):
        theme = self.theme or _get_theme()
        if theme:
            theme.apply_mpl(self.fig, self.ax)

        palette = QApplication.instance().palette() if QApplication.instance() else None
        if palette and self.selector:
            edge_color = palette.color(QPalette.Mid).name()
            artist = getattr(self.selector, "_selection_artist", None)
            if artist is not None:
                try:
                    artist.set_edgecolor(edge_color)
                except Exception:
                    pass

        self.draw_idle()

    def enable_selector(self, enabled: bool):
        if self.selector is None:
            self.selector = RectangleSelector(
                self.ax,
                onselect=self._on_select,
                useblit=True,
                button=[1],
                minspanx=0.00001,
                minspany=0,
                spancoords="data",
                interactive=True,
                props=dict(edgecolor="0.3", linestyle="--", linewidth=1, fill=False),
                grab_range=5,
                drag_from_anywhere=True,
            )
        self.selector.set_active(enabled)

    def reset_selector(self):
        if self.selector:
            try:
                self.selector.set_visible(False)
            except Exception:
                artist = getattr(self.selector, "_selection_artist", None)
                if artist is not None:
                    artist.set_visible(False)
            self.draw_idle()

    def clear_plot(self):
        self.ax.clear()
        self._times = None
        self._time_nums = None
        self._low_flux = None
        self._high_flux = None
        self._low_label = "P(low)"
        self._high_label = "P(high)"
        self._units = ""
        self._spacecraft = ""
        self._source_files = ()
        style_axes(self.ax)
        self.apply_theme()

    def plot_sep(self, result: SepProtonRangeData, start_dt: datetime, end_dt: datetime):
        self.ax.clear()
        style_axes(self.ax)

        self._times = np.asarray(result.times, dtype=object)
        self._time_nums = np.asarray(mdates.date2num(list(self._times)), dtype=float)
        self._low_flux = np.asarray(result.low_flux, dtype=float)
        self._high_flux = np.asarray(result.high_flux, dtype=float)
        self._low_label = result.low_channel_label
        self._high_label = result.high_channel_label
        self._units = result.units or ""
        self._spacecraft = result.spacecraft
        self._source_files = tuple(result.source_files)

        low_plot = np.where(self._low_flux > 0.0, self._low_flux, np.nan)
        high_plot = np.where(self._high_flux > 0.0, self._high_flux, np.nan)

        self.ax.plot(self._times, low_plot, color=_LOW_CHANNEL_COLOR, linewidth=1.4, label=self._low_label)
        self.ax.plot(self._times, high_plot, color=_HIGH_CHANNEL_COLOR, linewidth=1.4, label=self._high_label)

        self.ax.set_yscale("log")
        self.ax.set_ylabel(f"Proton Flux ({self._units})" if self._units else "Proton Flux")
        self.ax.set_xlabel("Time (UTC)")
        source_count = len(self._source_files)
        source_suffix = f"{source_count} daily file{'s' if source_count != 1 else ''}"
        self.ax.set_title(
            f"{result.spacecraft} SEP Proton Flux ({start_dt:%Y-%m-%d %H:%M} to {end_dt:%Y-%m-%d %H:%M} UTC)\n"
            f"Channels: {result.low_channel_label} and {result.high_channel_label} | Source: {source_suffix}"
        )
        self.ax.legend(loc="upper right")

        span_hours = max(1.0, (end_dt - start_dt).total_seconds() / 3600.0)
        locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
        if span_hours <= 72:
            formatter = mdates.DateFormatter("%Y-%m-%d\n%H:%M")
        elif span_hours <= 24 * 120:
            formatter = mdates.DateFormatter("%Y-%m-%d")
        else:
            formatter = mdates.DateFormatter("%Y-%m")
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(formatter)

        self.fig.autofmt_xdate()
        self.apply_theme()

    def _on_select(self, eclick, erelease):
        if self._times is None or self._low_flux is None:
            return

        x0, x1 = eclick.xdata, erelease.xdata
        if x0 is None or x1 is None:
            return
        if x1 < x0:
            x0, x1 = x1, x0

        t0 = mdates.num2date(x0).replace(tzinfo=None)
        t1 = mdates.num2date(x1).replace(tzinfo=None)
        mask = (self._times >= t0) & (self._times <= t1)
        if not np.any(mask):
            if self.on_event_info:
                self.on_event_info({"status": "No samples in selected area."})
            return

        sel_times = self._times[mask]
        sel_flux = np.asarray(self._low_flux[mask], dtype=float)
        finite_indices = np.where(np.isfinite(sel_flux))[0]
        if finite_indices.size == 0:
            if self.on_event_info:
                self.on_event_info({"status": "No finite low-channel samples in selection."})
            return

        peak_local_index = int(finite_indices[np.nanargmax(sel_flux[finite_indices])])
        peak_flux = float(sel_flux[peak_local_index])
        peak_time = sel_times[peak_local_index]
        rise_seconds = (peak_time - sel_times[0]).total_seconds()
        decay_seconds = (sel_times[-1] - peak_time).total_seconds()
        event_seconds = (sel_times[-1] - sel_times[0]).total_seconds()

        if self.on_event_info:
            self.on_event_info(
                {
                    "status": "OK",
                    "t0": sel_times[0],
                    "t1": sel_times[-1],
                    "n": len(sel_times),
                    "peak_flux": peak_flux,
                    "peak_time": peak_time,
                    "rise_time": fmt_timedelta_seconds(rise_seconds),
                    "decay_time": fmt_timedelta_seconds(decay_seconds),
                    "event_time": fmt_timedelta_seconds(event_seconds),
                }
            )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("GOES SEP Proton Flux Plotter")
        self.resize(1250, 760)

        self.theme = _get_theme()
        if self.theme:
            self.theme.themeChanged.connect(self._on_theme_changed)

        self.current_start_dt: Optional[datetime] = None
        self.current_end_dt: Optional[datetime] = None
        self.current_result = SepProtonRangeData.empty()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)

        self.top_panel = QWidget(objectName="top_panel")
        top_panel_layout = QHBoxLayout(self.top_panel)
        top_panel_layout.setContentsMargins(12, 12, 12, 12)
        top_panel_layout.setSpacing(16)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(8)
        title_lbl = QLabel("Choose a UTC time range to fetch and plot GOES SEP proton flux")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        controls_col.addWidget(title_lbl)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("Date"), 0, 1)
        grid.addWidget(QLabel("Hour"), 0, 2)
        grid.addWidget(QLabel("Minute"), 0, 3)
        grid.addWidget(QLabel("Spacecraft"), 0, 4)

        start_hdr = QLabel("Start:")
        start_hdr.setStyleSheet("font-weight: 600;")
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.start_hour, self.start_min = _build_time_combo(1)

        end_hdr = QLabel("End:")
        end_hdr.setStyleSheet("font-weight: 600;")
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_hour, self.end_min = _build_time_combo(1)

        self.spacecraft_cb = QComboBox()
        self.spacecraft_cb.addItem("Auto (GOES-19 -> GOES-16)", "auto")
        for goes_num in (19, 18, 17, 16):
            self.spacecraft_cb.addItem(f"GOES-{goes_num}", goes_num)
        self.spacecraft_cb.setCurrentIndex(0)

        default_end = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        default_start = default_end - timedelta(hours=24)
        _set_dt(self.start_date, self.start_hour, self.start_min, default_start)
        _set_dt(self.end_date, self.end_hour, self.end_min, default_end)

        grid.addWidget(start_hdr, 1, 0)
        grid.addWidget(self.start_date, 1, 1)
        grid.addWidget(self.start_hour, 1, 2)
        grid.addWidget(self.start_min, 1, 3)
        grid.addWidget(QLabel("Use:"), 1, 4, alignment=Qt.AlignRight)
        grid.addWidget(self.spacecraft_cb, 1, 5)

        grid.addWidget(end_hdr, 2, 0)
        grid.addWidget(self.end_date, 2, 1)
        grid.addWidget(self.end_hour, 2, 2)
        grid.addWidget(self.end_min, 2, 3)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_cb = QComboBox()
        self.preset_cb.addItems(
            [
                "Last 24 hours",
                "Last 72 hours",
                "Last 7 days",
                "Current UTC day",
            ]
        )
        apply_preset_btn = QPushButton("Apply")
        apply_preset_btn.clicked.connect(self.apply_preset)
        preset_row.addWidget(self.preset_cb, 1)
        preset_row.addWidget(apply_preset_btn)

        btn_row = QHBoxLayout()
        self.plot_btn = QPushButton("Plot SEP Proton Flux")
        self.plot_btn.setFixedHeight(32)
        self.plot_btn.clicked.connect(self.on_plot_clicked)
        self.save_plot_btn = QPushButton("Save Plot")
        self.save_plot_btn.setFixedHeight(32)
        self.save_plot_btn.clicked.connect(self.on_save_plot)
        self.save_data_btn = QPushButton("Save Data")
        self.save_data_btn.setFixedHeight(32)
        self.save_data_btn.clicked.connect(self.on_save_data)
        self.reset_btn = QPushButton("Clear Selection")
        self.reset_btn.setFixedHeight(32)
        self.reset_btn.clicked.connect(self.on_reset)
        btn_row.addWidget(self.plot_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(self.save_plot_btn)
        btn_row.addWidget(self.save_data_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch(1)

        controls_col.addLayout(grid)
        controls_col.addLayout(preset_row)
        controls_col.addLayout(btn_row)

        info_group = QGroupBox("SEP Analysis")
        info_form = QFormLayout()
        info_form.setLabelAlignment(Qt.AlignLeft)

        self.info_spacecraft = QLabel("—")
        self.info_channels = QLabel("—")
        self.info_samples = QLabel("—")
        self.info_source_files = QLabel("—")
        self.info_plot_peak_low = QLabel("—")
        self.info_plot_peak_high = QLabel("—")
        self.info_selection = QLabel("—")
        self.info_peak_flux = QLabel("—")
        self.info_peak_time = QLabel("—")
        self.info_rise_time = QLabel("—")
        self.info_decay_time = QLabel("—")
        self.info_event_time = QLabel("—")

        info_form.addRow("Spacecraft:", self.info_spacecraft)
        info_form.addRow("Channels:", self.info_channels)
        info_form.addRow("Samples:", self.info_samples)
        info_form.addRow("Source files:", self.info_source_files)
        info_form.addRow("Plot peak (low):", self.info_plot_peak_low)
        info_form.addRow("Plot peak (high):", self.info_plot_peak_high)
        info_form.addRow("Selection window:", self.info_selection)
        info_form.addRow("Selection peak (low):", self.info_peak_flux)
        info_form.addRow("Selection peak time:", self.info_peak_time)
        info_form.addRow("Rise time:", self.info_rise_time)
        info_form.addRow("Decay time:", self.info_decay_time)
        info_form.addRow("Selection duration:", self.info_event_time)
        info_group.setLayout(info_form)

        top_panel_layout.addLayout(controls_col, 3)
        top_panel_layout.addWidget(info_group, 2)
        root.addWidget(self.top_panel, 0)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setLineWidth(1)
        root.addWidget(divider)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 12, 12, 12)
        bottom_layout.setSpacing(8)

        self.canvas = PlotCanvas(self, theme=self.theme)
        bottom_layout.addWidget(self.canvas, 1)
        root.addWidget(bottom, 1)

        self.sb = self.statusBar()
        self.sb.showMessage("Ready")
        self.cursor_label = QLabel(self._default_cursor_text())
        self.cursor_label.setStyleSheet("padding-right: 8px;")
        self.sb.addPermanentWidget(self.cursor_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setMinimumWidth(200)
        self.progress.setVisible(False)
        self.sb.addPermanentWidget(self.progress, 1)
        self._cid_motion_status = self.canvas.mpl_connect("motion_notify_event", self.on_plot_mouse_motion)

        self.canvas.on_event_info = self.update_event_info
        self.thread: Optional[QThread] = None
        self.worker: Optional[DataWorker] = None

        self._apply_theme_to_panels()
        self._clear_info()

    def _apply_theme_to_panels(self):
        if self.theme and hasattr(self.theme, "view_mode") and self.theme.view_mode() == "modern":
            self.top_panel.setAutoFillBackground(False)
            return

        palette = self.top_panel.palette()
        app_palette = QApplication.instance().palette()
        palette.setColor(QPalette.Window, app_palette.color(QPalette.AlternateBase))
        self.top_panel.setAutoFillBackground(True)
        self.top_panel.setPalette(palette)

    def _on_theme_changed(self, _dark: bool):
        self._apply_theme_to_panels()
        self.canvas.apply_theme()

    def start_progress(self, text: str = "", indeterminate: bool = True):
        self.sb.showMessage(text)
        self.progress.setVisible(True)
        if indeterminate:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        QApplication.processEvents()

    @Slot(object, object)
    def progress_report(self, value, text):
        if text:
            self.sb.showMessage(text)
        if value is not None:
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, int(value))))

    def finish_progress(self, text: str = "Done."):
        self.progress.setValue(100)
        self.sb.showMessage(text)
        QTimer.singleShot(600, lambda: self.progress.setVisible(False))

    def apply_preset(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        preset = self.preset_cb.currentText()
        if preset == "Last 24 hours":
            start_dt = now - timedelta(hours=24)
            end_dt = now
        elif preset == "Last 72 hours":
            start_dt = now - timedelta(hours=72)
            end_dt = now
        elif preset == "Last 7 days":
            start_dt = now - timedelta(days=7)
            end_dt = now
        else:
            start_dt = now.replace(hour=0, minute=0)
            end_dt = now

        _set_dt(self.start_date, self.start_hour, self.start_min, start_dt)
        _set_dt(self.end_date, self.end_hour, self.end_min, end_dt)
        self.sb.showMessage(f"Preset applied: {preset}")

    def _set_buttons_enabled(self, enabled: bool):
        for button in (self.plot_btn, self.save_plot_btn, self.save_data_btn, self.reset_btn):
            button.setEnabled(enabled)

    def _clear_selection_info(self):
        self.info_selection.setText("—")
        self.info_peak_flux.setText("—")
        self.info_peak_time.setText("—")
        self.info_rise_time.setText("—")
        self.info_decay_time.setText("—")
        self.info_event_time.setText("—")

    def _clear_info(self):
        self.info_spacecraft.setText("—")
        self.info_channels.setText("—")
        self.info_samples.setText("—")
        self.info_source_files.setText("—")
        self.info_plot_peak_low.setText("—")
        self.info_plot_peak_high.setText("—")
        self._clear_selection_info()

    def _default_cursor_text(self) -> str:
        return "UTC = —   |   Low = —   |   High = —"

    def _set_cursor_default(self):
        self.cursor_label.setText(self._default_cursor_text())

    def _format_flux(self, value: float) -> str:
        if not np.isfinite(value):
            return "nan"
        return f"{float(value):.3e}"

    def _format_peak(self, value: float, timestamp: datetime) -> str:
        unit_suffix = f" {self.current_result.units}" if self.current_result.units else ""
        return f"{self._format_flux(value)}{unit_suffix} at {timestamp:%Y-%m-%d %H:%M}"

    def _format_cursor_text(self, x: float, inside: bool) -> str:
        if not inside or not np.isfinite(x):
            return self._default_cursor_text()

        time_nums = getattr(self.canvas, "_time_nums", None)
        if time_nums is None or len(time_nums) == 0:
            return self._default_cursor_text()

        x_min = float(np.nanmin(time_nums))
        x_max = float(np.nanmax(time_nums))
        if x < x_min or x > x_max:
            return self._default_cursor_text()

        idx = int(np.argmin(np.abs(time_nums - float(x))))
        times = self.canvas._times
        low_flux = self.canvas._low_flux
        high_flux = self.canvas._high_flux
        if times is None or low_flux is None or high_flux is None or len(times) == 0:
            return self._default_cursor_text()

        timestamp = times[idx]
        unit_suffix = f" {self.canvas._units}" if self.canvas._units else ""
        return (
            f"UTC = {timestamp:%Y-%m-%d %H:%M:%S}   |   "
            f"{self.canvas._low_label} = {self._format_flux(float(low_flux[idx]))}{unit_suffix}   |   "
            f"{self.canvas._high_label} = {self._format_flux(float(high_flux[idx]))}{unit_suffix}"
        )

    def on_plot_mouse_motion(self, event):
        inside = event.inaxes == self.canvas.ax and event.xdata is not None
        x = float(event.xdata) if inside else 0.0
        self.cursor_label.setText(self._format_cursor_text(x, inside))

    def _update_plot_summary(self, result: SepProtonRangeData):
        if not result.times:
            self._clear_info()
            self.info_spacecraft.setText(result.spacecraft or "—")
            self.info_channels.setText(
                f"{result.low_channel_label} | {result.high_channel_label}"
                if result.low_channel_label or result.high_channel_label
                else "—"
            )
            self.info_source_files.setText(str(len(result.source_files)))
            return

        low_values = np.asarray(result.low_flux, dtype=float)
        high_values = np.asarray(result.high_flux, dtype=float)
        low_finite = np.where(np.isfinite(low_values))[0]
        high_finite = np.where(np.isfinite(high_values))[0]

        self.info_spacecraft.setText(result.spacecraft or "—")
        self.info_channels.setText(f"{result.low_channel_label} | {result.high_channel_label}")
        self.info_samples.setText(str(len(result.times)))
        self.info_source_files.setText(str(len(result.source_files)))

        if low_finite.size > 0:
            idx_low = int(low_finite[np.nanargmax(low_values[low_finite])])
            self.info_plot_peak_low.setText(self._format_peak(float(low_values[idx_low]), result.times[idx_low]))
        else:
            self.info_plot_peak_low.setText("—")

        if high_finite.size > 0:
            idx_high = int(high_finite[np.nanargmax(high_values[high_finite])])
            self.info_plot_peak_high.setText(self._format_peak(float(high_values[idx_high]), result.times[idx_high]))
        else:
            self.info_plot_peak_high.setText("—")

        self._clear_selection_info()

    def update_event_info(self, info: dict):
        if info.get("status") != "OK":
            self._clear_selection_info()
            return

        unit_suffix = f" {self.current_result.units}" if self.current_result.units else ""
        self.info_selection.setText(f"{info['t0']} - {info['t1']}  (n={info['n']})")
        self.info_peak_flux.setText(f"{self._format_flux(float(info['peak_flux']))}{unit_suffix}")
        self.info_peak_time.setText(str(info["peak_time"]))
        self.info_rise_time.setText(info.get("rise_time", "—"))
        self.info_decay_time.setText(info.get("decay_time", "—"))
        self.info_event_time.setText(info.get("event_time", "—"))

    def set_time_window(self, start_dt: datetime, end_dt: datetime, auto_plot: bool = True) -> bool:
        try:
            if end_dt <= start_dt:
                self.sb.showMessage("Sync skipped: End time must be after start time.")
                return False

            _set_dt(self.start_date, self.start_hour, self.start_min, start_dt)
            _set_dt(self.end_date, self.end_hour, self.end_min, end_dt)
            self.current_start_dt = start_dt
            self.current_end_dt = end_dt
            self.sb.showMessage(
                f"Synced time window: {start_dt:%Y-%m-%d %H:%M} - {end_dt:%Y-%m-%d %H:%M} UTC"
            )

            if auto_plot:
                self.on_plot_clicked()
            return True
        except Exception as exc:
            self.sb.showMessage(f"Sync failed: {exc}")
            return False

    def on_plot_clicked(self):
        try:
            start_dt = _get_dt(self.start_date, self.start_hour, self.start_min)
            end_dt = _get_dt(self.end_date, self.end_hour, self.end_min)
            if end_dt <= start_dt:
                raise ValueError("End time must be after start time.")

            spacecraft = self.spacecraft_cb.currentData()
            self.current_start_dt = start_dt
            self.current_end_dt = end_dt

            self._set_buttons_enabled(False)
            self.start_progress("Preparing GOES SEP proton request...", indeterminate=True)

            self.thread = QThread()
            self.worker = DataWorker(start_dt, end_dt, spacecraft)
            self.worker.moveToThread(self.thread)

            self.thread.started.connect(self.worker.run)
            self.worker.progress.connect(self.progress_report)
            self.worker.finished.connect(self._on_worker_finished)
            self.worker.failed.connect(self._on_worker_failed)
            self.worker.finished.connect(self.thread.quit)
            self.worker.failed.connect(self.thread.quit)
            self.thread.finished.connect(self.worker.deleteLater)
            self.thread.finished.connect(lambda: self._set_buttons_enabled(True))

            self.thread.start()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            self.progress.setVisible(False)
            self._set_buttons_enabled(True)
            self.sb.showMessage("Error.")

    @Slot(object)
    def _on_worker_finished(self, result):
        result = result if isinstance(result, SepProtonRangeData) else SepProtonRangeData.empty()
        self.current_result = result

        if not result.times:
            self.canvas.clear_plot()
            self._update_plot_summary(result)
            self._set_cursor_default()
            self.finish_progress("No SEP proton samples in the selected range.")
            QMessageBox.information(self, "No Data", "No SEP proton samples were found in the selected UTC range.")
            return

        self.progress_report(98, "Rendering SEP proton plot...")
        self.canvas.plot_sep(result, self.current_start_dt, self.current_end_dt)
        self.canvas.reset_selector()
        self._update_plot_summary(result)
        self._set_cursor_default()
        self.finish_progress(
            f"Plotted {len(result.times)} SEP proton samples from {result.spacecraft} "
            f"using {len(result.source_files)} daily file(s)."
        )

    @Slot(str)
    def _on_worker_failed(self, tb_str: str):
        lines = [line.strip() for line in str(tb_str).splitlines() if line.strip()]
        detail = lines[-1] if lines else "Could not load GOES SEP proton data."
        QMessageBox.critical(self, "SEP Proton Download Error", tb_str)
        self.progress.setVisible(False)
        self.sb.showMessage(detail)

    def on_save_plot(self):
        if not self.current_result.times:
            QMessageBox.information(self, "Nothing to Save", "Please plot some SEP proton data first.")
            return

        start = self.current_start_dt or self.current_result.times[0]
        end = self.current_end_dt or self.current_result.times[-1]
        spacecraft_text = (self.current_result.spacecraft or "GOES").replace(" ", "_")
        default_name = f"SEP_{spacecraft_text}_{start:%Y%m%d_%H%M}-{end:%Y%m%d_%H%M}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save Plot as PNG", default_name, "PNG Image (*.png)")
        if not path:
            return

        try:
            self.canvas.fig.savefig(path, dpi=150, bbox_inches="tight")
            self.sb.showMessage(f"Plot saved: {path}")
            theme = QApplication.instance().property("theme_manager")
            if theme:
                theme.apply_mpl(self.canvas.fig, self.canvas.ax)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def on_save_data(self):
        result = self.current_result
        if not result.times:
            QMessageBox.information(self, "Nothing to Save", "Please plot some SEP proton data first.")
            return

        start = self.current_start_dt or result.times[0]
        end = self.current_end_dt or result.times[-1]
        spacecraft_text = (result.spacecraft or "GOES").replace(" ", "_")
        default_name = f"SEP_{spacecraft_text}_{start:%Y%m%d_%H%M}-{end:%Y%m%d_%H%M}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Save Data as CSV", default_name, "CSV File (*.csv)")
        if not path:
            return

        try:
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "time_utc",
                        "low_channel_flux",
                        "high_channel_flux",
                        "low_channel_label",
                        "high_channel_label",
                        "units",
                        "spacecraft",
                    ]
                )
                for timestamp, low_flux, high_flux in zip(result.times, result.low_flux, result.high_flux):
                    writer.writerow(
                        [
                            timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            self._format_flux(float(low_flux)),
                            self._format_flux(float(high_flux)),
                            result.low_channel_label,
                            result.high_channel_label,
                            result.units,
                            result.spacecraft,
                        ]
                    )
            self.sb.showMessage(f"Data saved: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def on_reset(self):
        self.canvas.reset_selector()
        self._clear_selection_info()
        self.sb.showMessage("Selection cleared.")


def main():
    apply_origin_style()
    app = QApplication(sys.argv)
    if app.property("theme_manager") is None:
        theme = AppTheme(app)
        app.setProperty("theme_manager", theme)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
