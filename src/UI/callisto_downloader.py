"""
e-CALLISTO FITS Analyzer
Version 2.6.0-dev
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
import tempfile
from html import unescape
from urllib.parse import urljoin
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from astropy.io import fits
from src.Backend.fits_io import load_callisto_fits
from urllib3.util.retry import Retry

from PySide6.QtCore import (
    Qt, QDate, QDateTime, QThread, Signal, QObject, QRunnable, Slot,
    QThreadPool, QMetaObject, Q_ARG
)
from PySide6.QtWidgets import (
    QLabel, QPushButton, QComboBox, QVBoxLayout,
    QHBoxLayout, QDateEdit, QListWidget, QFileDialog, QMessageBox,
    QListWidgetItem, QProgressBar, QGroupBox, QDialog, QApplication,
    QCalendarWidget, QSpinBox, QTabWidget, QWidget, QDateTimeEdit,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
    QAbstractItemView
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

BASE_URL = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto/"
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 30
_REQUEST_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
_HREF_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*(?P<quote>['"]?)(?P<href>[^"' >]+)(?P=quote)""",
    re.IGNORECASE,
)
_FITS_SUFFIXES = (".fit.gz", ".fits.gz", ".fit", ".fits")
_DATE_RE = re.compile(r"^\d{8}$")
_TIME_RE = re.compile(r"^\d{6}$")

CALLISTO_STATIONS = (
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
    "NORWAY-RANDABERG", "NZ-WAIRAKEI-DLR", "PARAGUAY", "POLAND-BALDY", "POLAND-Grotniki",
    "ROMANIA", "ROSWELL-NM", "RWANDA", "SOUTHAFRICA-SANSA", "SPAIN-ALCALA",
    "SPAIN-PERALEJOS", "SPAIN-SIGUENZA", "SRI-Lanka", "SSRT", "SWISS-CalU",
    "SWISS-FM", "SWISS-HB9SCT", "SWISS-HEITERSWIL", "SWISS-IRSOL",
    "SWISS-Landschlacht", "SWISS-MUHEN", "TAIWAN-NCU", "THAILAND-Pathumthani",
    "TRIEST", "TURKEY", "UNAM", "URUGUAY", "USA-ARIZONA-ERAU", "USA-BOSTON",
    "UZBEKISTAN"
)


@dataclass(frozen=True)
class CallistoEventCandidate:
    station: str
    observed_at_utc: datetime
    filename: str
    url: str
    receiver_id: str


def extract_fits_links(html: str) -> list[str]:
    """Extract FITS file links from a simple directory listing page."""
    links: list[str] = []
    seen: set[str] = set()

    for match in _HREF_RE.finditer(str(html or "")):
        href = unescape(match.group("href")).strip()
        href = href.split("#", 1)[0].split("?", 1)[0]
        href_low = href.lower()
        if not href_low.endswith(_FITS_SUFFIXES):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)

    return links


def _strip_fits_suffix(filename: str) -> str:
    stem = os.path.basename(str(filename or "")).strip()
    for suffix in _FITS_SUFFIXES:
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def parse_callisto_archive_filename(filename: str) -> tuple[str, datetime, str]:
    """Return station, UTC timestamp, receiver id parsed from a CALLISTO archive filename."""
    base = os.path.basename(str(filename or "")).strip()
    stem = _strip_fits_suffix(base)
    parts = stem.split("_")

    for idx in range(1, len(parts) - 2):
        if _DATE_RE.match(parts[idx]) and idx + 1 < len(parts) and _TIME_RE.match(parts[idx + 1]):
            station = "_".join(parts[:idx]).strip()
            if not station:
                break
            try:
                observed = datetime.strptime(parts[idx] + parts[idx + 1], "%Y%m%d%H%M%S")
            except ValueError as exc:
                raise ValueError(f"Invalid CALLISTO timestamp in filename: {base}") from exc
            receiver_id = parts[-1].strip()
            if not receiver_id:
                raise ValueError(f"Missing receiver id in CALLISTO filename: {base}")
            return station, observed, receiver_id

    raise ValueError(f"Invalid CALLISTO filename format: {base}")


def _station_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _candidate_matches_station(
    *,
    parsed_station: str,
    filename: str,
    selected_station: str,
) -> bool:
    selected_key = _station_key(selected_station)
    if not selected_key:
        return False
    if _station_key(parsed_station) == selected_key:
        return True
    return os.path.basename(filename).lower().startswith(str(selected_station).strip().lower() + "_")


def utc_archive_dates_for_window(start_dt: datetime, stop_dt: datetime) -> list:
    if stop_dt < start_dt:
        raise ValueError("End time must be after start time.")

    dates = []
    current = start_dt.date()
    last = stop_dt.date()
    while current <= last:
        dates.append(current)
        current = current + timedelta(days=1)
    return dates


def filter_event_candidates(
    hrefs: list[str],
    *,
    day_url: str,
    selected_stations: list[str],
    start_dt: datetime,
    stop_dt: datetime,
) -> list[CallistoEventCandidate]:
    candidates: list[CallistoEventCandidate] = []
    selected = [str(station or "").strip() for station in selected_stations if str(station or "").strip()]

    for href in hrefs:
        filename = os.path.basename(str(href or ""))
        try:
            station, observed_at, receiver_id = parse_callisto_archive_filename(filename)
        except ValueError:
            continue
        if observed_at < start_dt or observed_at > stop_dt:
            continue
        if not any(
            _candidate_matches_station(
                parsed_station=station,
                filename=filename,
                selected_station=selected_station,
            )
            for selected_station in selected
        ):
            continue
        candidates.append(
            CallistoEventCandidate(
                station=station,
                observed_at_utc=observed_at,
                filename=filename,
                url=urljoin(day_url, href),
                receiver_id=receiver_id,
            )
        )

    return candidates


def sort_event_candidates(candidates: list[CallistoEventCandidate]) -> list[CallistoEventCandidate]:
    seen: set[str] = set()
    unique: list[CallistoEventCandidate] = []
    for candidate in candidates:
        key = candidate.url or candidate.filename
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    unique.sort(key=lambda item: (_station_key(item.station), item.observed_at_utc, item.filename.lower()))
    return unique


def _normalize_utc_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def check_archive_server(client=None) -> tuple[bool, str]:
    session = client or build_archive_session()
    close_session = client is None

    try:
        try:
            response = session.head(BASE_URL, timeout=8, allow_redirects=True)
            try:
                if response.status_code < 400:
                    return True, ""
            finally:
                response.close()
        except Exception:
            pass

        response = session.get(BASE_URL, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
        try:
            if response.status_code >= 400:
                return False, f"HTTP {response.status_code}"
            return True, ""
        finally:
            response.close()
    except Exception as e:
        return False, str(e)
    finally:
        if close_session:
            session.close()


def build_archive_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=_REQUEST_RETRY_STATUS_CODES,
        allowed_methods=frozenset({"HEAD", "GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update({"User-Agent": "e-CALLISTO FITS Analyzer/2.6.0-dev"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# -----------------------------
# Worker: fetch list of files
# -----------------------------
class FetchWorker(QObject):
    finished = Signal(object)        # list[tuple[str, str]] OR [("__SERVER_UNREACHABLE__", msg)] etc.
    progressMax = Signal(int)        # set progress bar maximum
    progressStep = Signal(int)       # set progress bar value

    def __init__(self, date_py, station: str):
        super().__init__()
        self.date = date_py          # datetime.date
        self.station = station

    def _check_server(self, client=None) -> tuple[bool, str]:
        return check_archive_server(client)

    def _day_url(self) -> str:
        return f"{BASE_URL}{self.date.year}/{self.date.month:02}/{self.date.day:02}/"

    def run(self):
        results: list[tuple[str, str]] = []

        with build_archive_session() as session:
            ok, msg = self._check_server(session)
            if not ok:
                results.append(("__SERVER_UNREACHABLE__", msg))
                self.finished.emit(results)
                return

            url_day = self._day_url()

            try:
                with session.get(url_day, timeout=REQUEST_TIMEOUT) as page:
                    if page.status_code >= 400:
                        raise RuntimeError(f"HTTP {page.status_code} for {url_day}")

                    # Avoid DOM parsers here; a lightweight href scan is enough for the
                    # Apache directory listings served by the e-CALLISTO archive.
                    hrefs = extract_fits_links(page.text)

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

            except requests.RequestException as e:
                results = [("__SERVER_UNREACHABLE__", str(e))]
            except Exception as e:
                results = [("__FETCH_ERROR__", str(e))]

        self.finished.emit(results)


class EventFetchWorker(QObject):
    finished = Signal(object)
    progressMax = Signal(int)
    progressStep = Signal(int)

    def __init__(self, start_dt: datetime, stop_dt: datetime, stations: list[str]):
        super().__init__()
        self.start_dt = _normalize_utc_datetime(start_dt)
        self.stop_dt = _normalize_utc_datetime(stop_dt)
        self.stations = [str(station or "").strip() for station in stations if str(station or "").strip()]

    def _day_url(self, date_py) -> str:
        return f"{BASE_URL}{date_py.year}/{date_py.month:02}/{date_py.day:02}/"

    def run(self):
        try:
            dates = utc_archive_dates_for_window(self.start_dt, self.stop_dt)
        except ValueError as exc:
            self.finished.emit({"error": str(exc), "candidates": [], "warnings": []})
            return

        candidates: list[CallistoEventCandidate] = []
        warnings: list[str] = []

        with build_archive_session() as session:
            ok, msg = check_archive_server(session)
            if not ok:
                self.finished.emit({"error": f"FITS server is not responding: {msg}", "candidates": [], "warnings": []})
                return

            self.progressMax.emit(len(dates))
            for index, date_py in enumerate(dates, start=1):
                url_day = self._day_url(date_py)
                try:
                    with session.get(url_day, timeout=REQUEST_TIMEOUT) as page:
                        if page.status_code >= 400:
                            raise RuntimeError(f"HTTP {page.status_code} for {url_day}")
                        hrefs = extract_fits_links(page.text)
                    candidates.extend(
                        filter_event_candidates(
                            hrefs,
                            day_url=url_day,
                            selected_stations=self.stations,
                            start_dt=self.start_dt,
                            stop_dt=self.stop_dt,
                        )
                    )
                except requests.RequestException as exc:
                    warnings.append(f"{date_py:%Y-%m-%d}: {exc}")
                except Exception as exc:
                    warnings.append(f"{date_py:%Y-%m-%d}: {exc}")
                self.progressStep.emit(index)

        candidates = sort_event_candidates(candidates)
        missing_stations = [
            station
            for station in self.stations
            if not any(
                _candidate_matches_station(
                    parsed_station=candidate.station,
                    filename=candidate.filename,
                    selected_station=station,
                )
                for candidate in candidates
            )
        ]
        self.finished.emit(
            {
                "candidates": candidates,
                "warnings": warnings,
                "missing_stations": missing_stations,
            }
        )


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
            with build_archive_session() as session:
                with session.get(self.url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
                    r.raise_for_status()
                    with open(self.out_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 128):
                            if chunk:
                                f.write(chunk)
            self.done.emit(True)
        except Exception:
            self.done.emit(False)


class EventDownloadTask(QObject, QRunnable):
    done = Signal(str, str, bool, str)

    def __init__(self, candidate: CallistoEventCandidate, out_path: str):
        QObject.__init__(self)
        QRunnable.__init__(self)
        self.candidate = candidate
        self.out_path = out_path
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        try:
            with build_archive_session() as session:
                with session.get(self.candidate.url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
                    r.raise_for_status()
                    with open(self.out_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 128):
                            if chunk:
                                f.write(chunk)
            self.done.emit(self.candidate.filename, self.out_path, True, "")
        except Exception as exc:
            self.done.emit(self.candidate.filename, self.out_path, False, str(exc))


# -----------------------------
# Preview window
# -----------------------------
class PreviewWindow(QDialog):
    def __init__(self, file_path: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"FITS Preview: {title}")
        self.setMinimumSize(900, 600)

        res = load_callisto_fits(file_path, memmap=False)
        data = res.data
        freqs = res.freqs
        times = res.time

        fig = Figure()
        canvas = FigureCanvas(fig)
        ax = fig.add_subplot(111)

        if freqs is not None and times is not None and len(freqs) > 1 and len(times) > 1:
            freqs = np.array(freqs, dtype=float).ravel()
            times = np.array(times, dtype=float).ravel()

            fmin = float(np.nanmin(freqs))
            fmax = float(np.nanmax(freqs))
            tmin = float(np.nanmin(times))
            tmax = float(np.nanmax(times))

            # If freqs are descending (high -> low), row 0 should be at the top
            origin = "upper" if freqs[0] > freqs[-1] else "lower"

            extent = [tmin, tmax, fmin, fmax]
            im = ax.imshow(data, aspect="auto", extent=extent, origin=origin, cmap="inferno")

            ax.set_xlabel("Time [s]")
            ax.set_ylabel("Frequency [MHz]")

            # Force visible y-axis to start at 0 MHz
            ax.set_ylim(fmin, fmax)

        else:
            # Fallback: show channels, but keep 0 at bottom (not inverted)
            im = ax.imshow(data, aspect="auto", origin="lower", cmap="inferno")
            ax.set_xlabel("Time bin")
            ax.set_ylabel("Frequency channel")
            ax.set_ylim(0.0, float(data.shape[0] - 1))

        cbar = fig.colorbar(im, ax=ax)

        theme = QApplication.instance().property("theme_manager")
        if theme:
            theme.apply_mpl(fig, ax, cbar)

        layout = QVBoxLayout()
        layout.addWidget(canvas)
        self.setLayout(layout)

        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)


class DownloaderCalendarWidget(QCalendarWidget):
    def __init__(self, configure_hook, parent=None):
        super().__init__(parent)
        self._configure_hook = configure_hook

    def showEvent(self, event):
        super().showEvent(event)
        if callable(self._configure_hook):
            self._configure_hook(self)


# -----------------------------
# Main downloader dialog
# -----------------------------
class CallistoDownloaderApp(QDialog):
    import_request = Signal(list)   # list of URLs
    comparison_request = Signal(list)   # list of downloaded local FITS paths
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

        self._event_fetch_thread: QThread | None = None
        self._event_fetch_worker: EventFetchWorker | None = None
        self._event_candidates: list[CallistoEventCandidate] = []
        self._event_download_total = 0
        self._event_download_done = 0
        self._event_download_success_paths: list[str] = []
        self._event_download_failures: list[str] = []

        self.setWindowTitle("e-CALLISTO FITS Downloader")
        self.resize(1050, 720)


        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_single_station_tab(), "Single Station")
        self.tabs.addTab(self._build_event_tab(), "Multi-Station Event")
        layout.addWidget(self.tabs)
        self.setLayout(layout)

    def _build_single_station_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # ---- Parameters
        param_group = QGroupBox("Observation Parameters")
        param_layout = QHBoxLayout()

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumWidth(max(140, self.date_edit.sizeHint().width()))
        self.calendar_popup = DownloaderCalendarWidget(self._configure_calendar_popup, self)
        self.date_edit.setCalendarWidget(self.calendar_popup)
        self._configure_calendar_popup(self.calendar_popup)

        self.station_dropdown = QComboBox()
        self.station_dropdown.addItems(CALLISTO_STATIONS)

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
        return page

    def _build_event_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        station_group = QGroupBox("Stations")
        station_layout = QVBoxLayout(station_group)
        self.event_station_filter = QLineEdit(self)
        self.event_station_filter.setPlaceholderText("Filter stations")
        self.event_station_filter.textChanged.connect(self._filter_event_stations)
        self.event_station_list = QListWidget(self)
        self.event_station_list.setMinimumHeight(140)
        for station in CALLISTO_STATIONS:
            item = QListWidgetItem(station)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.event_station_list.addItem(item)
        station_button_row = QHBoxLayout()
        self.event_select_all_stations_btn = QPushButton("Select All")
        self.event_clear_stations_btn = QPushButton("Clear")
        self.event_select_all_stations_btn.clicked.connect(self.select_all_event_stations)
        self.event_clear_stations_btn.clicked.connect(self.clear_event_stations)
        station_button_row.addWidget(self.event_select_all_stations_btn)
        station_button_row.addWidget(self.event_clear_stations_btn)
        station_layout.addWidget(self.event_station_filter)
        station_layout.addWidget(self.event_station_list)
        station_layout.addLayout(station_button_row)

        window_group = QGroupBox("Event Time Window (UTC)")
        window_layout = QHBoxLayout(window_group)
        now = QDateTime.currentDateTimeUtc()
        self.event_start_dt_edit = QDateTimeEdit(now.addSecs(-3600), self)
        self.event_stop_dt_edit = QDateTimeEdit(now, self)
        for edit in (self.event_start_dt_edit, self.event_stop_dt_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setMinimumWidth(190)
        self.event_search_btn = QPushButton("Search Matching FITS")
        self.event_search_btn.clicked.connect(self.search_event_fits)
        window_layout.addWidget(QLabel("Start:"))
        window_layout.addWidget(self.event_start_dt_edit)
        window_layout.addWidget(QLabel("Stop:"))
        window_layout.addWidget(self.event_stop_dt_edit)
        window_layout.addWidget(self.event_search_btn)

        results_group = QGroupBox("Matching FITS Files")
        results_layout = QVBoxLayout(results_group)
        self.event_results_table = QTableWidget(0, 5, self)
        self.event_results_table.setHorizontalHeaderLabels(["", "Station", "UTC Time", "Filename", "Receiver"])
        self.event_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.event_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.event_results_table.verticalHeader().setVisible(False)
        self.event_results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.event_results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.event_results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.event_results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.event_results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        results_layout.addWidget(self.event_results_table)

        action_group = QGroupBox("Actions")
        action_layout = QHBoxLayout(action_group)
        self.event_select_all_results_btn = QPushButton("Select All")
        self.event_clear_results_btn = QPushButton("Clear Selection")
        self.event_download_btn = QPushButton("Download Selected")
        self.event_auto_open_chk = QCheckBox("Open in Multi-Station Comparison after download")
        self.event_auto_open_chk.setChecked(True)
        self.event_select_all_results_btn.clicked.connect(self.select_all_event_results)
        self.event_clear_results_btn.clicked.connect(self.clear_event_results_selection)
        self.event_download_btn.clicked.connect(self.download_selected_event_files)
        action_layout.addWidget(self.event_select_all_results_btn)
        action_layout.addWidget(self.event_clear_results_btn)
        action_layout.addWidget(self.event_download_btn)
        action_layout.addWidget(self.event_auto_open_chk)

        self.event_progress_bar = QProgressBar(self)
        self.event_progress_bar.setVisible(False)
        self.event_status_label = QLabel("Select stations and a UTC event time window.", self)
        self.event_status_label.setWordWrap(True)

        top_row = QHBoxLayout()
        top_row.addWidget(station_group, 1)
        top_row.addWidget(window_group, 1)
        layout.addLayout(top_row)
        layout.addWidget(results_group, 1)
        layout.addWidget(action_group)
        layout.addWidget(self.event_progress_bar)
        layout.addWidget(self.event_status_label)
        self._sync_event_actions()
        return page

    def _configure_calendar_popup(self, calendar: QCalendarWidget):
        year_edit = calendar.findChild(QSpinBox, "qt_calendar_yearedit")
        if year_edit is None:
            return

        year_edit.setMinimumWidth(96)
        year_edit.setMaximumWidth(96)
        year_edit.setMinimumHeight(34)
        year_edit.setAlignment(Qt.AlignCenter)
        year_edit.setStyleSheet(
            """
            QSpinBox {
                min-height: 34px;
                padding: 0px 22px 0px 4px;
            }
            QSpinBox::up-button,
            QSpinBox::down-button {
                width: 20px;
            }
            QSpinBox::up-button {
                height: 16px;
            }
            QSpinBox::down-button {
                height: 16px;
            }
            QSpinBox::up-arrow,
            QSpinBox::down-arrow {
                width: 10px;
                height: 10px;
            }
            """
        )

        line_edit = year_edit.lineEdit()
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignCenter)
            line_edit.setMinimumWidth(60)
            line_edit.setStyleSheet("border: none; background: transparent; padding: 0px 2px 0px 0px;")

    # -----------------------------
    # Multi-station event workflow
    # -----------------------------
    def _filter_event_stations(self, text: str) -> None:
        query = str(text or "").strip().lower()
        for row in range(self.event_station_list.count()):
            item = self.event_station_list.item(row)
            item.setHidden(bool(query) and query not in item.text().lower())

    def select_all_event_stations(self):
        for row in range(self.event_station_list.count()):
            item = self.event_station_list.item(row)
            if not item.isHidden():
                item.setCheckState(Qt.Checked)

    def clear_event_stations(self):
        for row in range(self.event_station_list.count()):
            self.event_station_list.item(row).setCheckState(Qt.Unchecked)

    def _checked_event_stations(self) -> list[str]:
        return [
            self.event_station_list.item(row).text()
            for row in range(self.event_station_list.count())
            if self.event_station_list.item(row).checkState() == Qt.Checked
        ]

    def _event_datetime_from_edit(self, edit: QDateTimeEdit) -> datetime:
        return _normalize_utc_datetime(edit.dateTime().toPython())

    def _sync_event_actions(self) -> None:
        has_results = self.event_results_table.rowCount() > 0
        self.event_select_all_results_btn.setEnabled(has_results)
        self.event_clear_results_btn.setEnabled(has_results)
        self.event_download_btn.setEnabled(has_results)

    def search_event_fits(self):
        stations = self._checked_event_stations()
        if not stations:
            QMessageBox.warning(self, "No Stations", "Please select at least one station.")
            return

        start_dt = self._event_datetime_from_edit(self.event_start_dt_edit)
        stop_dt = self._event_datetime_from_edit(self.event_stop_dt_edit)
        if stop_dt < start_dt:
            QMessageBox.warning(self, "Invalid Time Window", "Stop time must be after start time.")
            return

        if self._event_fetch_thread is not None and self._event_fetch_thread.isRunning():
            QMessageBox.information(self, "Search In Progress", "An event search is already running.")
            return

        self._event_candidates = []
        self.event_results_table.setRowCount(0)
        self._sync_event_actions()
        self.event_status_label.setText("Searching archive listings...")
        self.event_progress_bar.setVisible(True)
        self.event_progress_bar.setValue(0)
        self.event_progress_bar.setMaximum(0)
        self.event_search_btn.setEnabled(False)

        self._event_fetch_thread = QThread(self)
        self._event_fetch_worker = EventFetchWorker(start_dt, stop_dt, stations)
        self._event_fetch_worker.moveToThread(self._event_fetch_thread)
        self._event_fetch_thread.started.connect(self._event_fetch_worker.run)
        self._event_fetch_worker.progressMax.connect(
            lambda value: QMetaObject.invokeMethod(
                self.event_progress_bar,
                "setMaximum",
                Qt.QueuedConnection,
                Q_ARG(int, value),
            )
        )
        self._event_fetch_worker.progressStep.connect(self.update_event_fetch_progress)
        self._event_fetch_worker.finished.connect(self.display_event_search_results)
        self._event_fetch_worker.finished.connect(self._event_fetch_thread.quit)
        self._event_fetch_worker.finished.connect(self._event_fetch_worker.deleteLater)

        def _cleanup_event_thread():
            self._event_fetch_thread.deleteLater()
            self._event_fetch_thread = None
            self._event_fetch_worker = None
            self.event_search_btn.setEnabled(True)

        self._event_fetch_thread.finished.connect(_cleanup_event_thread)
        self._event_fetch_thread.start()

    def update_event_fetch_progress(self, step: int):
        QMetaObject.invokeMethod(
            self.event_progress_bar,
            "setValue",
            Qt.QueuedConnection,
            Q_ARG(int, step),
        )

    def display_event_search_results(self, payload):
        self.event_progress_bar.setVisible(False)
        self.event_search_btn.setEnabled(True)

        if not isinstance(payload, dict):
            QMessageBox.critical(self, "Search Error", "Unexpected event search result.")
            return
        if payload.get("error"):
            QMessageBox.critical(self, "Search Error", str(payload.get("error")))
            return

        candidates = list(payload.get("candidates") or [])
        self._event_candidates = candidates
        self.event_results_table.setRowCount(0)

        for candidate in candidates:
            self._append_event_candidate_row(candidate)

        self._sync_event_actions()
        warnings = list(payload.get("warnings") or [])
        missing = list(payload.get("missing_stations") or [])
        if candidates:
            status = f"Found {len(candidates)} matching FITS file(s)."
        else:
            status = "No matching FITS files were found for the selected stations and time window."
        if missing:
            shown = ", ".join(missing[:8])
            suffix = "..." if len(missing) > 8 else ""
            status += f" No matches for: {shown}{suffix}."
        if warnings:
            status += " Warnings: " + "; ".join(str(item) for item in warnings[:3])
        self.event_status_label.setText(status)

    def _append_event_candidate_row(self, candidate: CallistoEventCandidate) -> None:
        row = self.event_results_table.rowCount()
        self.event_results_table.insertRow(row)

        check_item = QTableWidgetItem("")
        check_item.setFlags(check_item.flags() | Qt.ItemIsUserCheckable)
        check_item.setCheckState(Qt.Checked)
        check_item.setData(Qt.UserRole, candidate)
        self.event_results_table.setItem(row, 0, check_item)
        self.event_results_table.setItem(row, 1, QTableWidgetItem(candidate.station))
        self.event_results_table.setItem(
            row,
            2,
            QTableWidgetItem(candidate.observed_at_utc.strftime("%Y-%m-%d %H:%M:%S")),
        )
        filename_item = QTableWidgetItem(candidate.filename)
        filename_item.setToolTip(candidate.url)
        self.event_results_table.setItem(row, 3, filename_item)
        self.event_results_table.setItem(row, 4, QTableWidgetItem(candidate.receiver_id))

    def select_all_event_results(self):
        for row in range(self.event_results_table.rowCount()):
            item = self.event_results_table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Checked)

    def clear_event_results_selection(self):
        for row in range(self.event_results_table.rowCount()):
            item = self.event_results_table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked)

    def _checked_event_candidates(self) -> list[CallistoEventCandidate]:
        selected: list[CallistoEventCandidate] = []
        for row in range(self.event_results_table.rowCount()):
            item = self.event_results_table.item(row, 0)
            if item is None or item.checkState() != Qt.Checked:
                continue
            candidate = item.data(Qt.UserRole)
            if isinstance(candidate, CallistoEventCandidate):
                selected.append(candidate)
        return selected

    def download_selected_event_files(self):
        selected = self._checked_event_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select files to download.")
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not output_dir:
            return

        self._event_download_total = len(selected)
        self._event_download_done = 0
        self._event_download_success_paths = []
        self._event_download_failures = []
        self.event_download_btn.setEnabled(False)
        self.event_search_btn.setEnabled(False)
        self.event_progress_bar.setVisible(True)
        self.event_progress_bar.setMaximum(len(selected))
        self.event_progress_bar.setValue(0)
        self.event_status_label.setText("Downloading selected event FITS files...")

        for candidate in selected:
            out_path = os.path.join(output_dir, candidate.filename)
            task = EventDownloadTask(candidate, out_path)
            task.done.connect(self._on_event_download_done)
            self.threadpool.start(task)

    @Slot(str, str, bool, str)
    def _on_event_download_done(self, filename: str, out_path: str, success: bool, error: str):
        self._event_download_done += 1
        if success:
            self._event_download_success_paths.append(out_path)
        else:
            self._event_download_failures.append(f"{filename}: {error or 'download failed'}")

        QMetaObject.invokeMethod(
            self.event_progress_bar,
            "setValue",
            Qt.QueuedConnection,
            Q_ARG(int, self._event_download_done),
        )

        if self._event_download_done >= self._event_download_total:
            QMetaObject.invokeMethod(
                self.event_progress_bar,
                "setVisible",
                Qt.QueuedConnection,
                Q_ARG(bool, False),
            )
            QMetaObject.invokeMethod(self, "finish_event_download", Qt.QueuedConnection)

    @Slot()
    def finish_event_download(self):
        self.event_download_btn.setEnabled(self.event_results_table.rowCount() > 0)
        self.event_search_btn.setEnabled(True)

        success_count = len(self._event_download_success_paths)
        failed_count = len(self._event_download_failures)
        status = f"Downloaded {success_count} of {self._event_download_total} selected FITS file(s)."
        if failed_count:
            status += f" {failed_count} failed."
        auto_open = self.event_auto_open_chk.isChecked()
        if auto_open and success_count >= 2:
            status += " Opening Multi-Station Comparison."
        elif auto_open:
            status += " At least two successful downloads are required to open comparison mode."
        self.event_status_label.setText(status)

        details = [status]
        if self._event_download_failures:
            details.append("")
            details.append("Failures:")
            details.extend(self._event_download_failures[:8])
            if len(self._event_download_failures) > 8:
                details.append("...")
        QMessageBox.information(self, "Download Complete", "\n".join(details))

        if auto_open and success_count >= 2:
            self.comparison_request.emit(list(self._event_download_success_paths))

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
                with build_archive_session() as session:
                    with session.get(url, timeout=DOWNLOAD_TIMEOUT) as r:
                        r.raise_for_status()

                        suffix = ".fit.gz" if name.lower().endswith(".fit.gz") else ".fit"
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(r.content)
                            tmp_path = tmp.name

                win = PreviewWindow(tmp_path, name, parent=self)
                win.show()

            except Exception as e:
                QMessageBox.critical(self, "Preview Error", f"{name}\n\n{e}")
