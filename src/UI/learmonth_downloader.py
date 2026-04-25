"""
e-CALLISTO FITS Analyzer
Version 2.4.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QStandardPaths, QThread, QTime, Qt, Signal, QObject, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QDateEdit,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
)

from src.Backend.learmonth import (
    LearmonthArchiveError,
    LearmonthChunk,
    LearmonthNotFoundError,
    download_learmonth_day,
    learmonth_fit_filename,
    list_learmonth_chunks,
    resolve_learmonth_url,
    write_learmonth_chunk_fit,
)


def _default_learmonth_cache_dir() -> Path:
    app_data = str(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "").strip()
    if not app_data:
        app_data = tempfile.gettempdir()
    out = Path(app_data) / "learmonth_cache"
    out.mkdir(parents=True, exist_ok=True)
    return out


class LearmonthLoadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    not_found = Signal(str)

    def __init__(self, day, cache_dir: str):
        super().__init__()
        self.day = day
        self.cache_dir = str(cache_dir or "")

    @Slot()
    def run(self):
        try:
            local_path = download_learmonth_day(self.day, self.cache_dir)
            chunks = list_learmonth_chunks(local_path)
            payload = {
                "local_path": local_path,
                "chunks": chunks,
                "url": resolve_learmonth_url(self.day),
                "filename": os.path.basename(local_path),
                "size_bytes": os.path.getsize(local_path),
            }
            self.finished.emit(payload)
        except LearmonthNotFoundError as exc:
            self.not_found.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class LearmonthConvertWorker(QObject):
    progress_text = Signal(str)
    progress_range = Signal(int, int)
    progress_value = Signal(int)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, day_path: str, chunks: list[LearmonthChunk], output_dir: str):
        super().__init__()
        self.day_path = str(day_path or "")
        self.chunks = list(chunks or [])
        self.output_dir = str(output_dir or "")

    @Slot()
    def run(self):
        if not self.day_path or not os.path.isfile(self.day_path):
            self.failed.emit("Learmonth source file is missing.")
            return
        if not self.chunks:
            self.failed.emit("No Learmonth chunks were selected.")
            return
        if not self.output_dir:
            self.failed.emit("No output directory was provided for Learmonth FIT conversion.")
            return

        written: list[str] = []
        self.progress_text.emit("Converting selected Learmonth chunks...")
        self.progress_range.emit(0, len(self.chunks))
        self.progress_value.emit(0)

        try:
            os.makedirs(self.output_dir, exist_ok=True)
            for index, chunk in enumerate(self.chunks, start=1):
                out_path = os.path.join(self.output_dir, learmonth_fit_filename(chunk))
                written.append(write_learmonth_chunk_fit(self.day_path, chunk, out_path))
                self.progress_value.emit(index)
        except Exception as exc:
            self.failed.emit(f"Could not convert Learmonth chunks to FIT:\n{exc}")
            return

        self.finished.emit(written)


class LearmonthDownloaderApp(QDialog):
    import_request = Signal(list)
    import_success = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache_dir = _default_learmonth_cache_dir()
        self._busy = False
        self._loaded_day_path: str | None = None
        self._loaded_chunks: list[LearmonthChunk] = []
        self._loaded_url: str = ""
        self._load_thread: QThread | None = None
        self._load_worker: LearmonthLoadWorker | None = None
        self._convert_thread: QThread | None = None
        self._convert_worker: LearmonthConvertWorker | None = None
        self._import_after_convert = False

        self.setWindowTitle("Learmonth Solar Radio Browser")
        self.resize(960, 640)

        self.init_ui()
        self._sync_action_state()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        query_group = QGroupBox("Observation Parameters")
        query_layout = QHBoxLayout()

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumWidth(140)

        self.time_edit = QTimeEdit(QTime.currentTime())
        self.time_edit.setDisplayFormat("HH:mm:ss")
        self.time_edit.setMinimumWidth(120)

        self.show_button = QPushButton("Show Available Data")
        self.show_button.clicked.connect(self.show_available_data)

        query_layout.addWidget(QLabel("Date:"))
        query_layout.addWidget(self.date_edit)
        query_layout.addWidget(QLabel("Time:"))
        query_layout.addWidget(self.time_edit)
        query_layout.addStretch(1)
        query_layout.addWidget(self.show_button)
        query_group.setLayout(query_layout)

        raw_group = QGroupBox("Daily Raw File")
        raw_layout = QVBoxLayout()
        self.raw_file_label = QLabel("Load a Learmonth date to inspect the daily .srs archive file.")
        self.raw_file_label.setWordWrap(True)
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.download_selected_day_file)
        raw_layout.addWidget(self.raw_file_label)
        raw_layout.addWidget(self.download_button, alignment=Qt.AlignLeft)
        raw_group.setLayout(raw_layout)

        chunk_group = QGroupBox("Available 15-Minute Chunks")
        chunk_layout = QVBoxLayout()
        self.chunk_list = QListWidget()
        self.chunk_list.itemChanged.connect(self._sync_action_state)
        self.chunk_list.currentRowChanged.connect(lambda _row: self._sync_action_state())
        chunk_layout.addWidget(self.chunk_list)

        action_layout = QHBoxLayout()
        self.select_all_button = QPushButton("Select All")
        self.deselect_all_button = QPushButton("Deselect All")
        self.convert_button = QPushButton("Convert to FIT")
        self.convert_import_button = QPushButton("Convert and Import")

        self.select_all_button.clicked.connect(self.select_all_chunks)
        self.deselect_all_button.clicked.connect(self.deselect_all_chunks)
        self.convert_button.clicked.connect(self.convert_selected_chunks)
        self.convert_import_button.clicked.connect(self.convert_and_import_selected)

        for button in (
            self.select_all_button,
            self.deselect_all_button,
            self.convert_button,
            self.convert_import_button,
        ):
            action_layout.addWidget(button)

        chunk_layout.addLayout(action_layout)
        chunk_group.setLayout(chunk_layout)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        layout.addWidget(query_group)
        layout.addWidget(raw_group)
        layout.addWidget(chunk_group)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        self.setLayout(layout)

    def _set_busy(self, busy: bool, *, indeterminate: bool = False):
        self._busy = bool(busy)
        if busy:
            self.progress_bar.setVisible(True)
            if indeterminate:
                self.progress_bar.setRange(0, 0)
            else:
                self.progress_bar.setRange(0, 1)
                self.progress_bar.setValue(0)
        else:
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
        self._sync_action_state()

    def _sync_action_state(self):
        has_day = bool(self._loaded_day_path and os.path.isfile(self._loaded_day_path))
        has_chunks = self.chunk_list.count() > 0
        checked_count = len(self._checked_chunks())

        self.show_button.setEnabled(not self._busy)
        self.download_button.setEnabled((not self._busy) and has_day)
        self.select_all_button.setEnabled((not self._busy) and has_chunks)
        self.deselect_all_button.setEnabled((not self._busy) and has_chunks)
        self.convert_button.setEnabled((not self._busy) and checked_count > 0)
        self.convert_import_button.setEnabled((not self._busy) and checked_count > 0)

    def _checked_chunks(self) -> list[LearmonthChunk]:
        checked: list[LearmonthChunk] = []
        for row in range(self.chunk_list.count()):
            item = self.chunk_list.item(row)
            if item.checkState() != Qt.Checked:
                continue
            chunk = item.data(Qt.UserRole)
            if isinstance(chunk, LearmonthChunk):
                checked.append(chunk)
        return checked

    def _set_status_text(self, text: str):
        self.status_label.setText(str(text or ""))

    def show_available_data(self):
        if self._load_thread is not None and self._load_thread.isRunning():
            QMessageBox.information(self, "Learmonth Loader", "Learmonth data are already being loaded.")
            return

        self.chunk_list.clear()
        self._loaded_day_path = None
        self._loaded_chunks = []
        self._loaded_url = ""
        self.raw_file_label.setText("Loading Learmonth archive metadata...")
        self._set_status_text("Downloading or reusing the cached Learmonth day file...")
        self._set_busy(True, indeterminate=True)

        self._load_thread = QThread(self)
        self._load_worker = LearmonthLoadWorker(self.date_edit.date().toPython(), str(self._cache_dir))
        self._load_worker.moveToThread(self._load_thread)

        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.not_found.connect(self._on_load_not_found)

        self._load_worker.finished.connect(self._load_thread.quit)
        self._load_worker.failed.connect(self._load_thread.quit)
        self._load_worker.not_found.connect(self._load_thread.quit)
        self._load_worker.finished.connect(self._load_worker.deleteLater)
        self._load_worker.failed.connect(self._load_worker.deleteLater)
        self._load_worker.not_found.connect(self._load_worker.deleteLater)
        self._load_thread.finished.connect(self._cleanup_load_worker)
        self._load_thread.start()

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

        self._loaded_day_path = str(payload.get("local_path", "") or "")
        self._loaded_url = str(payload.get("url", "") or "")
        self._loaded_chunks = list(payload.get("chunks", []) or [])
        filename = str(payload.get("filename", "") or os.path.basename(self._loaded_day_path or ""))
        size_bytes = int(payload.get("size_bytes", 0) or 0)

        self.raw_file_label.setText(
            f"Archive file: {filename}\n"
            f"URL: {self._loaded_url}\n"
            f"Cached file: {self._loaded_day_path}\n"
            f"Size: {self._format_size(size_bytes)}\n"
            f"Available chunks: {len(self._loaded_chunks)}"
        )

        self.chunk_list.clear()
        for chunk in self._loaded_chunks:
            item = QListWidgetItem(self._chunk_label(chunk))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, chunk)
            self.chunk_list.addItem(item)

        matched_index = self._highlight_matching_chunk()
        if matched_index is not None:
            self._set_status_text(
                f"Loaded {len(self._loaded_chunks)} Learmonth chunks. Highlighted chunk #{matched_index + 1} "
                f"for the selected time."
            )
        else:
            self._set_status_text(f"Loaded {len(self._loaded_chunks)} Learmonth chunks for the selected day.")

        self._sync_action_state()

    @Slot(str)
    def _on_load_failed(self, message: str):
        self._set_busy(False)
        self.raw_file_label.setText("Could not load Learmonth archive metadata.")
        self._set_status_text("")
        QMessageBox.critical(self, "Learmonth Error", str(message or "Unknown Learmonth archive error."))
        self._sync_action_state()

    @Slot(str)
    def _on_load_not_found(self, message: str):
        self._set_busy(False)
        self.raw_file_label.setText("No Learmonth raw archive file was found for the selected date.")
        self._set_status_text("")
        QMessageBox.information(self, "No Data Found", str(message or "No Learmonth data were found."))
        self._sync_action_state()

    def _highlight_matching_chunk(self) -> int | None:
        target = datetime.combine(self.date_edit.date().toPython(), self.time_edit.time().toPython())
        for index, chunk in enumerate(self._loaded_chunks):
            if chunk.start_dt <= target < chunk.end_dt:
                item = self.chunk_list.item(index)
                if item is not None:
                    item.setSelected(True)
                    self.chunk_list.setCurrentItem(item)
                    self.chunk_list.scrollToItem(item)
                return index
        return None

    def select_all_chunks(self):
        for row in range(self.chunk_list.count()):
            self.chunk_list.item(row).setCheckState(Qt.Checked)

    def deselect_all_chunks(self):
        for row in range(self.chunk_list.count()):
            self.chunk_list.item(row).setCheckState(Qt.Unchecked)

    def download_selected_day_file(self):
        if not self._loaded_day_path or not os.path.isfile(self._loaded_day_path):
            QMessageBox.warning(self, "No File", "Load a Learmonth day file before downloading.")
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not output_dir:
            return

        destination = os.path.join(output_dir, os.path.basename(self._loaded_day_path))
        try:
            shutil.copy2(self._loaded_day_path, destination)
        except Exception as exc:
            QMessageBox.critical(self, "Download Failed", f"Could not copy the Learmonth file:\n{exc}")
            return

        QMessageBox.information(self, "Download Complete", f"Learmonth raw file saved to:\n{destination}")

    def convert_selected_chunks(self):
        chunks = self._checked_chunks()
        if not chunks:
            QMessageBox.warning(self, "No Selection", "Please select at least one Learmonth chunk to convert.")
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not output_dir:
            return

        self._start_convert_worker(chunks, output_dir, import_after=False)

    def convert_and_import_selected(self):
        chunks = self._checked_chunks()
        if not chunks:
            QMessageBox.warning(self, "No Selection", "Please select at least one Learmonth chunk to convert.")
            return

        converted_root = self._cache_dir / "converted"
        converted_root.mkdir(parents=True, exist_ok=True)
        output_dir = tempfile.mkdtemp(prefix="learmonth_fit_", dir=str(converted_root))
        self._start_convert_worker(chunks, output_dir, import_after=True)

    def _start_convert_worker(self, chunks: list[LearmonthChunk], output_dir: str, *, import_after: bool):
        if self._convert_thread is not None and self._convert_thread.isRunning():
            QMessageBox.information(self, "Learmonth Conversion", "A Learmonth conversion is already in progress.")
            return
        if not self._loaded_day_path or not os.path.isfile(self._loaded_day_path):
            QMessageBox.warning(self, "No File", "Load a Learmonth day file before converting chunks.")
            return

        self._import_after_convert = bool(import_after)
        self._set_status_text("Converting selected Learmonth chunks to FIT...")
        self._set_busy(True, indeterminate=False)

        self._convert_thread = QThread(self)
        self._convert_worker = LearmonthConvertWorker(self._loaded_day_path, chunks, output_dir)
        self._convert_worker.moveToThread(self._convert_thread)

        self._convert_thread.started.connect(self._convert_worker.run)
        self._convert_worker.progress_text.connect(self._set_status_text)
        self._convert_worker.progress_range.connect(self._on_convert_progress_range)
        self._convert_worker.progress_value.connect(self._on_convert_progress_value)
        self._convert_worker.finished.connect(self._on_convert_finished)
        self._convert_worker.failed.connect(self._on_convert_failed)

        self._convert_worker.finished.connect(self._convert_thread.quit)
        self._convert_worker.failed.connect(self._convert_thread.quit)
        self._convert_worker.finished.connect(self._convert_worker.deleteLater)
        self._convert_worker.failed.connect(self._convert_worker.deleteLater)
        self._convert_thread.finished.connect(self._cleanup_convert_worker)
        self._convert_thread.start()

    def _cleanup_convert_worker(self):
        if self._convert_thread is not None:
            try:
                self._convert_thread.deleteLater()
            except Exception:
                pass
        self._convert_thread = None
        self._convert_worker = None

    @Slot(int, int)
    def _on_convert_progress_range(self, minimum: int, maximum: int):
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(int(minimum), max(int(maximum), int(minimum)))
        self.progress_bar.setValue(int(minimum))

    @Slot(int)
    def _on_convert_progress_value(self, value: int):
        self.progress_bar.setValue(int(value))

    @Slot(list)
    def _on_convert_finished(self, paths: list[str]):
        self._set_busy(False)
        self._set_status_text("")

        if self._import_after_convert:
            self.import_request.emit(list(paths or []))
            return

        QMessageBox.information(
            self,
            "Conversion Complete",
            f"Converted {len(paths or [])} Learmonth chunk(s) to FIT format.",
        )

    @Slot(str)
    def _on_convert_failed(self, message: str):
        self._set_busy(False)
        self._set_status_text("")
        QMessageBox.critical(self, "Conversion Failed", str(message or "Learmonth FIT conversion failed."))

    @staticmethod
    def _chunk_label(chunk: LearmonthChunk) -> str:
        suffix = " [partial]" if chunk.is_partial else ""
        return (
            f"{chunk.start_dt:%Y-%m-%d %H:%M:%S}  ->  {chunk.end_dt:%Y-%m-%d %H:%M:%S}"
            f"  |  {chunk.scan_count} scans{suffix}"
        )

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        value = float(max(0, int(size_bytes)))
        units = ["B", "KB", "MB", "GB"]
        unit_index = 0
        while value >= 1024.0 and unit_index < len(units) - 1:
            value /= 1024.0
            unit_index += 1
        if unit_index == 0:
            return f"{int(value)} {units[unit_index]}"
        return f"{value:.1f} {units[unit_index]}"
