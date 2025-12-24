"""
e-CALLISTO FITS Analyzer
Version 1.7.4
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

import os
import tempfile
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from astropy.io import fits

from PySide6.QtCore import (
    Qt, QDate, QThread, Signal, QObject, QRunnable, Slot,
    QThreadPool, QMetaObject, Q_ARG
)
from PySide6.QtWidgets import (
    QLabel, QPushButton, QComboBox, QVBoxLayout,
    QHBoxLayout, QDateEdit, QListWidget, QFileDialog, QMessageBox,
    QListWidgetItem, QProgressBar, QGroupBox, QDialog
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

BASE_URL = "http://soleil80.cs.technik.fhnw.ch/solarradio/data/2002-20yy_Callisto/"


# -----------------------------
# Worker: fetch list of files
# -----------------------------
class FetchWorker(QObject):
    finished = Signal(list)          # list[tuple[str, str]] OR [("__SERVER_UNREACHABLE__", msg)] etc.
    progressMax = Signal(int)        # set progress bar maximum
    progressStep = Signal(int)       # set progress bar value

    def __init__(self, date_py, station: str):
        super().__init__()
        self.date = date_py          # datetime.date
        self.station = station

    def _check_server(self) -> tuple[bool, str]:
        try:
            r = requests.head(BASE_URL, timeout=8, allow_redirects=True)
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}"
            return True, ""
        except Exception as e:
            return False, str(e)

    def _day_url(self) -> str:
        return f"{BASE_URL}{self.date.year}/{self.date.month:02}/{self.date.day:02}/"

    def run(self):
        results: list[tuple[str, str]] = []

        ok, msg = self._check_server()
        if not ok:
            results.append(("__SERVER_UNREACHABLE__", msg))
            self.finished.emit(results)
            return

        url_day = self._day_url()

        try:
            page = requests.get(url_day, timeout=15)
            if page.status_code >= 400:
                raise RuntimeError(f"HTTP {page.status_code} for {url_day}")

            soup = BeautifulSoup(page.text, "html.parser")

            # Collect all FITS links (compressed + uncompressed)
            hrefs = []
            for a in soup.find_all("a"):
                href = a.get("href", "")
                if not href:
                    continue
                href_low = href.lower()
                if href_low.endswith(".fit.gz") or href_low.endswith(".fit"):
                    hrefs.append(href)

            self.progressMax.emit(len(hrefs))

            station_key = self.station.strip().lower()

            for i, href in enumerate(hrefs, start=1):
                # Build absolute URL safely (some listings use relative links)
                abs_url = urljoin(url_day, href)
                filename = os.path.basename(href)

                # Filter by station for the whole day
                if station_key in filename.lower():
                    results.append((filename, abs_url))

                self.progressStep.emit(i)

        except Exception as e:
            results = [("__FETCH_ERROR__", str(e))]

        self.finished.emit(results)


# -----------------------------
# Download runnable
# -----------------------------
class DownloadTask(QObject, QRunnable):
    done = Signal(bool)

    def __init__(self, url: str, out_path: str):
        QObject.__init__(self)
        QRunnable.__init__(self)
        self.url = url
        self.out_path = out_path
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        try:
            with requests.get(self.url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(self.out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            f.write(chunk)
            self.done.emit(True)
        except Exception:
            self.done.emit(False)


# -----------------------------
# Preview window
# -----------------------------
class PreviewWindow(QDialog):
    def __init__(self, file_path: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"FITS Preview: {title}")
        self.setMinimumSize(900, 600)

        # Astropy can open .fit and .fit.gz paths directly
        with fits.open(file_path) as hdul:
            data = hdul[0].data

            # Many CALLISTO FITS have freq/time in extension 1
            freqs = None
            times = None
            if len(hdul) > 1 and hasattr(hdul[1], "data") and hdul[1].data is not None:
                cols = getattr(hdul[1].data, "columns", None)
                # Safer access for common schemas
                try:
                    if "frequency" in hdul[1].data.names:
                        freqs = hdul[1].data["frequency"][0]
                    if "time" in hdul[1].data.names:
                        times = hdul[1].data["time"][0]
                except Exception:
                    freqs = None
                    times = None

        fig = Figure()
        canvas = FigureCanvas(fig)
        ax = fig.add_subplot(111)

        # If we do not have time/freq arrays, plot without extent
        if freqs is not None and times is not None and len(freqs) > 1 and len(times) > 1:
            extent = [0, float(times[-1]), float(freqs[-1]), float(freqs[0])]
            im = ax.imshow(data, aspect="auto", extent=extent, cmap="inferno")
            ax.set_xlabel("Time [s]")
            ax.set_ylabel("Frequency [MHz]")
        else:
            im = ax.imshow(data, aspect="auto", cmap="inferno")
            ax.set_xlabel("Time bin")
            ax.set_ylabel("Frequency channel")

        fig.colorbar(im, ax=ax)

        layout = QVBoxLayout()
        layout.addWidget(canvas)
        self.setLayout(layout)

        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)


# -----------------------------
# Main downloader dialog
# -----------------------------
class CallistoDownloaderApp(QDialog):
    import_request = Signal(list)   # list of URLs
    import_success = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_files: list[str] = []
        self.file_url_map: dict[str, str] = {}
        self.threadpool = QThreadPool.globalInstance()

        self._fetch_thread: QThread | None = None
        self._fetch_worker: FetchWorker | None = None

        self._download_total = 0
        self._download_done = 0

        self.setWindowTitle("e-CALLISTO FITS Downloader")
        self.resize(900, 600)
        self.setStyleSheet("QWidget { font-family: Arial, sans-serif; font-size: 13px; }")

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- Parameters
        param_group = QGroupBox("Observation Parameters")
        param_layout = QHBoxLayout()

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)

        self.station_dropdown = QComboBox()
        self.station_dropdown.addItems([
            "ALASKA-ANCHORAGE", "ALASKA-COHOE", "ALASKA-HAARP", "ALGERIA-CRAAG",
            "ALMATY", "Arecibo-observatory", "AUSTRIA-Krumbach", "AUSTRIA-MICHELBACH",
            "AUSTRIA-OE3FLB", "AUSTRIA-UNIGRAZ", "Australia-ASSA", "BRAZIL", "BIR",
            "Croatia-Visnjan", "DENMARK", "EGYPT-Alexandria", "EGYPT-SpaceAgency",
            "ETHIOPIA", "FINLAND-Siuntio", "FINLAND-Kempele", "GERMANY-ESSEN", "GERMANY-DLR",
            "GLASGOW", "GREENLAND", "HUMAIN", "HURBANOVO", "INDIA-GAURI", "INDIA-Nashik",
            "INDIA-OOTY", "INDIA-UDAIPUR", "INDONESIA", "ITALY-Strassolt", "JAPAN-IBARAKI",
            "KASI", "KRIM", "MEXART", "MEXICO-ENSENADA-UNAM", "MEXICO-FCFM-UANL",
            "MEXICO-FCFM-UNACH", "MEXICO-LANCE-A", "MEXICO-LANCE-B",
            "MEXICO-UANL-INFIERNILLO", "MONGOLIA-UB", "MRO", "MRT1", "MRT3",
            "Malaysia_Banting", "NASA-GSFC", "NORWAY-EGERSUND", "NORWAY-NY-AALESUND",
            "NORWAY-RANDABERG", "PARAGUAY", "POLAND-BALDY", "POLAND-Grotniki",
            "ROMANIA", "ROSWELL-NM", "RWANDA", "SOUTHAFRICA-SANSA", "SPAIN-ALCALA",
            "SPAIN-PERALEJOS", "SPAIN-SIGUENZA", "SRI-Lanka", "SSRT", "SWISS-CalU",
            "SWISS-FM", "SWISS-HB9SCT", "SWISS-HEITERSWIL", "SWISS-IRSOL",
            "SWISS-Landschlacht", "SWISS-MUHEN", "TAIWAN-NCU", "THAILAND-Pathumthani",
            "TRIEST", "TURKEY", "UNAM", "URUGUAY", "USA-ARIZONA-ERAU", "USA-BOSTON",
            "UZBEKISTAN"
        ])

        self.show_button = QPushButton("Show Available FITS")
        self.show_button.clicked.connect(self.show_available_fits)

        param_layout.addWidget(QLabel("Date:"))
        param_layout.addWidget(self.date_edit)
        param_layout.addWidget(QLabel("Station:"))
        param_layout.addWidget(self.station_dropdown)
        param_layout.addWidget(self.show_button)
        param_group.setLayout(param_layout)

        # ---- File list
        file_group = QGroupBox("Available FITS Files (Whole Day)")
        file_layout = QVBoxLayout()
        self.file_list = QListWidget()
        file_layout.addWidget(self.file_list)
        file_group.setLayout(file_layout)

        # ---- Actions
        action_group = QGroupBox("Actions")
        action_layout = QHBoxLayout()

        self.select_all_btn = QPushButton("Select All")
        self.deselect_all_btn = QPushButton("Deselect All")
        self.download_btn = QPushButton("Download Selected")
        self.preview_btn = QPushButton("Preview Selected")
        self.import_button = QPushButton("Import")

        self.select_all_btn.clicked.connect(self.select_all_files)
        self.deselect_all_btn.clicked.connect(self.deselect_all_files)
        self.download_btn.clicked.connect(self.download_selected_files)
        self.preview_btn.clicked.connect(self.preview_selected_files)
        self.import_button.clicked.connect(self.handle_import)

        for b in [self.select_all_btn, self.deselect_all_btn, self.download_btn, self.preview_btn, self.import_button]:
            action_layout.addWidget(b)
        action_group.setLayout(action_layout)

        # ---- Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        layout.addWidget(param_group)
        layout.addWidget(file_group)
        layout.addWidget(action_group)
        layout.addWidget(self.progress_bar)
        self.setLayout(layout)

    # -----------------------------
    # Fetch list
    # -----------------------------
    def update_fetch_progress(self, step: int):
        QMetaObject.invokeMethod(
            self.progress_bar, "setValue",
            Qt.QueuedConnection, Q_ARG(int, step)
        )

    def show_available_fits(self):
        self.file_list.clear()
        self.file_url_map.clear()

        date_py = self.date_edit.date().toPython()
        station = self.station_dropdown.currentText()

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(0)

        # Always create a new thread
        self._fetch_thread = QThread(self)
        self._fetch_worker = FetchWorker(date_py, station)

        self._fetch_worker.moveToThread(self._fetch_thread)

        self._fetch_worker.progressMax.connect(
            lambda m: QMetaObject.invokeMethod(
                self.progress_bar,
                "setMaximum",
                Qt.QueuedConnection,
                Q_ARG(int, m)
            )
        )

        self._fetch_worker.progressStep.connect(self.update_fetch_progress)
        self._fetch_worker.finished.connect(self.display_fetched_files)

        # Proper cleanup
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_worker.finished.connect(self._fetch_worker.deleteLater)

        def _cleanup_thread():
            self._fetch_thread.deleteLater()
            self._fetch_thread = None
            self._fetch_worker = None

        self._fetch_thread.finished.connect(_cleanup_thread)

        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_thread.start()

    def display_fetched_files(self, files: list):
        self.file_list.clear()
        self.progress_bar.setVisible(False)

        if files and files[0][0] == "__SERVER_UNREACHABLE__":
            QMessageBox.critical(self, "Server Error",
                                 f"FITS server is not responding.\n\nDetails:\n{files[0][1]}")
            return

        if files and files[0][0] == "__FETCH_ERROR__":
            QMessageBox.critical(self, "Fetch Error",
                                 f"Could not load FITS directory for selected date.\n\nDetails:\n{files[0][1]}")
            return

        valid_files = [(name, url) for (name, url) in files if name and url]
        if not valid_files:
            QMessageBox.information(self, "No Data Found",
                                    "No FITS files were found for the selected station on that date.")
            return

        # Sort by filename (usually sorts by time)
        valid_files.sort(key=lambda x: x[0].lower())

        for name, url in valid_files:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.file_list.addItem(item)
            self.file_url_map[name] = url

    # -----------------------------
    # Selection helpers
    # -----------------------------
    def select_all_files(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.Checked)

    def deselect_all_files(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.Unchecked)

    def _checked_items(self) -> list[QListWidgetItem]:
        return [
            self.file_list.item(i)
            for i in range(self.file_list.count())
            if self.file_list.item(i).checkState() == Qt.Checked
        ]

    # -----------------------------
    # Download
    # -----------------------------
    def download_selected_files(self):
        output_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not output_dir:
            return

        selected = self._checked_items()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select files to download.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(selected))
        self.progress_bar.setValue(0)
        self._download_total = len(selected)
        self._download_done = 0

        for item in selected:
            name = item.text()
            url = self.file_url_map.get(name)
            if not url:
                self._on_download_done(False)
                continue

            out_path = os.path.join(output_dir, name)

            task = DownloadTask(url, out_path)
            task.done.connect(self._on_download_done)
            self.threadpool.start(task)

    @Slot(bool)
    def _on_download_done(self, success: bool):
        self._download_done += 1

        QMetaObject.invokeMethod(
            self.progress_bar, "setValue",
            Qt.QueuedConnection, Q_ARG(int, self._download_done)
        )

        if self._download_done >= self._download_total:
            QMetaObject.invokeMethod(
                self.progress_bar, "setVisible",
                Qt.QueuedConnection, Q_ARG(bool, False)
            )
            QMetaObject.invokeMethod(
                self, "show_download_complete_message",
                Qt.QueuedConnection
            )

    @Slot()
    def show_download_complete_message(self):
        QMessageBox.information(self, "Download Complete", "All selected files downloaded.")

    # -----------------------------
    # Import
    # -----------------------------
    def handle_import(self):
        selected = self._checked_items()
        urls = []

        for item in selected:
            name = item.text()
            url = self.file_url_map.get(name)
            if url:
                urls.append(url)

        if not urls:
            QMessageBox.warning(self, "No Selection", "Please select at least one FITS file.")
            return

        self.import_request.emit(urls)

    # -----------------------------
    # Preview
    # -----------------------------
    def preview_selected_files(self):
        selected = self._checked_items()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select files to preview.")
            return

        for item in selected:
            name = item.text()
            url = self.file_url_map.get(name)
            if not url:
                continue

            try:
                # Download to temp file, then open with astropy
                r = requests.get(url, timeout=30)
                r.raise_for_status()

                suffix = ".fit.gz" if name.lower().endswith(".fit.gz") else ".fit"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(r.content)
                    tmp_path = tmp.name

                win = PreviewWindow(tmp_path, name, parent=self)
                win.show()

            except Exception as e:
                QMessageBox.critical(self, "Preview Error", f"{name}\n\n{e}")
