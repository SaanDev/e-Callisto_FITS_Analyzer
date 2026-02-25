"""
e-CALLISTO FITS Analyzer
Version 2.2-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import csv
import shutil
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QDateTime, QObject, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.Backend.sunpy_analysis import summarize_map_roi, summarize_xrs_interval
from src.Backend.sunpy_archive import (
    DATA_KIND_MAP,
    DATA_KIND_TIMESERIES,
    SunPyFetchResult,
    SunPyLoadResult,
    SunPyQuerySpec,
    SunPySearchResult,
    fetch,
    load_downloaded,
    search,
)
from src.UI.sunpy_plot_window import SunPyPlotWindow


def _get_theme():
    app = QApplication.instance()
    if not app:
        return None
    return app.property("theme_manager")


def _default_cache_dir() -> Path:
    app_data = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    if not app_data:
        app_data = str(Path.home() / ".local" / "share" / "e-callisto-fits-analyzer")
    out = Path(app_data) / "sunpy_cache"
    out.mkdir(parents=True, exist_ok=True)
    return out


class SunPyWorker(QObject):
    progress = Signal(object, object)
    search_finished = Signal(object)
    load_finished = Signal(object, object)
    partial_warning = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        mode: str,
        *,
        query_spec: SunPyQuerySpec | None = None,
        search_result: SunPySearchResult | None = None,
        selected_rows: list[int] | None = None,
        cache_dir: str | Path | None = None,
    ):
        super().__init__()
        self.mode = str(mode or "").strip().lower()
        self.query_spec = query_spec
        self.search_result = search_result
        self.selected_rows = list(selected_rows or [])
        self.cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else _default_cache_dir()

    @Slot()
    def run(self):
        try:
            if self.mode == "search":
                if self.query_spec is None:
                    raise ValueError("Search mode requires a query spec.")
                self.progress.emit(None, "Searching SunPy archives...")
                result = search(self.query_spec)
                self.progress.emit(100, f"Found {len(result.rows)} candidate files.")
                self.search_finished.emit(result)
                return

            if self.mode == "fetch_load":
                if self.search_result is None:
                    raise ValueError("Fetch/load mode requires a search result.")
                self.progress.emit(2, "Preparing download session...")
                fetch_result = fetch(
                    self.search_result,
                    self.cache_dir,
                    selected_rows=self.selected_rows,
                    progress_cb=lambda v, t: self.progress.emit(
                        5 + int(max(0, min(100, int(v))) * 0.80),
                        t,
                    ),
                )
                if fetch_result.failed_count > 0:
                    details = "\n".join(fetch_result.errors[:8])
                    more = "" if fetch_result.failed_count <= 8 else f"\n...and {fetch_result.failed_count - 8} more."
                    self.partial_warning.emit(
                        "Some downloads failed, but at least one file was retrieved.\n"
                        f"{details}{more}"
                    )
                if not fetch_result.paths:
                    details = "\n".join(fetch_result.errors[:6])
                    more = "" if fetch_result.failed_count <= 6 else f"\n...and {fetch_result.failed_count - 6} more."
                    hint = (
                        "Try selecting fewer rows or increasing sample cadence for SDO/AIA "
                        "(for example 120s or 300s)."
                    )
                    raise RuntimeError(
                        "No files could be downloaded from the selected records.\n"
                        f"{hint}\n\nDownload errors:\n{details or '(No downloader details were returned.)'}{more}"
                    )

                self.progress.emit(88, "Loading downloaded files...")
                load_result = load_downloaded(fetch_result.paths, data_kind=self.search_result.data_kind)
                self.progress.emit(96, "Finalizing data...")
                self.progress.emit(100, "Data loaded.")
                self.load_finished.emit(fetch_result, load_result)
                return

            raise ValueError(f"Unknown worker mode '{self.mode}'.")
        except Exception:
            self.failed.emit(traceback.format_exc())


class SunPySolarViewer(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SunPy Multi-Mission Solar Explorer v1.0-beta")
        self.resize(1220, 900)

        self.theme = _get_theme()
        if self.theme and hasattr(self.theme, "themeChanged"):
            self.theme.themeChanged.connect(self._on_theme_changed)

        self.cache_dir = _default_cache_dir()

        self._search_result: SunPySearchResult | None = None
        self._last_query_spec: SunPyQuerySpec | None = None
        self._loaded_result: SunPyLoadResult | None = None
        self._map_frames: list[Any] = []
        self._current_map_data: np.ndarray | None = None
        self._map_roi_bounds: tuple[int, int, int, int] | None = None
        self._analysis_payload: dict[str, Any] = {}

        self._plot_window: SunPyPlotWindow | None = None
        self._active_thread: QThread | None = None
        self._active_worker: SunPyWorker | None = None
        self._busy = False
        self._close_blocked_notice_active = False
        self._progress_target = 0
        self._progress_value = 0
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(24)
        self._progress_timer.timeout.connect(self._tick_progress)

        self._build_ui()
        self._connect_signals()
        self._on_spacecraft_changed()

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        query_group = QGroupBox("Archive Query")
        qlayout = QGridLayout(query_group)
        root.addWidget(query_group)

        self.spacecraft_combo = QComboBox()
        for item in ("SDO", "SOHO", "STEREO_A", "GOES"):
            self.spacecraft_combo.addItem(item)

        self.instrument_combo = QComboBox()

        self.detector_label = QLabel("Detector")
        self.detector_combo = QComboBox()
        self.detector_combo.addItems(["C2", "C3"])

        self.wavelength_label = QLabel("Wavelength (A)")
        self.wavelength_combo = QComboBox()
        self.wavelength_combo.setEditable(True)

        self.satellite_label = QLabel("GOES Satellite")
        self.satellite_combo = QComboBox()
        for sat in (13, 14, 15, 16, 17, 18):
            self.satellite_combo.addItem(str(sat), userData=sat)
        self.satellite_combo.setCurrentText("16")

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        start_default = now - timedelta(hours=2)
        self.start_dt_edit = QDateTimeEdit(QDateTime(start_default))
        self.end_dt_edit = QDateTimeEdit(QDateTime(now))
        for edit in (self.start_dt_edit, self.end_dt_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")

        self.sample_seconds_spin = QSpinBox()
        self.sample_seconds_spin.setRange(0, 3600)
        self.sample_seconds_spin.setValue(60)
        self.sample_seconds_spin.setToolTip("Sample cadence in seconds (0 = full archive cadence, slower).")

        self.max_records_spin = QSpinBox()
        self.max_records_spin.setRange(1, 5000)
        self.max_records_spin.setValue(100)

        self.search_btn = QPushButton("Search Archives")
        self.retry_btn = QPushButton("Retry Last Query")
        self.retry_btn.setEnabled(False)
        self.open_cache_btn = QPushButton("Open Cache Folder")

        row = 0
        qlayout.addWidget(QLabel("Spacecraft"), row, 0)
        qlayout.addWidget(self.spacecraft_combo, row, 1)
        qlayout.addWidget(QLabel("Instrument"), row, 2)
        qlayout.addWidget(self.instrument_combo, row, 3)
        qlayout.addWidget(self.detector_label, row, 4)
        qlayout.addWidget(self.detector_combo, row, 5)

        row += 1
        qlayout.addWidget(self.wavelength_label, row, 0)
        qlayout.addWidget(self.wavelength_combo, row, 1)
        qlayout.addWidget(self.satellite_label, row, 2)
        qlayout.addWidget(self.satellite_combo, row, 3)
        qlayout.addWidget(QLabel("Sample (s)"), row, 4)
        qlayout.addWidget(self.sample_seconds_spin, row, 5)

        row += 1
        qlayout.addWidget(QLabel("Start (UTC)"), row, 0)
        qlayout.addWidget(self.start_dt_edit, row, 1, 1, 2)
        qlayout.addWidget(QLabel("End (UTC)"), row, 3)
        qlayout.addWidget(self.end_dt_edit, row, 4, 1, 2)

        row += 1
        qlayout.addWidget(QLabel("Max Records"), row, 0)
        qlayout.addWidget(self.max_records_spin, row, 1)
        qlayout.addWidget(self.search_btn, row, 2)
        qlayout.addWidget(self.retry_btn, row, 3)
        qlayout.addWidget(self.open_cache_btn, row, 4, 1, 2)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        results_group = QGroupBox("Search Results")
        results_layout = QVBoxLayout(results_group)
        root.addWidget(results_group)

        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels(
            ["Use", "Start", "End", "Source", "Instrument", "Provider", "File ID", "Size"]
        )
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.results_table.setColumnWidth(0, 44)
        self.results_table.setColumnWidth(1, 145)
        self.results_table.setColumnWidth(2, 145)
        self.results_table.setColumnWidth(3, 96)
        self.results_table.setColumnWidth(4, 96)
        self.results_table.setColumnWidth(5, 120)
        self.results_table.setColumnWidth(6, 460)
        self.results_table.setColumnWidth(7, 90)
        results_layout.addWidget(self.results_table)

        results_actions = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.clear_selection_btn = QPushButton("Clear Selection")
        self.download_load_btn = QPushButton("Download && Load Selected")
        self.download_load_btn.setEnabled(False)
        results_actions.addWidget(self.select_all_btn)
        results_actions.addWidget(self.clear_selection_btn)
        results_actions.addStretch(1)
        results_actions.addWidget(self.download_load_btn)
        results_layout.addLayout(results_actions)

        analysis_group = QGroupBox("Analysis and Export")
        analysis_layout = QVBoxLayout(analysis_group)
        root.addWidget(analysis_group, 1)

        actions_row = QHBoxLayout()
        self.open_plot_btn = QPushButton("Open Plot Window")
        self.open_plot_btn.setEnabled(False)
        self.export_plot_btn = QPushButton("Export Plot")
        self.export_plot_btn.setEnabled(False)
        self.export_analysis_btn = QPushButton("Export Analysis CSV")
        self.export_analysis_btn.setEnabled(False)
        actions_row.addWidget(self.open_plot_btn)
        actions_row.addStretch(1)
        actions_row.addWidget(self.export_plot_btn)
        actions_row.addWidget(self.export_analysis_btn)
        analysis_layout.addLayout(actions_row)

        self.analysis_text = QTextEdit()
        self.analysis_text.setReadOnly(True)
        analysis_layout.addWidget(self.analysis_text, 1)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

    def _connect_signals(self):
        self.spacecraft_combo.currentTextChanged.connect(self._on_spacecraft_changed)
        self.instrument_combo.currentTextChanged.connect(self._on_instrument_changed)
        self.search_btn.clicked.connect(self.search_archives)
        self.retry_btn.clicked.connect(self.retry_last_query)
        self.open_cache_btn.clicked.connect(self.open_cache_folder)
        self.select_all_btn.clicked.connect(self.select_all_rows)
        self.clear_selection_btn.clicked.connect(self.clear_all_rows)
        self.download_load_btn.clicked.connect(self.download_and_load_selected)
        self.open_plot_btn.clicked.connect(self.open_plot_window)
        self.export_plot_btn.clicked.connect(self.export_plot)
        self.export_analysis_btn.clicked.connect(self.export_analysis_csv)

    def _on_theme_changed(self, _dark: bool):
        if self._plot_window is not None:
            self._plot_window.theme = self.theme
            self._plot_window.apply_theme()

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = bool(busy)
        self.search_btn.setEnabled(not busy)
        self.download_load_btn.setEnabled((not busy) and bool(self._search_result and self._search_result.rows))
        self.retry_btn.setEnabled((not busy) and self._last_query_spec is not None)
        self.progress.setVisible(bool(busy))
        if busy:
            self.progress.setRange(0, 100)
            self._progress_value = 0
            self._progress_target = 0
            self.progress.setValue(0)
            if text:
                self.statusBar().showMessage(text)
        else:
            self._progress_timer.stop()

    def _tick_progress(self):
        if self.progress.maximum() <= 0:
            return
        target = int(max(0, min(100, self._progress_target)))
        current = int(max(0, min(100, self._progress_value)))
        if current >= target:
            self._progress_timer.stop()
            return

        delta = target - current
        step = max(1, min(10, int(round(delta * 0.35))))
        current = min(target, current + step)
        self._progress_value = current
        self.progress.setValue(current)

    def _on_spacecraft_changed(self):
        spacecraft = self.spacecraft_combo.currentText().strip().upper()
        self.instrument_combo.blockSignals(True)
        self.instrument_combo.clear()
        if spacecraft == "SDO":
            self.instrument_combo.addItem("AIA")
        elif spacecraft == "SOHO":
            self.instrument_combo.addItem("LASCO")
        elif spacecraft == "STEREO_A":
            self.instrument_combo.addItem("EUVI")
        elif spacecraft == "GOES":
            self.instrument_combo.addItem("XRS")
        self.instrument_combo.blockSignals(False)
        self._on_instrument_changed()

    def _set_wavelength_values(self, values: list[int], default_value: int):
        self.wavelength_combo.clear()
        for value in values:
            self.wavelength_combo.addItem(str(value))
        self.wavelength_combo.setCurrentText(str(default_value))

    def _on_instrument_changed(self):
        spacecraft = self.spacecraft_combo.currentText().strip().upper()
        instrument = self.instrument_combo.currentText().strip().upper()

        is_lasco = spacecraft == "SOHO" and instrument == "LASCO"
        is_wave = (spacecraft == "SDO" and instrument == "AIA") or (spacecraft == "STEREO_A" and instrument == "EUVI")
        is_goes = spacecraft == "GOES" and instrument == "XRS"

        self.detector_label.setVisible(is_lasco)
        self.detector_combo.setVisible(is_lasco)
        self.wavelength_label.setVisible(is_wave)
        self.wavelength_combo.setVisible(is_wave)
        self.satellite_label.setVisible(is_goes)
        self.satellite_combo.setVisible(is_goes)

        if spacecraft == "SDO" and instrument == "AIA":
            self._set_wavelength_values([94, 131, 171, 193, 211, 304, 335, 1600, 1700], default_value=193)
        elif spacecraft == "STEREO_A" and instrument == "EUVI":
            self._set_wavelength_values([171, 195, 284, 304], default_value=195)

    def _build_query_spec(self) -> SunPyQuerySpec:
        start_dt = self.start_dt_edit.dateTime().toPython().replace(tzinfo=None)
        end_dt = self.end_dt_edit.dateTime().toPython().replace(tzinfo=None)

        spacecraft = self.spacecraft_combo.currentText().strip().upper()
        instrument = self.instrument_combo.currentText().strip().upper()

        detector = None
        wavelength = None
        satellite_number = None

        if self.detector_combo.isVisible():
            detector = self.detector_combo.currentText().strip().upper()
        if self.wavelength_combo.isVisible():
            text = self.wavelength_combo.currentText().strip()
            wavelength = float(text) if text else None
        if self.satellite_combo.isVisible():
            satellite_number = int(self.satellite_combo.currentData())

        sample_seconds = int(self.sample_seconds_spin.value() or 0)
        sample_value = sample_seconds if sample_seconds > 0 else None

        return SunPyQuerySpec(
            start_dt=start_dt,
            end_dt=end_dt,
            spacecraft=spacecraft,
            instrument=instrument,
            wavelength_angstrom=wavelength,
            detector=detector,
            satellite_number=satellite_number,
            sample_seconds=sample_value,
            max_records=int(self.max_records_spin.value()),
        )

    def _start_worker(self, worker: SunPyWorker):
        if self._active_thread is not None:
            QMessageBox.information(self, "SunPy", "Another SunPy operation is still running.")
            return

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_worker_progress)
        worker.failed.connect(self._on_worker_failed)
        worker.partial_warning.connect(self._on_partial_warning)
        worker.search_finished.connect(self._on_search_finished)
        worker.load_finished.connect(self._on_load_finished)

        worker.failed.connect(thread.quit)
        worker.search_finished.connect(thread.quit)
        worker.load_finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_worker_stopped)

        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _on_worker_stopped(self):
        self._active_thread = None
        self._active_worker = None
        self._close_blocked_notice_active = False
        self._set_busy(False)

    def is_operation_running(self) -> bool:
        thread = self._active_thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except Exception:
            return True

    @Slot(object, object)
    def _on_worker_progress(self, value, text):
        if value is None:
            self.progress.setRange(0, 0)
            self._progress_timer.stop()
        else:
            if self.progress.maximum() <= 0:
                self.progress.setRange(0, 100)
                self.progress.setValue(max(0, min(100, int(self._progress_value))))
            self._progress_target = max(0, min(100, int(value)))
            if not self._progress_timer.isActive():
                self._progress_timer.start()
        if text:
            self.statusBar().showMessage(str(text))

    @Slot(str)
    def _on_worker_failed(self, tb_text: str):
        self.statusBar().showMessage("SunPy operation failed.", 5000)
        short = str(tb_text).strip().splitlines()[-1] if tb_text else "Unknown error"
        self.analysis_text.setPlainText("Operation failed.\n\n" + short)
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("SunPy Error")
        msg.setText(short)
        msg.setDetailedText(tb_text)
        msg.exec()

    @Slot(str)
    def _on_partial_warning(self, message: str):
        QMessageBox.warning(self, "Partial Download", message)

    @Slot(object)
    def _on_search_finished(self, result_obj: object):
        result = result_obj if isinstance(result_obj, SunPySearchResult) else None
        if result is None:
            self._on_worker_failed("Search worker returned an unexpected payload.")
            return

        self._search_result = result
        self._populate_results_table(result)
        self.download_load_btn.setEnabled(bool(result.rows))
        self.export_plot_btn.setEnabled(False)
        self.export_analysis_btn.setEnabled(False)

        if not result.rows:
            self.analysis_text.setPlainText("No results found.\nTry a different time range or instrument setup.")
            self.statusBar().showMessage("No archive matches found.", 5000)
            return

        self.statusBar().showMessage(f"Found {len(result.rows)} records.", 5000)
        self.analysis_text.setPlainText("Search complete. Select rows and click 'Download && Load Selected'.")

    @Slot(object, object)
    def _on_load_finished(self, fetch_obj: object, load_obj: object):
        try:
            fetch_result = fetch_obj if isinstance(fetch_obj, SunPyFetchResult) else None
            load_result = load_obj if isinstance(load_obj, SunPyLoadResult) else None
            if fetch_result is None or load_result is None:
                self._on_worker_failed("Fetch/load worker returned an unexpected payload.")
                return

            self._loaded_result = load_result
            if load_result.data_kind == DATA_KIND_MAP:
                self._apply_loaded_map_result(load_result)
            elif load_result.data_kind == DATA_KIND_TIMESERIES:
                self._apply_loaded_timeseries_result(load_result)
            else:
                self._on_worker_failed(f"Unsupported loaded data kind: {load_result.data_kind}")
                return

            msg = (
                f"Loaded {len(fetch_result.paths)} file(s)."
                if fetch_result.failed_count == 0
                else f"Loaded {len(fetch_result.paths)} file(s), with {fetch_result.failed_count} failures."
            )
            self.statusBar().showMessage(msg, 6000)
            self.open_plot_btn.setEnabled(True)
            self.export_plot_btn.setEnabled(True)
            self.export_analysis_btn.setEnabled(bool(self._analysis_payload))
            self.open_plot_window()
        except Exception:
            self._on_worker_failed(traceback.format_exc())

    def _populate_results_table(self, result: SunPySearchResult):
        rows = result.rows
        self.results_table.setUpdatesEnabled(False)
        try:
            self.results_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                select_item = QTableWidgetItem("")
                select_item.setFlags(select_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                select_item.setCheckState(Qt.Checked if row.selected else Qt.Unchecked)
                self.results_table.setItem(row_index, 0, select_item)

                self.results_table.setItem(row_index, 1, QTableWidgetItem(row.start.strftime("%Y-%m-%d %H:%M:%S")))
                self.results_table.setItem(row_index, 2, QTableWidgetItem(row.end.strftime("%Y-%m-%d %H:%M:%S")))
                self.results_table.setItem(row_index, 3, QTableWidgetItem(row.source))
                self.results_table.setItem(row_index, 4, QTableWidgetItem(row.instrument))
                self.results_table.setItem(row_index, 5, QTableWidgetItem(row.provider))
                self.results_table.setItem(row_index, 6, QTableWidgetItem(row.fileid))
                self.results_table.setItem(row_index, 7, QTableWidgetItem(row.size))
        finally:
            self.results_table.setUpdatesEnabled(True)

    def select_all_rows(self):
        for i in range(self.results_table.rowCount()):
            item = self.results_table.item(i, 0)
            if item is not None:
                item.setCheckState(Qt.Checked)

    def clear_all_rows(self):
        for i in range(self.results_table.rowCount()):
            item = self.results_table.item(i, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked)

    def _checked_rows(self) -> list[int]:
        checked: list[int] = []
        for i in range(self.results_table.rowCount()):
            item = self.results_table.item(i, 0)
            if item is not None and item.checkState() == Qt.Checked:
                checked.append(i)
        return checked

    def search_archives(self):
        try:
            spec = self._build_query_spec()
        except Exception as exc:
            QMessageBox.warning(self, "SunPy Query", f"Invalid query inputs: {exc}")
            return

        self._last_query_spec = spec
        self.retry_btn.setEnabled(True)
        self._set_busy(True, "Searching SunPy archives...")
        self._start_worker(SunPyWorker("search", query_spec=spec))

    def retry_last_query(self):
        if self._last_query_spec is None:
            QMessageBox.information(self, "SunPy", "No previous query is available.")
            return
        self._set_busy(True, "Retrying last query...")
        self._start_worker(SunPyWorker("search", query_spec=self._last_query_spec))

    def download_and_load_selected(self):
        if self._search_result is None:
            QMessageBox.information(self, "SunPy", "Run a search first.")
            return

        selected_rows = self._checked_rows()
        if not selected_rows:
            QMessageBox.information(self, "SunPy", "Select at least one result row.")
            return

        self._set_busy(True, "Downloading selected rows...")
        self._start_worker(
            SunPyWorker(
                "fetch_load",
                search_result=self._search_result,
                selected_rows=selected_rows,
                cache_dir=self.cache_dir,
            )
        )

    def _ensure_plot_window(self) -> SunPyPlotWindow:
        if self._plot_window is None:
            self._plot_window = SunPyPlotWindow(theme=self.theme)
            self._plot_window.mapFrameChanged.connect(self._on_plot_frame_changed)
            self._plot_window.mapRoiChanged.connect(self._on_plot_roi_changed)
        return self._plot_window

    def open_plot_window(self):
        if self._plot_window is None:
            QMessageBox.information(self, "SunPy", "No data loaded yet. Load data first.")
            return
        self._plot_window.show()
        self._plot_window.raise_()
        self._plot_window.activateWindow()

    def _apply_loaded_map_result(self, load_result: SunPyLoadResult):
        self._map_frames = self._extract_map_frames(load_result.maps_or_timeseries)
        if not self._map_frames:
            raise RuntimeError("No map frames were loaded from downloaded files.")

        self._map_roi_bounds = None
        self._current_map_data = None
        first = self._map_frames[0]
        query_spec = self._search_result.spec if self._search_result is not None else None
        metadata = {
            "observatory": self._safe_meta_text(getattr(first, "observatory", None)),
            "instrument": self._safe_meta_text(getattr(first, "instrument", None)),
            "detector": self._safe_meta_text(getattr(first, "detector", None)),
            "wavelength": self._safe_meta_text(getattr(first, "wavelength", None)),
            "date": self._safe_meta_text(getattr(first, "date", None)),
            "query_spacecraft": self._safe_meta_text(getattr(query_spec, "spacecraft", None)),
            "query_instrument": self._safe_meta_text(getattr(query_spec, "instrument", None)),
            "query_detector": self._safe_meta_text(getattr(query_spec, "detector", None)),
        }
        metadata.update({k: self._safe_meta_text(v) for k, v in (load_result.metadata or {}).items()})

        plot_window = self._ensure_plot_window()
        plot_window.set_map_frames(self._map_frames, metadata=metadata)
        self._sync_map_analysis_from_plot()

    def _extract_map_frames(self, loaded_obj: Any) -> list[Any]:
        maps_attr = getattr(loaded_obj, "maps", None)
        if maps_attr is not None:
            try:
                return list(maps_attr)
            except Exception:
                pass
        if isinstance(loaded_obj, (list, tuple)):
            return list(loaded_obj)
        return [loaded_obj]

    def _safe_meta_text(self, value: Any) -> str:
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
            preview = [self._safe_meta_text(v) for v in list(value)[:4]]
            preview = [x for x in preview if x]
            if not preview:
                return ""
            suffix = "..." if len(value) > 4 else ""
            return ", ".join(preview) + suffix
        try:
            return str(value).strip()
        except Exception:
            return repr(value)

    @Slot(int)
    def _on_plot_frame_changed(self, _frame_idx: int):
        self._sync_map_analysis_from_plot()

    @Slot(object)
    def _on_plot_roi_changed(self, bounds_obj: object):
        bounds = bounds_obj if isinstance(bounds_obj, tuple) else None
        self._map_roi_bounds = bounds
        if self._plot_window is not None and self._plot_window.current_map_data() is not None:
            self._current_map_data = self._plot_window.current_map_data()
        self._update_map_analysis()

    def _sync_map_analysis_from_plot(self):
        if self._plot_window is None:
            return
        current = self._plot_window.current_map_data()
        if current is None:
            return
        self._current_map_data = current
        self._map_roi_bounds = self._plot_window.current_roi_bounds()
        self._update_map_analysis()

    def _update_map_analysis(self):
        if self._current_map_data is None:
            return
        summary = summarize_map_roi(self._current_map_data, roi_bounds=self._map_roi_bounds)

        roi_text = "Full frame"
        if self._map_roi_bounds is not None:
            x0, x1, y0, y1 = self._map_roi_bounds
            roi_text = f"x=[{x0},{x1}], y=[{y0},{y1}]"

        lines = [
            "Map Analysis",
            f"ROI: {roi_text}",
            f"Pixels: {summary.n_pixels}",
            f"Min: {summary.min:.6g}",
            f"Max: {summary.max:.6g}",
            f"Mean: {summary.mean:.6g}",
            f"Median: {summary.median:.6g}",
            f"Std: {summary.std:.6g}",
            f"P95: {summary.p95:.6g}",
            f"P99: {summary.p99:.6g}",
        ]
        self.analysis_text.setPlainText("\n".join(lines))
        self._analysis_payload = {
            "kind": "map",
            "roi": roi_text,
            **asdict(summary),
        }

    def _apply_loaded_timeseries_result(self, load_result: SunPyLoadResult):
        self._map_frames = []
        self._map_roi_bounds = None
        self._current_map_data = None

        ts = load_result.maps_or_timeseries
        to_dataframe = getattr(ts, "to_dataframe", None)
        if not callable(to_dataframe):
            raise RuntimeError("Loaded TimeSeries object does not provide to_dataframe().")
        frame = to_dataframe()
        if frame is None or len(frame) == 0:
            raise RuntimeError("Loaded TimeSeries object has no data.")

        numeric_cols = [str(c) for c in frame.columns if np.issubdtype(frame[c].dtype, np.number)]
        if not numeric_cols:
            raise RuntimeError("GOES/XRS TimeSeries has no numeric columns to plot.")

        short_col = self._pick_column(numeric_cols, preferred=("xrsa", "short", "0.5"))
        long_col = self._pick_column(numeric_cols, preferred=("xrsb", "long", "1.0", "8.0"))
        if short_col is None and long_col is None:
            short_col = numeric_cols[0]
        if short_col is None:
            short_col = long_col
        if long_col is None and len(numeric_cols) > 1:
            long_col = numeric_cols[1]

        times = list(frame.index.to_pydatetime())
        short_flux = np.asarray(frame[short_col], dtype=float) if short_col else None
        long_flux = np.asarray(frame[long_col], dtype=float) if long_col else None

        analysis_flux = long_flux if long_flux is not None else short_flux
        if analysis_flux is None:
            raise RuntimeError("No XRS flux channel is available for analysis.")
        summary = summarize_xrs_interval(analysis_flux, times=times)
        peak_time = summary.peak_time.strftime("%Y-%m-%d %H:%M:%S") if summary.peak_time else "N/A"
        lines = [
            "XRS Analysis",
            f"Samples: {len(times)}",
            f"Short channel: {short_col or 'N/A'}",
            f"Long channel: {long_col or 'N/A'}",
            f"Peak flux: {summary.peak_flux:.6e} W/m^2",
            f"Peak time: {peak_time} UTC",
            f"Median flux: {summary.median_flux:.6e} W/m^2",
            f"Rise time: {summary.rise_seconds:.1f} s",
            f"Decay time: {summary.decay_seconds:.1f} s",
            f"Flare class: {summary.flare_class}",
        ]
        self.analysis_text.setPlainText("\n".join(lines))
        self._analysis_payload = {
            "kind": "timeseries",
            "samples": len(times),
            "short_channel": short_col or "",
            "long_channel": long_col or "",
            **asdict(summary),
            "peak_time": peak_time,
        }

        plot_window = self._ensure_plot_window()
        plot_window.set_timeseries(
            times,
            channels={"short": short_flux, "long": long_flux},
            metadata={
                "short_label": short_col or "",
                "long_label": long_col or "",
                "n_samples": len(times),
            },
        )

    def _pick_column(self, columns: list[str], preferred: tuple[str, ...]) -> str | None:
        for col in columns:
            lower = col.lower()
            if any(token in lower for token in preferred):
                return col
        return None

    def export_plot(self):
        if self._plot_window is None or not self._plot_window.has_plot_content():
            QMessageBox.information(self, "Export Plot", "No plot is available yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Plot",
            "sunpy_plot.png",
            "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tiff *.tif);;JPG (*.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            self._plot_window.save_current_plot(path)
            self.statusBar().showMessage(f"Plot saved: {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Plot", str(exc))

    def export_analysis_csv(self):
        if not self._analysis_payload:
            QMessageBox.information(self, "Export Analysis", "No analysis is available yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Analysis",
            "sunpy_analysis.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            keys = list(self._analysis_payload.keys())
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=keys)
                writer.writeheader()
                writer.writerow(self._analysis_payload)
            self.statusBar().showMessage(f"Analysis saved: {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export Analysis", str(exc))

    def open_cache_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.cache_dir)))

    def _cleanup_cache_dir(self):
        try:
            cache_path = Path(self.cache_dir).expanduser().resolve()
        except Exception:
            return
        if not cache_path.exists():
            return
        try:
            shutil.rmtree(cache_path)
        except Exception:
            # Cleanup is best-effort; failures should not block app shutdown.
            pass

    def set_time_window(self, start_dt: datetime, end_dt: datetime, auto_query: bool = True) -> bool:
        try:
            if end_dt <= start_dt:
                self.statusBar().showMessage("Sync skipped: End time must be after start time.", 4000)
                return False
            self.start_dt_edit.setDateTime(QDateTime(start_dt.replace(tzinfo=None)))
            self.end_dt_edit.setDateTime(QDateTime(end_dt.replace(tzinfo=None)))
            self.statusBar().showMessage(
                f"Synced time window: {start_dt:%Y-%m-%d %H:%M:%S} - {end_dt:%Y-%m-%d %H:%M:%S} UTC",
                5000,
            )
            if auto_query:
                self.search_archives()
            return True
        except Exception as exc:
            self.statusBar().showMessage(f"Sync failed: {exc}", 5000)
            return False

    def closeEvent(self, event):
        if self.is_operation_running():
            try:
                if self._active_thread is not None:
                    self._active_thread.quit()
                    self._active_thread.wait(250)
            except Exception:
                pass
            if self.is_operation_running():
                self.statusBar().showMessage(
                    "SunPy operation still running. Please wait for it to finish before closing.",
                    5000,
                )
                self._close_blocked_notice_active = True
                event.ignore()
                return
        try:
            if self._plot_window is not None:
                self._plot_window.close()
                self._plot_window.deleteLater()
                self._plot_window = None
        except Exception:
            pass
        self._cleanup_cache_dir()
        super().closeEvent(event)
