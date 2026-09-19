"""Resolve catalog bursts to the existing downloader's FITS candidates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
import re
from threading import Event
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlsplit

from PySide6.QtCore import QObject, Signal, Slot

from src.backend.radio.callisto_archive import (
    REQUEST_TIMEOUT,
    build_archive_session,
    day_url,
    extract_fits_links,
)
from src.backend.radio.callisto_naming import parse_callisto_archive_filename

if TYPE_CHECKING:
    from src.backend.radio.burst_list import BurstEvent
    from src.ui.downloads.callisto_downloader import CallistoEventCandidate


NOMINAL_FITS_DURATION = timedelta(seconds=900)
_EXPLICIT_STOP_RE = re.compile(
    r"_\d{8}_\d{6}_(?P<stop>\d{6})_[^_]+\.fits?(?:\.gz)?$", re.IGNORECASE
)

# Expand only documented station spellings. In particular, a site prefix must
# not select unrelated stations, and choosing LANCE-A must not select LANCE-B.
_STATION_ALIASES = {
    "mexico-lance": ("mexico-lance-a", "mexico-lance-b"),
    "uk-glasgow": ("glasgow",),
    "glasgow": ("uk-glasgow",),
    "glsagow": ("glasgow", "uk-glasgow"),
    "alaska-haar": ("alaska-haarp",),
    "malaysia_banting": ("malaysia-banting",),
    "malaysia-banting": ("malaysia_banting",),
}


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _catalog_window(start_dt: datetime, stop_dt: datetime) -> tuple[datetime, datetime]:
    start = _utc_naive(start_dt)
    stop = _utc_naive(stop_dt)
    if stop < start:
        raise ValueError("The burst end time must be at or after its start time.")
    # Monstein catalog times have minute precision. Include that entire final
    # minute, also for an impulsive burst whose start and end are identical.
    return start, stop + timedelta(minutes=1)


def burst_archive_dates(start_dt: datetime, stop_dt: datetime) -> list[date]:
    """UTC directories needed for a catalog interval, including FITS lookback.

    ``stop_dt`` is the catalog's inclusive final minute. The preceding UTC
    date is checked as well: a filename with an explicit stop can describe a
    longer observation than the nominal 900 seconds and cross midnight.
    """
    start, stop = _catalog_window(start_dt, stop_dt)
    current = start.date() - timedelta(days=1)
    last = (stop - timedelta(microseconds=1)).date()
    dates = []
    while current <= last:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _station_key(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def station_matches_burst(archive_station: str, selected_station: str) -> bool:
    """Match an exact station or an explicitly supported catalog alias."""
    archive_key = _station_key(archive_station)
    selected_key = _station_key(selected_station)
    return bool(selected_key) and (
        archive_key == selected_key or archive_key in _STATION_ALIASES.get(selected_key, ())
    )


def _observation_stop(filename: str, start: datetime) -> datetime:
    explicit = _EXPLICIT_STOP_RE.search(filename)
    if explicit is None:
        return start + NOMINAL_FITS_DURATION
    stop = datetime.strptime(start.strftime("%Y%m%d") + explicit.group("stop"), "%Y%m%d%H%M%S")
    if stop < start:
        stop += timedelta(days=1)
    return stop


def filter_burst_candidates(
    hrefs: list[str],
    *,
    day_url: str,
    selected_stations: list[str],
    start_dt: datetime,
    stop_dt: datetime,
) -> list[CallistoEventCandidate]:
    """Return files whose observation intervals overlap the catalog minutes.

    Archive filenames normally mark the beginning of a 900-second segment;
    when a filename supplies an explicit stop time, that time takes precedence.
    """
    # callisto_downloader imports the Burst List widget; keep this dependency
    # local so adding its tab does not create a module import cycle.
    from src.ui.downloads.callisto_downloader import CallistoEventCandidate, sort_event_candidates

    start, stop = _catalog_window(start_dt, stop_dt)
    candidates = []
    for href in hrefs:
        filename = unquote(os.path.basename(urlsplit(str(href or "")).path))
        try:
            station, observed_at, receiver_id = parse_callisto_archive_filename(filename)
            observed_stop = _observation_stop(filename, observed_at)
        except ValueError:
            continue
        if observed_at >= stop or observed_stop <= start:
            continue
        if not any(station_matches_burst(station, selected) for selected in selected_stations):
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
    return sort_event_candidates(candidates)


class BurstFitsWorker(QObject):
    """Read archive listings in a worker thread, leaving transfers to the UI."""

    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, event: BurstEvent, stations: list[str], padding_minutes: int = 0):
        super().__init__()
        # QObject.event is a virtual method used when Qt changes thread
        # affinity; storing the catalog event there breaks moveToThread().
        self.burst_event = event
        self.stations = list(dict.fromkeys(
            str(station or "").strip() for station in stations if str(station or "").strip()
        ))
        self.padding_minutes = padding_minutes
        self._cancel_requested = Event()

    @Slot()
    def request_cancel(self):
        self._cancel_requested.set()

    @Slot()
    def run(self):
        from src.ui.downloads.callisto_downloader import sort_event_candidates

        candidates = []
        warnings = []

        def cancelled():
            if not self._cancel_requested.is_set():
                return False
            self.finished.emit({"candidates": [], "warnings": warnings, "cancelled": True})
            return True

        if cancelled():
            return
        try:
            if not self.stations:
                raise ValueError("Select at least one station to find FITS files.")
            if self.padding_minutes < 0:
                raise ValueError("Burst padding must not be negative.")
            _catalog_window(self.burst_event.start_utc, self.burst_event.end_utc)
            padding = timedelta(minutes=self.padding_minutes)
            start = _utc_naive(self.burst_event.start_utc) - padding
            stop = _utc_naive(self.burst_event.end_utc) + padding
            dates = burst_archive_dates(start, stop)
            successful_days = 0
            with build_archive_session() as session:
                for index, observation_date in enumerate(dates):
                    if cancelled():
                        return
                    url = day_url(observation_date)
                    self.progress.emit(index, len(dates), f"Reading FITS archive for {observation_date:%Y-%m-%d}...")
                    if cancelled():
                        return
                    try:
                        with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                            if response.status_code >= 400:
                                raise RuntimeError(f"HTTP {response.status_code} for {url}")
                            hrefs = extract_fits_links(response.text)
                        if cancelled():
                            return
                        candidates.extend(filter_burst_candidates(
                            hrefs,
                            day_url=url,
                            selected_stations=self.stations,
                            start_dt=start,
                            stop_dt=stop,
                        ))
                        successful_days += 1
                    except Exception as exc:
                        if cancelled():
                            return
                        warnings.append(f"{observation_date:%Y-%m-%d}: {exc}")
                    self.progress.emit(index + 1, len(dates), f"Checked {index + 1} of {len(dates)} archive days.")
            if cancelled():
                return
            candidates = sort_event_candidates(candidates)
            if not successful_days:
                self.finished.emit({
                    "candidates": [], "warnings": warnings,
                    "error": "Unable to read the FITS archive for the selected burst.",
                })
                return
            missing = [
                station for station in self.stations
                if not any(station_matches_burst(candidate.station, station) for candidate in candidates)
            ]
            if missing:
                warnings.append("No overlapping archive files found for: " + ", ".join(missing) + ".")
            self.finished.emit({"candidates": candidates, "warnings": warnings})
        except Exception as exc:
            if cancelled():
                return
            self.finished.emit({"candidates": [], "warnings": warnings, "error": str(exc)})
