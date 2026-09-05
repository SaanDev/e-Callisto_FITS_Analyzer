"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QStandardPaths, QThread, QTime, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
)

from src.Backend.swaves import (
    DEFAULT_PAD_SECONDS,
    FIRST_ARCHIVE_DAY,
    SPACECRAFT_AHEAD,
    SPACECRAFT_BEHIND,
    SPACECRAFT_LABELS,
    SPACECRAFT_ORDER,
    STEREO_B_LAST_CONTACT,
    SwavesArchiveError,
    SwavesCancelled,
    SwavesNotFoundError,
    load_swaves_window,
)
from src.UI.gui_shared import fit_window_to_screen


def _default_swaves_cache_dir() -> Path:
    app_data = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    if not app_data:
        app_data = tempfile.gettempdir()
    out = Path(app_data) / "swaves_cache"
    out.mkdir(parents=True, exist_ok=True)
    return out


class SwavesLoadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    not_found = Signal(str)
    cancelled = Signal()
    progress_text = Signal(str)
    progress_value = Signal(int)

    def __init__(self, start_utc, end_utc, spacecraft, cache_dir: str, base_utc=None):
        super().__init__()
        self.start_utc = start_utc
        self.end_utc = end_utc
        self.spacecraft = spacecraft
        self.cache_dir = str(cache_dir)
        self.base_utc = base_utc
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def _progress(self, message: str, percent: int):
        self.progress_text.emit(str(message))
        self.progress_value.emit(int(percent))

    @Slot()
    def run(self):
        try:
            payload = load_swaves_window(
                self.start_utc,
                self.end_utc,
                self.spacecraft,
                self.cache_dir,
                base_utc=self.base_utc,
                progress_cb=self._progress,
                cancel_cb=self._cancel.is_set,
            )
            self.finished.emit(payload)
        except SwavesCancelled:
            self.cancelled.emit()
        except SwavesNotFoundError as exc:
            self.not_found.emit(str(exc))
        except SwavesArchiveError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(str(exc))


class SwavesDownloaderApp(QDialog):
    """Date/time/spacecraft picker for the STEREO/SWAVES dynamic spectrum."""

    swaves_ready = Signal(object)

    def __init__(self, parent=None, *, callisto_window=None, callisto_base_utc=None):
        super().__init__(parent)
        self._cache_dir = _default_swaves_cache_dir()
        self._busy = False
        self._callisto_window = callisto_window
        self._callisto_base_utc = callisto_base_utc
        self._payload = None
        self._load_thread: QThread | None = None
        self._load_worker: SwavesLoadWorker | None = None

        self.setWindowTitle("STEREO/SWAVES Radio Spectrograph")
        fit_window_to_screen(self, 640, 460)

        self.init_ui()
        self._apply_default_window()
        self._sync_action_state()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        query_group = QGroupBox("Observation Window (UTC)")
        form = QFormLayout()

        self.date_edit = QDateEdit(QDate.currentDate().addDays(-1))
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumDate(QDate(FIRST_ARCHIVE_DAY.year, FIRST_ARCHIVE_DAY.month, FIRST_ARCHIVE_DAY.day))
        self.date_edit.dateChanged.connect(self._on_date_changed)

        self.time_edit = QTimeEdit(QTime(0, 0, 0))
        self.time_edit.setDisplayFormat("HH:mm:ss")
        self.time_edit.timeChanged.connect(self._update_summary)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 24.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setValue(4.0)
        self.duration_spin.setSuffix(" h")
        self.duration_spin.valueChanged.connect(self._update_summary)

        self.spacecraft_combo = QComboBox()
        for key in SPACECRAFT_ORDER:
            self.spacecraft_combo.addItem(SPACECRAFT_LABELS[key], key)
        self.spacecraft_combo.setCurrentIndex(0)

        self.pad_spin = QSpinBox()
        self.pad_spin.setRange(0, 720)
        self.pad_spin.setValue(int(DEFAULT_PAD_SECONDS // 60))
        self.pad_spin.setSuffix(" min")
        self.pad_spin.setToolTip(
            "Extra context added on each side when the window is taken from the loaded CALLISTO spectrum.\n"
            "A burst leaving the CALLISTO band keeps drifting for hours before it reaches the SWAVES range."
        )

        form.addRow("Start date:", self.date_edit)
        form.addRow("Start time:", self.time_edit)
        form.addRow("Duration:", self.duration_spin)
        form.addRow("Spacecraft:", self.spacecraft_combo)
        form.addRow("Sync padding:", self.pad_spin)
        query_group.setLayout(form)

        self.use_callisto_btn = QPushButton("Use CALLISTO Window")
        self.use_callisto_btn.setToolTip(
            "Copy the time range of the loaded CALLISTO spectrum, widened by the sync padding."
        )
        self.use_callisto_btn.clicked.connect(self.use_callisto_window)

        self.plot_button = QPushButton("Download and Plot")
        self.plot_button.setDefault(True)
        self.plot_button.clicked.connect(self.download_and_plot)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_load)

        action_row = QHBoxLayout()
        action_row.addWidget(self.use_callisto_btn)
        action_row.addStretch(1)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.plot_button)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        self.close_box = QDialogButtonBox(QDialogButtonBox.Close)
        self.close_box.rejected.connect(self.reject)

        layout.addWidget(query_group)
        layout.addWidget(self.summary_label)
        layout.addLayout(action_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.close_box)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def set_callisto_context(self, window, base_utc=None) -> None:
        """Give the dialog the analyzer's (start_utc, end_utc) and x=0 epoch.

        ``base_utc`` is the UTC instant the CALLISTO x axis counts from, and
        is what the payload's ``x_seconds`` are measured against so both
        panels land on the same coordinate frame.
        """
        self._callisto_window = window
        self._callisto_base_utc = base_utc
        self._sync_action_state()

    # Backwards-compatible alias.
    def set_callisto_window(self, window) -> None:
        self.set_callisto_context(window, self._callisto_base_utc)

    def _apply_default_window(self):
        if self._callisto_window:
            self.use_callisto_window()
        self._sync_behind_availability()
        self._update_summary()

    def selected_spacecraft(self) -> str:
        data = self.spacecraft_combo.currentData()
        return str(data or SPACECRAFT_AHEAD)

    def selected_start_utc(self) -> datetime:
        day = self.date_edit.date().toPython()
        moment = self.time_edit.time().toPython()
        return datetime(
            day.year,
            day.month,
            day.day,
            moment.hour,
            moment.minute,
            moment.second,
            tzinfo=timezone.utc,
        )

    def selected_end_utc(self) -> datetime:
        return self.selected_start_utc() + timedelta(hours=float(self.duration_spin.value()))

    def pad_seconds(self) -> int:
        return int(self.pad_spin.value()) * 60

    def _behind_available(self, day: date) -> bool:
        return day <= STEREO_B_LAST_CONTACT

    def _on_date_changed(self, *_args):
        self._sync_behind_availability()
        self._update_summary()

    def _sync_behind_availability(self):
        day = self.date_edit.date().toPython()
        available = self._behind_available(day)
        index = self.spacecraft_combo.findData(SPACECRAFT_BEHIND)
        if index < 0:
            return

        model = self.spacecraft_combo.model()
        item = model.item(index) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(bool(available))
        tip = (
            ""
            if available
            else f"Contact with STEREO-B was lost on {STEREO_B_LAST_CONTACT.isoformat()}; later files carry fill only."
        )
        self.spacecraft_combo.setItemData(index, tip, Qt.ToolTipRole)

        if not available and self.spacecraft_combo.currentIndex() == index:
            ahead_index = self.spacecraft_combo.findData(SPACECRAFT_AHEAD)
            if ahead_index >= 0:
                self.spacecraft_combo.setCurrentIndex(ahead_index)

    def _update_summary(self, *_args):
        start = self.selected_start_utc()
        end = self.selected_end_utc()
        days = (end.date() - start.date()).days + 1
        note = f"Spans {days} archive day files." if days > 1 else "Single archive day file."
        self.summary_label.setText(
            f"{start:%Y-%m-%d %H:%M:%S} → {end:%Y-%m-%d %H:%M:%S} UTC  ({note})"
        )

    def _set_busy(self, busy: bool, *, indeterminate: bool = False):
        self._busy = bool(busy)
        if busy:
            self.progress_bar.setVisible(True)
            if indeterminate:
                self.progress_bar.setRange(0, 0)
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
        else:
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        self._sync_action_state()

    def _sync_action_state(self):
        self.plot_button.setEnabled(not self._busy)
        self.date_edit.setEnabled(not self._busy)
        self.time_edit.setEnabled(not self._busy)
        self.duration_spin.setEnabled(not self._busy)
        self.spacecraft_combo.setEnabled(not self._busy)
        self.pad_spin.setEnabled(not self._busy)
        self.cancel_button.setEnabled(self._busy)
        self.use_callisto_btn.setEnabled((not self._busy) and bool(self._callisto_window))

    def _set_status_text(self, text: str):
        self.status_label.setText(str(text or ""))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def use_callisto_window(self):
        if not self._callisto_window:
            QMessageBox.information(
                self,
                "SWAVES",
                "Load a CALLISTO FITS file first so its time window can be copied.",
            )
            return

        start, end = self._callisto_window
        pad = timedelta(seconds=self.pad_seconds())
        padded_start = start - pad
        padded_end = end + pad

        self.date_edit.setDate(QDate(padded_start.year, padded_start.month, padded_start.day))
        self.time_edit.setTime(QTime(padded_start.hour, padded_start.minute, padded_start.second))
        hours = max(0.1, (padded_end - padded_start).total_seconds() / 3600.0)
        self.duration_spin.setValue(min(24.0, hours))
        self._update_summary()
        self._set_status_text(
            f"Window copied from the loaded CALLISTO spectrum with ±{self.pad_spin.value()} min of context."
        )

    def download_and_plot(self):
        if self._load_thread is not None and self._load_thread.isRunning():
            QMessageBox.information(self, "SWAVES", "SWAVES data are already being loaded.")
            return

        start = self.selected_start_utc()
        end = self.selected_end_utc()
        if start.date() < FIRST_ARCHIVE_DAY:
            QMessageBox.warning(
                self,
                "SWAVES",
                f"The SWAVES archive starts on {FIRST_ARCHIVE_DAY.isoformat()}.",
            )
            return

        craft = self.selected_spacecraft()
        if craft == SPACECRAFT_BEHIND and not self._behind_available(start.date()):
            QMessageBox.warning(
                self,
                "SWAVES",
                f"STEREO-B stopped returning data on {STEREO_B_LAST_CONTACT.isoformat()}.",
            )
            return

        base_utc = self._callisto_base_utc or start

        self._set_status_text("Contacting the SPDF SWAVES archive...")
        self._set_busy(True)

        self._load_thread = QThread(self)
        self._load_worker = SwavesLoadWorker(start, end, craft, str(self._cache_dir), base_utc=base_utc)
        self._load_worker.moveToThread(self._load_thread)

        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.progress_text.connect(self._set_status_text)
        self._load_worker.progress_value.connect(self.progress_bar.setValue)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.not_found.connect(self._on_load_not_found)
        self._load_worker.cancelled.connect(self._on_load_cancelled)

        for signal in (
            self._load_worker.finished,
            self._load_worker.failed,
            self._load_worker.not_found,
            self._load_worker.cancelled,
        ):
            signal.connect(self._load_thread.quit)
            signal.connect(self._load_worker.deleteLater)

        self._load_thread.finished.connect(self._cleanup_load_worker)
        self._load_thread.start()

    def cancel_load(self):
        worker = self._load_worker
        if worker is not None:
            worker.cancel()
            self._set_status_text("Cancelling...")

    def is_operation_running(self) -> bool:
        return bool(self._load_thread is not None and self._load_thread.isRunning())

    def _cleanup_load_worker(self):
        if self._load_thread is not None:
            try:
                self._load_thread.deleteLater()
            except Exception:
                pass
        self._load_thread = None
        self._load_worker = None

    @Slot(object)
    def _on_load_finished(self, payload):
        self._set_busy(False)
        self._payload = payload
        rows, columns = payload.intensity_db.shape
        self._set_status_text(
            f"Loaded {payload.spacecraft_label}: {columns} one-minute samples, "
            f"{rows} log-frequency rows, from {len(payload.source_files)} archive file(s)."
        )
        self.swaves_ready.emit(payload)

    @Slot(str)
    def _on_load_failed(self, message: str):
        self._set_busy(False)
        self._set_status_text("")
        QMessageBox.critical(self, "SWAVES Error", str(message or "Unknown SWAVES archive error."))

    @Slot(str)
    def _on_load_not_found(self, message: str):
        self._set_busy(False)
        self._set_status_text("")
        QMessageBox.information(self, "SWAVES", str(message or "No SWAVES data were found."))

    @Slot()
    def _on_load_cancelled(self):
        self._set_busy(False)
        self._set_status_text("SWAVES load cancelled.")

    def closeEvent(self, event):
        if self.is_operation_running():
            self.cancel_load()
        super().closeEvent(event)
