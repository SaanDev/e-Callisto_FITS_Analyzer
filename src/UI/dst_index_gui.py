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

from src.Backend.dst_index import load_dst_range
from src.UI.mpl_style import apply_origin_style, style_axes
from src.UI.theme_manager import AppTheme


def _get_theme():
    app = QApplication.instance()
    if not app:
        return None
    return app.property("theme_manager")


def _build_hour_combo() -> QComboBox:
    combo = QComboBox()
    for hour in range(24):
        combo.addItem(f"{hour:02d}:00", hour)
    return combo


def _get_dt(date_edit: QDateEdit, hour_cb: QComboBox) -> datetime:
    date = date_edit.date()
    return datetime(date.year(), date.month(), date.day(), int(hour_cb.currentData()), 0, 0)


def _set_dt(date_edit: QDateEdit, hour_cb: QComboBox, dt: datetime) -> None:
    date_edit.setDate(QDate(dt.year, dt.month, dt.day))
    idx = hour_cb.findData(int(dt.hour))
    if idx >= 0:
        hour_cb.setCurrentIndex(idx)


class DataWorker(QObject):
    progress = Signal(object, object)
    finished = Signal(object, object, object)
    failed = Signal(str)

    def __init__(self, start_dt: datetime, end_dt: datetime):
        super().__init__()
        self.start_dt = start_dt
        self.end_dt = end_dt

    @Slot()
    def run(self):
        try:
            times, values, sources = load_dst_range(
                self.start_dt,
                self.end_dt,
                progress_cb=lambda value, text: self.progress.emit(value, text),
            )
            self.finished.emit(times, values, sources)
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
        self._values: Optional[np.ndarray] = None
        self._sources: tuple[str, ...] = ()
        self.apply_theme()

    def apply_theme(self):
        theme = self.theme or _get_theme()
        if theme:
            theme.apply_mpl(self.fig, self.ax)
        self.draw_idle()

    def clear_plot(self):
        self.ax.clear()
        self._times = None
        self._time_nums = None
        self._values = None
        self._sources = ()
        style_axes(self.ax)
        self.apply_theme()

    def plot_dst(
        self,
        times: np.ndarray,
        values: np.ndarray,
        start_dt: datetime,
        end_dt: datetime,
        sources: tuple[str, ...],
    ) -> None:
        self.ax.clear()
        style_axes(self.ax)

        self._times = np.array(times, dtype=object)
        self._time_nums = np.asarray(mdates.date2num(list(self._times)), dtype=float)
        self._values = np.array(values, dtype=float)
        self._sources = tuple(sources or ())

        line_color = "#1f77b4"
        self.ax.plot(self._times, self._values, color=line_color, linewidth=1.6, marker="o", markersize=2.6)
        self.ax.fill_between(self._times, self._values, 0.0, color=line_color, alpha=0.10)

        for level, label in ((0, "Quiet"), (-50, "-50 nT"), (-100, "-100 nT"), (-200, "-200 nT")):
            self.ax.axhline(level, color="0.5", linestyle="--", linewidth=0.9, alpha=0.6)
            if len(self._times) > 0:
                self.ax.text(self._times[0], level, f"  {label}", va="bottom", fontsize=8, alpha=0.85)

        self.ax.set_ylabel("Dst (nT)")
        self.ax.set_xlabel("Time (UTC)")
        source_text = ", ".join(self._sources) if self._sources else "Kyoto"
        self.ax.set_title(
            f"Kyoto Dst Index ({start_dt:%Y-%m-%d %H:%M} to {end_dt:%Y-%m-%d %H:%M} UTC)\n"
            f"Source: {source_text}"
        )

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Dst Index")
        self.resize(1220, 760)

        self.theme = _get_theme()
        if self.theme:
            self.theme.themeChanged.connect(self._on_theme_changed)

        self.current_start_dt: Optional[datetime] = None
        self.current_end_dt: Optional[datetime] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)

        self.top_panel = QWidget(objectName="top_panel")
        top_layout = QHBoxLayout(self.top_panel)
        top_layout.setContentsMargins(12, 12, 12, 12)
        top_layout.setSpacing(16)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(8)
        title_lbl = QLabel("Choose a UTC time range to fetch and plot Kyoto Dst data")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        controls_col.addWidget(title_lbl)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("Date"), 0, 1)
        grid.addWidget(QLabel("Hour"), 0, 2)

        start_hdr = QLabel("Start:")
        start_hdr.setStyleSheet("font-weight: 600;")
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.start_hour = _build_hour_combo()

        end_hdr = QLabel("End:")
        end_hdr.setStyleSheet("font-weight: 600;")
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_hour = _build_hour_combo()

        default_end = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
        default_start = default_end - timedelta(days=7)
        _set_dt(self.start_date, self.start_hour, default_start)
        _set_dt(self.end_date, self.end_hour, default_end)

        grid.addWidget(start_hdr, 1, 0)
        grid.addWidget(self.start_date, 1, 1)
        grid.addWidget(self.start_hour, 1, 2)
        grid.addWidget(end_hdr, 2, 0)
        grid.addWidget(self.end_date, 2, 1)
        grid.addWidget(self.end_hour, 2, 2)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_cb = QComboBox()
        self.preset_cb.addItems(
            [
                "Last 24 hours",
                "Last 7 days",
                "Current month",
                "Previous month",
            ]
        )
        apply_preset_btn = QPushButton("Apply")
        apply_preset_btn.clicked.connect(self.apply_preset)
        preset_row.addWidget(self.preset_cb, 1)
        preset_row.addWidget(apply_preset_btn)

        btn_row = QHBoxLayout()
        self.plot_btn = QPushButton("Plot Dst Data")
        self.plot_btn.setFixedHeight(32)
        self.plot_btn.clicked.connect(self.on_plot_clicked)
        self.save_plot_btn = QPushButton("Save Plot")
        self.save_plot_btn.setFixedHeight(32)
        self.save_plot_btn.clicked.connect(self.on_save_plot)
        self.save_data_btn = QPushButton("Save Data")
        self.save_data_btn.setFixedHeight(32)
        self.save_data_btn.clicked.connect(self.on_save_data)
        self.reset_btn = QPushButton("Reset")
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

        info_group = QGroupBox("Dst Summary")
        info_form = QFormLayout()
        info_form.setLabelAlignment(Qt.AlignLeft)
        self.info_points = QLabel("—")
        self.info_sources = QLabel("—")
        self.info_min = QLabel("—")
        self.info_max = QLabel("—")
        self.info_mean = QLabel("—")
        info_form.addRow("Samples:", self.info_points)
        info_form.addRow("Sources:", self.info_sources)
        info_form.addRow("Minimum Dst:", self.info_min)
        info_form.addRow("Maximum Dst:", self.info_max)
        info_form.addRow("Mean Dst:", self.info_mean)
        info_group.setLayout(info_form)

        top_layout.addLayout(controls_col, 3)
        top_layout.addWidget(info_group, 2)
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

        self.thread: Optional[QThread] = None
        self.worker: Optional[DataWorker] = None

        self._apply_theme_to_panels()

    def _apply_theme_to_panels(self):
        if self.theme and hasattr(self.theme, "view_mode") and self.theme.view_mode() == "modern":
            self.top_panel.setAutoFillBackground(False)
            return

        pal = self.top_panel.palette()
        app_pal = QApplication.instance().palette()
        pal.setColor(QPalette.Window, app_pal.color(QPalette.AlternateBase))
        self.top_panel.setAutoFillBackground(True)
        self.top_panel.setPalette(pal)

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
        now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
        preset = self.preset_cb.currentText()
        if preset == "Last 24 hours":
            start_dt = now - timedelta(hours=24)
            end_dt = now
        elif preset == "Last 7 days":
            start_dt = now - timedelta(days=7)
            end_dt = now
        elif preset == "Current month":
            start_dt = now.replace(day=1, hour=0)
            end_dt = now
        else:
            month_anchor = now.replace(day=1, hour=0)
            end_dt = month_anchor - timedelta(hours=1)
            start_dt = end_dt.replace(day=1, hour=0)

        _set_dt(self.start_date, self.start_hour, start_dt)
        _set_dt(self.end_date, self.end_hour, end_dt)
        self.sb.showMessage(f"Preset applied: {preset}")

    def _set_buttons_enabled(self, enabled: bool):
        for button in (self.plot_btn, self.save_plot_btn, self.save_data_btn, self.reset_btn):
            button.setEnabled(enabled)

    def _clear_info(self):
        self.info_points.setText("—")
        self.info_sources.setText("—")
        self.info_min.setText("—")
        self.info_max.setText("—")
        self.info_mean.setText("—")

    def _default_cursor_text(self) -> str:
        return "UTC = —   |   Dst = —"

    def _set_cursor_default(self):
        self.cursor_label.setText(self._default_cursor_text())

    def _format_cursor_text(self, x: float, inside: bool) -> str:
        if not inside or not np.isfinite(x):
            return self._default_cursor_text()

        time_nums = getattr(self.canvas, "_time_nums", None)
        values = getattr(self.canvas, "_values", None)
        if time_nums is None or values is None or len(time_nums) == 0 or len(values) == 0:
            return self._default_cursor_text()

        x_min = float(np.nanmin(time_nums))
        x_max = float(np.nanmax(time_nums))
        if x < x_min or x > x_max:
            return self._default_cursor_text()

        cursor_dt = mdates.num2date(float(x)).replace(tzinfo=None)
        dst_value = float(np.interp(float(x), time_nums, values))
        return f"UTC = {cursor_dt:%Y-%m-%d %H:%M:%S}   |   Dst = {dst_value:.1f} nT"

    def on_plot_mouse_motion(self, event):
        inside = event.inaxes == self.canvas.ax and event.xdata is not None
        x = float(event.xdata) if inside else 0.0
        self.cursor_label.setText(self._format_cursor_text(x, inside))

    def update_summary(self, times: np.ndarray, values: np.ndarray, sources: tuple[str, ...]):
        if len(times) == 0:
            self._clear_info()
            return
        idx_min = int(np.nanargmin(values))
        idx_max = int(np.nanargmax(values))
        self.info_points.setText(str(len(times)))
        self.info_sources.setText(", ".join(sources) if sources else "—")
        self.info_min.setText(f"{values[idx_min]:.0f} nT at {times[idx_min]:%Y-%m-%d %H:%M}")
        self.info_max.setText(f"{values[idx_max]:.0f} nT at {times[idx_max]:%Y-%m-%d %H:%M}")
        self.info_mean.setText(f"{float(np.nanmean(values)):.1f} nT")

    def set_time_window(self, start_dt: datetime, end_dt: datetime, auto_plot: bool = True) -> bool:
        try:
            if end_dt < start_dt:
                self.sb.showMessage("Sync skipped: End time must be after start time.")
                return False

            _set_dt(self.start_date, self.start_hour, start_dt)
            _set_dt(self.end_date, self.end_hour, end_dt)
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
            start_dt = _get_dt(self.start_date, self.start_hour)
            end_dt = _get_dt(self.end_date, self.end_hour)
            if end_dt < start_dt:
                raise ValueError("End time must be after start time.")

            self.current_start_dt = start_dt
            self.current_end_dt = end_dt
            self._set_buttons_enabled(False)
            self.start_progress("Preparing Kyoto Dst request...", indeterminate=True)

            self.thread = QThread()
            self.worker = DataWorker(start_dt, end_dt)
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

    @Slot(object, object, object)
    def _on_worker_finished(self, times, values, sources):
        if len(times) == 0:
            self.canvas.clear_plot()
            self._clear_info()
            self._set_cursor_default()
            self.finish_progress("No Dst samples in the selected range.")
            QMessageBox.information(self, "No Data", "No Dst samples were found in the selected UTC range.")
            return

        self.progress_report(98, "Rendering Dst plot...")
        self.canvas.plot_dst(times, values, self.current_start_dt, self.current_end_dt, tuple(sources))
        self.update_summary(times, values, tuple(sources))
        self._set_cursor_default()
        self.finish_progress(f"Plotted {len(times)} hourly Dst samples.")

    @Slot(str)
    def _on_worker_failed(self, tb_str: str):
        QMessageBox.critical(self, "Dst Download Error", tb_str)
        self.progress.setVisible(False)
        self.sb.showMessage("Error.")

    def on_save_plot(self):
        if self.canvas._times is None or self.canvas._values is None or len(self.canvas._times) == 0:
            QMessageBox.information(self, "Nothing to Save", "Please plot some Dst data first.")
            return
        start = self.current_start_dt or self.canvas._times[0]
        end = self.current_end_dt or self.canvas._times[-1]
        default_name = f"DST_{start:%Y%m%d_%H00}-{end:%Y%m%d_%H00}.png"
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
        times = self.canvas._times
        values = self.canvas._values
        if times is None or values is None or len(times) == 0:
            QMessageBox.information(self, "Nothing to Save", "Please plot some Dst data first.")
            return

        start = self.current_start_dt or times[0]
        end = self.current_end_dt or times[-1]
        default_name = f"DST_{start:%Y%m%d_%H00}-{end:%Y%m%d_%H00}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Save Data as CSV", default_name, "CSV File (*.csv)")
        if not path:
            return

        try:
            with open(path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["time_utc", "dst_nt"])
                for timestamp, value in zip(times, values):
                    writer.writerow([timestamp.strftime("%Y-%m-%d %H:%M:%S"), f"{float(value):.0f}"])
            self.sb.showMessage(f"Data saved: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def on_reset(self):
        self.canvas.clear_plot()
        self._clear_info()
        self._set_cursor_default()
        self.sb.showMessage("Plot cleared.")


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
