"""Browse the Monstein catalog and use the downloader's shared FITS workflow."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from PySide6.QtCore import QDate, QObject, QThread, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.backend.radio.burst_list import (
    BurstEvent, BurstListCancelled, fetch_burst_events, filter_burst_events,
)
from src.ui.radio.burst_fits import BurstFitsWorker
from src.ui.radio.burst_preview import BurstPreviewWorker

CATALOG_URL = "https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/"


class BurstCatalogWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, start_date, end_date):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date
        self._cancelled = False

    def request_cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        try:
            result = fetch_burst_events(
                self.start_date, self.end_date,
                progress=self.progress.emit, cancelled=lambda: self._cancelled,
            )
            self.finished.emit({"events": result.events, "warnings": result.warnings})
        except BurstListCancelled:
            self.finished.emit({"cancelled": True})
        except Exception as exc:
            self.finished.emit({"error": str(exc)})


def _table(headers, parent):
    table = QTableWidget(0, len(headers), parent)
    table.setObjectName("EventResultsTable")
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class BurstListTab(QWidget):
    import_request = Signal(list)
    comparison_request = Signal(list)
    idle = Signal()
    cache_changed = Signal()
    busy_changed = Signal(bool)

    def __init__(self, stations, parent=None):
        super().__init__(parent)
        self.setObjectName("DownloaderTabPage")
        self._known_stations = tuple(stations)
        self._events: list[BurstEvent] = []
        self._warnings: list[str] = []
        self._thread = None
        self._worker = None
        self._download_tasks = []
        self._download_done = 0
        self._download_failures = []
        self._download_progress = {}
        self._preview_windows = []
        self._job_kind = ""
        self._job_outcome = ""
        self._cancel_requested = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(8)
        header = QHBoxLayout()
        introduction = QLabel("Find a burst, choose its FITS files, then preview or import the data.", self)
        introduction.setWordWrap(True)
        header.addWidget(introduction, 1)
        source = QLabel(f'<a href="{CATALOG_URL}">Monstein source catalog ↗</a>', self)
        source.setOpenExternalLinks(True)
        header.addWidget(source)
        layout.addLayout(header)
        filters = QGroupBox("Catalog filters", self)
        filters.setObjectName("DownloaderSection")
        grid = QGridLayout(filters)
        grid.setContentsMargins(6, 6, 6, 6)
        today = QDate(datetime.now(timezone.utc).date())
        self.start_date_edit = QDateEdit(today.addDays(-30), self)
        self.end_date_edit = QDateEdit(today, self)
        for edit in (self.start_date_edit, self.end_date_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMinimumDate(QDate(2010, 1, 1))
            edit.dateChanged.connect(self._dates_changed)
        self.load_button = QPushButton("Load Events", self)
        self.load_button.setObjectName("PrimaryDownloaderButton")
        self.load_button.clicked.connect(self.load_events)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.request_cancel)
        self.type_filter = QComboBox(self)
        self.type_filter.addItem("All types", "")
        for name in ("I", "II", "III", "IV", "V", "CTM", "U", "J", "RFI"):
            self.type_filter.addItem(name, name)
        self.station_filter = QLineEdit(self)
        self.station_filter.setPlaceholderText("Any reporting station")
        self.min_stations = QSpinBox(self)
        self.min_stations.setRange(0, 200)
        self.min_stations.setSpecialValueText("Any")
        self.text_filter = QLineEdit(self)
        self.text_filter.setPlaceholderText("Search type, stations or remarks")
        fields = [("From (UTC)", self.start_date_edit), ("Through (UTC)", self.end_date_edit),
                  ("Burst type", self.type_filter), ("Reporting station", self.station_filter),
                  ("Min. stations", self.min_stations)]
        for column, (label, widget) in enumerate(fields):
            grid.addWidget(QLabel(label), 0, column)
            grid.addWidget(widget, 1, column)
        grid.addWidget(self.load_button, 1, 5)
        grid.addWidget(self.text_filter, 2, 0, 1, 5)
        self.reset_filters_button = QPushButton("Reset Filters", self)
        self.reset_filters_button.clicked.connect(self.reset_filters)
        grid.addWidget(self.reset_filters_button, 2, 5)
        grid.setColumnStretch(3, 1)
        layout.addWidget(filters)

        # Keep progress at eye level and preserve the completed state so fast
        # cached operations do not appear to do nothing.
        activity = QWidget(self)
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(4, 0, 4, 0)
        activity_layout.setSpacing(3)
        self.activity_label = QLabel("Ready — load events for the selected dates.", self)
        self.activity_label.setTextFormat(Qt.PlainText)
        self.activity_label.setWordWrap(True)
        activity_layout.addWidget(self.activity_label)
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setMinimumHeight(18)
        self.progress_detail = QLabel("", self)
        self.progress_detail.setMinimumWidth(45)
        self.progress_detail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_detail)
        progress_row.addWidget(self.cancel_button)
        activity_layout.addLayout(progress_row)
        layout.addWidget(activity)

        self.splitter = QSplitter(Qt.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        catalog_panel = QGroupBox("1. Choose a burst", self)
        catalog_panel.setObjectName("DownloaderSection")
        catalog_layout = QVBoxLayout(catalog_panel)
        catalog_layout.setContentsMargins(6, 6, 6, 6)
        self.catalog_table = _table(
            ["Start (UTC)", "End (UTC)", "Type", "Stations", "Observatories / remarks"], self,
        )
        self.catalog_table.setSortingEnabled(True)
        self.catalog_table.itemSelectionChanged.connect(self._event_selected)
        self.catalog_table.itemDoubleClicked.connect(lambda _item: self.find_fits())
        self.catalog_table.setMinimumHeight(80)
        catalog_layout.addWidget(self.catalog_table)
        self.catalog_status = QLabel("Choose a UTC date range and load the event list.", self)
        self.catalog_status.setWordWrap(True)
        self.catalog_status.setObjectName("DownloaderStatusLabel")
        catalog_layout.addWidget(self.catalog_status)
        self.splitter.addWidget(catalog_panel)

        fits_group = QGroupBox("2. Preview or import FITS", self)
        fits_group.setObjectName("DownloaderSection")
        fits_layout = QVBoxLayout(fits_group)
        fits_layout.setContentsMargins(6, 6, 6, 6)
        fits_layout.setSpacing(5)
        self.selection_label = QLabel("Select a burst above, then find the FITS files covering it.", self)
        self.selection_label.setWordWrap(True)
        fits_layout.addWidget(self.selection_label)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Station:"))
        self.fits_station_combo = QComboBox(self)
        self.fits_station_combo.setEditable(True)
        self.fits_station_combo.setMinimumContentsLength(16)
        self.fits_station_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.fits_station_combo.setToolTip("Search all reported stations, select one, or enter an archive station name.")
        self.fits_station_combo.addItem("Choose a station", "")
        controls.addWidget(self.fits_station_combo, 1)
        controls.addWidget(QLabel("Extra time:"))
        self.padding_spin = QSpinBox(self)
        self.padding_spin.setRange(0, 120)
        self.padding_spin.setValue(2)
        self.padding_spin.setSuffix(" min")
        self.padding_spin.setToolTip("Extra time on both sides of the burst. Search includes overlapping 15-minute FITS files.")
        controls.addWidget(self.padding_spin)
        self.find_button = QPushButton("Find FITS", self)
        self.find_button.setObjectName("PrimaryDownloaderButton")
        self.find_button.clicked.connect(self.find_fits)
        controls.addWidget(self.find_button)
        fits_layout.addLayout(controls)
        self.fits_table = _table(["", "Station", "Start (UTC)", "Focus", "Filename"], self)
        self.fits_table.setMinimumHeight(80)
        self.fits_table.itemChanged.connect(self._sync_actions)
        fits_layout.addWidget(self.fits_table, 1)
        actions = QHBoxLayout()
        self.select_all_button = QPushButton("Select All", self)
        self.clear_button = QPushButton("Clear Selection", self)
        self.download_button = QPushButton("Download", self)
        self.preview_button = QPushButton("Preview", self)
        self.preview_button.setToolTip("Preview the checked FITS files as spectrograms; cached files are reused.")
        self.import_button = QPushButton("Import", self)
        self.compare_button = QPushButton("Compare", self)
        self.select_all_button.clicked.connect(lambda: self._check_files(Qt.Checked))
        self.clear_button.clicked.connect(lambda: self._check_files(Qt.Unchecked))
        self.download_button.clicked.connect(self.download_selected)
        self.preview_button.clicked.connect(self.preview_selected)
        self.import_button.clicked.connect(self.import_selected)
        self.compare_button.clicked.connect(self.compare_selected)
        for button in (self.select_all_button, self.clear_button):
            actions.addWidget(button)
        actions.addStretch(1)
        for button in (self.preview_button, self.download_button, self.import_button, self.compare_button):
            actions.addWidget(button)
        fits_layout.addLayout(actions)
        self.selection_count = QLabel("No files selected", self)
        self.selection_count.setObjectName("DownloaderStatusLabel")
        self.fits_status = QLabel("Catalog times are UTC with minute precision. Files are matched by time and station.", self)
        self.fits_status.setWordWrap(True)
        self.fits_status.setObjectName("DownloaderStatusLabel")
        status_row = QHBoxLayout()
        status_row.addWidget(self.fits_status, 1)
        status_row.addWidget(self.selection_count)
        fits_layout.addLayout(status_row)
        self.splitter.addWidget(fits_group)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)
        self.type_filter.currentIndexChanged.connect(self.apply_filters)
        self.station_filter.textChanged.connect(self.apply_filters)
        self.min_stations.valueChanged.connect(self.apply_filters)
        self.text_filter.textChanged.connect(self.apply_filters)
        self.fits_station_combo.currentTextChanged.connect(self._clear_fits)
        self.padding_spin.valueChanged.connect(self._clear_fits)
        self._sync_actions()

    def date_time_state(self):
        return {"burst_start_date": self.start_date_edit.date(), "burst_end_date": self.end_date_edit.date()}

    def restore_date_time_state(self, state):
        for key, edit in (("burst_start_date", self.start_date_edit), ("burst_end_date", self.end_date_edit)):
            value = state.get(key)
            if isinstance(value, QDate) and value.isValid():
                edit.setDate(value)

    def is_busy(self):
        return self._thread is not None or bool(self._download_tasks)

    def request_cancel(self):
        if self._worker is not None:
            self._worker.request_cancel()
            self._cancel_requested = True
            self.cancel_button.setEnabled(False)
            self.activity_label.setText("Cancelling — waiting for the current operation to finish…")

    def reset_filters(self):
        for widget in (self.type_filter, self.station_filter, self.min_stations, self.text_filter):
            widget.blockSignals(True)
        self.type_filter.setCurrentIndex(0)
        self.station_filter.clear()
        self.min_stations.setValue(0)
        self.text_filter.clear()
        for widget in (self.type_filter, self.station_filter, self.min_stations, self.text_filter):
            widget.blockSignals(False)
        self.apply_filters()

    def _sync_actions(self, *_args):
        busy = self.is_busy()
        for widget in (self.start_date_edit, self.end_date_edit, self.load_button,
                       self.type_filter, self.station_filter, self.min_stations,
                       self.text_filter, self.catalog_table, self.fits_station_combo,
                       self.padding_spin, self.fits_table, self.reset_filters_button):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(self._worker is not None and not self._cancel_requested)
        self.find_button.setEnabled(not busy and self.selected_event() is not None)
        has_files = self.fits_table.rowCount() > 0
        for button in (self.select_all_button, self.clear_button):
            button.setEnabled(not busy and has_files)
        selected = len(self.selected_files())
        self.selection_count.setText(f"{selected} of {self.fits_table.rowCount()} files selected")
        for button in (self.preview_button, self.download_button, self.import_button, self.compare_button):
            button.setEnabled(not busy and selected > 0)

    def _dates_changed(self, *_args):
        self._events = []
        self._warnings = []
        self.catalog_table.setRowCount(0)
        self._clear_fits()
        self.catalog_status.setText("Date range changed. Load Events to refresh the catalog.")
        self._reset_progress("Dates changed — load events to refresh the catalog.")
        self._sync_actions()

    def _clear_fits(self, *_args):
        self.fits_table.setRowCount(0)
        self.fits_status.setText("Find FITS to load files for the selected burst and station.")
        self._sync_actions()

    def _start_worker(self, worker, receiver):
        self._job_kind = ("catalog" if receiver == self.display_catalog else
                          "preview" if receiver == self.display_preview else "fits")
        self._job_outcome = ""
        self._cancel_requested = False
        self._worker = worker
        self._thread = QThread(self)
        try:
            worker.moveToThread(self._thread)
        except Exception as exc:
            self._thread.deleteLater()
            worker.deleteLater()
            self._thread = self._worker = None
            receiver({"error": f"Could not start the background operation: {exc}"})
            self._finish_progress()
            self._sync_actions()
            return
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._progress)
        worker.finished.connect(receiver)
        worker.finished.connect(self._thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._worker_finished)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("%p%")
        self.progress_detail.setText("…")
        self.activity_label.setText({"catalog": "Loading the event catalog…", "fits": "Searching the FITS archive…",
                                     "preview": "Preparing spectrogram preview…"}[self._job_kind])
        self._sync_actions()
        self.busy_changed.emit(True)
        self._thread.start()

    @Slot()
    def _worker_finished(self):
        self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self._finish_progress()
        self._sync_actions()
        self.busy_changed.emit(False)
        self.idle.emit()

    @Slot(int, int, str)
    def _progress(self, current, total, message):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat("%p%")
        self.progress_detail.setText(f"{min(100, int(100 * current / total))}%" if total else "…")
        if not self._cancel_requested:
            self.activity_label.setText(message)

    def _reset_progress(self, message):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.progress_detail.clear()
        self.activity_label.setText(message)

    def _finish_progress(self):
        self.progress_bar.setRange(0, 100)
        if self._job_outcome in {"error", "cancelled"}:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Cancelled" if self._job_outcome == "cancelled" else "Failed")
            self.progress_detail.setText("Cancelled" if self._job_outcome == "cancelled" else "Failed")
            label = self.catalog_status if self._job_kind == "catalog" else self.fits_status
            self.activity_label.setText(label.text())
        else:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("%p%")
            self.progress_detail.setText("100%")
            self.activity_label.setText({"catalog": "Event catalog loaded.", "fits": "FITS search complete.",
                                         "preview": "Spectrogram preview ready."}.get(self._job_kind, "Complete."))

    def load_events(self):
        if self.is_busy():
            return
        start, end = self.start_date_edit.date(), self.end_date_edit.date()
        if end < start:
            QMessageBox.warning(self, "Invalid Date Range", "The end date must be on or after the start date.")
            return
        self._events = []
        self._warnings = []
        self.catalog_table.setRowCount(0)
        self._clear_fits()
        self.catalog_status.setText("Loading the source catalog…")
        self._start_worker(BurstCatalogWorker(start.toPython(), end.toPython()), self.display_catalog)

    @Slot(object)
    def display_catalog(self, payload):
        if payload.get("cancelled"):
            self._job_outcome = "cancelled"
            self.catalog_status.setText("Catalog loading cancelled.")
            return
        if payload.get("error"):
            self._job_outcome = "error"
            self.catalog_status.setText("Could not load the burst list: " + payload["error"])
            return
        self._events = list(payload.get("events") or [])
        self._warnings = list(payload.get("warnings") or [])
        selected_type = self.type_filter.currentData()
        self.type_filter.blockSignals(True)
        self.type_filter.clear()
        self.type_filter.addItem("All types", "")
        families = {"I", "II", "III", "IV", "V", "CTM", "U", "J", "RFI"}
        for name in sorted(families | {event.burst_type for event in self._events if event.burst_type}):
            self.type_filter.addItem(name, name)
        self.type_filter.setCurrentIndex(max(0, self.type_filter.findData(selected_type)))
        self.type_filter.blockSignals(False)
        self.apply_filters()

    def apply_filters(self, *_args):
        events = filter_burst_events(
            self._events, burst_type=self.type_filter.currentData() or "",
            station=self.station_filter.text(), text=self.text_filter.text(),
        )
        events = [event for event in events if len(event.stations) >= self.min_stations.value()]
        self.catalog_table.setSortingEnabled(False)
        self.catalog_table.setRowCount(0)
        for event in events:
            row = self.catalog_table.rowCount()
            self.catalog_table.insertRow(row)
            detail = ", ".join(event.stations)
            if event.remarks:
                detail += (" — " if detail else "") + event.remarks
            if event.frequency_min_mhz is not None or event.frequency_max_mhz is not None:
                detail += f" [{event.frequency_min_mhz if event.frequency_min_mhz is not None else '?'}–{event.frequency_max_mhz if event.frequency_max_mhz is not None else '?'} MHz]"
            values = [event.start_utc.strftime("%Y-%m-%d %H:%M:%S"),
                      event.end_utc.strftime("%Y-%m-%d %H:%M:%S"), event.burst_type or "Unknown",
                      len(event.stations), detail]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, value)
                item.setData(Qt.UserRole, event)
                item.setToolTip(event.raw_line + "\n\n" + event.source_url)
                self.catalog_table.setItem(row, column, item)
        self.catalog_table.setSortingEnabled(True)
        self.catalog_table.sortItems(0, Qt.AscendingOrder)
        status = f"{len(events)} of {len(self._events)} events match the filters."
        if not self._events:
            status = "No burst events were available for this date range."
        if self._warnings:
            status += " " + " ".join(self._warnings[:2])
            if len(self._warnings) > 2:
                status += f" (+{len(self._warnings) - 2} more; hover for details.)"
        self.catalog_status.setText(status)
        self.catalog_status.setToolTip("\n".join(self._warnings))
        self._event_selected()

    def selected_event(self):
        rows = self.catalog_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.catalog_table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _event_selected(self):
        event = self.selected_event()
        self.fits_station_combo.blockSignals(True)
        self.fits_station_combo.clear()
        if event is None:
            self.selection_label.setText("Select a burst above, then find the FITS files covering it.")
            self.fits_station_combo.addItem("Choose a station", "")
        else:
            self.selection_label.setText(
                f"{event.burst_type or 'Unknown type'} · {event.start_utc:%Y-%m-%d %H:%M:%S} – "
                f"{event.end_utc:%Y-%m-%d %H:%M:%S} UTC"
            )
            if event.stations:
                self.fits_station_combo.addItem("All reported stations", "__reported__")
            else:
                self.fits_station_combo.addItem("Choose a station", "")
            stations = list(dict.fromkeys([*event.stations, *self._known_stations]))
            for station in stations:
                self.fits_station_combo.addItem(station, station)
        self.fits_station_combo.blockSignals(False)
        self._clear_fits()

    def find_fits(self):
        if self.is_busy():
            return
        event = self.selected_event()
        if event is None:
            return
        text = self.fits_station_combo.currentText().strip()
        index = self.fits_station_combo.findText(text, Qt.MatchExactly)
        value = self.fits_station_combo.itemData(index) if index >= 0 else text
        stations = list(event.stations) if value == "__reported__" else ([value] if value else [])
        if not stations:
            QMessageBox.warning(self, "No Station", "Choose or enter a station to search for this burst.")
            return
        self._clear_fits()
        self.fits_status.setText("Searching archive listings for files covering the burst…")
        self._start_worker(BurstFitsWorker(event, stations, self.padding_spin.value()), self.display_fits)

    @Slot(object)
    def display_fits(self, payload):
        if payload.get("cancelled"):
            self._job_outcome = "cancelled"
            self.fits_status.setText("FITS search cancelled.")
            return
        if payload.get("error"):
            self._job_outcome = "error"
            self.fits_status.setText("Could not find FITS files: " + payload["error"])
            return
        candidates = list(payload.get("candidates") or [])
        self.fits_table.blockSignals(True)
        self.fits_table.setRowCount(0)
        for candidate in candidates:
            row = self.fits_table.rowCount()
            self.fits_table.insertRow(row)
            values = ["", candidate.station, candidate.observed_at_utc.strftime("%Y-%m-%d %H:%M:%S"),
                      candidate.receiver_id, candidate.filename]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(candidate.url)
                if column == 0:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked)
                    item.setData(Qt.UserRole, candidate)
                self.fits_table.setItem(row, column, item)
        self.fits_table.blockSignals(False)
        status = f"Found {len(candidates)} FITS file(s) covering the selected window."
        if not candidates:
            status = "No matching FITS files. Try another station or increase the padding."
        warnings = list(payload.get("warnings") or [])
        if warnings:
            status += " " + " ".join(warnings[:2])
        self.fits_status.setText(status)
        self.fits_status.setToolTip("\n".join(warnings))
        self._sync_actions()

    def _check_files(self, state):
        for row in range(self.fits_table.rowCount()):
            self.fits_table.item(row, 0).setCheckState(state)

    def selected_files(self):
        selected = []
        for row in range(self.fits_table.rowCount()):
            item = self.fits_table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                selected.append(item.data(Qt.UserRole))
        return selected

    def preview_selected(self):
        selected = self.selected_files()
        if not selected or self.is_busy():
            return
        self.fits_status.setText(f"Preparing a preview of {len(selected)} selected FITS file(s)…")
        self._start_worker(BurstPreviewWorker(selected), self.display_preview)

    @Slot(object)
    def display_preview(self, payload):
        from src.ui.downloads.callisto_downloader import PreviewWindow

        self.cache_changed.emit()
        if payload.get("cancelled") or self._cancel_requested:
            self._job_outcome = "cancelled"
            self.fits_status.setText("Preview preparation cancelled.")
            return
        panels = list(payload.get("panels") or [])
        if payload.get("error") or not panels:
            self._job_outcome = "error"
            self.fits_status.setText("Could not preview FITS files: " + str(payload.get("error") or "No preview available."))
            return
        try:
            title = panels[0].get("title", "") if len(panels) == 1 else f"{len(panels)} panels"
            preview = PreviewWindow(panels, title, parent=self.window())
            self._preview_windows.append(preview)
            preview.destroyed.connect(lambda *_args, ref=preview: self._forget_preview(ref))
            preview.show()
        except Exception as exc:
            self._job_outcome = "error"
            self.fits_status.setText(f"Could not display the preview: {exc}")
            return
        errors = list(payload.get("errors") or [])
        status = f"Preview opened · {len(panels)} panel(s) · {payload.get('cached_count', 0)} file(s) reused from cache."
        if errors:
            status += f" {len(errors)} warning(s); hover for details."
        self.fits_status.setText(status)
        self.fits_status.setToolTip("\n".join(errors))

    def _forget_preview(self, preview):
        if preview in self._preview_windows:
            self._preview_windows.remove(preview)

    def import_selected(self):
        selected = self.selected_files()
        if selected and not self.is_busy():
            self.import_request.emit([candidate.url for candidate in selected])

    def compare_selected(self):
        selected = self.selected_files()
        if selected and not self.is_busy():
            self.comparison_request.emit([candidate.url for candidate in selected])

    def download_selected(self):
        from src.ui.downloads.callisto_downloader import EventDownloadTask

        selected = self.selected_files()
        if not selected or self.is_busy():
            return
        output_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not output_dir:
            return
        self._download_done = 0
        self._download_failures = []
        self._download_progress = {candidate.filename: 0.0 for candidate in selected}
        self._job_kind = "download"
        self._job_outcome = ""
        self._cancel_requested = False
        self._download_tasks = [EventDownloadTask(candidate, os.path.join(output_dir, candidate.filename))
                                for candidate in selected]
        self.progress_bar.setRange(0, len(selected) * 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_detail.setText("0%")
        self.activity_label.setText(f"Downloading {len(selected)} FITS file(s)…")
        self.fits_status.setText("Downloading selected FITS files…")
        self._sync_actions()
        self.busy_changed.emit(True)
        for task in self._download_tasks:
            task.progress.connect(self._download_progress_changed)
            task.done.connect(self._download_finished)
            QThreadPool.globalInstance().start(task)

    @Slot(str, float, str)
    def _download_progress_changed(self, filename, fraction, message):
        if not self._download_tasks or filename not in self._download_progress:
            return
        self._download_progress[filename] = max(self._download_progress[filename], min(1.0, max(0.0, fraction)))
        self.progress_bar.setValue(round(1000 * sum(self._download_progress.values())))
        self.progress_detail.setText(f"{int(100 * sum(self._download_progress.values()) / len(self._download_tasks))}%")
        self.activity_label.setText(f"{message} · {self._download_done}/{len(self._download_tasks)} files complete")

    @Slot(str, str, bool, str)
    def _download_finished(self, filename, _path, success, error):
        self._download_done += 1
        if not success:
            self._download_failures.append(f"{filename}: {error}")
        self._download_progress_changed(filename, 1.0, f"{'Saved' if success else 'Failed'} {filename}")
        if self._download_done < len(self._download_tasks):
            return
        total = len(self._download_tasks)
        self._download_tasks = []
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_detail.setText("100%")
        status = f"Downloaded {total - len(self._download_failures)} of {total} selected FITS file(s)."
        if self._download_failures:
            status += f" {len(self._download_failures)} failed."
        self.activity_label.setText(status)
        self.fits_status.setText(status)
        self.fits_status.setToolTip("\n".join(self._download_failures))
        self._sync_actions()
        self.busy_changed.emit(False)
        self.cache_changed.emit()
        if self._download_failures:
            QMessageBox.warning(self, "Download Complete", status + "\n\n" + "\n".join(self._download_failures[:8]))
        self.idle.emit()
