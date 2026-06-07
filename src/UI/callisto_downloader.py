"""
e-CALLISTO FITS Analyzer
Version 2.6.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from src.Backend.spectral_overview import (
    SpectralOverviewCancelled,
    SpectralOverviewResult,
    SpectralOverviewSource,
    build_spectral_overview,
    render_spectral_overview_figure,
)
from src.UI.gui_shared import pick_export_path
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
    QAbstractItemView, QGridLayout, QSizePolicy, QScrollArea
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
_OVERVIEW_EXPORT_FILTERS = "PNG (*.png);;PDF (*.pdf);;EPS (*.eps);;SVG (*.svg);;TIFF (*.tiff)"

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


def filter_spectral_overview_candidates(
    hrefs: list[str],
    *,
    day_url: str,
    station: str,
    observation_date,
) -> list[CallistoEventCandidate]:
    selected_key = _station_key(station)
    candidates: list[CallistoEventCandidate] = []
    for href in hrefs:
        filename = os.path.basename(str(href or ""))
        try:
            parsed_station, observed_at, receiver_id = parse_callisto_archive_filename(filename)
        except ValueError:
            continue
        if _station_key(parsed_station) != selected_key or observed_at.date() != observation_date:
            continue
        candidates.append(
            CallistoEventCandidate(
                station=parsed_station,
                observed_at_utc=observed_at,
                filename=filename,
                url=urljoin(day_url, href),
                receiver_id=receiver_id,
            )
        )
    return sort_event_candidates(candidates)


def select_spectral_overview_focus_code(
    candidates: list[CallistoEventCandidate],
    requested_focus_code: str = "",
) -> tuple[list[str], str, list[CallistoEventCandidate]]:
    groups: dict[str, list[CallistoEventCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(str(candidate.receiver_id or "").strip(), []).append(candidate)
    groups.pop("", None)
    focus_codes = sorted(groups)
    if not focus_codes:
        raise ValueError("No focus codes were found for the selected station and date.")

    requested = str(requested_focus_code or "").strip()
    if requested:
        if requested not in groups:
            raise ValueError(f"Focus code '{requested}' is not available for the selected station and date.")
        selected = requested
    else:
        selected = sorted(focus_codes, key=lambda code: (-len(groups[code]), code))[0]
    return focus_codes, selected, sort_event_candidates(groups[selected])


def group_spectral_overview_candidates(
    candidates: list[CallistoEventCandidate],
) -> dict[str, list[CallistoEventCandidate]]:
    groups: dict[str, list[CallistoEventCandidate]] = {}
    for candidate in candidates:
        focus_code = str(candidate.receiver_id or "").strip()
        if focus_code:
            groups.setdefault(focus_code, []).append(candidate)
    return {
        focus_code: sort_event_candidates(groups[focus_code])
        for focus_code in sorted(groups)
    }


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
    session.headers.update({"User-Agent": "e-CALLISTO FITS Analyzer/2.6.0"})
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


class SpectralOverviewWorker(QObject):
    progressRange = Signal(int, int)
    progressValue = Signal(int)
    progressText = Signal(str)
    focusCodesDiscovered = Signal(object, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, date_py, station: str, focus_code: str = ""):
        super().__init__()
        self.date = date_py
        self.station = str(station or "").strip()
        self.focus_code = str(focus_code or "").strip()
        self._cancel_requested = False

    @Slot()
    def request_cancel(self):
        self._cancel_requested = True

    def _is_cancelled(self) -> bool:
        return bool(self._cancel_requested)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise SpectralOverviewCancelled("Spectral overview generation was cancelled.")

    def _day_url(self) -> str:
        return f"{BASE_URL}{self.date.year}/{self.date.month:02}/{self.date.day:02}/"

    @Slot()
    def run(self):
        try:
            self._check_cancelled()
            self.progressText.emit("Reading the selected archive day...")
            day_url = self._day_url()
            with build_archive_session() as session:
                ok, message = check_archive_server(session)
                if not ok:
                    raise RuntimeError(f"FITS server is not responding: {message}")
                with session.get(day_url, timeout=REQUEST_TIMEOUT) as page:
                    if page.status_code >= 400:
                        raise RuntimeError(f"HTTP {page.status_code} for {day_url}")
                    hrefs = extract_fits_links(page.text)

                candidates = filter_spectral_overview_candidates(
                    hrefs,
                    day_url=day_url,
                    station=self.station,
                    observation_date=self.date,
                )
                if not candidates:
                    raise ValueError("No FITS files were found for the selected station on that UTC date.")

                focus_groups = group_spectral_overview_candidates(candidates)
                focus_codes = list(focus_groups)
                if not focus_codes:
                    raise ValueError("No focus codes were found for the selected station and date.")
                if self.focus_code:
                    if self.focus_code not in focus_groups:
                        raise ValueError(
                            f"Focus code '{self.focus_code}' is not available for the selected station and date."
                        )
                    selected_codes = [self.focus_code]
                else:
                    selected_codes = focus_codes
                selected = [
                    candidate
                    for focus_code in selected_codes
                    for candidate in focus_groups[focus_code]
                ]
                self.focusCodesDiscovered.emit(focus_codes, self.focus_code)
                self.progressRange.emit(0, len(selected))
                self.progressValue.emit(0)

                warnings_by_focus: dict[str, list[str]] = {focus_code: [] for focus_code in selected_codes}
                sources_by_focus: dict[str, list[SpectralOverviewSource]] = {
                    focus_code: [] for focus_code in selected_codes
                }
                with tempfile.TemporaryDirectory(prefix="callisto_spectral_overview_") as temp_dir:
                    for index, candidate in enumerate(selected, start=1):
                        self._check_cancelled()
                        self.progressText.emit(
                            f"Downloading {candidate.filename} ({index}/{len(selected)})..."
                        )
                        out_path = os.path.join(temp_dir, candidate.filename)
                        try:
                            with session.get(candidate.url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
                                response.raise_for_status()
                                with open(out_path, "wb") as handle:
                                    for chunk in response.iter_content(chunk_size=1024 * 128):
                                        self._check_cancelled()
                                        if chunk:
                                            handle.write(chunk)
                            sources_by_focus[candidate.receiver_id].append(
                                SpectralOverviewSource(
                                    path=out_path,
                                    station=candidate.station,
                                    observed_at_utc=candidate.observed_at_utc,
                                    focus_code=candidate.receiver_id,
                                    filename=candidate.filename,
                                )
                            )
                        except SpectralOverviewCancelled:
                            raise
                        except Exception as exc:
                            warnings_by_focus[candidate.receiver_id].append(f"{candidate.filename}: {exc}")
                        self.progressValue.emit(index)

                    if not any(sources_by_focus.values()):
                        raise ValueError("None of the selected focus-code FITS files could be downloaded.")

                    self.progressRange.emit(0, 0)
                    results: list[SpectralOverviewResult] = []
                    build_errors: list[str] = []
                    for focus_index, focus_code in enumerate(selected_codes, start=1):
                        self._check_cancelled()
                        sources = sources_by_focus[focus_code]
                        if not sources:
                            build_errors.append(f"Focus code {focus_code}: no FITS files could be downloaded.")
                            continue
                        self.progressText.emit(
                            f"Building focus code {focus_code} ({focus_index}/{len(selected_codes)})..."
                        )
                        try:
                            result = build_spectral_overview(
                                sources,
                                temp_dir=temp_dir,
                                cancel_check=self._is_cancelled,
                                progress_callback=self.progressText.emit,
                            )
                            results.append(
                                replace(
                                    result,
                                    total_sources=len(focus_groups[focus_code]),
                                    warnings=tuple(warnings_by_focus[focus_code]) + tuple(result.warnings),
                                )
                            )
                        except SpectralOverviewCancelled:
                            raise
                        except Exception as exc:
                            build_errors.append(f"Focus code {focus_code}: {exc}")
                    if not results:
                        raise ValueError("\n".join(build_errors) or "No focus-code overview could be generated.")
                    self._check_cancelled()
                    self.finished.emit({"results": results, "errors": build_errors})
        except SpectralOverviewCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


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
    comparison_request = Signal(list)   # list of URLs or local FITS paths
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

        self._overview_thread: QThread | None = None
        self._overview_worker: SpectralOverviewWorker | None = None
        self._overview_result: SpectralOverviewResult | None = None
        self._overview_figure: Figure | None = None
        self._overview_results: dict[str, SpectralOverviewResult] = {}
        self._overview_figures: dict[str, Figure] = {}
        self._overview_canvases: dict[str, FigureCanvas] = {}
        self._overview_close_after_finish = False

        self.setWindowTitle("e-CALLISTO FITS Downloader")
        self.setObjectName("CallistoDownloaderDialog")
        self.setMinimumSize(1280, 820)
        self.resize(1540, 980)


        self.init_ui()
        self._connect_theme_updates()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("DownloaderTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_single_station_tab(), "Single Station")
        self.tabs.addTab(self._build_event_tab(), "Multi-Station Event")
        self.tabs.addTab(self._build_spectral_overview_tab(), "Spectral Overview")
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self._apply_downloader_style()

    def _connect_theme_updates(self) -> None:
        app = QApplication.instance()
        theme = app.property("theme_manager") if app is not None else None
        if theme is None:
            return
        try:
            theme.themeChanged.connect(lambda _dark: self._apply_downloader_style())
        except Exception:
            pass
        try:
            theme.viewModeChanged.connect(lambda _mode: self._apply_downloader_style())
        except Exception:
            pass

    def _apply_downloader_style(self) -> None:
        app = QApplication.instance()
        theme = app.property("theme_manager") if app is not None else None
        view_mode = ""
        dark = False
        if theme is not None:
            try:
                view_mode = str(theme.view_mode()).lower()
            except Exception:
                view_mode = ""
            try:
                dark = bool(theme.is_dark())
            except Exception:
                dark = False
        if view_mode and view_mode != "modern":
            self.setStyleSheet("")
            return

        if dark:
            page_bg = "#0f151e"
            surface_bg = "#171f2b"
            surface_alt = "#202b3b"
            input_bg = "#121a25"
            border = "#314055"
            text = "#e8eef8"
            muted = "#9db0c9"
            hover = "#29364a"
            accent = "#4ea3ff"
            accent_pressed = "#2f83d8"
            accent_soft = "#1f3650"
        else:
            page_bg = "#f4f7fc"
            surface_bg = "#ffffff"
            surface_alt = "#f5f9ff"
            input_bg = "#ffffff"
            border = "#d3dcea"
            text = "#202a36"
            muted = "#61758f"
            hover = "#ecf3ff"
            accent = "#146fda"
            accent_pressed = "#0f5fba"
            accent_soft = "#e8f2ff"

        self.setStyleSheet(
            f"""
            QDialog#CallistoDownloaderDialog {{
                background: {page_bg};
            }}
            QTabWidget#DownloaderTabs::pane {{
                border: none;
                background: transparent;
                top: 6px;
            }}
            QTabWidget#DownloaderTabs QTabBar {{
                qproperty-drawBase: 0;
            }}
            QTabWidget#DownloaderTabs QTabBar::tab {{
                min-height: 30px;
                min-width: 150px;
                padding: 6px 18px;
                margin-right: 6px;
                border: 1px solid {border};
                border-radius: 8px;
                background: {surface_bg};
                color: {muted};
                font-weight: 600;
            }}
            QTabWidget#DownloaderTabs QTabBar::tab:hover {{
                background: {hover};
                color: {text};
            }}
            QTabWidget#DownloaderTabs QTabBar::tab:selected {{
                background: {accent_soft};
                border-color: {accent};
                color: {text};
            }}
            QWidget#DownloaderTabPage {{
                background: transparent;
            }}
            QGroupBox#DownloaderSection {{
                background: {surface_bg};
                border: 1px solid {border};
                border-radius: 8px;
                margin-top: 12px;
                padding: 10px;
                font-weight: 600;
            }}
            QGroupBox#DownloaderSection::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {muted};
            }}
            QListWidget#EventStationList,
            QListWidget#DownloaderFileList,
            QTableWidget#EventResultsTable {{
                background: {input_bg};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 5px;
            }}
            QTableWidget#EventResultsTable::item {{
                padding: 5px;
            }}
            QPushButton#PrimaryDownloaderButton {{
                background: {accent};
                border-color: {accent};
                color: #ffffff;
                font-weight: 600;
            }}
            QPushButton#PrimaryDownloaderButton:hover {{
                background: {accent_pressed};
                border-color: {accent_pressed};
            }}
            QPushButton#PrimaryDownloaderButton:disabled {{
                background: {surface_alt};
                border-color: {border};
                color: {muted};
            }}
            QLabel#DownloaderStatusLabel {{
                color: {muted};
                padding: 2px 4px;
            }}
            QCheckBox#AutoOpenComparison {{
                color: {text};
            }}
            """
        )

    def _build_single_station_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("DownloaderTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(8)

        # ---- Parameters
        param_group = QGroupBox("Observation Parameters")
        param_group.setObjectName("DownloaderSection")
        param_layout = QHBoxLayout()
        param_layout.setSpacing(8)

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
        self.show_button.setObjectName("PrimaryDownloaderButton")
        self.show_button.clicked.connect(self.show_available_fits)

        param_layout.addWidget(QLabel("Date:"))
        param_layout.addWidget(self.date_edit)
        param_layout.addWidget(QLabel("Station:"))
        param_layout.addWidget(self.station_dropdown)
        param_layout.addWidget(self.show_button)
        param_group.setLayout(param_layout)

        # ---- File list
        file_group = QGroupBox("Available FITS Files (Whole Day)")
        file_group.setObjectName("DownloaderSection")
        file_layout = QVBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setObjectName("DownloaderFileList")
        file_layout.addWidget(self.file_list)
        file_group.setLayout(file_layout)

        # ---- Actions
        action_group = QGroupBox("Actions")
        action_group.setObjectName("DownloaderSection")
        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)

        self.select_all_btn = QPushButton("Select All")
        self.deselect_all_btn = QPushButton("Deselect All")
        self.download_btn = QPushButton("Download Selected")
        self.preview_btn = QPushButton("Preview Selected")
        self.compare_button = QPushButton("Compare")
        self.import_button = QPushButton("Import")

        self.select_all_btn.clicked.connect(self.select_all_files)
        self.deselect_all_btn.clicked.connect(self.deselect_all_files)
        self.download_btn.clicked.connect(self.download_selected_files)
        self.preview_btn.clicked.connect(self.preview_selected_files)
        self.compare_button.clicked.connect(self.handle_compare)
        self.import_button.clicked.connect(self.handle_import)

        for b in [self.select_all_btn, self.deselect_all_btn, self.download_btn, self.preview_btn, self.compare_button, self.import_button]:
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
        page.setObjectName("DownloaderTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(8)

        station_group = QGroupBox("Stations")
        station_group.setObjectName("DownloaderSection")
        station_group.setMinimumWidth(280)
        station_group.setMaximumWidth(360)
        station_layout = QVBoxLayout(station_group)
        station_layout.setSpacing(8)
        self.event_station_filter = QLineEdit(self)
        self.event_station_filter.setPlaceholderText("Filter stations")
        self.event_station_filter.textChanged.connect(self._filter_event_stations)
        self.event_station_list = QListWidget(self)
        self.event_station_list.setObjectName("EventStationList")
        self.event_station_list.setMinimumHeight(260)
        self.event_station_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        for station in CALLISTO_STATIONS:
            item = QListWidgetItem(station)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.event_station_list.addItem(item)
        station_button_row = QHBoxLayout()
        station_button_row.setSpacing(8)
        self.event_select_all_stations_btn = QPushButton("Select All")
        self.event_clear_stations_btn = QPushButton("Clear")
        self.event_select_all_stations_btn.clicked.connect(self.select_all_event_stations)
        self.event_clear_stations_btn.clicked.connect(self.clear_event_stations)
        station_button_row.addWidget(self.event_select_all_stations_btn)
        station_button_row.addWidget(self.event_clear_stations_btn)
        station_layout.addWidget(self.event_station_filter)
        station_layout.addWidget(self.event_station_list)
        station_layout.addLayout(station_button_row)

        window_group = QGroupBox("Event Window")
        window_group.setObjectName("DownloaderSection")
        window_layout = QGridLayout(window_group)
        window_layout.setHorizontalSpacing(8)
        window_layout.setVerticalSpacing(8)
        now = QDateTime.currentDateTimeUtc()
        self.event_start_dt_edit = QDateTimeEdit(now.addSecs(-3600), self)
        self.event_stop_dt_edit = QDateTimeEdit(now, self)
        for edit in (self.event_start_dt_edit, self.event_stop_dt_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setMinimumWidth(190)
        self.event_search_btn = QPushButton("Search Matching FITS")
        self.event_search_btn.setObjectName("PrimaryDownloaderButton")
        self.event_search_btn.clicked.connect(self.search_event_fits)
        window_layout.addWidget(QLabel("Start (UTC):"), 0, 0)
        window_layout.addWidget(self.event_start_dt_edit, 0, 1)
        window_layout.addWidget(QLabel("Stop (UTC):"), 1, 0)
        window_layout.addWidget(self.event_stop_dt_edit, 1, 1)
        window_layout.addWidget(self.event_search_btn, 0, 2, 2, 1)
        window_layout.setColumnStretch(1, 1)

        results_group = QGroupBox("Matching FITS Files")
        results_group.setObjectName("DownloaderSection")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(10, 12, 10, 10)
        self.event_results_table = QTableWidget(0, 5, self)
        self.event_results_table.setObjectName("EventResultsTable")
        self.event_results_table.setHorizontalHeaderLabels(["", "Station", "UTC Time", "Filename", "Receiver"])
        self.event_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.event_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.event_results_table.setAlternatingRowColors(True)
        self.event_results_table.setShowGrid(False)
        self.event_results_table.setWordWrap(False)
        self.event_results_table.verticalHeader().setVisible(False)
        self.event_results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.event_results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.event_results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.event_results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.event_results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        results_layout.addWidget(self.event_results_table)

        action_group = QGroupBox("Download")
        action_group.setObjectName("DownloaderSection")
        action_layout = QHBoxLayout(action_group)
        action_layout.setSpacing(8)
        self.event_select_all_results_btn = QPushButton("Select All")
        self.event_clear_results_btn = QPushButton("Clear Selection")
        self.event_compare_btn = QPushButton("Compare")
        self.event_download_btn = QPushButton("Download Selected")
        self.event_download_btn.setObjectName("PrimaryDownloaderButton")
        self.event_auto_open_chk = QCheckBox("Open in Multi-Station Comparison after download")
        self.event_auto_open_chk.setObjectName("AutoOpenComparison")
        self.event_auto_open_chk.setChecked(True)
        self.event_select_all_results_btn.clicked.connect(self.select_all_event_results)
        self.event_clear_results_btn.clicked.connect(self.clear_event_results_selection)
        self.event_compare_btn.clicked.connect(self.compare_selected_event_files)
        self.event_download_btn.clicked.connect(self.download_selected_event_files)
        action_layout.addWidget(self.event_select_all_results_btn)
        action_layout.addWidget(self.event_clear_results_btn)
        action_layout.addWidget(self.event_compare_btn)
        action_layout.addWidget(self.event_download_btn)
        action_layout.addStretch(1)
        action_layout.addWidget(self.event_auto_open_chk)

        self.event_progress_bar = QProgressBar(self)
        self.event_progress_bar.setVisible(False)
        self.event_status_label = QLabel("Select stations and a UTC event time window.", self)
        self.event_status_label.setObjectName("DownloaderStatusLabel")
        self.event_status_label.setWordWrap(True)

        right_panel = QWidget(self)
        right_panel.setObjectName("DownloaderWorkflowPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(window_group, 0)
        right_layout.addWidget(results_group, 1)
        right_layout.addWidget(action_group, 0)
        right_layout.addWidget(self.event_progress_bar, 0)
        right_layout.addWidget(self.event_status_label, 0)

        content_row = QHBoxLayout()
        content_row.setSpacing(10)
        content_row.addWidget(station_group, 0)
        content_row.addWidget(right_panel, 1)
        layout.addLayout(content_row, 1)
        self._sync_event_actions()
        return page

    def _build_spectral_overview_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("DownloaderTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(8)

        controls_group = QGroupBox("Full-Day Spectral Overview")
        controls_group.setObjectName("DownloaderSection")
        controls_layout = QGridLayout(controls_group)
        controls_layout.setHorizontalSpacing(8)
        controls_layout.setVerticalSpacing(8)

        self.overview_date_edit = QDateEdit(QDate.currentDate(), self)
        self.overview_date_edit.setCalendarPopup(True)
        self.overview_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.overview_date_edit.setMinimumWidth(140)
        self.overview_calendar_popup = DownloaderCalendarWidget(self._configure_calendar_popup, self)
        self.overview_date_edit.setCalendarWidget(self.overview_calendar_popup)
        self._configure_calendar_popup(self.overview_calendar_popup)

        self.overview_station_dropdown = QComboBox(self)
        self.overview_station_dropdown.addItems(CALLISTO_STATIONS)
        self.overview_station_dropdown.setMinimumWidth(220)

        self.overview_focus_combo = QComboBox(self)
        self.overview_focus_combo.addItem("All available focus codes", "")
        self.overview_focus_combo.setMinimumWidth(210)
        self.overview_date_edit.dateChanged.connect(self._reset_overview_focus_selector)
        self.overview_station_dropdown.currentTextChanged.connect(self._reset_overview_focus_selector)

        self.overview_generate_btn = QPushButton("Generate Overview", self)
        self.overview_generate_btn.setObjectName("PrimaryDownloaderButton")
        self.overview_cancel_btn = QPushButton("Cancel", self)
        self.overview_export_btn = QPushButton("Export...", self)
        self.overview_cancel_btn.setEnabled(False)
        self.overview_export_btn.setEnabled(False)
        self.overview_generate_btn.clicked.connect(self.generate_spectral_overview)
        self.overview_cancel_btn.clicked.connect(self.cancel_spectral_overview)
        self.overview_export_btn.clicked.connect(self.export_spectral_overview)

        controls_layout.addWidget(QLabel("Date (UTC):", self), 0, 0)
        controls_layout.addWidget(self.overview_date_edit, 0, 1)
        controls_layout.addWidget(QLabel("Station:", self), 0, 2)
        controls_layout.addWidget(self.overview_station_dropdown, 0, 3)
        controls_layout.addWidget(QLabel("Focus code:", self), 0, 4)
        controls_layout.addWidget(self.overview_focus_combo, 0, 5)
        controls_layout.addWidget(self.overview_generate_btn, 0, 6)
        controls_layout.addWidget(self.overview_cancel_btn, 0, 7)
        controls_layout.addWidget(self.overview_export_btn, 0, 8)
        controls_layout.setColumnStretch(3, 1)

        self.overview_progress_bar = QProgressBar(self)
        self.overview_progress_bar.setVisible(False)
        self.overview_status_label = QLabel(
            "Select a station and UTC date, then generate full-day Plotutil median-dB overviews "
            "for every available focus code.",
            self,
        )
        self.overview_status_label.setObjectName("DownloaderStatusLabel")
        self.overview_status_label.setWordWrap(True)
        controls_layout.addWidget(self.overview_progress_bar, 1, 0, 1, 9)
        controls_layout.addWidget(self.overview_status_label, 2, 0, 1, 9)

        plot_group = QGroupBox("Overview Preview")
        plot_group.setObjectName("DownloaderSection")
        plot_layout = QVBoxLayout(plot_group)
        self.overview_preview_tabs = QTabWidget(self)
        self.overview_preview_tabs.currentChanged.connect(self._on_overview_preview_tab_changed)
        placeholder = Figure(figsize=(12, 8), facecolor="white")
        placeholder_ax = placeholder.add_subplot(111)
        placeholder_ax.axis("off")
        placeholder_ax.text(
            0.5,
            0.5,
            "Generated spectral overview will appear here.",
            ha="center",
            va="center",
            color="#737b84",
        )
        self.overview_canvas = FigureCanvas(placeholder)
        self.overview_canvas.setMinimumSize(1400, 980)
        self.overview_plot_scroll = QScrollArea(self)
        self.overview_plot_scroll.setWidgetResizable(False)
        self.overview_plot_scroll.setWidget(self.overview_canvas)
        self.overview_preview_tabs.addTab(self.overview_plot_scroll, "Preview")
        plot_layout.addWidget(self.overview_preview_tabs)

        layout.addWidget(controls_group, 0)
        layout.addWidget(plot_group, 1)
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
        year_edit.setMinimumHeight(34)

        line_edit = year_edit.lineEdit()
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignCenter)
            line_edit.setMinimumWidth(60)
            line_edit.setStyleSheet("border: none; background: transparent; padding: 0px 2px 0px 0px;")

    # -----------------------------
    # Full-day spectral overview
    # -----------------------------
    def _reset_overview_focus_selector(self, *_args) -> None:
        self.overview_focus_combo.clear()
        self.overview_focus_combo.addItem("All available focus codes", "")

    def _set_overview_running(self, running: bool) -> None:
        self.tabs.setTabEnabled(0, not running)
        self.tabs.setTabEnabled(1, not running)
        self.overview_date_edit.setEnabled(not running)
        self.overview_station_dropdown.setEnabled(not running)
        self.overview_focus_combo.setEnabled(not running)
        self.overview_generate_btn.setEnabled(not running)
        self.overview_cancel_btn.setEnabled(running)
        self.overview_export_btn.setEnabled((not running) and self._overview_figure is not None)
        self.overview_progress_bar.setVisible(running)

    def generate_spectral_overview(self):
        if self._overview_thread is not None and self._overview_thread.isRunning():
            QMessageBox.information(self, "Overview In Progress", "A spectral overview is already being generated.")
            return

        date_py = self.overview_date_edit.date().toPython()
        station = self.overview_station_dropdown.currentText()
        focus_code = str(self.overview_focus_combo.currentData() or "")
        self._set_overview_running(True)
        self.overview_progress_bar.setRange(0, 0)
        self.overview_progress_bar.setValue(0)
        self.overview_status_label.setText("Preparing full-day spectral overview...")
        self.overview_status_label.setToolTip("")

        self._overview_thread = QThread(self)
        self._overview_worker = SpectralOverviewWorker(date_py, station, focus_code)
        self._overview_worker.moveToThread(self._overview_thread)
        self._overview_thread.started.connect(self._overview_worker.run)
        self._overview_worker.progressRange.connect(self.overview_progress_bar.setRange)
        self._overview_worker.progressValue.connect(self.overview_progress_bar.setValue)
        self._overview_worker.progressText.connect(self.overview_status_label.setText)
        self._overview_worker.focusCodesDiscovered.connect(self._update_overview_focus_codes)
        self._overview_worker.finished.connect(self._on_spectral_overview_finished)
        self._overview_worker.failed.connect(self._on_spectral_overview_failed)
        self._overview_worker.cancelled.connect(self._on_spectral_overview_cancelled)
        for terminal_signal in (
            self._overview_worker.finished,
            self._overview_worker.failed,
            self._overview_worker.cancelled,
        ):
            terminal_signal.connect(self._overview_thread.quit)
            terminal_signal.connect(self._overview_worker.deleteLater)

        def _cleanup_overview_thread():
            if self._overview_thread is not None:
                self._overview_thread.deleteLater()
            self._overview_thread = None
            self._overview_worker = None
            self._set_overview_running(False)
            if self._overview_close_after_finish:
                self._overview_close_after_finish = False
                self.close()

        self._overview_thread.finished.connect(_cleanup_overview_thread)
        self._overview_thread.start()

    @Slot()
    def cancel_spectral_overview(self):
        if self._overview_worker is None:
            return
        self.overview_status_label.setText("Cancelling spectral overview generation...")
        self.overview_cancel_btn.setEnabled(False)
        self._overview_worker.request_cancel()

    @Slot(object, str)
    def _update_overview_focus_codes(self, focus_codes, selected_focus: str):
        requested = str(self.overview_focus_combo.currentData() or "")
        codes = [str(code) for code in list(focus_codes or [])]
        self.overview_focus_combo.clear()
        self.overview_focus_combo.addItem(f"All available focus codes ({len(codes)})", "")
        for code in codes:
            self.overview_focus_combo.addItem(code, code)
        if requested and requested in codes:
            self.overview_focus_combo.setCurrentIndex(self.overview_focus_combo.findData(requested))

    def _clear_overview_previews(self) -> None:
        for figure in self._overview_figures.values():
            try:
                figure.clear()
            except Exception:
                pass
        self._overview_results.clear()
        self._overview_figures.clear()
        self._overview_canvases.clear()
        self._overview_result = None
        self._overview_figure = None
        while self.overview_preview_tabs.count():
            widget = self.overview_preview_tabs.widget(0)
            self.overview_preview_tabs.removeTab(0)
            if widget is not None:
                widget.deleteLater()

    def _set_overview_results(self, results: list[SpectralOverviewResult]) -> None:
        rendered: list[tuple[SpectralOverviewResult, Figure]] = []
        for result in results:
            rendered.append((result, render_spectral_overview_figure(result)))

        self._clear_overview_previews()
        for result, figure in rendered:
            focus_code = str(result.focus_code)
            canvas = FigureCanvas(figure)
            canvas.setMinimumSize(1400, 1000)
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(False)
            scroll.setWidget(canvas)
            self.overview_preview_tabs.addTab(scroll, f"Focus {focus_code}")
            self._overview_results[focus_code] = result
            self._overview_figures[focus_code] = figure
            self._overview_canvases[focus_code] = canvas
            canvas.draw_idle()
        self.overview_preview_tabs.setCurrentIndex(0)
        self._on_overview_preview_tab_changed(0)

    @Slot(int)
    def _on_overview_preview_tab_changed(self, index: int) -> None:
        if index < 0:
            self._overview_result = None
            self._overview_figure = None
            self.overview_export_btn.setEnabled(False)
            return
        focus_code = self.overview_preview_tabs.tabText(index).removeprefix("Focus ").strip()
        self._overview_result = self._overview_results.get(focus_code)
        self._overview_figure = self._overview_figures.get(focus_code)
        self.overview_export_btn.setEnabled(
            self._overview_figure is not None
            and not (self._overview_thread is not None and self._overview_thread.isRunning())
        )

    @Slot(object)
    def _on_spectral_overview_finished(self, payload):
        if isinstance(payload, SpectralOverviewResult):
            results = [payload]
            errors = []
        elif isinstance(payload, dict):
            results = list(payload.get("results") or [])
            errors = list(payload.get("errors") or [])
        else:
            self._on_spectral_overview_failed("Unexpected spectral overview result.")
            return
        if not results or not all(isinstance(result, SpectralOverviewResult) for result in results):
            self._on_spectral_overview_failed("No valid focus-code spectral overview was returned.")
            return
        try:
            self._set_overview_results(results)
        except Exception as exc:
            self._on_spectral_overview_failed(f"Could not render spectral overview:\n{exc}")
            return

        total_files = sum(result.total_sources for result in results)
        loaded_files = sum(result.loaded_sources for result in results)
        total_warnings = sum(len(result.warnings) for result in results) + len(errors)
        focus_codes = ", ".join(result.focus_code for result in results)
        status = (
            f"Generated {len(results)} focus-code overview(s): {focus_codes}. "
            f"Loaded {loaded_files}/{total_files} FITS file(s)."
        )
        warning_details = errors + [
            f"Focus {result.focus_code}: {warning}"
            for result in results
            for warning in result.warnings
        ]
        if total_warnings:
            status += f" {total_warnings} warning(s); hover for details."
            self.overview_status_label.setToolTip("\n".join(warning_details))
        else:
            self.overview_status_label.setToolTip("")
        self.overview_status_label.setText(status)
        self.overview_export_btn.setEnabled(self._overview_figure is not None)

    @Slot(str)
    def _on_spectral_overview_failed(self, message: str):
        self.overview_status_label.setText("Spectral overview generation failed.")
        QMessageBox.critical(
            self,
            "Spectral Overview Failed",
            str(message or "Could not generate the spectral overview."),
        )

    @Slot()
    def _on_spectral_overview_cancelled(self):
        self.overview_status_label.setText("Spectral overview generation cancelled.")

    def export_spectral_overview(self):
        if self._overview_figure is None or self._overview_result is None:
            QMessageBox.information(self, "Export Spectral Overview", "Generate a spectral overview before exporting.")
            return
        result = self._overview_result
        default_name = (
            f"{result.station}_{result.observation_date:%Y%m%d}_{result.focus_code}_spectral_overview"
        )
        path, ext = pick_export_path(
            self,
            "Export Spectral Overview",
            default_name,
            _OVERVIEW_EXPORT_FILTERS,
            default_filter="PNG (*.png)",
        )
        if not path:
            return
        try:
            current_ext = os.path.splitext(path)[1].lstrip(".").lower()
            export_ext = current_ext or str(ext or "png").lower()
            export_format = "tiff" if export_ext in {"tif", "tiff"} else export_ext
            self._overview_figure.savefig(
                path,
                dpi=300,
                bbox_inches="tight",
                facecolor="white",
                format=export_format,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export Spectral Overview Failed",
                f"Could not export spectral overview:\n{exc}",
            )
            return
        QMessageBox.information(self, "Export Spectral Overview", f"Spectral overview saved:\n{path}")

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
        self.event_compare_btn.setEnabled(has_results)
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

    def compare_selected_event_files(self):
        selected = self._checked_event_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select files to compare.")
            return

        self.comparison_request.emit([candidate.url for candidate in selected])
        self.accept()

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

    def handle_compare(self):
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

        self.comparison_request.emit(urls)
        self.accept()

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

    def closeEvent(self, event):
        if self._overview_thread is not None and self._overview_thread.isRunning():
            self._overview_close_after_finish = True
            if self._overview_worker is not None:
                self._overview_worker.request_cancel()
            event.ignore()
            return
        if hasattr(self, "overview_preview_tabs"):
            self._clear_overview_previews()
        super().closeEvent(event)
