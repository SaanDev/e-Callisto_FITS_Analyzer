from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QComboBox, QVBoxLayout,
    QHBoxLayout, QDateEdit, QListWidget, QFileDialog, QMessageBox,
    QListWidgetItem, QProgressBar, QGroupBox, QMainWindow, QDialog
)
from PySide6.QtCore import (
    Qt, QDate, QThread, Signal, QObject, QRunnable, Slot,
    QThreadPool, QMetaObject, Q_ARG
)
from astropy.io import fits
import requests
from bs4 import BeautifulSoup
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import tempfile, os

BASE_URL = 'http://soleil80.cs.technik.fhnw.ch/solarradio/data/2002-20yy_Callisto/'

class FetchWorker(QObject):
    finished = Signal(list)
    progressMax = Signal(int)
    progressStep = Signal(int)

    def __init__(self, date, hour, station):
        super().__init__()
        self.date = date
        self.hour = hour
        self.station = station

    def run(self):
        results = []
        url_day = f"{BASE_URL}{self.date.year}/{self.date.month:02}/{self.date.day:02}/"
        try:
            page = requests.get(url_day, timeout=10)
            soup = BeautifulSoup(page.content, 'html.parser')
            files = [a.get('href') for a in soup.find_all('a') if a.get('href', '').endswith('.fit.gz')]
            self.progressMax.emit(len(files))
            for i, file in enumerate(files, 1):
                if self.station.lower() in file.lower():
                    parts = file.split('_')
                    if len(parts) >= 3 and int(parts[2][:2]) == self.hour:
                        results.append((file, url_day + file))
                self.progressStep.emit(i)
        except Exception as e:
            results.append((f"❌ Error: {e}", None))
        self.finished.emit(results)

class DownloadTask(QRunnable):
    def __init__(self, url, filename, output_dir, callback):
        super().__init__()
        self.url = url
        self.filename = filename
        self.output_dir = output_dir
        self.callback = callback

    @Slot()
    def run(self):
        try:
            r = requests.get(self.url)
            with open(os.path.join(self.output_dir, self.filename), 'wb') as f:
                f.write(r.content)
            self.callback(True)
        except Exception:
            self.callback(False)

class PreviewWindow(QDialog):
    def __init__(self, file_path, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"FITS Preview: {title}")
        self.setMinimumSize(900, 600)

        with fits.open(file_path) as hdul:
            data = hdul[0].data
            freqs = hdul[1].data['frequency'][0]
            time = hdul[1].data['time'][0]

        extent = [0, time[-1], freqs[-1], freqs[0]]

        fig = Figure()
        canvas = FigureCanvas(fig)
        ax = fig.add_subplot(111)
        im = ax.imshow(data, aspect='auto', extent=extent, cmap='inferno')
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Frequency [MHz]")
        fig.colorbar(im, ax=ax)

        layout = QVBoxLayout()
        layout.addWidget(canvas)
        self.setLayout(layout)

        # Make the preview window stay in front but non-blocking
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)


class CallistoDownloaderApp(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("e-CALLISTO FITS Downloader")
        self.resize(900, 600)
        self.threadpool = QThreadPool()
        self.preview_windows = []
        self.downloaded_count = 0

        self.setStyleSheet("QWidget { font-family: Arial, sans-serif; font-size: 13px; }")

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        param_group = QGroupBox("Observation Parameters")
        param_layout = QHBoxLayout()
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.hour_combo = QComboBox()
        self.hour_combo.addItems([f"{h:02}" for h in range(24)])
        self.station_dropdown = QComboBox()
        self.station_dropdown.addItems([
            'ALASKA-ANCHORAGE',
            'ALASKA-COHOE',
            'ALASKA-HAARP',
            'ALGERIA-CRAAG',
            'ALMATY',
            'Arecibo-observatory',
            'AUSTRIA-Krumbach',
            'AUSTRIA-MICHELBACH',
            'AUSTRIA-OE3FLB',
            'AUSTRIA-UNIGRAZ',
            'Australia-ASSA',
            'BRAZIL',
            'BIR',
            'Croatia-Visnjan',
            'DENMARK',
            'EGYPT-Alexandria',
            'EGYPT-SpaceAgency',
            'ETHIOPIA',
            'FINLAND-Siuntio',
            'FINLAND-Kempele',
            'GERMANY-ESSEN',
            'GERMANY-DLR',
            'GLASGOW',
            'GREENLAND',
            'HUMAIN',
            'HURBANOVO',
            'INDIA-GAURI',
            'INDIA-Nashik',
            'INDIA-OOTY',
            'INDIA-UDAIPUR',
            'INDONESIA',
            'ITALY-Strassolt',
            'JAPAN-IBARAKI',
            'KASI',
            'KRIM',
            'MEXART',
            'MEXICO-ENSENADA-UNAM',
            'MEXICO-FCFM-UANL',
            'MEXICO-FCFM-UNACH',
            'MEXICO-LANCE-A',
            'MEXICO-LANCE-B',
            'MEXICO-UANL-INFIERNILLO',
            'MONGOLIA-UB',
            'MRO',
            'MRT1',
            'MRT3',
            'Malaysia_Banting',
            'NASA-GSFC',
            'NORWAY-EGERSUND',
            'NORWAY-NY-AALESUND',
            'NORWAY-RANDABERG',
            'PARAGUAY',
            'POLAND-BALDY',
            'POLAND-Grotniki',
            'ROMANIA',
            'ROSWELL-NM',
            'RWANDA',
            'SOUTHAFRICA-SANSA',
            'SPAIN-ALCALA',
            'SPAIN-PERALEJOS',
            'SPAIN-SIGUENZA',
            'SRI-Lanka',
            'SSRT',
            'SWISS-CalU',
            'SWISS-FM',
            'SWISS-HB9SCT',
            'SWISS-HEITERSWIL',
            'SWISS-IRSOL',
            'SWISS-Landschlacht',
            'SWISS-MUHEN',
            'TAIWAN-NCU',
            'THAILAND-Pathumthani',
            'TRIEST',
            'TURKEY',
            'UNAM',
            'URUGUAY',
            'USA-ARIZONA-ERAU',
            'USA-BOSTON',
            'UZBEKISTAN'
        ])

        self.show_button = QPushButton("Show Available FITS")
        self.show_button.clicked.connect(self.show_available_fits)
        for w in [QLabel("Date:"), self.date_edit, QLabel("Hour:"), self.hour_combo,
                  QLabel("Station:"), self.station_dropdown, self.show_button]:
            param_layout.addWidget(w)
        param_group.setLayout(param_layout)

        file_group = QGroupBox("Available FITS Files")
        self.file_list = QListWidget()
        self.file_url_map = {}
        file_layout = QVBoxLayout()
        file_layout.addWidget(self.file_list)
        file_group.setLayout(file_layout)

        action_group = QGroupBox("Actions")
        action_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.deselect_all_btn = QPushButton("Deselect All")
        self.download_btn = QPushButton("Download Selected")
        self.preview_btn = QPushButton("Preview Selected")
        self.select_all_btn.clicked.connect(self.select_all_files)
        self.deselect_all_btn.clicked.connect(self.deselect_all_files)
        self.download_btn.clicked.connect(self.download_selected_files)
        self.preview_btn.clicked.connect(self.preview_selected_files)
        for b in [self.select_all_btn, self.deselect_all_btn, self.download_btn, self.preview_btn]:
            action_layout.addWidget(b)
        action_group.setLayout(action_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        layout.addWidget(param_group)
        layout.addWidget(file_group)
        layout.addWidget(action_group)
        layout.addWidget(self.progress_bar)
        self.setLayout(layout)

    def update_fetch_progress(self, step):
        QMetaObject.invokeMethod(self.progress_bar, "setValue", Qt.QueuedConnection, Q_ARG(int, step))

    def show_available_fits(self):
        self.file_list.clear()
        self.file_url_map.clear()
        date = self.date_edit.date().toPython()
        hour = int(self.hour_combo.currentText())
        station = self.station_dropdown.currentText()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(0)

        self.thread = QThread()
        self.worker = FetchWorker(date, hour, station)
        self.worker.moveToThread(self.thread)

        self.worker.progressMax.connect(lambda maxval: QMetaObject.invokeMethod(self.progress_bar, "setMaximum", Qt.QueuedConnection, Q_ARG(int, maxval)))
        self.worker.progressStep.connect(self.update_fetch_progress)
        self.worker.finished.connect(self.display_fetched_files)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def display_fetched_files(self, files):
        self.file_list.clear()
        self.progress_bar.setVisible(False)

        valid_files = [(name, url) for name, url in files if url]
        if not valid_files:
            QMessageBox.information(self, "No Data Found",
                                    "No FITS files were found for the selected station and time.")
            return

        for name, url in valid_files:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.file_list.addItem(item)
            self.file_url_map[name] = url

    def select_all_files(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.Checked)

    def deselect_all_files(self):
        for i in range(self.file_list.count()):
            self.file_list.item(i).setCheckState(Qt.Unchecked)

    def download_selected_files(self):
        output_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not output_dir:
            return
        selected = [self.file_list.item(i) for i in range(self.file_list.count()) if self.file_list.item(i).checkState() == Qt.Checked]
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select files to download.")
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(selected))
        self.progress_bar.setValue(0)
        self.downloaded_count = 0
        for item in selected:
            name = item.text()
            url = self.file_url_map.get(name)
            if url:
                task = DownloadTask(url, name, output_dir, self.update_download_progress)
                self.threadpool.start(task)

    def update_download_progress(self, success):
        self.downloaded_count += 1
        QMetaObject.invokeMethod(self.progress_bar, "setValue", Qt.QueuedConnection, Q_ARG(int, self.downloaded_count))
        if self.downloaded_count == self.progress_bar.maximum():
            QMetaObject.invokeMethod(self.progress_bar, "setVisible", Qt.QueuedConnection, Q_ARG(bool, False))
            QMetaObject.invokeMethod(self, "show_download_complete_message", Qt.QueuedConnection)

    @Slot()
    def show_download_complete_message(self):
        QMessageBox.information(self, "Download Complete", "All selected files downloaded.")

    def preview_selected_files(self):
        selected = [self.file_list.item(i) for i in range(self.file_list.count()) if
                    self.file_list.item(i).checkState() == Qt.Checked]
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select files to preview.")
            return
        for item in selected:
            name = item.text()
            url = self.file_url_map.get(name)
            if url:
                try:
                    r = requests.get(url)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".fit.gz") as tmp:
                        tmp.write(r.content)
                        tmp_path = tmp.name
                    win = PreviewWindow(tmp_path, name, parent=self)
                    win.show()
                    self.preview_windows.append(win)
                except Exception as e:
                    QMessageBox.critical(self, "Preview Error", f"{name}\n{e}")

