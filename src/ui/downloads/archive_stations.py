"""
e-CALLISTO FITS Analyzer
Version 3.1.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Station pickers that follow the e-CALLISTO archive instead of a fixed list.

Whenever a picker's date changes it asks its ArchiveStationLoader, which reads
the UTC day directory listings off the GUI thread. One application-wide cache
shares each day's stations between the downloader tabs and across reopenings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import time

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QComboBox, QLabel

from src.backend.radio.callisto_archive import build_archive_session, fetch_day_stations

# Coalesce the dateChanged bursts of typing or stepping through a date.
LOOKUP_DELAY_MS = 400
# A finished UTC day no longer changes. The current and previous days still
# receive uploads, so their station lists are read again after this long.
RECENT_DAY_TTL_SECONDS = 600


def fetch_stations_for_day(observation_date) -> list[str]:
    with build_archive_session() as session:
        return fetch_day_stations(observation_date, session)


def merge_station_lists(station_lists) -> list[str]:
    """Union of station lists, case-insensitively, sorted like the archive listing."""
    stations: dict[str, str] = {}
    for station_list in station_lists:
        for station in station_list:
            stations.setdefault(str(station).casefold(), str(station))
    return sorted(stations.values(), key=str.casefold)


def describe_days(days) -> str:
    days = list(days)
    if not days:
        return "the selected dates"
    if len(days) == 1:
        return f"{days[0]:%Y-%m-%d}"
    return f"{days[0]:%Y-%m-%d} to {days[-1]:%Y-%m-%d}"


def _is_recent(day) -> bool:
    return day >= datetime.now(timezone.utc).date() - timedelta(days=1)


class _DayStationsTask(QRunnable):
    def __init__(self, service: "_ArchiveStationService", day, fetch):
        super().__init__()
        self._service = service
        self._day = day
        self._fetch = fetch

    def run(self):
        try:
            stations, error = tuple(self._fetch(self._day)), ""
        except Exception as exc:
            stations, error = None, str(exc) or exc.__class__.__name__
        try:
            self._service.dayFetched.emit(self._day, stations, error)
        except RuntimeError:
            # The application is shutting down and the service is gone.
            pass


class _ArchiveStationService(QObject):
    """Per-day station lists, fetched at most once at a time for each day."""

    dayResolved = Signal(object)
    dayFetched = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stations: dict = {}
        self._errors: dict = {}
        self._pending: set = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self.dayFetched.connect(self._store)

    def cached(self, day):
        entry = self._stations.get(day)
        if entry is None:
            return None
        fetched_at, stations = entry
        if _is_recent(day) and time.monotonic() - fetched_at > RECENT_DAY_TTL_SECONDS:
            return None
        return stations

    def error(self, day) -> str:
        return self._errors.get(day, "")

    def fetch(self, day) -> None:
        if day in self._pending:
            return
        self._pending.add(day)
        self._errors.pop(day, None)
        # Resolve the fetch function now, on the GUI thread.
        self._pool.start(_DayStationsTask(self, day, fetch_stations_for_day))

    def forget(self, days=None) -> None:
        if days is None:
            self._stations.clear()
            self._errors.clear()
            return
        for day in days:
            self._stations.pop(day, None)
            self._errors.pop(day, None)

    @Slot(object, object, str)
    def _store(self, day, stations, error):
        self._pending.discard(day)
        if stations is None:
            self._errors[day] = error
        else:
            self._stations[day] = (time.monotonic(), stations)
        self.dayResolved.emit(day)


_service: _ArchiveStationService | None = None


def _station_service() -> _ArchiveStationService:
    global _service
    if _service is None:
        _service = _ArchiveStationService(QCoreApplication.instance())
    return _service


def clear_station_cache() -> None:
    if _service is not None:
        _service.forget()


class ArchiveStationLoader(QObject):
    """Debounced station lookup for one picker; reports the union over its days."""

    loading = Signal()
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None, *, delay_ms: int | None = None):
        super().__init__(parent)
        self._days: tuple = ()
        self._state = "idle"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LOOKUP_DELAY_MS if delay_ms is None else delay_ms)
        self._timer.timeout.connect(self._fetch_missing)
        self._service = _station_service()
        self._service.dayResolved.connect(self._on_day_resolved)

    def days(self) -> tuple:
        return self._days

    def state(self) -> str:
        """``idle``, ``loading``, ``loaded`` or ``failed``."""
        return self._state

    def request(self, days) -> None:
        """Look up the stations for ``days``; a newer request supersedes this one."""
        self._days = tuple(dict.fromkeys(days))
        self._timer.stop()
        if self._finish_from_cache():
            return
        self._state = "loading"
        self.loading.emit()
        self._timer.start()

    def reload(self) -> None:
        """Read the current days from the archive again, ignoring cached lists."""
        self._service.forget(self._days)
        self.request(self._days)

    def cancel(self) -> None:
        """Drop the current request; nothing more is reported for it."""
        self._timer.stop()
        self._days = ()
        self._state = "idle"

    @Slot()
    def _fetch_missing(self):
        for day in self._days:
            if self._service.cached(day) is None:
                self._service.fetch(day)

    @Slot(object)
    def _on_day_resolved(self, day):
        if self._state != "loading" or day not in self._days:
            return
        error = self._service.error(day)
        if error and self._service.cached(day) is None:
            self._timer.stop()
            self._state = "failed"
            self.failed.emit(error)
            return
        self._finish_from_cache()

    def _finish_from_cache(self) -> bool:
        station_lists = [self._service.cached(day) for day in self._days]
        if any(stations is None for stations in station_lists):
            return False
        self._timer.stop()
        self._state = "loaded"
        self.loaded.emit(merge_station_lists(station_lists))
        return True


class StationComboBinding(QObject):
    """Fill a QComboBox from an ArchiveStationLoader.

    A station the user picked stays selected on every later date that has it.
    """

    def __init__(self, combo: QComboBox, loader: ArchiveStationLoader):
        super().__init__(combo)
        self._combo = combo
        self._preferred = ""
        combo.textActivated.connect(self._remember)
        loader.loading.connect(self._show_loading)
        loader.loaded.connect(self._fill)
        loader.failed.connect(self._show_failed)

    @Slot(str)
    def _remember(self, station: str):
        self._preferred = station

    def _show_empty(self, placeholder: str) -> None:
        self._combo.setPlaceholderText(placeholder)
        self._combo.clear()

    @Slot()
    def _show_loading(self):
        self._show_empty("Loading stations...")

    @Slot(str)
    def _show_failed(self, _message: str):
        self._show_empty("Stations unavailable")

    @Slot(object)
    def _fill(self, stations):
        stations = list(stations)
        if not stations:
            self._show_empty("No stations on this date")
            return
        self._combo.clear()
        self._combo.addItems(stations)
        keys = [station.casefold() for station in stations]
        preferred = self._preferred.casefold()
        self._combo.setCurrentIndex(keys.index(preferred) if preferred in keys else 0)


class StationAvailabilityLabel(QLabel):
    """One-line status for an ArchiveStationLoader, with a retry link after a failure."""

    def __init__(self, loader: ArchiveStationLoader, parent=None):
        super().__init__(parent)
        self.setObjectName("DownloaderStatusLabel")
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self._loader = loader
        self._summary = ""
        loader.loading.connect(self._show_loading)
        loader.loaded.connect(self._show_loaded)
        loader.failed.connect(self._show_failed)
        self.linkActivated.connect(lambda _link: loader.reload())

    def summary(self) -> str:
        """The current status as plain text."""
        return self._summary

    def show_message(self, text: str) -> None:
        self._set(text, escape(text))

    def _set(self, summary: str, html: str, tooltip: str = "") -> None:
        self._summary = summary
        self.setText(html)
        self.setToolTip(tooltip)

    @Slot()
    def _show_loading(self):
        self.show_message(f"Loading the stations with data on {describe_days(self._loader.days())} (UTC)...")

    @Slot(object)
    def _show_loaded(self, stations):
        days = describe_days(self._loader.days())
        count = len(stations)
        if count:
            noun = "station has" if count == 1 else "stations have"
            self.show_message(f"{count} {noun} data on {days} (UTC).")
        else:
            self.show_message(f"No station has published data for {days} (UTC).")

    @Slot(str)
    def _show_failed(self, message: str):
        summary = f"Could not load the stations for {describe_days(self._loader.days())}."
        self._set(summary, f'{escape(summary)} <a href="retry">Retry</a>', message)
