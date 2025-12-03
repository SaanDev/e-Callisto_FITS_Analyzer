"""
e-CALLISTO FITS Analyzer
Version 1.7.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import os
import sys
import csv
import traceback
from datetime import datetime
from typing import Optional, Tuple

import requests
import netCDF4 as nc
import numpy as np
import cftime

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.widgets import RectangleSelector
import matplotlib.dates as mdates

from PySide6.QtCore import Qt, QDate, QTimer, QObject, Signal, Slot, QThread
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QMessageBox, QDateEdit, QComboBox, QFrame, QSizePolicy,
    QGroupBox, QFormLayout, QFileDialog, QProgressBar
)

import tempfile, atexit, shutil
CACHE_DIR = tempfile.mkdtemp(prefix="goes_xrs_cache_")
@atexit.register
def _cleanup_cache_dir():
    shutil.rmtree(CACHE_DIR, ignore_errors=True)

BASE_URL_TMPL = (
    "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/"
    "goes/goes{goes_num}/l2/data/xrsf-l2-avg1m_science"
)
CANDIDATE_VERSIONS = ["v2-2-2", "v2-2-1", "v2-2-0"]

def day_parts(dt: datetime) -> Tuple[int, int, int]:
    return dt.year, dt.month, dt.day

def build_filename(goes_num: int, year: int, month: int, day: int, version: str) -> str:
    return f"sci_xrsf-l2-avg1m_g{goes_num:02d}_d{year:04d}{month:02d}{day:02d}_{version}.nc"

def build_url(goes_num: int, year: int, month: int, day: int, version: str) -> str:
    base = BASE_URL_TMPL.format(goes_num=goes_num)
    return f"{base}/{year:04d}/{month:02d}/{build_filename(goes_num, year, month, day, version)}"

def get_local_path(filename: str) -> str:
    return os.path.join(CACHE_DIR, filename)

def download_file(goes_num: int, year: int, month: int, day: int, progress_cb=None) -> str:
    last_err = None
    if progress_cb:
        progress_cb(None, f"Locating file for GOES-{goes_num}…")

    for ver in CANDIDATE_VERSIONS:
        fname = build_filename(goes_num, year, month, day, ver)
        url = build_url(goes_num, year, month, day, ver)
        local = get_local_path(fname)
        if os.path.exists(local):
            if progress_cb: progress_cb(20, f"Using cached file: {fname}")
            return local
        try:
            if progress_cb: progress_cb(None, f"Downloading {fname}…")
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(local, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk: continue
                    f.write(chunk); downloaded += len(chunk)
                    if total and progress_cb:
                        pct = 5 + int(65 * downloaded / total)
                        progress_cb(pct, f"Downloading… {downloaded//1024} KB")
            if progress_cb: progress_cb(75, "Download complete.")
            return local
        except Exception as e:
            last_err = e

    raise FileNotFoundError(
        f"GOES-{goes_num} XRS file for {year:04d}-{month:02d}-{day:02d} "
        f"is not available on the NOAA server. "
        f"Tried versions: {', '.join(CANDIDATE_VERSIONS)}."
    )


def load_and_slice(local_nc_path: str, start_dt: datetime, end_dt: datetime, progress_cb=None):
    if progress_cb: progress_cb(80, "Reading NetCDF…")
    with nc.Dataset(local_nc_path) as ds:
        time_var = ds.variables["time"]
        times = cftime.num2pydate(time_var[:], time_var.units)
        var_names = list(ds.variables.keys())
        possible_short = [v for v in var_names if "xrsa" in v.lower()]
        possible_long  = [v for v in var_names if "xrsb" in v.lower()]
        if not (possible_short and possible_long):
            raise KeyError("Cannot find XRS variables (xrsa/xrsb) in file.")
        xrsa = ds.variables[possible_short[0]][:]
        xrsb = ds.variables[possible_long[0]][:]
    if progress_cb: progress_cb(88, "Slicing data…")
    times_np = np.array(times, dtype=object)
    mask = (times_np >= start_dt) & (times_np <= end_dt)
    return times_np[mask], xrsa[mask], xrsb[mask]

def classify_goes_flux(peak_flux_wm2: float) -> str:
    if peak_flux_wm2 < 1e-7:
        base, letter = 1e-8, "A"
    elif peak_flux_wm2 < 1e-6:
        base, letter = 1e-7, "B"
    elif peak_flux_wm2 < 1e-5:
        base, letter = 1e-6, "C"
    elif peak_flux_wm2 < 1e-4:
        base, letter = 1e-5, "M"
    else:
        base, letter = 1e-4, "X"
    return f"{letter}{peak_flux_wm2 / base:.1f}"

def fmt_timedelta_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60); h, m = divmod(m, 60)
    return f"{h:d}h {m:02d}m {s:02d}s" if h else f"{m:d}m {s:02d}s"

from PySide6.QtCore import QObject, Signal, Slot, QThread

class DataWorker(QObject):
    progress = Signal(object, object)                 # (value:int|None, text:str|None)
    finished = Signal(object, object, object, str, int)  # (times, xrsa, xrsb, local_path, goes_num)
    failed   = Signal(str)

    def __init__(self, goes_num: int, year: int, month: int, day: int, start_dt: datetime, end_dt: datetime):
        super().__init__()
        self.goes_num = goes_num
        self.year, self.month, self.day = year, month, day
        self.start_dt, self.end_dt = start_dt, end_dt

    @Slot()
    def run(self):
        try:
            local_path = download_file(self.goes_num, self.year, self.month, self.day,
                                       progress_cb=lambda v,t: self.progress.emit(v, t))
            times, xrsa, xrsb = load_and_slice(local_path, self.start_dt, self.end_dt,
                                               progress_cb=lambda v,t: self.progress.emit(v, t))
            self.progress.emit(95, "Preparing plot…")
            self.finished.emit(times, xrsa, xrsb, local_path, self.goes_num)
        except Exception:
            self.failed.emit(traceback.format_exc())

class PlotCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None):
        self.fig = Figure(figsize=(8, 5), tight_layout=True)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

        self._times: Optional[np.ndarray] = None
        self._xrsa: Optional[np.ndarray] = None
        self._xrsb: Optional[np.ndarray] = None

        self.on_flare_info = None

        self.selector: Optional[RectangleSelector] = None
        self.enable_selector(True)

    def enable_selector(self, enabled: bool):
        if self.selector is None:
            self.selector = RectangleSelector(
                self.ax,
                onselect=self._on_select,
                useblit=True,
                button=[1],
                minspanx=0.00001,
                minspany=0,
                spancoords='data',
                interactive=True,
                props=dict(edgecolor='0.3', linestyle='--', linewidth=1, fill=False),
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
                if artist:
                    artist.set_visible(False)
            self.draw_idle()

    def clear_plot(self):
        self.ax.clear()
        self.draw_idle()

    def plot_xrs(self, times, xrsa, xrsb, start_dt: datetime, end_dt: datetime, goes_num: int):
        self.ax.clear()

        self._times = np.array(times, dtype=object)
        self._xrsa  = np.array(xrsa, dtype=float)
        self._xrsb  = np.array(xrsb, dtype=float)

        self.ax.plot(self._times, self._xrsa, label="Short (XRS-A)", linewidth=1)
        self.ax.plot(self._times, self._xrsb, label="Long (XRS-B)",  linewidth=1)

        self.ax.set_yscale("log")
        self.ax.set_ylabel("X-ray Flux (W/m²)")
        self.ax.set_xlabel("Time (UTC)")
        self.ax.set_title(f"GOES-{goes_num} XRS X-ray Flux ({start_dt.isoformat()} — {end_dt.isoformat()})")

        flare_levels = {"A1": 1e-8, "B1": 1e-7, "C1": 1e-6, "M1": 1e-5, "X1": 1e-4}
        if len(self._times) > 0:
            for label, level in flare_levels.items():
                self.ax.axhline(y=level, color="gray", ls="--", lw=0.8)
                self.ax.text(self._times[0], level * 1.15, label, color="gray", fontsize=8, va="bottom")

        self.ax.legend()
        self.fig.autofmt_xdate()
        self.draw_idle()

    def _on_select(self, eclick, erelease):
        if self._times is None or self._xrsb is None:
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
            if self.on_flare_info:
                self.on_flare_info({
                    "status": "No samples in selected area.",
                    "t0": None, "t1": None, "n": 0,
                    "peak_flux": None, "peak_time": None,
                    "rise_time": None, "decay_time": None, "flare_time": None,
                    "class_label": None
                })
            return

        sel_times = self._times[mask]
        sel_xrsb  = self._xrsb[mask]
        idx_peak  = int(np.nanargmax(sel_xrsb))
        peak_flux = float(sel_xrsb[idx_peak])
        peak_time = sel_times[idx_peak]

        # Times
        rise_seconds  = (peak_time     - sel_times[0]).total_seconds()
        decay_seconds = (sel_times[-1] - peak_time    ).total_seconds()
        flare_seconds = (sel_times[-1] - sel_times[0] ).total_seconds()

        if self.on_flare_info:
            self.on_flare_info({
                "status": "OK",
                "t0": sel_times[0], "t1": sel_times[-1], "n": len(sel_times),
                "peak_flux": peak_flux,
                "peak_time": peak_time,
                "rise_time":  fmt_timedelta_seconds(rise_seconds),
                "decay_time": fmt_timedelta_seconds(decay_seconds),
                "flare_time": fmt_timedelta_seconds(flare_seconds),
                "class_label": classify_goes_flux(peak_flux)
            })


def _build_time_combo(minute_step: int = 1) -> Tuple[QComboBox, QComboBox]:
    hour_cb = QComboBox()
    for h in range(24): hour_cb.addItem(f"{h:02d}", h)
    minute_cb = QComboBox()
    for m in range(0, 60, minute_step): minute_cb.addItem(f"{m:02d}", m)
    hour_cb.setCurrentIndex(0); minute_cb.setCurrentIndex(0)
    return hour_cb, minute_cb

def _get_dt(date_edit: QDateEdit, hour_cb: QComboBox, minute_cb: QComboBox) -> datetime:
    qd: QDate = date_edit.date()
    return datetime(qd.year(), qd.month(), qd.day(), int(hour_cb.currentData()), int(minute_cb.currentData()), 0)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("GOES XRS Plotter")
        self.resize(1250, 760)

        self.current_start_dt: Optional[datetime] = None
        self.current_end_dt: Optional[datetime] = None
        self.current_goes_num: int = 16  # default

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setSpacing(0)

        top_panel = QWidget(objectName="top_panel")
        top_panel_layout = QHBoxLayout(top_panel)
        top_panel_layout.setContentsMargins(12, 12, 12, 12)
        top_panel_layout.setSpacing(16)

        controls_col = QVBoxLayout(); controls_col.setSpacing(8)
        title_lbl = QLabel("Select the time range to plot")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        controls_col.addWidget(title_lbl)

        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(6)
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("Date"), 0, 1)
        grid.addWidget(QLabel("Hour"), 0, 2)
        grid.addWidget(QLabel("Minute"), 0, 3)
        grid.addWidget(QLabel("Spacecraft"), 0, 4)

        start_hdr = QLabel("Start:"); start_hdr.setStyleSheet("font-weight: 600;")
        self.start_date = QDateEdit(); self.start_date.setCalendarPopup(True); self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.start_hour, self.start_min = _build_time_combo(1)

        end_hdr = QLabel("End:"); end_hdr.setStyleSheet("font-weight: 600;")
        self.end_date = QDateEdit(); self.end_date.setCalendarPopup(True); self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_hour, self.end_min = _build_time_combo(1)

        self.spacecraft_cb = QComboBox()
        for n in (16, 17, 18, 19):
            self.spacecraft_cb.addItem(f"GOES-{n}", n)
        self.spacecraft_cb.setCurrentIndex(0)

        self.start_date.setDate(QDate.currentDate()); self.end_date.setDate(QDate.currentDate())
        self.start_hour.setCurrentIndex(0); self.start_min.setCurrentIndex(0)
        self.end_hour.setCurrentIndex(23); self.end_min.setCurrentIndex(59)

        grid.addWidget(start_hdr, 1, 0); grid.addWidget(self.start_date, 1, 1); grid.addWidget(self.start_hour, 1, 2); grid.addWidget(self.start_min, 1, 3)
        grid.addWidget(end_hdr,   2, 0); grid.addWidget(self.end_date,   2, 1); grid.addWidget(self.end_hour,   2, 2); grid.addWidget(self.end_min,   2, 3)
        grid.addWidget(QLabel("Use:"), 1, 4, alignment=Qt.AlignRight); grid.addWidget(self.spacecraft_cb, 1, 5)

        preset_row = QHBoxLayout()
        preset_lbl = QLabel("Preset:")
        self.preset_cb = QComboBox(); self.preset_cb.addItem("Whole day (00:00–23:59)")
        apply_preset_btn = QPushButton("Apply"); apply_preset_btn.clicked.connect(self.apply_preset)
        preset_row.addWidget(preset_lbl); preset_row.addWidget(self.preset_cb, 1); preset_row.addWidget(apply_preset_btn)

        btn_row = QHBoxLayout()
        self.plot_btn = QPushButton("Plot XRS Data"); self.plot_btn.setFixedHeight(32); self.plot_btn.clicked.connect(self.on_plot_clicked)
        self.save_plot_btn = QPushButton("Save Plot"); self.save_plot_btn.setFixedHeight(32); self.save_plot_btn.clicked.connect(self.on_save_plot)
        self.save_data_btn = QPushButton("Save Data"); self.save_data_btn.setFixedHeight(32); self.save_data_btn.clicked.connect(self.on_save_data)
        self.reset_btn = QPushButton("Reset"); self.reset_btn.setFixedHeight(32); self.reset_btn.clicked.connect(self.on_reset)
        btn_row.addWidget(self.plot_btn); btn_row.addSpacing(8)
        btn_row.addWidget(self.save_plot_btn); btn_row.addWidget(self.save_data_btn); btn_row.addWidget(self.reset_btn)
        btn_row.addStretch(1)

        controls_col.addLayout(grid); controls_col.addLayout(preset_row); controls_col.addLayout(btn_row)

        info_group = QGroupBox("Flare Information")
        info_form = QFormLayout(); info_form.setLabelAlignment(Qt.AlignLeft)
        self.info_selection = QLabel("—")
        self.info_peak_flux = QLabel("—")
        self.info_peak_time = QLabel("—")
        self.info_rise_time = QLabel("—")
        self.info_decay_time = QLabel("—")
        self.info_flare_time = QLabel("—")
        self.info_class = QLabel("—")
        info_form.addRow("Selection window:", self.info_selection)
        info_form.addRow("Peak X-ray Flux (W/m²):", self.info_peak_flux)
        info_form.addRow("Peak Time (UTC):", self.info_peak_time)
        info_form.addRow("Rise Time:", self.info_rise_time)
        info_form.addRow("Decay Time:", self.info_decay_time)
        info_form.addRow("Flare Duration:", self.info_flare_time)
        info_form.addRow("GOES Class:", self.info_class)
        info_group.setLayout(info_form)

        top_panel_layout.addLayout(controls_col, 3)
        top_panel_layout.addWidget(info_group, 2)
        root.addWidget(top_panel, 0)

        divider = QFrame(); divider.setFrameShape(QFrame.HLine); divider.setFrameShadow(QFrame.Plain)
        divider.setStyleSheet("background:#ffffff; min-height:2px; max-height:2px;"); root.addWidget(divider)

        bottom = QWidget(); bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 12, 12, 12); bottom_layout.setSpacing(8)
        self.canvas = PlotCanvas(self); bottom_layout.addWidget(self.canvas, 1)
        root.addWidget(bottom, 1)

        self.sb = self.statusBar()
        self.sb.showMessage("Ready")
        self.progress = QProgressBar()
        self.progress.setTextVisible(True); self.progress.setFormat("%p%")
        self.progress.setMinimumWidth(200); self.progress.setVisible(False)
        self.sb.addPermanentWidget(self.progress, 1)

        self.setStyleSheet("#top_panel { background: #f7f7f7; }")
        self.canvas.on_flare_info = self.update_flare_info

        self.thread: Optional[QThread] = None
        self.worker: Optional[DataWorker] = None

    def start_progress(self, text: str = "", indeterminate: bool = True):
        self.sb.showMessage(text)
        self.progress.setVisible(True)
        if indeterminate:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100); self.progress.setValue(0)
        QApplication.processEvents()

    @Slot(object, object)
    def progress_report(self, value, text):
        if text: self.sb.showMessage(text)
        if value is not None:
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, int(value))))

    def finish_progress(self, text: str = "Done."):
        self.progress.setValue(100)
        self.sb.showMessage(text)
        QTimer.singleShot(600, lambda: self.progress.setVisible(False))

    def apply_preset(self):
        date = self.start_date.date()
        self.end_date.setDate(date)
        self.start_hour.setCurrentIndex(0); self.start_min.setCurrentIndex(0)
        self.end_hour.setCurrentIndex(23);  self.end_min.setCurrentIndex(59)
        self.sb.showMessage("Preset applied: Whole day 00:00–23:59")

    def on_plot_clicked(self):
        try:
            start_dt = _get_dt(self.start_date, self.start_hour, self.start_min)
            end_dt   = _get_dt(self.end_date,   self.end_hour,   self.end_min)
            if end_dt <= start_dt:
                raise ValueError("End time must be after start time.")
            y1, m1, d1 = day_parts(start_dt); y2, m2, d2 = day_parts(end_dt)
            if (y1, m1, d1) != (y2, m2, d2):
                raise ValueError("For now, please choose a start and end within the same day.")

            goes_num = int(self.spacecraft_cb.currentData())
            self.current_goes_num = goes_num
            self.current_start_dt, self.current_end_dt = start_dt, end_dt

            for b in (self.plot_btn, self.save_plot_btn, self.save_data_btn, self.reset_btn):
                b.setEnabled(False)

            self.start_progress(f"Preparing (GOES-{goes_num})…", indeterminate=True)
            self.thread = QThread()
            self.worker = DataWorker(goes_num, y1, m1, d1, start_dt, end_dt)
            self.worker.moveToThread(self.thread)

            self.thread.started.connect(self.worker.run)
            self.worker.progress.connect(self.progress_report)
            self.worker.finished.connect(self._on_worker_finished)
            self.worker.failed.connect(self._on_worker_failed)
            self.worker.finished.connect(self.thread.quit)
            self.worker.failed.connect(self.thread.quit)
            self.thread.finished.connect(self.worker.deleteLater)
            self.thread.finished.connect(self._re_enable_buttons)

            self.thread.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.progress.setVisible(False)
            self.sb.showMessage("Error.")

    @Slot()
    def _re_enable_buttons(self):
        for b in (self.plot_btn, self.save_plot_btn, self.save_data_btn, self.reset_btn):
            b.setEnabled(True)

    @Slot(object, object, object, str, int)
    def _on_worker_finished(self, times, xrsa, xrsb, local_path, goes_num):
        if len(times) == 0:
            QMessageBox.information(self, "No Data", "No samples found in the selected time range.")
            self.canvas.clear_plot()
            self.finish_progress("No data in range.")
            return
        self.progress_report(98, "Rendering plot…")
        self.canvas.plot_xrs(times, xrsa, xrsb, self.current_start_dt, self.current_end_dt, goes_num)
        self.canvas.reset_selector()
        # Clear flare info after new plot
        self.update_flare_info({"status": "Plotted", "t0": None, "t1": None, "n": None,
                                "peak_flux": None, "peak_time": None,
                                "rise_time": None, "decay_time": None, "flare_time": None,
                                "class_label": None})
        self.finish_progress(f"Plotted {len(times)} points (GOES-{goes_num}). Source: {os.path.basename(local_path)}")

    @Slot(str)
    @Slot(str)
    def _on_worker_failed(self, tb_str: str):
        # Show a clean error message if it looks like a FileNotFoundError
        if "FileNotFoundError" in tb_str:
            QMessageBox.warning(
                self,
                "Data Not Available",
                "The requested GOES XRS file could not be found on the NOAA archive.\n\n"
                "Please check the selected date/time and spacecraft.\n\n"
                "Details:\n" + tb_str.splitlines()[-1]
            )
            self.sb.showMessage("File not available.")
        else:
            QMessageBox.critical(self, "Error", tb_str)
            self.sb.showMessage("Error.")
        self.progress.setVisible(False)

    def on_save_plot(self):
        if self.canvas.fig is None or self.canvas._times is None or len(self.canvas._times) == 0:
            QMessageBox.information(self, "Nothing to Save", "Please plot some data first."); return
        start = self.current_start_dt or self.canvas._times[0]
        end = self.current_end_dt or self.canvas._times[-1]
        default_name = f"GOES{self.current_goes_num}_XRS_{start:%Y%m%d_%H%M}-{end:%H%M}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save Plot as PNG", default_name, "PNG Image (*.png)")
        if not path: return
        try:
            self.canvas.fig.savefig(path, dpi=150, bbox_inches="tight")
            self.sb.showMessage(f"Plot saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def on_save_data(self):
        t = self.canvas._times; a = self.canvas._xrsa; b = self.canvas._xrsb
        if t is None or a is None or b is None or len(t) == 0:
            QMessageBox.information(self, "Nothing to Save", "Please plot some data first."); return
        start = self.current_start_dt or t[0]; end = self.current_end_dt or t[-1]
        default_name = f"GOES{self.current_goes_num}_XRS_{start:%Y%m%d_%H%M}-{end:%H%M}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Save Data as CSV", default_name, "CSV File (*.csv)")
        if not path: return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["time_utc", "xrsa_Wm2", "xrsb_Wm2"])
                for ti, ai, bi in zip(t, a, b):
                    writer.writerow([ti.strftime("%Y-%m-%d %H:%M:%S"), f"{ai:.6e}", f"{bi:.6e}"])
            self.sb.showMessage(f"Data saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def on_reset(self):
        self.canvas.reset_selector()
        self.update_flare_info({"status": "Plotted", "t0": None, "t1": None, "n": None,
                                "peak_flux": None, "peak_time": None,
                                "rise_time": None, "decay_time": None, "flare_time": None,
                                "class_label": None})
        self.sb.showMessage("Selection cleared.")

    def update_flare_info(self, info: dict):
        if info.get("status") != "OK":
            t0, t1, n = info.get("t0"), info.get("t1"), info.get("n")
            self.info_selection.setText(f"{t0} — {t1}  (n={n})" if (t0 and t1 and n is not None) else "—")
            self.info_peak_flux.setText("—")
            self.info_peak_time.setText("—")
            self.info_rise_time.setText("—")
            self.info_decay_time.setText("—")     # clear
            self.info_flare_time.setText("—")     # clear
            self.info_class.setText("—")
            return

        self.info_selection.setText(f"{info['t0']} — {info['t1']}  (n={info['n']})")
        self.info_peak_flux.setText(f"{info['peak_flux']:.3e}")
        self.info_peak_time.setText(str(info['peak_time']))
        self.info_rise_time.setText(info['rise_time'])
        self.info_decay_time.setText(info.get('decay_time', "—"))
        self.info_flare_time.setText(info.get('flare_time', "—"))
        self.info_class.setText(info['class_label'])

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
