"""
e-CALLISTO FITS Analyzer
Version 3.0.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.

Walking a loaded dataset forwards and backwards through the archive.

A CALLISTO dataset in the main window is one or more 15-minute observations of
the same station, each covering the same set of focus codes.  This module reads
that structure back off the source filenames and resolves the file set for the
adjacent observation, preferring files already sitting next to the current ones
on disk and falling back to the archive day listing.

The adjacency window matches ``burst_processor``'s combine rules exactly, so any
segment resolved here is combinable by construction.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from src.Backend.callisto_archive import (
    REQUEST_TIMEOUT,
    build_archive_session,
    day_url,
    extract_fits_links,
)
from src.Backend.callisto_naming import _FITS_SUFFIXES, parse_callisto_archive_filename
from src.Backend.burst_processor import (
    MAX_CONSECUTIVE_SECONDS,
    MIN_CONSECUTIVE_SECONDS,
    _normalize_focus_code,
)

DIRECTION_NEXT = "next"
DIRECTION_PREVIOUS = "previous"

#: Nominal spacing when a dataset has a single segment and no measured step.
NOMINAL_STEP_SECONDS = 900.0


@dataclass(frozen=True)
class TimelineSegment:
    """One observation timestamp and the file(s) covering its focus codes."""

    observed_at: datetime
    focus_codes: tuple[str, ...]
    paths: tuple[str, ...]


@dataclass(frozen=True)
class TimelineState:
    """The segment structure of a loaded dataset."""

    station: str
    focus_codes: tuple[str, ...]
    segments: tuple[TimelineSegment, ...]
    step_seconds: float

    @property
    def start(self) -> datetime:
        return self.segments[0].observed_at

    @property
    def stop(self) -> datetime:
        return self.segments[-1].observed_at

    @property
    def paths(self) -> list[str]:
        return [path for segment in self.segments for path in segment.paths]


@dataclass(frozen=True)
class ArchiveEntry:
    station: str
    observed_at: datetime
    focus_code: str
    filename: str
    url: str


class TimelineUnavailable(Exception):
    """No adjacent segment could be resolved; the message says why."""


def _station_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def describe_timeline(paths) -> TimelineState:
    """Read the segment structure out of a dataset's source filenames."""
    cleaned = [str(path) for path in list(paths or []) if str(path or "").strip()]
    if not cleaned:
        raise ValueError("No source files to describe.")

    stations: set[str] = set()
    grouped: dict[datetime, dict[str, str]] = {}
    for path in cleaned:
        station, observed_at, focus_code = parse_callisto_archive_filename(os.path.basename(path))
        stations.add(_station_key(station))
        cells = grouped.setdefault(observed_at, {})
        cells[_normalize_focus_code(focus_code)] = path

    if len(stations) != 1:
        raise ValueError(
            "A timeline must come from one station; found: " + ", ".join(sorted(stations)) + "."
        )

    # Take the display station from the first file so casing is preserved.
    station = parse_callisto_archive_filename(os.path.basename(cleaned[0]))[0]

    ordered = sorted(grouped)
    focus_codes = tuple(sorted({focus for cells in grouped.values() for focus in cells}))
    for observed_at in ordered:
        if set(grouped[observed_at]) != set(focus_codes):
            raise ValueError(
                "Every observation in a timeline must cover the same focus codes; "
                f"{observed_at:%Y-%m-%d %H:%M:%S} does not."
            )

    segments = tuple(
        TimelineSegment(
            observed_at=observed_at,
            focus_codes=focus_codes,
            paths=tuple(grouped[observed_at][focus] for focus in focus_codes),
        )
        for observed_at in ordered
    )

    if len(ordered) >= 2:
        step = float((ordered[1] - ordered[0]).total_seconds())
    else:
        step = NOMINAL_STEP_SECONDS

    return TimelineState(
        station=station,
        focus_codes=focus_codes,
        segments=segments,
        step_seconds=step,
    )


def _normalize_direction(direction: str) -> str:
    value = str(direction or "").strip().lower()
    if value not in (DIRECTION_NEXT, DIRECTION_PREVIOUS):
        raise ValueError(f"Direction must be '{DIRECTION_NEXT}' or '{DIRECTION_PREVIOUS}'.")
    return value


def target_timestamp(state: TimelineState, direction: str) -> datetime:
    """Return the nominal timestamp of the segment adjacent to ``state``."""
    if _normalize_direction(direction) == DIRECTION_NEXT:
        return state.stop + timedelta(seconds=state.step_seconds)
    return state.start - timedelta(seconds=state.step_seconds)


def _edge_timestamp(state: TimelineState, direction: str) -> datetime:
    return state.stop if direction == DIRECTION_NEXT else state.start


def _offset_is_consecutive(edge: datetime, candidate: datetime, direction: str) -> bool:
    """True when ``candidate`` sits one valid observation step from ``edge``.

    The bounds are burst_processor's, so anything accepted here will also be
    accepted by ``combine_time`` when the files are actually merged.
    """
    delta = float((candidate - edge).total_seconds())
    if direction == DIRECTION_PREVIOUS:
        delta = -delta
    return MIN_CONSECUTIVE_SECONDS <= delta <= MAX_CONSECUTIVE_SECONDS


def _pick_closest(edge: datetime, direction: str, stamps) -> datetime | None:
    target = None
    best = None
    for stamp in stamps:
        if not _offset_is_consecutive(edge, stamp, direction):
            continue
        distance = abs(float((stamp - edge).total_seconds()))
        if best is None or distance < best:
            best = distance
            target = stamp
    return target


# -----------------------------
# Local sibling lookup
# -----------------------------
def _candidate_directories(state: TimelineState) -> list[str]:
    seen: set[str] = set()
    directories: list[str] = []
    for segment in state.segments:
        for path in segment.paths:
            folder = os.path.dirname(os.path.abspath(path))
            if folder and folder not in seen and os.path.isdir(folder):
                seen.add(folder)
                directories.append(folder)
    return directories


def find_local_segment(state: TimelineState, direction: str) -> list[str] | None:
    """Return the adjacent segment's files from the folders already in use.

    Returns ``None`` when the folders hold no complete adjacent observation, so
    the caller can fall through to the archive.
    """
    direction = _normalize_direction(direction)
    edge = _edge_timestamp(state, direction)
    wanted = set(state.focus_codes)
    station_key = _station_key(state.station)

    found: dict[datetime, dict[str, str]] = {}
    for folder in _candidate_directories(state):
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for name in entries:
            if not name.lower().endswith(_FITS_SUFFIXES):
                continue
            try:
                station, observed_at, focus_code = parse_callisto_archive_filename(name)
            except ValueError:
                continue
            if _station_key(station) != station_key:
                continue
            if not _offset_is_consecutive(edge, observed_at, direction):
                continue
            focus = _normalize_focus_code(focus_code)
            if focus not in wanted:
                continue
            found.setdefault(observed_at, {}).setdefault(focus, os.path.join(folder, name))

    complete = [stamp for stamp, cells in found.items() if set(cells) == wanted]
    target = _pick_closest(edge, direction, complete)
    if target is None:
        return None
    cells = found[target]
    return [cells[focus] for focus in state.focus_codes]


# -----------------------------
# Archive day listing
# -----------------------------
_DAY_CACHE: dict[date, tuple[float, list[ArchiveEntry]]] = {}

#: A finished day never gains files, so it is cached for the process.  The
#: current UTC day is still being written to, so it expires quickly enough that
#: a user following live data sees new observations appear.
TODAY_CACHE_TTL_SECONDS = 120.0


def clear_day_cache() -> None:
    _DAY_CACHE.clear()


def _cache_is_fresh(observation_date: date, stored_at: float) -> bool:
    if observation_date < datetime.now(timezone.utc).date():
        return True
    return (time.monotonic() - stored_at) < TODAY_CACHE_TTL_SECONDS


def list_archive_day(observation_date: date, *, session=None, use_cache: bool = True) -> list[ArchiveEntry]:
    """List one archive day, parsed into entries.

    Past days are cached for the life of the process; the current day expires
    after :data:`TODAY_CACHE_TTL_SECONDS` so live observations become visible.
    """
    key = observation_date
    if use_cache and key in _DAY_CACHE:
        stored_at, cached = _DAY_CACHE[key]
        if _cache_is_fresh(observation_date, stored_at):
            return cached

    url = day_url(observation_date)
    owns_session = session is None
    if owns_session:
        session = build_archive_session()

    try:
        with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            status = int(getattr(response, "status_code", 200))
            if status == 404:
                entries: list[ArchiveEntry] = []
                _DAY_CACHE[key] = (time.monotonic(), entries)
                return entries
            if status >= 400:
                raise TimelineUnavailable(f"Archive returned HTTP {status} for {observation_date}.")
            hrefs = extract_fits_links(response.text)
    except TimelineUnavailable:
        raise
    except Exception as exc:
        raise TimelineUnavailable(f"Could not reach the archive: {exc}") from exc
    finally:
        if owns_session:
            session.close()

    entries = []
    for href in hrefs:
        filename = os.path.basename(href)
        try:
            station, observed_at, focus_code = parse_callisto_archive_filename(filename)
        except ValueError:
            continue
        entries.append(
            ArchiveEntry(
                station=station,
                observed_at=observed_at,
                focus_code=_normalize_focus_code(focus_code),
                filename=filename,
                url=urljoin(url, href),
            )
        )

    _DAY_CACHE[key] = (time.monotonic(), entries)
    return entries


def _relevant_days(edge: datetime, direction: str) -> list[date]:
    """Days that could hold the adjacent observation, nearest first.

    The step can cross midnight, so the neighbouring day has to be searched too.
    """
    days = [edge.date()]
    neighbour = (edge + timedelta(days=1)).date() if direction == DIRECTION_NEXT else (
        edge - timedelta(days=1)
    ).date()
    if neighbour != days[0]:
        days.append(neighbour)
    return days


def find_archive_segment(
    state: TimelineState,
    direction: str,
    *,
    session=None,
) -> list[ArchiveEntry]:
    """Return the archive entries forming the adjacent segment.

    Raises :class:`TimelineUnavailable` with a human-readable reason when the
    archive has nothing usable there.
    """
    direction = _normalize_direction(direction)
    edge = _edge_timestamp(state, direction)
    wanted = set(state.focus_codes)
    station_key = _station_key(state.station)

    owns_session = session is None
    if owns_session:
        session = build_archive_session()

    try:
        found: dict[datetime, dict[str, ArchiveEntry]] = {}
        for day in _relevant_days(edge, direction):
            for entry in list_archive_day(day, session=session):
                if _station_key(entry.station) != station_key:
                    continue
                if not _offset_is_consecutive(edge, entry.observed_at, direction):
                    continue
                if entry.focus_code not in wanted:
                    continue
                found.setdefault(entry.observed_at, {}).setdefault(entry.focus_code, entry)
    finally:
        if owns_session:
            session.close()

    if not found:
        when = "after" if direction == DIRECTION_NEXT else "before"
        raise TimelineUnavailable(
            f"No {state.station} data {when} {edge:%H:%M} in the archive."
        )

    complete = [stamp for stamp, cells in found.items() if set(cells) == wanted]
    target = _pick_closest(edge, direction, complete)
    if target is None:
        partial = _pick_closest(edge, direction, list(found))
        if partial is not None:
            missing = sorted(wanted - set(found[partial]))
            raise TimelineUnavailable(
                f"{state.station} at {partial:%H:%M} is missing focus code(s) "
                f"{', '.join(missing)}; the combined set cannot be extended."
            )
        when = "after" if direction == DIRECTION_NEXT else "before"
        raise TimelineUnavailable(
            f"No consecutive {state.station} observation {when} {edge:%H:%M}."
        )

    cells = found[target]
    return [cells[focus] for focus in state.focus_codes]


def resolve_segment(
    state: TimelineState,
    direction: str,
    *,
    session=None,
    progress_cb=None,
) -> list[str]:
    """Return local paths for the adjacent segment, downloading if needed.

    Files already sitting beside the current sources are used as-is; anything
    else is pulled through the shared cache.
    """
    local = find_local_segment(state, direction)
    if local:
        if callable(progress_cb):
            progress_cb(1.0, f"Using local files for {os.path.basename(local[0])}")
        return local

    from src.Backend import callisto_cache

    entries = find_archive_segment(state, direction, session=session)
    paths: list[str] = []
    for entry in entries:
        path, was_cached = callisto_cache.fetch_cached(
            entry.url, entry.filename, session=session, progress_cb=progress_cb
        )
        if callable(progress_cb):
            verb = "Using cached" if was_cached else "Downloaded"
            progress_cb(1.0, f"{verb} {entry.filename}")
        paths.append(str(path))
    return paths


def probe_segment(state: TimelineState, direction: str, *, session=None) -> tuple[bool, str]:
    """Report whether an adjacent segment exists, and why not when it does not."""
    try:
        if find_local_segment(state, direction):
            return True, ""
        find_archive_segment(state, direction, session=session)
        return True, ""
    except TimelineUnavailable as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def extended_paths(state: TimelineState, new_paths, direction: str) -> list[str]:
    """Merge a resolved segment into the dataset's path list, time-ordered."""
    direction = _normalize_direction(direction)
    if direction == DIRECTION_NEXT:
        return [*state.paths, *new_paths]
    return [*new_paths, *state.paths]


def trimmed_paths(state: TimelineState, edge: str) -> tuple[list[str], float]:
    """Drop the first or last segment.

    Returns the remaining paths and the time shift the removal causes: negative
    by one segment's duration when trimming the start, zero when trimming the
    end.
    """
    value = str(edge or "").strip().lower()
    if value not in ("start", "end"):
        raise ValueError("Trim edge must be 'start' or 'end'.")
    if len(state.segments) < 2:
        raise ValueError("A single-observation dataset cannot be trimmed further.")

    if value == "start":
        remaining = state.segments[1:]
        shift = -float((remaining[0].observed_at - state.start).total_seconds())
    else:
        remaining = state.segments[:-1]
        shift = 0.0

    return [path for segment in remaining for path in segment.paths], shift
